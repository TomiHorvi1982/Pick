//! UDP: the sending side (called from the input callback) and the receiving thread.

use crate::codec::Encoder;
use crate::packet::{self, Header, MAX_PACKET};
use crate::playout::Item;
use crate::stats::Stats;
use ringbuf::traits::Producer;
use std::net::{SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, Mutex};

/// Where to send and whom to listen to; set from the control socket while audio runs.
pub type PeerCell = Mutex<Option<SocketAddr>>;

/// The peer without waiting: audio threads never block on the lock, they keep the last value they saw.
#[inline]
pub fn peer_now(cell: &PeerCell, cached: &mut Option<SocketAddr>) -> Option<SocketAddr> {
    if let Ok(g) = cell.try_lock() {
        *cached = *g;
    }
    *cached
}
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Gathers samples into frames, encodes and sends each one at once.
pub struct Sender {
    enc: Encoder,
    frame: usize,
    acc: Vec<f32>,
    seq: u32,
    ts: u32,
    sock: UdpSocket,
    peer: Arc<PeerCell>,
    cached: Option<SocketAddr>,
    pay: [u8; MAX_PACKET],
    buf: [u8; MAX_PACKET],
    stats: Arc<Stats>,
}

impl Sender {
    pub fn new(enc: Encoder, frame: usize, sock: UdpSocket, peer: Arc<PeerCell>, stats: Arc<Stats>) -> Self {
        let _ = sock.set_nonblocking(true);
        Sender {
            enc,
            frame,
            acc: Vec::with_capacity(frame),
            seq: 0,
            ts: 0,
            sock,
            peer,
            cached: None,
            pay: [0; MAX_PACKET],
            buf: [0; MAX_PACKET],
            stats,
        }
    }

    #[inline]
    pub fn push(&mut self, s: f32) {
        self.acc.push(s);
        if self.acc.len() == self.frame {
            self.flush();
        }
    }

    fn flush(&mut self) {
        let n = self.enc.encode(&self.acc, &mut self.pay[..MAX_PACKET - packet::HEADER]);
        self.acc.clear();
        let h = Header { codec: self.enc.codec(), seq: self.seq, ts: self.ts };
        self.seq = self.seq.wrapping_add(1);
        self.ts = self.ts.wrapping_add(self.frame as u32);
        let Some(to) = peer_now(&self.peer, &mut self.cached) else { return };
        if n == 0 {
            return;
        }
        let len = packet::write(&h, &self.pay[..n], &mut self.buf);
        if len > 0 && self.sock.send_to(&self.buf[..len], to).is_ok() {
            self.stats.tx.fetch_add(1, Relaxed);
        }
    }
}

/// Receives datagrams and hands them to the output callback through `prod`.
/// With `from`, only packets from the current peer's address are taken (any port: NAT may change it).
pub fn spawn_receiver<P>(
    sock: UdpSocket,
    mut prod: P,
    epoch: Instant,
    from: Option<Arc<PeerCell>>,
    stats: Arc<Stats>,
    stop: Arc<AtomicBool>,
) -> JoinHandle<()>
where
    P: Producer<Item = Item> + Send + 'static,
{
    std::thread::Builder::new()
        .name("pje-recv".into())
        .spawn(move || {
            let _ = sock.set_read_timeout(Some(Duration::from_millis(100)));
            let mut it = Item::EMPTY;
            let mut cached = None;
            while !stop.load(Relaxed) {
                match sock.recv_from(&mut it.data) {
                    Ok((n, src)) => {
                        if let Some(cell) = &from {
                            match peer_now(cell, &mut cached) {
                                Some(p) if p.ip() == src.ip() => {}
                                _ => continue,
                            }
                        }
                        it.arr_us = epoch.elapsed().as_micros() as u64;
                        stats.last_rx_us.store(it.arr_us.max(1), Relaxed);
                        it.len = n as u16;
                        let _ = prod.try_push(it); // a full ring means the output is stuck: drop
                    }
                    Err(_) => continue,
                }
            }
        })
        .expect("spawn receiver")
}
