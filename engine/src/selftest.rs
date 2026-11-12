//! A test without a sound card: a sender and a "sound card" thread on this machine, real UDP
//! through a relay that adds jitter and loss, and a clock skew between the two ends.
//! Bursts of a 1 kHz tone go out every 0.5 s; the latency is how long each takes to come out.
//! It measures the network + jitter buffer part only (the device buffers come on top).

use crate::codec::{Decoder, Encoder, RATE};
use crate::net::{spawn_receiver, Sender};
use crate::packet::Codec;
use crate::playout::{Item, Playout};
use crate::stats::Stats;
use anyhow::Result;
use ringbuf::traits::{Consumer, Split};
use ringbuf::HeapRb;
use std::net::UdpSocket;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub struct SelftestOpts {
    pub secs: u64,
    pub codec: Codec,
    pub frame: usize,
    pub bitrate: i32,
    pub jitter_ms: f64,
    pub loss: f64,
    pub skew_ppm: f64,
    pub block: usize,
    pub min_target: u32,
    pub max_target: u32,
}

pub struct Report {
    pub latencies_ms: Vec<f64>,
    pub lost: u64,
    pub underruns: u64,
    pub target: u32,
    pub ratio_ppm: i32,
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> f64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        (self.0 >> 11) as f64 / (1u64 << 53) as f64
    }
}

fn sleep_until(t: Instant) {
    loop {
        let now = Instant::now();
        if now >= t {
            return;
        }
        let left = t - now;
        if left > Duration::from_micros(1500) {
            std::thread::sleep(left - Duration::from_micros(1000));
        } else {
            std::hint::spin_loop();
        }
    }
}

const PERIOD: u64 = RATE as u64 / 2; // a burst every 0.5 s
const BURST: u64 = 240; // 5 ms of tone

pub fn run(o: SelftestOpts, verbose: bool) -> Result<Report> {
    let stats = Arc::new(Stats::default());
    let stop = Arc::new(AtomicBool::new(false));
    let tx_sock = UdpSocket::bind("127.0.0.1:0")?;
    let relay = UdpSocket::bind("127.0.0.1:0")?;
    let rx_sock = UdpSocket::bind("127.0.0.1:0")?;
    let relay_addr = relay.local_addr()?;
    let rx_addr = rx_sock.local_addr()?;
    let start = Instant::now() + Duration::from_millis(50);

    // the relay: random delay and loss
    let rstop = stop.clone();
    let (jit, loss) = (o.jitter_ms, o.loss);
    let relay_t = std::thread::spawn(move || {
        relay.set_nonblocking(true).ok();
        let mut rng = Rng(0x9E3779B97F4A7C15);
        let mut q: Vec<(Instant, Vec<u8>)> = Vec::new();
        let mut buf = [0u8; 2048];
        while !rstop.load(Relaxed) {
            while let Ok((n, _)) = relay.recv_from(&mut buf) {
                if rng.next() < loss {
                    continue;
                }
                let due = Instant::now() + Duration::from_secs_f64(rng.next() * jit / 1000.0);
                q.push((due, buf[..n].to_vec()));
            }
            let now = Instant::now();
            q.sort_by_key(|p| p.0);
            while q.first().is_some_and(|p| p.0 <= now) {
                let (_, d) = q.remove(0);
                let _ = relay.send_to(&d, rx_addr);
            }
            std::thread::sleep(Duration::from_micros(200));
        }
    });

    // the sender: a guitar that plays bursts, paced by the wall clock
    let sstop = stop.clone();
    let (frame, secs) = (o.frame, o.secs);
    let mut sender = Sender::new(Encoder::new(o.codec, o.bitrate)?, frame, tx_sock, Arc::new(std::sync::Mutex::new(Some(relay_addr))), stats.clone());
    let send_t = std::thread::spawn(move || {
        let mut idx: u64 = 0;
        while !sstop.load(Relaxed) && idx < secs * RATE as u64 {
            sleep_until(start + Duration::from_secs_f64((idx + frame as u64) as f64 / RATE as f64));
            for _ in 0..frame {
                let ph = idx % PERIOD;
                let s = if ph < BURST { (ph as f32 * 2.0 * std::f32::consts::PI * 1000.0 / RATE as f32).sin() * 0.5 } else { 0.0 };
                sender.push(s);
                idx += 1;
            }
        }
    });

    let (prod, mut cons) = HeapRb::<Item>::new(256).split();
    let _rx = spawn_receiver(rx_sock, prod, start, None, stats.clone(), stop.clone());

    // the "sound card" at the other end, with its own (skewed) clock
    // as the engine does: the buffer always holds a whole card block more than a frame
    let min_target = o.min_target.max(o.block.div_ceil(frame) as u32 + 1);
    let mut play = Playout::new(Decoder::new(o.codec, frame)?, frame, min_target, o.max_target.max(min_target + 8), stats.clone());
    let block = o.block;
    let rate_out = RATE as f64 * (1.0 + o.skew_ppm * 1e-6);
    let mut out = vec![0.0f32; block];
    let mut lat = Vec::new();
    let mut quiet = PERIOD as usize; // samples since the last loud one
    let mut n: u64 = 0;
    let mut next_report = 1.0;
    let mut ppm_seen = 0i32; // the resampler's ratio while still playing (it relaxes once the sender stops)
    // the first burst leaves when the sender has a whole frame of it
    let first_out = Duration::from_secs_f64(frame as f64 / RATE as f64);
    loop {
        let t_block = start + first_out + Duration::from_secs_f64(n as f64 / rate_out);
        if t_block > start + Duration::from_secs(o.secs) + Duration::from_millis(200) {
            break;
        }
        sleep_until(t_block);
        while let Some(it) = cons.try_pop() {
            play.push(&it);
        }
        play.render(&mut out);
        for (i, s) in out.iter().enumerate() {
            if s.abs() > 0.15 {
                if quiet > (PERIOD / 2) as usize {
                    let t_out = (t_block - start).as_secs_f64() + i as f64 / rate_out;
                    let burst_t = (t_out * 2.0).floor() / 2.0; // when that burst began
                    // the sender's sample clock starts at `start`; the burst needs one frame before it can leave
                    lat.push((t_out - burst_t) * 1000.0);
                }
                quiet = 0;
            } else {
                quiet += 1;
            }
        }
        n += block as u64;
        let el = (t_block - start).as_secs_f64();
        if el < o.secs as f64 - 0.3 {
            ppm_seen = stats.ratio_ppm.load(Relaxed);
        }
        if verbose && el >= next_report {
            next_report += 1.0;
            println!("{:>3}s  {}", el as u64, stats.line());
        }
    }
    stop.store(true, Relaxed);
    let _ = send_t.join();
    let _ = relay_t.join();
    Ok(Report {
        latencies_ms: lat,
        lost: stats.lost.load(Relaxed),
        underruns: stats.underruns.load(Relaxed),
        target: stats.target_frames.load(Relaxed),
        ratio_ppm: ppm_seen,
    })
}

