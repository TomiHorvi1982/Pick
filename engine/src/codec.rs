//! Opus (low delay, CBR) or raw 16-bit PCM, both mono 48 kHz.

use crate::packet::Codec;
use anyhow::{bail, Result};

pub const RATE: u32 = 48_000;

/// Frame length in samples for a duration Opus accepts (2.5 / 5 / 10 ms).
pub fn frame_samples(ms: f32) -> Result<usize> {
    let n = (ms * RATE as f32 / 1000.0).round() as usize;
    match n {
        120 | 240 | 480 => Ok(n),
        _ => bail!("frame must be 2.5, 5 or 10 ms (got {ms})"),
    }
}

pub enum Encoder {
    Pcm,
    Opus(opus::Encoder),
}

impl Encoder {
    pub fn new(codec: Codec, bitrate: i32) -> Result<Self> {
        Ok(match codec {
            Codec::Pcm16 => Encoder::Pcm,
            Codec::Opus => {
                let mut e = opus::Encoder::new(RATE, opus::Channels::Mono, opus::Application::LowDelay)?;
                e.set_bitrate(opus::Bitrate::Bits(bitrate))?;
                e.set_vbr(false)?;
                Encoder::Opus(e)
            }
        })
    }

    pub fn codec(&self) -> Codec {
        match self {
            Encoder::Pcm => Codec::Pcm16,
            Encoder::Opus(_) => Codec::Opus,
        }
    }

    /// Encodes one frame into `out`; returns the byte count (0 on failure).
    pub fn encode(&mut self, pcm: &[f32], out: &mut [u8]) -> usize {
        match self {
            Encoder::Pcm => {
                let n = pcm.len() * 2;
                if n > out.len() {
                    return 0;
                }
                for (i, s) in pcm.iter().enumerate() {
                    let v = (s.clamp(-1.0, 1.0) * 32767.0) as i16;
                    out[2 * i..2 * i + 2].copy_from_slice(&v.to_le_bytes());
                }
                n
            }
            Encoder::Opus(e) => e.encode_float(pcm, out).unwrap_or(0),
        }
    }
}

pub enum Decoder {
    Pcm { last: Vec<f32>, fade: f32 },
    Opus(opus::Decoder),
}

impl Decoder {
    pub fn new(codec: Codec, frame: usize) -> Result<Self> {
        Ok(match codec {
            Codec::Pcm16 => Decoder::Pcm { last: vec![0.0; frame], fade: 0.0 },
            Codec::Opus => Decoder::Opus(opus::Decoder::new(RATE, opus::Channels::Mono)?),
        })
    }

    pub fn codec(&self) -> Codec {
        match self {
            Decoder::Pcm { .. } => Codec::Pcm16,
            Decoder::Opus(_) => Codec::Opus,
        }
    }

    /// Decodes one frame into `out` (its length is the frame); `None` conceals a lost one.
    pub fn decode(&mut self, data: Option<&[u8]>, out: &mut [f32]) {
        match self {
            Decoder::Pcm { last, fade } => match data {
                Some(d) if d.len() == out.len() * 2 => {
                    for (i, o) in out.iter_mut().enumerate() {
                        *o = i16::from_le_bytes([d[2 * i], d[2 * i + 1]]) as f32 / 32768.0;
                    }
                    last.copy_from_slice(out);
                    *fade = 1.0;
                }
                _ => {
                    // repeat the last frame, fading out over a few frames
                    let step = 1.0 / (out.len() as f32 * 3.0);
                    for (o, l) in out.iter_mut().zip(last.iter()) {
                        *fade = (*fade - step).max(0.0);
                        *o = l * *fade;
                    }
                }
            },
            Decoder::Opus(d) => {
                let input: &[u8] = data.unwrap_or(&[]);
                if d.decode_float(input, out, false).is_err() {
                    out.fill(0.0);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(n: usize) -> Vec<f32> {
        (0..n).map(|i| (i as f32 * 2.0 * std::f32::consts::PI * 440.0 / RATE as f32).sin() * 0.5).collect()
    }

    #[test]
    fn frame_sizes() {
        assert_eq!(frame_samples(2.5).unwrap(), 120);
        assert_eq!(frame_samples(5.0).unwrap(), 240);
        assert!(frame_samples(3.0).is_err());
    }

    #[test]
    fn pcm_roundtrip() {
        let mut e = Encoder::new(Codec::Pcm16, 0).unwrap();
        let mut d = Decoder::new(Codec::Pcm16, 120).unwrap();
        let x = sine(120);
        let mut buf = [0u8; 512];
        let n = e.encode(&x, &mut buf);
        assert_eq!(n, 240);
        let mut y = vec![0.0; 120];
        d.decode(Some(&buf[..n]), &mut y);
        for (a, b) in x.iter().zip(&y) {
            assert!((a - b).abs() < 1e-3);
        }
        d.decode(None, &mut y); // concealment does not blow up and fades
        assert!(y.iter().all(|v| v.abs() <= 0.5));
    }

    #[test]
    fn opus_roundtrip_has_energy() {
        let mut e = Encoder::new(Codec::Opus, 128_000).unwrap();
        let mut d = Decoder::new(Codec::Opus, 120).unwrap();
        let x = sine(120 * 50);
        let mut y = vec![0.0; 120];
        let mut energy = 0.0;
        let mut buf = [0u8; 1200];
        for f in x.chunks(120) {
            let n = e.encode(f, &mut buf);
            assert!(n > 0);
            d.decode(Some(&buf[..n]), &mut y);
            energy += y.iter().map(|v| v * v).sum::<f32>();
        }
        assert!(energy > 10.0, "energy {energy}");
        d.decode(None, &mut y); // PLC
    }
}
