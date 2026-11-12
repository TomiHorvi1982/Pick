//! The control socket: a WebSocket on 127.0.0.1 that Pick's pages talk to.
//!
//! * only pages from allowed origins get in (Pick itself, the jam gateway on the tailnet, extra
//!   ones given with --allow-origin); anything else is refused at the handshake;
//! * one page controls the engine at a time: a newer connection takes over, the older one is closed
//!   and its link stopped;
//! * when the page goes away, the link stops (nothing keeps sending on its own).
//!
//! Page → engine (JSON): hello · devices · start {source, input, output, in_ch, buffer, codec, frame,
//! bitrate, rate} · peer {addr} · mix {gain, pan} · stop. Binary messages are the page's sound
//! (Float32Array, mono) when started with source "browser".
//! Engine → page: hello · devices · started · stats (twice a second) · stopped · error.

use crate::audio::devices_json;
use crate::engine::{tailscale_ip, Engine, Source, StartOpts};
use crate::packet::Codec;
use crate::stats::peak_take;
use anyhow::Result;
use serde_json::{json, Value};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tungstenite::handshake::server::{ErrorResponse, Request, Response};
use tungstenite::{Message, WebSocket};

pub const DEFAULT_ADDR: &str = "127.0.0.1:9877";
pub const UDP_PORT: u16 = 47000;

#[derive(Clone, Debug)]
pub struct Origins(Vec<String>);

impl Origins {
    pub fn new(extra: &[String]) -> Self {
        let mut v: Vec<String> = ["http://127.0.0.1:8765", "http://localhost:8765", "https://*.ts.net"].iter().map(|s| s.to_string()).collect();
        v.extend(extra.iter().map(|s| s.trim_end_matches('/').to_lowercase()));
        Origins(v)
    }

    /// Exact match, or "scheme://*.suffix" for any one-or-more-label host under it.
    pub fn allows(&self, origin: &str) -> bool {
        let o = origin.trim_end_matches('/').to_lowercase();
        self.0.iter().any(|rule| match rule.split_once("://*.") {
            Some((scheme, suffix)) => o
                .strip_prefix(&format!("{scheme}://"))
                .is_some_and(|host| {
                    let host = host.split(':').next().unwrap_or("");
                    host.len() > suffix.len() + 1
                        && host.ends_with(&format!(".{suffix}"))
                        && host.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.')
                }),
            None => *rule == o,
        })
    }

    pub fn list(&self) -> &[String] {
        &self.0
    }
}

/// The address told to the other side: given with --ip (home network, tests), else this machine's Tailscale one.
static ADVERTISE: std::sync::OnceLock<Option<std::net::IpAddr>> = std::sync::OnceLock::new();

fn my_ip() -> Option<std::net::IpAddr> {
    ADVERTISE.get().copied().flatten().or_else(tailscale_ip)
}

pub fn serve(addr: &str, origins: Origins, ip: Option<std::net::IpAddr>) -> Result<()> {
    let _ = ADVERTISE.set(ip);
    let listener = TcpListener::bind(addr)?;
    println!("Pick Jam Engine {} — control on ws://{addr}", env!("CARGO_PKG_VERSION"));
    println!("allowed pages: {}", origins.list().join(", "));
    if let Some(ip) = ip {
        println!("address for the peer: {ip} (given)");
    } else if let Some(ip) = tailscale_ip() {
        println!("Tailscale: {ip}");
    } else {
        println!("Tailscale: not found (the native link needs it)");
    }
    // list the devices once in the background, so the first page gets them at once
    std::thread::spawn(|| {
        fresh_devices(&None);
    });
    let generation = Arc::new(AtomicU64::new(0));
    for conn in listener.incoming() {
        let Ok(stream) = conn else { continue };
        let (origins, generation) = (origins.clone(), generation.clone());
        std::thread::spawn(move || {
            if let Err(e) = handle(stream, &origins, &generation) {
                eprintln!("control: {e}");
            }
        });
    }
    Ok(())
}