pub fn summary(r: &Report) -> String {
    let mut l = r.latencies_ms.clone();
    l.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let pct = |p: f64| if l.is_empty() { f64::NAN } else { l[((l.len() - 1) as f64 * p) as usize] };
    format!(
        "bursts heard {} · latency median {:.2} ms, p90 {:.2} ms, max {:.2} ms · lost {} · underruns {} · target {} frames · drift {:+} ppm",
        l.len(), pct(0.5), pct(0.9), pct(1.0), r.lost, r.underruns, r.target, r.ratio_ppm
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // real-time tests: one at a time, or they fight over the CPU and the numbers mean nothing
    static SERIAL: Mutex<()> = Mutex::new(());

    fn opts(codec: Codec, jitter_ms: f64, loss: f64, skew_ppm: f64) -> SelftestOpts {
        SelftestOpts { secs: 4, codec, frame: 120, bitrate: 128_000, jitter_ms, loss, skew_ppm, block: 64, min_target: 1, max_target: 24 }
    }

    #[test]
    fn clean_link_is_fast() {
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let r = run(opts(Codec::Pcm16, 0.0, 0.0, 0.0), false).unwrap();
        assert!(r.latencies_ms.len() >= 6, "{}", summary(&r));
        let med = { let mut l = r.latencies_ms.clone(); l.sort_by(|a, b| a.partial_cmp(b).unwrap()); l[l.len() / 2] };
        assert!(med < 16.0, "{}", summary(&r));   // ~12 ms on a quiet machine; shared CI runners need room
    }

    #[test]
    fn survives_jitter_loss_and_skew() {
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let r = run(opts(Codec::Opus, 3.0, 0.02, 300.0), false).unwrap();
        assert!(r.latencies_ms.len() >= 6, "{}", summary(&r));
        assert!(r.target >= 2, "{}", summary(&r));
    }
}
