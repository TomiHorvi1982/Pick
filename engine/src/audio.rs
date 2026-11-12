//! The sound card: one input stream (the guitar → network) and one output stream (network → ears).

use crate::codec::RATE;
use crate::packet::Codec;
use anyhow::{anyhow, bail, Result};
use cpal::traits::{DeviceTrait, HostTrait};
use cpal::{BufferSize, Device, FromSample, SampleFormat, SizedSample, Stream, StreamConfig};
use std::net::SocketAddr;
use std::time::Duration;

pub struct RunOpts {
    pub host: Option<String>,
    pub input: Option<String>,
    pub output: Option<String>,
    pub in_ch: usize,
    pub buffer: u32,
    pub listen: SocketAddr,
    pub peer: SocketAddr,
    pub codec: Codec,
    pub frame: usize,
    pub bitrate: i32,
    pub min_target: u32,
    pub max_target: u32,
}

/// The audio system: the platform default (CoreAudio, WASAPI), or one named, e.g. "asio".
pub fn host(name: &Option<String>) -> Result<cpal::Host> {
    match name.as_deref().map(str::to_lowercase).as_deref() {
        None | Some("") => Ok(cpal::default_host()),
        Some(n) => {
            let id = cpal::available_hosts()
                .into_iter()
                .find(|h| h.name().to_lowercase() == n)
                .ok_or_else(|| anyhow!("audio system {n:?} not available (have: {})", host_names().join(", ")))?;
            Ok(cpal::host_from_id(id)?)
        }
    }
}

pub fn host_names() -> Vec<String> {
    cpal::available_hosts().into_iter().map(|h| h.name().to_string()).collect()
}

pub fn list_devices(name: &Option<String>) -> Result<()> {
    let host = host(name)?;
    println!("host: {:?} (available: {})", host.id(), host_names().join(", "));
    let din = host.default_input_device().and_then(|d| d.name().ok());
    let dout = host.default_output_device().and_then(|d| d.name().ok());
    for d in host.devices()? {
        let name = d.name().unwrap_or_default();
        let ins = d.default_input_config().map(|c| c.channels()).unwrap_or(0);
        let outs = d.default_output_config().map(|c| c.channels()).unwrap_or(0);
        let rates: Vec<String> = d
            .supported_input_configs()
            .into_iter()
            .flatten()
            .chain(d.supported_output_configs().into_iter().flatten())
            .map(|c| format!("{}-{}", c.min_sample_rate().0, c.max_sample_rate().0))
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect();
        let mark = |b: bool| if b { "*" } else { " " };
        println!(
            "{}{} in {:>2} out {:>2}  {}  [{}]",
            mark(din.as_deref() == Some(&name)),
            mark(dout.as_deref() == Some(&name)),
            ins,
            outs,
            name,
            rates.join(", ")
        );
    }
    println!("(* first column = default input, second = default output)");
    Ok(())
}

pub fn pick(host: &cpal::Host, want: &Option<String>, input: bool) -> Result<Device> {
    match want {
        None => if input { host.default_input_device() } else { host.default_output_device() }
            .ok_or_else(|| anyhow!("no default {} device", if input { "input" } else { "output" })),
        Some(w) => {
            let w = w.to_lowercase();
            let devs: Vec<Device> = if input { host.input_devices()?.collect() } else { host.output_devices()?.collect() };
            devs.into_iter()
                .find(|d| d.name().map(|n| n.to_lowercase().contains(&w)).unwrap_or(false))
                .ok_or_else(|| anyhow!("no {} device matching {:?} (see `devices`)", if input { "input" } else { "output" }, want))
        }
    }
}

/// What we open the device with: its own sample rate and format (WASAPI shared mode only takes
/// the mix format; ASIO cards often give 32-bit integers). 48 kHz is chosen when the device offers it
/// in that format (not on Windows, see below); anything else is resampled inside the engine.
pub struct DevCfg {
    pub cfg: StreamConfig,
    pub format: SampleFormat,
}

pub fn config(dev: &Device, input: bool, buffer: u32) -> Result<DevCfg> {
    let def = if input { dev.default_input_config()? } else { dev.default_output_config()? };
    let format = def.sample_format();
    if !matches!(format, SampleFormat::F32 | SampleFormat::I16 | SampleFormat::I32 | SampleFormat::U16) {
        bail!("{}: sample format {format:?} not supported", dev.name().unwrap_or_default());
    }
    let has48 = |mut it: Box<dyn Iterator<Item = cpal::SupportedStreamConfigRange>>| {
        it.any(|c| c.min_sample_rate().0 <= RATE && c.max_sample_rate().0 >= RATE && c.sample_format() == format)
    };
    let has48 = if input {
        dev.supported_input_configs().map(|i| has48(Box::new(i))).unwrap_or(false)
    } else {
        dev.supported_output_configs().map(|i| has48(Box::new(i))).unwrap_or(false)
    };
    // Windows: WASAPI shared mode only opens at the mix format's rate, and an ASIO driver runs at the rate
    // its control panel says, so take the device's own rate there; elsewhere switch the device to 48 kHz.
    let rate = if has48 && !cfg!(windows) { RATE } else { def.sample_rate().0 };
    if !(8_000..=384_000).contains(&rate) {
        bail!("{}: sample rate {rate} not supported", dev.name().unwrap_or_default());
    }
    Ok(DevCfg {
        cfg: StreamConfig {
            channels: def.channels(),
            sample_rate: cpal::SampleRate(rate),
            buffer_size: if buffer > 0 { BufferSize::Fixed(buffer) } else { BufferSize::Default },
        },
        format,
    })
}

