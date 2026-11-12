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
/// The two cards' clock difference this controller will believe in (real ones are within ~±200 ppm).
const MAX_SKEW_PPM: f64 = 1000.0;
/// Seconds over which the buffer level is averaged: packets come in whole frames, so the level saws up and down.
const LEVEL_TAU: f64 = 1.0;
/// ppm of correction per ms the buffer is off its target (the quick part, pulls the level back)…
const KP: f64 = 200.0;
/// …and ppm per ms per second (the slow part: it learns the clock difference and keeps it), from a
/// slower average (LEARN_TAU) so the level's own wander is not taken for a clock difference.
const KI: f64 = 6.0;
const LEARN_TAU: f64 = 8.0;
/// The level must be this close to its target (ms) for the clock difference to be learnt from it.
const LEARN_MS: f64 = 1.0;
/// When the lowest level over the last SKIP_EVERY_MS stays more than this many frames above the target,
/// the surplus — left by a burst after a gap — goes at once: a frame is skipped, cross-faded into the
/// next so it does not click (the resampler alone would take ~1.5 ms a second). The lowest level, not an
/// average: it shows the surplus at once and does not count the sawing of whole frames as surplus.
const SKIP_FRAMES: f64 = 1.5;
/// …looked at every this many ms, at most one skip each time.
const SKIP_EVERY_MS: f64 = 30.0;

