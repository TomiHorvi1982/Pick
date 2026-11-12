//! One running link: the sound card streams, the UDP socket, the receiving thread.
//! Started and stopped from the control socket (or once by `run`); dropping it stops everything.

use crate::audio::{build_input, build_output, config, pick};
use crate::codec::{Decoder, Encoder, RATE};
use crate::net::{spawn_receiver, PeerCell, Sender};
use crate::packet::Codec;
use crate::playout::{Item, Playout};
use crate::stats::{peak_store, Stats};
use anyhow::{bail, Context, Result};
use cpal::traits::{DeviceTrait, StreamTrait};
use cpal::Stream;
use ringbuf::traits::{Consumer, Split};
use ringbuf::HeapRb;
use std::net::{IpAddr, SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Instant;

/// Where the guitar we send comes from.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Source {
    /// straight from the sound card's input channel (the least delay)
    Device,
    /// from the page (Pick's own sound, print/NAM/effects), sent over the control socket at this rate
    Browser { rate: u32 },
}

pub struct StartOpts {
    /// audio system: None = the platform default, Some("asio") etc.
    pub host: Option<String>,
    pub input: Option<String>,
    pub output: Option<String>,
    pub in_ch: usize,
    pub buffer: u32,
    pub source: Source,
    pub codec: Codec,
    pub frame: usize,
    pub bitrate: i32,
    pub min_target: u32,
    pub max_target: u32,
    pub listen: SocketAddr,
}

/// The other one's guitar in my ears: volume and pan, set while playing.
pub struct Mix {
    gain: AtomicU32,
    pan: AtomicU32,
}

impl Mix {
    fn new() -> Self {
        Mix { gain: AtomicU32::new(1f32.to_bits()), pan: AtomicU32::new(0f32.to_bits()) }
    }
    pub fn set(&self, gain: f32, pan: f32) {
        self.gain.store(gain.clamp(0.0, 4.0).to_bits(), Relaxed);
        self.pan.store(pan.clamp(-1.0, 1.0).to_bits(), Relaxed);
    }
    fn get(&self) -> (f32, f32) {
        (f32::from_bits(self.gain.load(Relaxed)), f32::from_bits(self.pan.load(Relaxed)))
    }
}

/// Linear resampling of the page's sound to 48 kHz (44.1 kHz pages; 48 kHz passes straight through).
pub struct Resampler {
    step: f64,
    pos: f64,
    buf: Vec<f32>,
}

impl Resampler {
    pub fn new(rate_in: u32) -> Self {
        Resampler { step: rate_in as f64 / RATE as f64, pos: 0.0, buf: Vec::with_capacity(1 << 16) }
    }
    pub fn process(&mut self, input: &[f32], out: impl FnMut(f32)) {
        self.process_iter(input.iter().copied(), out)
    }
    pub fn process_iter(&mut self, input: impl Iterator<Item = f32>, mut out: impl FnMut(f32)) {
        if (self.step - 1.0).abs() < 1e-9 {
            input.for_each(&mut out);
            return;
        }
        self.buf.extend(input);
        while self.pos + 1.0 < self.buf.len() as f64 {
            let i = self.pos as usize;
            let f = (self.pos - i as f64) as f32;
            out(self.buf[i] * (1.0 - f) + self.buf[i + 1] * f);
            self.pos += self.step;
        }
        let used = (self.pos as usize).min(self.buf.len());
        self.buf.drain(..used);
        self.pos -= used as f64;
    }
}

pub struct Engine {
    _streams: Vec<Stream>,
    stop: Arc<AtomicBool>,
    rx: Option<JoinHandle<()>>,
    peer: Arc<PeerCell>,
    pub mix: Arc<Mix>,
    pub stats: Arc<Stats>,
    pub epoch: Instant,
    pub port: u16,
    pub source: Source,
    pub host: String,
    pub in_rate: u32,
    pub out_rate: u32,
    pub input: String,
    pub output: String,
    pub in_ch: usize,
    pub buffer: u32,
    pub codec: Codec,
    pub frame: usize,
    browser: Option<(Sender, Resampler)>,
}