fn handle(stream: TcpStream, origins: &Origins, generation: &AtomicU64) -> Result<()> {
    let peer_addr = stream.peer_addr()?;
    let mut origin = String::new();
    let check = |req: &Request, resp: Response| -> std::result::Result<Response, ErrorResponse> {
        let o = req.headers().get("origin").and_then(|v| v.to_str().ok()).unwrap_or("");
        if origins.allows(o) {
            origin = o.to_string();
            Ok(resp)
        } else {
            let mut r = ErrorResponse::new(Some(format!("origin not allowed: {o:?}")));
            *r.status_mut() = tungstenite::http::StatusCode::FORBIDDEN;
            Err(r)
        }
    };
    let mut ws = match tungstenite::accept_hdr(stream, check) {
        Ok(ws) => ws,
        Err(e) => {
            eprintln!("refused {peer_addr}: {e}");
            return Ok(());
        }
    };
    let me = generation.fetch_add(1, Relaxed) + 1;
    println!("page connected: {origin}");
    ws.get_ref().set_read_timeout(Some(Duration::from_millis(50)))?;
    let mut eng: Option<Engine> = None;
    let mut last_stats = Instant::now();
    let result = run_loop(&mut ws, &mut eng, &mut last_stats, me, generation);
    drop(eng);
    let _ = ws.close(None);
    let _ = ws.flush();
    println!("page gone: {origin}");
    result
}

fn send(ws: &mut WebSocket<TcpStream>, v: Value) -> Result<()> {
    ws.send(Message::text(v.to_string()))?;
    Ok(())
}

fn run_loop(
    ws: &mut WebSocket<TcpStream>,
    eng: &mut Option<Engine>,
    last_stats: &mut Instant,
    me: u64,
    generation: &AtomicU64,
) -> Result<()> {
    loop {
        if generation.load(Relaxed) != me {
            let _ = send(ws, json!({"type": "error", "code": "taken", "msg": "another page took the engine over"}));
            return Ok(());
        }
        match ws.read() {
            Ok(Message::Text(t)) => {
                let m: Value = match serde_json::from_str(&t) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                if let Err(e) = command(ws, eng, &m) {
                    send(ws, json!({"type": "error", "for": m["type"], "msg": format!("{e:#}")}))?;
                }
            }
            Ok(Message::Binary(b)) => {
                if let Some(e) = eng.as_mut() {
                    let n = b.len() / 4;
                    let mut tmp = [0f32; 2048];
                    for chunk in b[..n * 4].chunks(4 * tmp.len()) {
                        let k = chunk.len() / 4;
                        for (i, c) in chunk.chunks_exact(4).enumerate() {
                            tmp[i] = f32::from_le_bytes([c[0], c[1], c[2], c[3]]);
                        }
                        e.push_browser(&tmp[..k]);
                    }
                }
            }
            Ok(Message::Close(_)) => return Ok(()),
            Ok(_) => {}
            Err(tungstenite::Error::Io(e)) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(tungstenite::Error::ConnectionClosed) | Err(tungstenite::Error::AlreadyClosed) => return Ok(()),
            Err(e) => return Err(e.into()),
        }
        if last_stats.elapsed() >= Duration::from_millis(500) {
            *last_stats = Instant::now();
            if let Some(e) = eng.as_ref() {
                send(ws, stats_json(e))?;
            }
        }
    }
}

fn stats_json(e: &Engine) -> Value {
    let s = &e.stats;
    json!({
        "type": "stats",
        "tx": s.tx.load(Relaxed), "rx": s.rx.load(Relaxed), "lost": s.lost.load(Relaxed),
        "late": s.late.load(Relaxed), "underruns": s.underruns.load(Relaxed),
        "jitter_ms": s.jitter_us.load(Relaxed) as f64 / 1000.0,
        "target": s.target_frames.load(Relaxed),
        "buffer_ms": s.fill_samples.load(Relaxed) as f64 / 48.0,
        "drift_ppm": s.ratio_ppm.load(Relaxed),
        "in_lat_ms": s.in_lat_us.load(Relaxed) as f64 / 1000.0,
        "out_lat_ms": s.out_lat_us.load(Relaxed) as f64 / 1000.0,
        "in_peak": peak_take(&s.in_peak),
        "peer_peak": peak_take(&s.peer_peak),
        "rx_age_ms": e.rx_age_ms(),
        "frame_ms": e.frame as f64 / 48.0,
        "peer": e.peer().map(|p| p.to_string()),
    })
}

/// The last device list per audio system; "devices" refreshes it (a card plugged in later).
static DEVICES: Mutex<Vec<(Option<String>, Value)>> = Mutex::new(Vec::new());

fn cached_devices(host: &Option<String>) -> Option<Value> {
    DEVICES.lock().ok()?.iter().find(|(h, _)| h == host).map(|(_, v)| v.clone())
}

fn fresh_devices(host: &Option<String>) -> Value {
    let v = devices_json(host);
    if let Ok(mut c) = DEVICES.lock() {
        c.retain(|(h, _)| h != host);
        c.push((host.clone(), v.clone()));
    }
    v
}

