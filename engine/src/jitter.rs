//! The jitter buffer: packets in by sequence number, frames out at the sound card's pace.
//!
//! * jitter estimate as in RFC 3550 (interarrival jitter, in samples);
//! * the target depth follows it: up at once, down slowly (one frame per ~1 s of steady play);
//! * a missing frame comes out as `Lost` (the decoder conceals it), a late one is dropped;
//! * an empty buffer goes back to buffering until the target is reached again.

use crate::packet::MAX_PACKET;

pub const CAP: u32 = 128; // slots; far more than any sane target

#[derive(Clone, Copy)]
struct Slot {
    seq: u32,
    used: bool,
    len: u16,
    data: [u8; MAX_PACKET],
}

pub enum Out<'a> {
    Packet(&'a [u8]),
    Lost,
    Silence,
}

#[derive(Default, Clone, Copy, Debug)]
pub struct Counters {
    pub received: u64,
    pub lost: u64,
    pub late: u64,
    pub underruns: u64,
    pub resets: u64,
}

pub struct JitterBuffer {
    slots: Vec<Slot>,
    frame: u32,
    next: u32,
    highest: Option<u32>,
    started: bool,
    jitter: f64,             // samples
    last: Option<(f64, u32)>, // (arrival in samples, ts)
    target: u32,
    min_target: u32,
    max_target: u32,
    calm: u32,               // pops since the target last wanted to go up
    pub counters: Counters,
}

/// a - b on the sequence circle
fn diff(a: u32, b: u32) -> i32 {
    a.wrapping_sub(b) as i32
}

impl JitterBuffer {
    pub fn new(frame: u32, min_target: u32, max_target: u32) -> Self {
        let empty = Slot { seq: 0, used: false, len: 0, data: [0; MAX_PACKET] };
        let max_target = max_target.clamp(1, CAP / 2);
        let min_target = min_target.clamp(1, max_target);
        JitterBuffer {
            slots: vec![empty; CAP as usize],
            frame,
            next: 0,
            highest: None,
            started: false,
            jitter: 0.0,
            last: None,
            target: min_target,
            min_target,
            max_target,
            calm: 0,
            counters: Counters::default(),
        }
    }

    pub fn target(&self) -> u32 {
        self.target
    }
    pub fn jitter_samples(&self) -> f64 {
        self.jitter
    }
    pub fn started(&self) -> bool {
        self.started
    }

    /// Frames waiting from the next one to play to the newest received (holes included).
    pub fn fill(&self) -> u32 {
        match self.highest {
            Some(h) => (diff(h, self.next) + 1).max(0) as u32,
            None => 0,
        }
    }

    fn reset(&mut self, seq: u32) {
        for s in self.slots.iter_mut() {
            s.used = false;
        }
        self.next = seq;
        self.highest = None;
        self.started = false;
        self.counters.resets += 1;
    }

    /// `arrival` is the receive time in samples (any fixed origin).
    pub fn insert(&mut self, seq: u32, ts: u32, arrival: f64, payload: &[u8]) {
        if payload.len() > MAX_PACKET {
            return;
        }
        self.counters.received += 1;
        if self.highest.is_none() && !self.started {
            self.next = seq;
        }
        let ahead = diff(seq, self.next);
        if ahead >= CAP as i32 || ahead < -(CAP as i32) {
            // the sender restarted or we were away: start over, and do not take the jump for jitter
            self.reset(seq);
            self.last = None;
        }

        if let Some((a0, t0)) = self.last {
            let d = (arrival - a0) - (ts.wrapping_sub(t0) as i32) as f64;
            // one packet far later than the jitter so far is the machine stalling (Windows can stop a
            // process for ~100 ms), not the network: it counts only up to 4× the jitter. A network that
            // really got worse sends many such packets and the limit grows with them.
            let lim = (4.0 * self.jitter).max(2.0 * self.frame as f64);
            self.jitter += (d.abs().min(lim) - self.jitter) / 16.0;
            self.adapt();
        }
        self.last = Some((arrival, ts));

        if diff(seq, self.next) < 0 {
            self.counters.late += 1;
            return;
        }
        let slot = &mut self.slots[(seq % CAP) as usize];
        slot.seq = seq;
        slot.used = true;
        slot.len = payload.len() as u16;
        slot.data[..payload.len()].copy_from_slice(payload);
        match self.highest {
            Some(h) if diff(seq, h) <= 0 => {}
            _ => self.highest = Some(seq),
        }
    }

    fn adapt(&mut self) {
        let want = 1 + (3.0 * self.jitter / self.frame as f64).ceil() as u32;
        let want = want.clamp(self.min_target, self.max_target);
        if want > self.target {
            self.target = want;
            self.calm = 0;
        }
    }

