//! The wire format: a 12-byte header and the encoded frame.
//!
//! | 0..2 magic "PJ" | 2 version | 3 codec | 4..8 seq (BE) | 8..12 timestamp in samples (BE) | payload… |

pub const MAGIC: [u8; 2] = *b"PJ";
pub const VERSION: u8 = 1;
pub const HEADER: usize = 12;
/// The largest datagram we send or accept (stays under a typical MTU, also through Tailscale).
pub const MAX_PACKET: usize = 1200;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Codec {
    Pcm16 = 0,
    Opus = 1,
}

impl Codec {
    fn from_u8(v: u8) -> Option<Codec> {
        match v {
            0 => Some(Codec::Pcm16),
            1 => Some(Codec::Opus),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Header {
    pub codec: Codec,
    pub seq: u32,
    pub ts: u32,
}

/// Writes header + payload into `out`; returns the datagram length (0 when it does not fit).
pub fn write(h: &Header, payload: &[u8], out: &mut [u8]) -> usize {
    let n = HEADER + payload.len();
    if n > out.len() || n > MAX_PACKET {
        return 0;
    }
    out[0..2].copy_from_slice(&MAGIC);
    out[2] = VERSION;
    out[3] = h.codec as u8;
    out[4..8].copy_from_slice(&h.seq.to_be_bytes());
    out[8..12].copy_from_slice(&h.ts.to_be_bytes());
    out[HEADER..n].copy_from_slice(payload);
    n
}

/// Parses a datagram; None for anything that is not ours.
pub fn parse(buf: &[u8]) -> Option<(Header, &[u8])> {
    if buf.len() < HEADER || buf[0..2] != MAGIC || buf[2] != VERSION {
        return None;
    }
    let codec = Codec::from_u8(buf[3])?;
    let seq = u32::from_be_bytes(buf[4..8].try_into().ok()?);
    let ts = u32::from_be_bytes(buf[8..12].try_into().ok()?);
    Some((Header { codec, seq, ts }, &buf[HEADER..]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let h = Header { codec: Codec::Opus, seq: 0xDEADBEEF, ts: 123_456 };
        let mut buf = [0u8; 64];
        let n = write(&h, &[1, 2, 3], &mut buf);
        assert_eq!(n, HEADER + 3);
        let (h2, p) = parse(&buf[..n]).unwrap();
        assert_eq!(h2, h);
        assert_eq!(p, &[1, 2, 3]);
    }

    #[test]
    fn rejects_foreign() {
        assert!(parse(b"hello world, not ours").is_none());
        assert!(parse(&[b'P', b'J', VERSION, 9, 0, 0, 0, 0, 0, 0, 0, 0]).is_none());
        assert!(parse(&[b'P', b'J']).is_none());
    }

    #[test]
    fn refuses_oversize() {
        let h = Header { codec: Codec::Pcm16, seq: 1, ts: 1 };
        let mut buf = vec![0u8; 4000];
        assert_eq!(write(&h, &vec![0u8; MAX_PACKET], &mut buf), 0);
    }
}