/// This machine's Tailscale address (100.64.0.0/10), found by asking the OS which local address
/// it would use to reach Tailscale's own resolver. Nothing is sent.
pub fn tailscale_ip() -> Option<IpAddr> {
    let s = UdpSocket::bind("0.0.0.0:0").ok()?;
    s.connect("100.100.100.100:53").ok()?;
    match s.local_addr().ok()?.ip() {
        IpAddr::V4(v4) if v4.octets()[0] == 100 && (64..128).contains(&v4.octets()[1]) => Some(IpAddr::V4(v4)),
        _ => None,
    }
}

fn bind(listen: SocketAddr) -> Result<UdpSocket> {
    // the usual port, or any free one if another engine still holds it
    UdpSocket::bind(listen)
        .or_else(|_| UdpSocket::bind(SocketAddr::new(listen.ip(), 0)))
        .with_context(|| format!("bind {listen}"))
}

impl Engine {
    pub fn start(o: StartOpts) -> Result<Engine> {
        let host = crate::audio::host(&o.host)?;
        let odev = pick(&host, &o.output, false)?;
        let ocfg = config(&odev, false, o.buffer)?;
        let out_rate = ocfg.cfg.sample_rate.0;
        let sock = bind(o.listen)?;
        let port = sock.local_addr()?.port();
        let stats = Arc::new(Stats::default());
        let stop = Arc::new(AtomicBool::new(false));
        let peer: Arc<PeerCell> = Arc::new(Mutex::new(None));
        let mix = Arc::new(Mix::new());
        let epoch = Instant::now();
        let mut streams = Vec::new();

        // receiving: network thread → ring → output callback
        let (prod, mut cons) = HeapRb::<Item>::new(256).split();
        let rx = spawn_receiver(sock.try_clone()?, prod, epoch, Some(peer.clone()), stats.clone(), stop.clone());
        // the sound card takes `buffer` samples at a time (and the far one sends in such bursts too): the buffer
        // must always hold a whole card block more than a frame, or a big block (256, 512) runs it dry
        let min_target = o.min_target.max((o.buffer as usize).div_ceil(o.frame) as u32 + 1);
        let max_target = o.max_target.max(min_target + 8);
        let mut play = Playout::new(Decoder::new(o.codec, o.frame)?, o.frame, min_target, max_target, stats.clone());
        play.set_out_rate(out_rate);
        let och = ocfg.cfg.channels as usize;
        let mut mono = vec![0.0f32; 8192];
        let (mx, st) = (mix.clone(), stats.clone());
        let ostream = build_output(&odev, &ocfg, move |data: &mut [f32], info: &cpal::OutputCallbackInfo| {
            let ts = info.timestamp();
            if let Some(d) = ts.playback.duration_since(&ts.callback) {
                st.out_lat_us.store(d.as_micros() as u32, Relaxed);
            }
            while let Some(it) = cons.try_pop() {
                play.push(&it);
            }
            let (g, pan) = mx.get();
            let (gl, gr) = (g * (1.0 - pan).min(1.0), g * (1.0 + pan).min(1.0));
            for chunk in data.chunks_mut(och * mono.len()) {
                let n = chunk.len() / och;
                play.render(&mut mono[..n]);
                let mut pk = 0.0f32;
                for (fr, s) in chunk.chunks_exact_mut(och).zip(&mono[..n]) {
                    pk = pk.max(s.abs());
                    if och == 1 {
                        fr[0] = s * g;
                    } else {
                        fr[0] = s * gl;
                        fr[1] = s * gr;
                        fr[2..].fill(0.0);
                    }
                }
                peak_store(&st.peer_peak, pk * g);
            }
        })?;
        streams.push(ostream);

        // sending
        let enc = Encoder::new(o.codec, o.bitrate)?;
        let mut sender = Sender::new(enc, o.frame, sock, peer.clone(), stats.clone());
        let mut input_name = String::new();
        let mut in_rate = 0;
        let browser = match o.source {
            Source::Device => {
                let idev = pick(&host, &o.input, true)?;
                let icfg = config(&idev, true, o.buffer)?;
                if o.in_ch == 0 || o.in_ch > icfg.cfg.channels as usize {
                    bail!("input channel {} out of range 1..={}", o.in_ch, icfg.cfg.channels);
                }
                input_name = idev.name().unwrap_or_default();
                let (ich, sel, st) = (icfg.cfg.channels as usize, o.in_ch - 1, stats.clone());
                in_rate = icfg.cfg.sample_rate.0;
                let mut rs = Resampler::new(in_rate);
                let istream = build_input(&idev, &icfg, move |data: &[f32], info: &cpal::InputCallbackInfo| {
                    let ts = info.timestamp();
                    if let Some(d) = ts.callback.duration_since(&ts.capture) {
                        st.in_lat_us.store(d.as_micros() as u32, Relaxed);
                    }
                    let mut pk = 0.0f32;
                    let ch = data.chunks_exact(ich).map(|fr| fr[sel]).inspect(|x| pk = pk.max(x.abs()));
                    rs.process_iter(ch, |y| sender.push(y));
                    peak_store(&st.in_peak, pk);
                })?;
                streams.push(istream);
                None
            }
            Source::Browser { rate } => {
                if !(8_000..=192_000).contains(&rate) {
                    bail!("page sample rate {rate} out of range");
                }
                Some((sender, Resampler::new(rate)))
            }
        };
        for s in &streams {
            s.play()?;
        }
        Ok(Engine {
            _streams: streams,
            stop,
            rx: Some(rx),
            peer,
            mix,
            stats,
            epoch,
            port,
            source: o.source,
            host: host.id().name().to_string(),
            in_rate,
            out_rate,
            input: input_name,
            output: odev.name().unwrap_or_default(),
            in_ch: o.in_ch,
            buffer: o.buffer,
            codec: o.codec,
            frame: o.frame,
            browser,
        })
    }