    /// The next frame to play.
    pub fn pop(&mut self) -> Out<'_> {
        if !self.started {
            if self.highest.is_some() && self.fill() >= self.target {
                self.started = true;
            } else {
                return Out::Silence;
            }
        }
        if self.fill() == 0 {
            // ran dry: conceal this frame, then buffer up again
            self.started = false;
            self.highest = None;
            self.counters.underruns += 1;
            return Out::Lost;
        }
        // slow way down: after ~1 s without the jitter asking for more
        self.calm += 1;
        // one frame down per second of calm (it was two: after a burst the target held the delay up ~10 s)
        let calm_pops = (48_000 / self.frame.max(1)).max(1);
        if self.calm >= calm_pops {
            self.calm = 0;
            let want = (1 + (3.0 * self.jitter / self.frame as f64).ceil() as u32)
                .clamp(self.min_target, self.max_target);
            if want < self.target {
                self.target -= 1;
            }
        }
        let seq = self.next;
        self.next = self.next.wrapping_add(1);
        let idx = (seq % CAP) as usize;
        if self.slots[idx].used && self.slots[idx].seq == seq {
            self.slots[idx].used = false;
            let len = self.slots[idx].len as usize;
            Out::Packet(&self.slots[idx].data[..len])
        } else {
            self.counters.lost += 1;
            Out::Lost
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const F: u32 = 120;

    fn feed(jb: &mut JitterBuffer, seq: u32, arr_frames: f64) {
        jb.insert(seq, seq * F, arr_frames * F as f64, &[seq as u8]);
    }

    fn take(jb: &mut JitterBuffer) -> Option<u8> {
        match jb.pop() {
            Out::Packet(p) => Some(p[0]),
            _ => None,
        }
    }

    #[test]
    fn buffers_then_plays_in_order() {
        let mut jb = JitterBuffer::new(F, 2, 8);
        feed(&mut jb, 10, 0.0);
        assert!(matches!(jb.pop(), Out::Silence));
        feed(&mut jb, 11, 1.0);
        assert_eq!(take(&mut jb), Some(10));
        assert_eq!(take(&mut jb), Some(11));
    }

    #[test]
    fn reorders() {
        let mut jb = JitterBuffer::new(F, 3, 8);
        feed(&mut jb, 0, 0.0);
        feed(&mut jb, 2, 2.0);
        feed(&mut jb, 1, 2.1);
        assert_eq!(take(&mut jb), Some(0));
        assert_eq!(take(&mut jb), Some(1));
        assert_eq!(take(&mut jb), Some(2));
    }

    #[test]
    fn hole_is_lost_and_late_is_dropped() {
        let mut jb = JitterBuffer::new(F, 3, 8);
        feed(&mut jb, 0, 0.0);
        feed(&mut jb, 2, 2.0);
        feed(&mut jb, 3, 3.0);
        assert_eq!(take(&mut jb), Some(0));
        assert!(matches!(jb.pop(), Out::Lost));
        assert_eq!(jb.counters.lost, 1);
        feed(&mut jb, 1, 3.5); // too late now
        assert_eq!(jb.counters.late, 1);
        assert_eq!(take(&mut jb), Some(2));
    }

    #[test]
    fn underrun_rebuffers() {
        let mut jb = JitterBuffer::new(F, 2, 8);
        feed(&mut jb, 0, 0.0);
        feed(&mut jb, 1, 1.0);
        assert_eq!(take(&mut jb), Some(0));
        assert_eq!(take(&mut jb), Some(1));
        assert!(matches!(jb.pop(), Out::Lost));
        assert_eq!(jb.counters.underruns, 1);
        feed(&mut jb, 2, 5.0);
        assert!(matches!(jb.pop(), Out::Silence));
        feed(&mut jb, 3, 6.0);
        assert_eq!(take(&mut jb), Some(2));
    }

    #[test]
    fn target_grows_with_jitter() {
        let mut jb = JitterBuffer::new(F, 1, 16);
        let mut x = 1u32;
        for seq in 0..400u32 {
            x ^= x << 13;
            x ^= x >> 17;
            x ^= x << 5;
            let wobble = (x % 300) as f64 / 100.0; // up to 3 frames late
            feed(&mut jb, seq, seq as f64 + wobble);
        }
        assert!(jb.target() >= 4, "target {} jitter {}", jb.target(), jb.jitter_samples());
        let mut calm = JitterBuffer::new(F, 1, 16);
        for seq in 0..400u32 {
            feed(&mut calm, seq, seq as f64);
        }
        assert_eq!(calm.target(), 1);
    }

    #[test]
    fn far_jump_resets() {
        let mut jb = JitterBuffer::new(F, 1, 8);
        feed(&mut jb, 0, 0.0);
        assert_eq!(take(&mut jb), Some(0));
        feed(&mut jb, 5000, 1.0);
        assert_eq!(jb.counters.resets, 1);
        assert_eq!(take(&mut jb), Some((5000u32) as u8));
    }

    #[test]
    fn sequence_wraps() {
        let mut jb = JitterBuffer::new(F, 2, 8);
        feed(&mut jb, u32::MAX, 0.0);
        feed(&mut jb, 0, 1.0);
        assert_eq!(take(&mut jb), Some(u32::MAX as u8));
        assert_eq!(take(&mut jb), Some(0));
    }
}