pub struct Playout {
    jb: JitterBuffer,
    dec: Decoder,
    frame: usize,
    fifo: Vec<f32>,
    pos: f64,
    ratio: f64,
    /// 48 kHz samples consumed per output sample (1.0 when the card runs at 48 kHz)
    base: f64,
    /// the buffer's level off its target, in 48 kHz samples, averaged (LEVEL_TAU)
    err: f64,
    /// the learnt clock difference, ppm (the integral part)
    skew: f64,
    /// the level off its target averaged slowly (LEARN_TAU), what the clock difference is learnt from
    err_slow: f64,
    /// the lowest level since the last look (SKIP_EVERY_MS)
    min_fill: f64,
    /// output samples per second (the card's rate)
    out_rate: f64,
    tmp: Vec<f32>,
    tmp2: Vec<f32>,
    /// skip a frame at the next pull (the level is far above its target)
    skip: bool,
    /// output samples since the last skip
    since_skip: f64,
    pub skipped: u64,
    /// whether a surplus may be dropped by skipping frames (a test of the resampler alone turns it off)
    pub skip_on: bool,
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
            skew: 0.0,
            err_slow: 0.0,
            min_fill: f64::INFINITY,
            out_rate: crate::codec::RATE as f64,
            tmp: vec![0.0; frame],
            tmp2: vec![0.0; frame],
            skip: false,
            since_skip: 0.0,
            skipped: 0,
            skip_on: true,
            stats,
            wrong_codec: 0,
        }
    }

    /// The sound card plays at `rate` instead of 48 kHz.
    pub fn set_out_rate(&mut self, rate: u32) {
        self.base = crate::codec::RATE as f64 / rate as f64;
        self.ratio = self.base;
        self.out_rate = rate as f64;
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

    fn decode_next(&mut self, second: bool) {
        let buf = if second { &mut self.tmp2 } else { &mut self.tmp };
        match self.jb.pop() {
            Out::Packet(p) => self.dec.decode(Some(p), buf),
            Out::Lost => self.dec.decode(None, buf),
            Out::Silence => buf.fill(0.0),
        }
    }

    fn pull(&mut self) {
        self.decode_next(false);
        if self.skip && self.jb.started() && self.jb.fill() >= 2 {
            // two frames into one: the first fades out as the second fades in (equal power), so the
            // frame that follows carries on from the second — one frame of surplus gone without a click
            self.skip = false;
            self.since_skip = 0.0;
            self.skipped += 1;
            self.decode_next(true);
            let n = self.tmp.len() as f32;
            for i in 0..self.tmp.len() {
                let w = (i as f32 + 0.5) / n * std::f32::consts::FRAC_PI_2;
                self.tmp[i] = self.tmp[i] * w.cos() + self.tmp2[i] * w.sin();
            }
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

        // drift, a PI controller: read a hair faster when the buffer stays above target, slower when below.
        // The level is averaged over a second (it saws by a whole frame with every packet); the integral
        // part learns the two cards' clock difference and keeps it through a reset or a gap.
        let frame = self.frame as f64;
        let fill = self.jb.fill() as f64 * frame + (self.fifo.len() as f64 - self.pos);
        if self.jb.started() {
            let dt = out.len() as f64 / self.out_rate;
            let target = self.jb.target() as f64 * frame + frame * 0.5;
            self.err += (fill - target - self.err) * (dt / LEVEL_TAU).min(1.0);
            let err_ms = self.err / 48.0;
            self.err_slow += (fill - target - self.err_slow) * (dt / LEARN_TAU).min(1.0);
            // learn only once settled near the target: the surplus a start leaves, or a jump of the target
            // (the network got worse), is the quick part's job — learnt, it would read as a clock difference
            if err_ms.abs() < LEARN_MS && (self.err_slow / 48.0).abs() < LEARN_MS {
                self.skew = (self.skew + KI * self.err_slow / 48.0 * dt).clamp(-MAX_SKEW_PPM, MAX_SKEW_PPM);
            }
            let ppm = (self.skew + KP * err_ms).clamp(-MAX_PPM, MAX_PPM);
            self.ratio = self.base * (1.0 + ppm * 1e-6);
            self.since_skip += out.len() as f64;
            self.min_fill = self.min_fill.min(fill);
            if self.since_skip / self.out_rate * 1000.0 >= SKIP_EVERY_MS {
                if self.skip_on && self.min_fill - target > SKIP_FRAMES * frame {
                    self.skip = true;           // pull() resets since_skip when it has skipped
                } else {
                    self.since_skip = 0.0;
                }
                self.min_fill = f64::INFINITY;
            }
        } else {
            self.skip = false;
            self.err = 0.0;
            self.err_slow = 0.0;
            self.min_fill = f64::INFINITY;
            self.ratio = self.base * (1.0 + self.skew * 1e-6);
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
        s.ratio_ppm.store(self.skew.round() as i32, Relaxed);     // the clock difference learnt, not the momentary pull
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
        p.skip_on = false; // all 40 frames come at once: here that is the point, not a surplus to drop
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
        assert!((4300..=4430).contains(&heard), "{heard}"); // 4410 give or take the edges
    }

    /// The far card plays `ppm` fast: packets come exactly every frame of real time, the output
    /// asks for (1 + ppm) as many samples. The controller must learn about −ppm and keep the buffer level.
    fn learns(ppm: f64) -> (f64, u32) {
        let frame = 120;
        let stats = Arc::new(Stats::default());
        let mut p = Playout::new(Decoder::new(Codec::Pcm16, frame).unwrap(), frame, 3, 3, stats.clone());
        let out_rate = 48_000.0 * (1.0 + ppm * 1e-6);
        let mut out = vec![0.0; 64];
        let (mut seq, mut t_out) = (0u32, 0.0f64);
        for _ in 0..(180.0 * out_rate / 64.0) as usize {        // 3 min
            while (seq as f64) * frame as f64 / 48_000.0 <= t_out + 0.005 {
                p.push(&item(seq, frame, (seq as u64) * 2500, 0.25));
                seq += 1;
            }
            p.render(&mut out);
            t_out += 64.0 / out_rate;
        }
        (stats.ratio_ppm.load(Relaxed) as f64, stats.underruns.load(Relaxed) as u32)
    }

    #[test]
    fn learns_the_clock_difference() {
        for ppm in [200.0, -150.0, 0.0] {
            let (got, under) = learns(ppm);
            assert!((got + ppm).abs() < 40.0, "far card {ppm:+} ppm: learnt {got:+} ppm");
            assert!(under <= 1, "far card {ppm:+} ppm: {under} underruns");
        }
    }

    /// A burst after a gap leaves the buffer 40 ms over its target (6 ms): within 2 s it must be back to a
    /// few ms over (skipped frames; the resampler takes the rest), not the ~25 s the resampler alone would take.
    #[test]
    fn surplus_goes_quickly() {
        let frame = 120;
        let stats = Arc::new(Stats::default());
        let mut p = Playout::new(Decoder::new(Codec::Pcm16, frame).unwrap(), frame, 2, 2, stats.clone());
        let mut out = vec![0.0; 64];
        let (mut seq, mut t) = (0u32, 0.0f64);
        let mut push_until = |p: &mut Playout, seq: &mut u32, t: f64| {
            while (*seq as f64) * frame as f64 / 48_000.0 <= t {
                p.push(&item(*seq, frame, (*seq as u64) * 2500, 0.25));
                *seq += 1;
            }
        };
        for _ in 0..1500 { push_until(&mut p, &mut seq, t); p.render(&mut out); t += 64.0 / 48_000.0; }   // 2 s settled
        for _ in 0..16 { p.push(&item(seq, frame, (seq as u64) * 2500, 0.25)); seq += 1; }       // 40 ms burst ahead
        let fill = |s: &Stats| s.fill_samples.load(Relaxed) as f64 / 48.0;
        for _ in 0..(2.0 * 48_000.0 / 64.0) as usize {                                            // 2 s later
            push_until(&mut p, &mut seq, t + 16.0 * frame as f64 / 48_000.0);   // the sender now runs 40 ms ahead
            p.render(&mut out);
            t += 64.0 / 48_000.0;
        }
        assert!(fill(&stats) < 11.0, "still {:.1} ms buffered", fill(&stats));
        assert!(p.skipped >= 8, "skipped {}", p.skipped);
        assert!(out.iter().all(|v| (*v - 0.25).abs() < 0.02), "the cross-fades keep the signal");
    }

    /// Same clocks at both ends, packets arriving with ±1 ms of jitter for 3 minutes: the learnt
    /// difference must stay small, not wander off (it is the jitter, not a clock difference).
    #[test]
    fn same_clocks_stay_near_zero() {
        let frame = 120;
        let stats = Arc::new(Stats::default());
        let mut p = Playout::new(Decoder::new(Codec::Pcm16, frame).unwrap(), frame, 3, 3, stats.clone());
        let mut out = vec![0.0; 64];
        let (mut seq, mut t, mut rng, mut worst) = (0u32, 0.0f64, 0x2545F4914F6CDD1Du64, 0i32);
        let mut due = 0.0f64;
        for k in 0..(180.0 * 48_000.0 / 64.0) as usize {
            while due <= t {
                p.push(&item(seq, frame, (seq as u64) * 2500, 0.25));
                seq += 1;
                rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
                let j = (rng % 2000) as f64 / 1e6;                 // 0–2 ms late
                due = seq as f64 * frame as f64 / 48_000.0 + j;
            }
            p.render(&mut out);
            t += 64.0 / 48_000.0;
            if k as f64 * 64.0 / 48_000.0 > 20.0 {
                worst = worst.max(stats.ratio_ppm.load(Relaxed).abs());
            }
        }
        assert!(worst < 60, "learnt up to {worst} ppm with the same clocks");
    }

    #[test]
    fn rejects_other_codec() {
        let frame = 120;
        let mut p = Playout::new(Decoder::new(Codec::Opus, frame).unwrap(), frame, 1, 8, Arc::new(Stats::default()));
        p.push(&item(0, frame, 0, 0.1));
        assert_eq!(p.wrong_codec, 1);
    }
}
