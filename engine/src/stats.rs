//! Counters shared between the audio callbacks and whoever reports them (lock-free).

use std::sync::atomic::Ordering::Relaxed;
use std::sync::atomic::{AtomicI32, AtomicU32, AtomicU64};

#[derive(Default)]
pub struct Stats {
    pub tx: AtomicU64,
    pub rx: AtomicU64,
    pub lost: AtomicU64,
    pub late: AtomicU64,
    pub underruns: AtomicU64,
    pub resets: AtomicU64,
    pub jitter_us: AtomicU32,
    pub target_frames: AtomicU32,
    pub fill_samples: AtomicU32,
    pub ratio_ppm: AtomicI32,
    /// sound card latencies measured from the callbacks' timestamps (µs)
    pub in_lat_us: AtomicU32,
    pub out_lat_us: AtomicU32,
    /// peaks since the last report, ×10 000
    pub in_peak: AtomicU32,
    pub peer_peak: AtomicU32,
    /// when the last datagram from the peer came (µs since the engine's epoch; 0 = never)
    pub last_rx_us: AtomicU64,
}

/// Remembers the highest value until it is taken.
pub fn peak_store(a: &AtomicU32, v: f32) {
    let v = (v.abs().min(1.0) * 10_000.0) as u32;
    if v > a.load(Relaxed) {
        a.store(v, Relaxed);
    }
}

pub fn peak_take(a: &AtomicU32) -> f32 {
    a.swap(0, Relaxed) as f32 / 10_000.0
}

impl Stats {
    pub fn line(&self) -> String {
        format!(
            "tx {} rx {} lost {} late {} underruns {} | jitter {:.2} ms target {} fr buffer {:.2} ms drift {:+} ppm",
            self.tx.load(Relaxed),
            self.rx.load(Relaxed),
            self.lost.load(Relaxed),
            self.late.load(Relaxed),
            self.underruns.load(Relaxed),
            self.jitter_us.load(Relaxed) as f64 / 1000.0,
            self.target_frames.load(Relaxed),
            self.fill_samples.load(Relaxed) as f64 / 48.0,
            self.ratio_ppm.load(Relaxed),
        )
    }
}
