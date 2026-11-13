//! Pick Jam Engine — the native sound path for Pick Jam (see docs/specs/2026-11-11-pick-jam-engine-design.md).
// Windows: no console window of its own (the friend double-clicks it; from a terminal it prints there).
#![cfg_attr(windows, windows_subsystem = "windows")]

mod audio;
mod codec;
mod control;
mod desktop;
mod engine;
mod jitter;
mod net;
mod packet;
mod playout;
mod selftest;
mod stats;

use anyhow::Result;
use clap::{Parser, Subcommand, ValueEnum};
use std::net::SocketAddr;

#[derive(Parser)]
#[command(name = "pick-jam-engine", version, about = "Pick Jam Engine: native low-latency guitar link")]
struct Cli {
    /// with no command: serve the control socket for Pick's pages
    #[command(subcommand)]
    cmd: Option<Cmd>,
}

#[derive(Clone, Copy, ValueEnum)]
enum CodecArg {
    Opus,
    Pcm,
}

impl From<CodecArg> for packet::Codec {
    fn from(c: CodecArg) -> Self {
        match c {
            CodecArg::Opus => packet::Codec::Opus,
            CodecArg::Pcm => packet::Codec::Pcm16,
        }
    }
}

#[derive(clap::Args)]
struct Link {
    /// opus (small packets) or pcm (no compression, ~770 kbit/s)
    #[arg(long, value_enum, default_value = "opus")]
    codec: CodecArg,
    /// frame length in ms: 2.5, 5 or 10
    #[arg(long, default_value_t = 2.5)]
    frame: f32,
    /// Opus bitrate in bit/s
    #[arg(long, default_value_t = 128_000)]
    bitrate: i32,
    /// smallest jitter buffer depth in frames
    #[arg(long, default_value_t = 1)]
    min_target: u32,
    /// largest jitter buffer depth in frames
    #[arg(long, default_value_t = 24)]
    max_target: u32,
}

#[derive(Subcommand)]
enum Cmd {
    /// Windows: install for this user (desktop icon "Pick Jam", the pickjam:// link the page uses) and open
    Install,
    /// open Pick Jam: the engine in the background, then the app window
    Open,
    /// a pickjam:// link from the page: start the engine (no window)
    Url { link: Option<String> },
    /// Wait for Pick's pages on the control socket (the default)
    Serve {
        #[arg(long, default_value = control::DEFAULT_ADDR)]
        addr: String,
        /// another page origin that may control the engine (e.g. https://jam.mydomain.cz); repeatable
        #[arg(long)]
        allow_origin: Vec<String>,
        /// address to tell the other side (default: this machine's Tailscale address)
        #[arg(long)]
        ip: Option<std::net::IpAddr>,
    },
    /// List sound devices
    Devices {
        /// audio system (e.g. asio); default: the platform's own
        #[arg(long)]
        host: Option<String>,
    },
    /// Play with a peer: guitar in → peer, peer → headphones
    Run {
        /// local UDP address to receive on
        #[arg(long, default_value = "0.0.0.0:47000")]
        listen: SocketAddr,
        /// the peer's address (Tailscale IP:port)
        #[arg(long)]
        peer: SocketAddr,
        /// audio system (e.g. asio); default: the platform's own (CoreAudio, WASAPI)
        #[arg(long)]
        host: Option<String>,
        /// input device (part of its name); default device when omitted
        #[arg(long)]
        input: Option<String>,
        /// output device (part of its name)
        #[arg(long)]
        output: Option<String>,
        /// input channel, 1-based (the guitar's input on the interface)
        #[arg(long, default_value_t = 1)]
        in_ch: usize,
        /// sound card buffer in samples (0 = device default)
        #[arg(long, default_value_t = 64)]
        buffer: u32,
        #[command(flatten)]
        link: Link,
    },
    /// Measure the link on this machine without a sound card (jitter, loss, clock skew simulated)
    Selftest {
        #[arg(long, default_value_t = 10)]
        secs: u64,
        /// random extra delay up to this many ms
        #[arg(long, default_value_t = 1.0)]
        jitter_ms: f64,
        /// share of packets dropped (0..1)
        #[arg(long, default_value_t = 0.0)]
        loss: f64,
        /// the far sound card's clock is this many ppm fast
        #[arg(long, default_value_t = 100.0)]
        skew_ppm: f64,
        /// output block in samples
        #[arg(long, default_value_t = 64)]
        block: usize,
        #[command(flatten)]
        link: Link,
    },
}

fn main() -> Result<()> {
    #[cfg(windows)]
    {
        let terminal = desktop::attach_console();
        // double-clicked (no arguments, no terminal): the downloaded copy installs itself, the installed one opens
        if std::env::args().len() == 1 && !terminal {
            if let Some(r) = desktop::double_clicked() {
                // no terminal to print to: what went wrong goes into a small window
                if let Err(e) = &r {
                    desktop::message("Pick Jam", &format!("Něco se nepovedlo:\n{e:#}"));
                }
                return r;
            }
        }
    }
    let cmd = Cli::parse().cmd.unwrap_or(Cmd::Serve { addr: control::DEFAULT_ADDR.into(), allow_origin: vec![], ip: None });
    match cmd {
        Cmd::Install => desktop::install(),
        Cmd::Open => desktop::open(),
        Cmd::Url { .. } => desktop::from_link(),
        Cmd::Serve { addr, allow_origin, ip } => control::serve(&addr, control::Origins::new(&allow_origin), ip),
        Cmd::Devices { host } => audio::list_devices(&host),
        Cmd::Run { listen, peer, host, input, output, in_ch, buffer, link } => audio::run(audio::RunOpts {
            host,
            input,
            output,
            in_ch,
            buffer,
            listen,
            peer,
            codec: link.codec.into(),
            frame: codec::frame_samples(link.frame)?,
            bitrate: link.bitrate,
            min_target: link.min_target,
            max_target: link.max_target,
        }),
        Cmd::Selftest { secs, jitter_ms, loss, skew_ppm, block, link } => {
            let r = selftest::run(
                selftest::SelftestOpts {
                    secs,
                    codec: link.codec.into(),
                    frame: codec::frame_samples(link.frame)?,
                    bitrate: link.bitrate,
                    jitter_ms,
                    loss,
                    skew_ppm,
                    block,
                    min_target: link.min_target,
                    max_target: link.max_target,
                },
                true,
            )?;
            println!("{}", selftest::summary(&r));
            Ok(())
        }
    }
}