    pub fn set_peer(&self, p: Option<SocketAddr>) {
        if let Ok(mut g) = self.peer.lock() {
            *g = p;
        }
    }

    pub fn peer(&self) -> Option<SocketAddr> {
        self.peer.lock().ok().and_then(|g| *g)
    }

    /// The page's sound (mono float samples at the rate given in `Source::Browser`).
    pub fn push_browser(&mut self, samples: &[f32]) {
        if let Some((sender, rs)) = self.browser.as_mut() {
            let st = &self.stats;
            let mut pk = 0.0f32;
            rs.process(samples, |s| {
                pk = pk.max(s.abs());
                sender.push(s);
            });
            peak_store(&st.in_peak, pk);
        }
    }

    pub fn info(&self) -> String {
        format!(
            "{} · in: {} · out: {} ({} Hz) · UDP port {} · {:?} {} ms frames · buffer {}",
            self.host,
            match self.source {
                Source::Device => format!("{} (ch {}, {} Hz)", self.input, self.in_ch, self.in_rate),
                Source::Browser { rate } => format!("page ({rate} Hz)"),
            },
            self.output,
            self.out_rate,
            self.port,
            self.codec,
            self.frame as f64 / 48.0,
            self.buffer
        )
    }

    /// ms since the last packet from the peer (None = nothing yet)
    pub fn rx_age_ms(&self) -> Option<u64> {
        match self.stats.last_rx_us.load(Relaxed) {
            0 => None,
            t => Some((self.epoch.elapsed().as_micros() as u64).saturating_sub(t) / 1000),
        }
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.stop.store(true, Relaxed);
        self._streams.clear();
        if let Some(h) = self.rx.take() {
            let _ = h.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resampler_passes_48k() {
        let mut r = Resampler::new(48_000);
        let mut out = vec![];
        r.process(&[0.1, 0.2, 0.3], |s| out.push(s));
        assert_eq!(out, vec![0.1, 0.2, 0.3]);
    }

    #[test]
    fn resampler_441_makes_more_samples() {
        let mut r = Resampler::new(44_100);
        let mut n = 0usize;
        for _ in 0..100 {
            r.process(&vec![0.5; 441], |s| {
                assert!((s - 0.5).abs() < 1e-6);
                n += 1;
            });
        }
        // 44 100 in → about 48 000 out
        assert!((47_990..=48_000).contains(&n), "{n}");
    }

    #[test]
    fn mix_clamps() {
        let m = Mix::new();
        m.set(9.0, -3.0);
        assert_eq!(m.get(), (4.0, -1.0));
    }
}
