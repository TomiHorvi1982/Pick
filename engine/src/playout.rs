//! The receiving end inside the output callback: packets → jitter buffer → decoder → a tiny
//! fractional resampler that keeps the buffer at its target (the two sound cards' clocks never
//! run at exactly the same rate). Nothing here allocates after `new`.

use crate::codec::Decoder;
use crate::jitter::{JitterBuffer, Out};
use crate::packet::{self, MAX_PACKET};
use crate::stats::Stats;
use std::sync::atomic::Ordering::Relaxed;
use std::sync::Arc;

/// One received datagram, as the network thread hands it over.
#[derive(Clone, Copy)]
pub struct Item {
    pub arr_us: u64,
    pub len: u16,
    pub data: [u8; MAX_PACKET],
}

impl Item {
    pub const EMPTY: Item = Item { arr_us: 0, len: 0, data: [0; MAX_PACKET] };
}

const MAX_PPM: f64 = 1500.0;

pub struct Playout {
    jb: JitterBuffer,
    dec: Decoder,
    frame: usize,
    fifo: Vec<f32>,
    pos: f64,
    ratio: f64,
    /// 48 kHz samples consumed per output sample (1.0 when the card runs at 48 kHz)
    base: f64,
    err: f64,
    tmp: Vec<f32>,
    stats: Arc<Stats>,
    pub wrong_codec: u64,
}

impl Playout {
    pub fn new(dec: Decoder, frame: usize, min_target: u32, max_target: u32, stats: Arc<Stats>) -> Self {
        Playout {
            jb: JitterBuffer::new(frame as u32, min_target, max_target),
            dec,
            frame,
            fifo: Vec::with_capacity(frame * 16 + 8192),
            pos: 0.0,
            ratio: 1.0,
            base: 1.0,
            err: 0.0,
            tmp: vec![0.0; frame],
            stats,
            wrong_codec: 0,
        }
    }

    /// The sound card plays at `rate` instead of 48 kHz.
    pub fn set_out_rate(&mut self, rate: u32) {
        self.base = crate::codec::RATE as f64 / rate as f64;
        self.ratio = self.base;
    }

    pub fn push(&mut self, it: &Item) {
        if let Some((h, p)) = packet::parse(&it.data[..it.len as usize]) {
            if h.codec != self.dec.codec() {
                self.wrong_codec += 1;
                return;
            }
            self.jb.insert(h.seq, h.ts, it.arr_us as f64 * 0.048, p);
        }
    }

    fn pull(&mut self) {
        match self.jb.pop() {
            Out::Packet(p) => self.dec.decode(Some(p), &mut self.tmp),
            Out::Lost => self.dec.decode(None, &mut self.tmp),
            Out::Silence => self.tmp.fill(0.0),
        }
        self.fifo.extend_from_slice(&self.tmp);
    }

    /// Fills `out` (mono) with the next samples.
    pub fn render(&mut self, out: &mut [f32]) {
        for o in out.iter_mut() {
            while self.pos as usize + 1 >= self.fifo.len() {
                self.pull();
            }
            let i = self.pos as usize;
            let f = (self.pos - i as f64) as f32;
            *o = self.fifo[i] * (1.0 - f) + self.fifo[i + 1] * f;
            self.pos += self.ratio;
        }
        let used = self.pos as usize;
        if used > 0 {
            self.fifo.drain(..used);
            self.pos -= used as f64;
        }

        // drift: read a hair faster when the buffer is above target, slower when below
        let frame = self.frame as f64;
        let fill = self.jb.fill() as f64 * frame + (self.fifo.len() as f64 - self.pos);
        if self.jb.started() {
            let target = self.jb.target() as f64 * frame + frame * 0.5;
            self.err += (fill - target - self.err) * 0.02;
            let ppm = (self.err / frame * 1000.0).clamp(-MAX_PPM, MAX_PPM);
            self.ratio = self.base * (1.0 + ppm * 1e-6);
        } else {
            self.err = 0.0;
            self.ratio = self.base;
        }

        let c = &self.jb.counters;
        let s = &self.stats;
        s.rx.store(c.received, Relaxed);
        s.lost.store(c.lost, Relaxed);
        s.late.store(c.late, Relaxed);
        s.underruns.store(c.underruns, Relaxed);
        s.resets.store(c.resets, Relaxed);
        s.jitter_us.store((self.jb.jitter_samples() / 0.048) as u32, Relaxed);
        s.target_frames.store(self.jb.target(), Relaxed);
        s.fill_samples.store(fill.max(0.0) as u32, Relaxed);
        s.ratio_ppm.store(((self.ratio / self.base - 1.0) * 1e6).round() as i32, Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::codec::Encoder;
    use crate::packet::{Codec, Header};

    fn item(seq: u32, frame: usize, arr_us: u64, value: f32) -> Item {
        let mut enc = Encoder::new(Codec::Pcm16, 0).unwrap();
        let pcm = vec![value; frame];
        let mut pay = [0u8; MAX_PACKET];
        let n = enc.encode(&pcm, &mut pay);
        let mut it = Item::EMPTY;
        let len = packet::write(&Header { codec: Codec::Pcm16, seq, ts: seq * frame as u32 }, &pay[..n], &mut it.data);
        it.len = len as u16;
        it.arr_us = arr_us;
        it
    }

    #[test]
    fn plays_what_arrives() {
        let frame = 120;
        let stats = Arc::new(Stats::default());
        let mut p = Playout::new(Decoder::new(Codec::Pcm16, frame).unwrap(), frame, 2, 8, stats.clone());
        let mut out = vec![0.0; 64];
        p.render(&mut out);
        assert!(out.iter().all(|v| *v == 0.0)); // nothing yet: silence
        for s in 0..4 {
            p.push(&item(s, frame, s as u64 * 2500, 0.25));
        }
        let mut heard = false;
        for _ in 0..8 {
            p.render(&mut out);
            heard |= out.iter().any(|v| (v - 0.25).abs() < 0.01);
        }
        assert!(heard);
        assert_eq!(stats.rx.load(Relaxed), 4);
    }

    #[test]
    fn plays_at_441() {
        let frame = 120;
        let mut p = Playout::new(Decoder::new(Codec::Pcm16, frame).unwrap(), frame, 2, 8, Arc::new(Stats::default()));
        p.set_out_rate(44_100);
        for s in 0..40 {
            p.push(&item(s, frame, s as u64 * 2500, 0.25));
        }
        // 40 frames of 48 kHz = 4800 samples ≈ 4410 at 44.1 kHz: all of it heard, nothing invented
        let mut out = vec![0.0; 147];
        let mut heard = 0;
        for _ in 0..40 {
            p.render(&mut out);
            heard += out.iter().filter(|v| (**v - 0.25).abs() < 0.01).count();
        }
        assert!((4300..=4420).contains(&heard), "{heard}");
    }

    #[test]
    fn rejects_other_codec() {
        let frame = 120;
        let mut p = Playout::new(Decoder::new(Codec::Opus, frame).unwrap(), frame, 1, 8, Arc::new(Stats::default()));
        p.push(&item(0, frame, 0, 0.1));
        assert_eq!(p.wrong_codec, 1);
    }
}
