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
/// The other engine's possible addresses (its public one, its home network one, Tailscale): the first
/// one a packet comes from becomes the peer. Packets from anywhere else are dropped.
pub type Cands = Mutex<Vec<SocketAddr>>;

/// A packet that only opens the way through both routers (the other side's parser ignores it).
pub const PUNCH: [u8; 12] = [b'P', b'J', crate::packet::VERSION, 0xFF, 0, 0, 0, 0, 0, 0, 0, 0];

/// This machine's address as the internet sees it (behind the router), asked of public STUN servers —
/// the same question a video call asks. From `sock`, so the answer is that socket's own mapping.
/// Call before the receiving thread starts (it reads the same socket).
pub fn stun_public(sock: &UdpSocket) -> Option<SocketAddr> {
    use std::net::ToSocketAddrs;
    let servers: Vec<SocketAddr> = ["stun.l.google.com:19302", "stun.cloudflare.com:3478", "stun1.l.google.com:19302"]
        .iter()
        .filter_map(|s| s.to_socket_addrs().ok().and_then(|mut it| it.find(|a| a.is_ipv4())))
        .collect();
    if servers.is_empty() {
        return None;
    }
    let seed = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(7);
    let mut req = [0u8; 20];
    req[0..2].copy_from_slice(&1u16.to_be_bytes()); // Binding Request, no attributes
    req[4..8].copy_from_slice(&0x2112_A442u32.to_be_bytes());
    for (i, b) in req[8..20].iter_mut().enumerate() {
        *b = (seed >> ((i % 8) * 8)) as u8 ^ (i as u8).wrapping_mul(37);
    }
    let old = sock.read_timeout().ok().flatten();
    let _ = sock.set_nonblocking(false);
    let _ = sock.set_read_timeout(Some(Duration::from_millis(300)));
    let mut got = None;
    let mut buf = [0u8; 512];
    'outer: for _ in 0..3 {
        for s in &servers {
            let _ = sock.send_to(&req, s);
        }
        let until = Instant::now() + Duration::from_millis(600);
        while Instant::now() < until {
            if let Ok((n, _)) = sock.recv_from(&mut buf) {
                if let Some(a) = parse_stun(&buf[..n], &req[8..20]) {
                    got = Some(a);
                    break 'outer;
                }
            }
        }
    }
    let _ = sock.set_read_timeout(old);
    got
}

fn parse_stun(b: &[u8], txid: &[u8]) -> Option<SocketAddr> {
    if b.len() < 20 || b[0..2] != [0x01, 0x01] || b[4..8] != 0x2112_A442u32.to_be_bytes() || &b[8..20] != txid {
        return None;
    }
    let mut i = 20;
    while i + 4 <= b.len() {
        let t = u16::from_be_bytes([b[i], b[i + 1]]);
        let l = u16::from_be_bytes([b[i + 2], b[i + 3]]) as usize;
        let v = b.get(i + 4..i + 4 + l)?;
        if (t == 0x0020 || t == 0x0001) && l >= 8 && v[1] == 0x01 {
            let (mut port, mut ip) = (u16::from_be_bytes([v[2], v[3]]), [v[4], v[5], v[6], v[7]]);
            if t == 0x0020 {
                port ^= 0x2112;
                for (k, x) in ip.iter_mut().enumerate() {
                    *x ^= 0x2112_A442u32.to_be_bytes()[k];
                }
            }
            return Some(SocketAddr::from((ip, port)));
        }
        i += 4 + l.div_ceil(4) * 4;
    }
    None
}

/// This machine's address in its home network (the one it would use to go out). Nothing is sent.
pub fn local_ip() -> Option<std::net::IpAddr> {
    let s = UdpSocket::bind("0.0.0.0:0").ok()?;
    s.connect("8.8.8.8:80").ok()?;
    Some(s.local_addr().ok()?.ip())
}

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
    from: Option<(Arc<PeerCell>, Arc<Cands>)>,
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
                        if let Some((cell, cands)) = &from {
                            match peer_now(cell, &mut cached) {
                                // the peer: its router may have moved it to another port — answer there
                                Some(p) if p.ip() == src.ip() => {
                                    if p.port() != src.port() {
                                        if let Ok(mut g) = cell.lock() {
                                            *g = Some(src);
                                        }
                                    }
                                }
                                // no peer yet: the first packet from one of its addresses makes it the peer
                                None if cands.lock().is_ok_and(|c| c.iter().any(|a| a.ip() == src.ip())) => {
                                    if let Ok(mut g) = cell.lock() {
                                        *g = Some(src);
                                    }
                                    cached = Some(src);
                                }
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

#[cfg(test)]
mod stun_tests {
    use super::*;

    #[test]
    fn reads_xor_mapped_address() {
        let tx = [9u8; 12];
        let mut b = vec![0x01, 0x01, 0, 12];
        b.extend_from_slice(&0x2112_A442u32.to_be_bytes());
        b.extend_from_slice(&tx);
        // XOR-MAPPED-ADDRESS 203.0.113.7:47000
        let port = 47000u16 ^ 0x2112;
        let ip = [203u8, 0, 113, 7];
        let m = 0x2112_A442u32.to_be_bytes();
        b.extend_from_slice(&[0x00, 0x20, 0, 8, 0, 0x01]);
        b.extend_from_slice(&port.to_be_bytes());
        b.extend((0..4).map(|k| ip[k] ^ m[k]));
        assert_eq!(parse_stun(&b, &tx), Some("203.0.113.7:47000".parse().unwrap()));
        assert_eq!(parse_stun(&b, &[1u8; 12]), None); // someone else's answer
    }
}