fn started_json(e: &Engine) -> Value {
    json!({
        "type": "started", "port": e.port, "ip": my_ip().map(|i| i.to_string()),
        "source": match e.source { Source::Device => "device", Source::Browser { .. } => "browser" },
        "host": e.host, "input": e.input, "output": e.output, "in_ch": e.in_ch, "buffer": e.buffer,
        "in_rate": e.in_rate, "out_rate": e.out_rate,
        "codec": match e.codec { Codec::Opus => "opus", Codec::Pcm16 => "pcm" },
        "frame_ms": e.frame as f64 / 48.0,
    })
}

fn command(ws: &mut WebSocket<TcpStream>, eng: &mut Option<Engine>, m: &Value) -> Result<()> {
    let s = |k: &str| m[k].as_str().filter(|v| !v.is_empty()).map(String::from);
    match m["type"].as_str().unwrap_or("") {
        // hello answers at once (the page's probe waits only briefly); listing the devices can take seconds
        // on some Windows machines, so the list follows in its own message (from the cache when it has one)
        "hello" => {
            let host = s("host");
            let cached = cached_devices(&host);
            send(ws, json!({
                "type": "hello", "version": env!("CARGO_PKG_VERSION"), "rate": crate::codec::RATE,
                "ip": my_ip().map(|i| i.to_string()), "devices": cached.clone(),
                "running": eng.as_ref().map(started_json),
            }))?;
            if cached.is_none() {
                send(ws, json!({"type": "devices", "devices": fresh_devices(&host)}))?;
            }
            Ok(())
        }
        "devices" => send(ws, json!({"type": "devices", "devices": fresh_devices(&s("host"))})),
        "start" => {
            *eng = None; // the old link first: it holds the sound card and the port
            let source = match m["source"].as_str() {
                Some("browser") => Source::Browser { rate: m["rate"].as_u64().unwrap_or(48_000) as u32 },
                _ => Source::Device,
            };
            let codec = match m["codec"].as_str() {
                Some("pcm") => Codec::Pcm16,
                _ => Codec::Opus,
            };
            let e = Engine::start(StartOpts {
                host: s("host"),
                input: s("input"),
                output: s("output"),
                in_ch: m["in_ch"].as_u64().unwrap_or(1) as usize,
                buffer: m["buffer"].as_u64().unwrap_or(64) as u32,
                source,
                codec,
                frame: crate::codec::frame_samples(m["frame"].as_f64().unwrap_or(2.5) as f32)?,
                bitrate: m["bitrate"].as_i64().unwrap_or(128_000) as i32,
                min_target: m["min_target"].as_u64().unwrap_or(1) as u32,
                max_target: m["max_target"].as_u64().unwrap_or(24) as u32,
                listen: SocketAddr::from(([0, 0, 0, 0], UDP_PORT)),
            })?;
            println!("started: {}", e.info());
            let v = started_json(&e);
            *eng = Some(e);
            send(ws, v)
        }
        "peer" => {
            let e = eng.as_ref().ok_or_else(|| anyhow::anyhow!("not started"))?;
            let addr: Option<SocketAddr> = match s("addr") {
                Some(a) => Some(a.parse()?),
                None => None,
            };
            e.set_peer(addr);
            println!("peer: {addr:?}");
            send(ws, json!({"type": "peer", "addr": addr.map(|a| a.to_string())}))
        }
        "mix" => {
            if let Some(e) = eng.as_ref() {
                e.mix.set(m["gain"].as_f64().unwrap_or(1.0) as f32, m["pan"].as_f64().unwrap_or(0.0) as f32);
            }
            Ok(())
        }
        "stop" => {
            *eng = None;
            send(ws, json!({"type": "stopped"}))
        }
        other => send(ws, json!({"type": "error", "msg": format!("unknown command {other:?}")})),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn origins() {
        let o = Origins::new(&["https://jam.example.cz/".into()]);
        assert!(o.allows("http://127.0.0.1:8765"));
        assert!(o.allows("https://maxbook.tail1234.ts.net"));
        assert!(o.allows("https://maxbook.tail1234.ts.net:443"));
        assert!(o.allows("https://jam.example.cz"));
        assert!(!o.allows("https://ts.net"));
        assert!(!o.allows("http://maxbook.tail1234.ts.net")); // not https
        assert!(!o.allows("https://evil.com/.ts.net"));
        assert!(!o.allows("https://evilts.net"));
        assert!(!o.allows("http://127.0.0.1:9999"));
        assert!(!o.allows(""));
        assert!(!o.allows("null"));
    }
}