fn stream_err(e: cpal::StreamError) {
    eprintln!("stream error: {e}");
}

/// An output stream in the device's own sample format; `fill` always writes interleaved f32.
pub fn build_output(
    dev: &Device,
    c: &DevCfg,
    fill: impl FnMut(&mut [f32], &cpal::OutputCallbackInfo) + Send + 'static,
) -> Result<Stream> {
    fn typed<T: SizedSample + FromSample<f32>>(
        dev: &Device,
        cfg: &StreamConfig,
        mut fill: impl FnMut(&mut [f32], &cpal::OutputCallbackInfo) + Send + 'static,
    ) -> Result<Stream> {
        let mut tmp = vec![0.0f32; 1 << 16];
        Ok(dev.build_output_stream(
            cfg,
            move |data: &mut [T], info: &cpal::OutputCallbackInfo| {
                for chunk in data.chunks_mut(tmp.len()) {
                    let t = &mut tmp[..chunk.len()];
                    fill(t, info);
                    for (d, s) in chunk.iter_mut().zip(t.iter()) {
                        *d = T::from_sample_(*s);
                    }
                }
            },
            stream_err,
            None,
        )?)
    }
    match c.format {
        SampleFormat::F32 => typed::<f32>(dev, &c.cfg, fill),
        SampleFormat::I16 => typed::<i16>(dev, &c.cfg, fill),
        SampleFormat::I32 => typed::<i32>(dev, &c.cfg, fill),
        SampleFormat::U16 => typed::<u16>(dev, &c.cfg, fill),
        f => bail!("sample format {f:?} not supported"),
    }
}

/// An input stream in the device's own sample format; `take` always gets interleaved f32.
pub fn build_input(
    dev: &Device,
    c: &DevCfg,
    take: impl FnMut(&[f32], &cpal::InputCallbackInfo) + Send + 'static,
) -> Result<Stream> {
    fn typed<T: SizedSample>(
        dev: &Device,
        cfg: &StreamConfig,
        mut take: impl FnMut(&[f32], &cpal::InputCallbackInfo) + Send + 'static,
    ) -> Result<Stream>
    where
        f32: FromSample<T>,
    {
        let mut tmp = vec![0.0f32; 1 << 16];
        Ok(dev.build_input_stream(
            cfg,
            move |data: &[T], info: &cpal::InputCallbackInfo| {
                for chunk in data.chunks(tmp.len()) {
                    let t = &mut tmp[..chunk.len()];
                    for (d, s) in t.iter_mut().zip(chunk.iter()) {
                        *d = <f32 as FromSample<T>>::from_sample_(*s);
                    }
                    take(t, info);
                }
            },
            stream_err,
            None,
        )?)
    }
    match c.format {
        SampleFormat::F32 => typed::<f32>(dev, &c.cfg, take),
        SampleFormat::I16 => typed::<i16>(dev, &c.cfg, take),
        SampleFormat::I32 => typed::<i32>(dev, &c.cfg, take),
        SampleFormat::U16 => typed::<u16>(dev, &c.cfg, take),
        f => bail!("sample format {f:?} not supported"),
    }
}

pub fn run(o: RunOpts) -> Result<()> {
    let eng = crate::engine::Engine::start(crate::engine::StartOpts {
        host: o.host,
        input: o.input,
        output: o.output,
        in_ch: o.in_ch,
        buffer: o.buffer,
        source: crate::engine::Source::Device,
        codec: o.codec,
        frame: o.frame,
        bitrate: o.bitrate,
        min_target: o.min_target,
        max_target: o.max_target,
        listen: o.listen,
    })?;
    eng.set_peer(Some(o.peer));
    println!("{}", eng.info());
    println!("running — Ctrl+C to stop");
    loop {
        std::thread::sleep(Duration::from_secs(1));
        println!("{}", eng.stats.line());
    }
}

/// The devices as JSON, for the page.
pub fn devices_json(name: &Option<String>) -> serde_json::Value {
    let host = match host(name) {
        Ok(h) => h,
        Err(e) => return serde_json::json!({"error": e.to_string(), "hosts": host_names(), "inputs": [], "outputs": []}),
    };
    let list = |input: bool| -> Vec<serde_json::Value> {
        let devs: Vec<Device> = if input {
            host.input_devices().map(|d| d.collect()).unwrap_or_default()
        } else {
            host.output_devices().map(|d| d.collect()).unwrap_or_default()
        };
        devs.into_iter()
            .filter_map(|d| {
                let name = d.name().ok()?;
                let cfg = if input { d.default_input_config().ok()? } else { d.default_output_config().ok()? };
                let c = config(&d, input, 0);
                Some(serde_json::json!({
                    "name": name,
                    "channels": cfg.channels(),
                    "ok": c.is_ok(),
                    "rate": c.as_ref().map(|c| c.cfg.sample_rate.0).unwrap_or(cfg.sample_rate().0),
                    "format": format!("{:?}", cfg.sample_format()),
                }))
            })
            .collect()
    };
    serde_json::json!({
        "host": host.id().name(),
        "hosts": host_names(),
        "inputs": list(true),
        "outputs": list(false),
        "default_input": host.default_input_device().and_then(|d| d.name().ok()),
        "default_output": host.default_output_device().and_then(|d| d.name().ok()),
    })
}
