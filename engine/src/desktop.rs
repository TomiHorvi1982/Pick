//! The engine as the friend's one download on Windows ("Pick Jam.exe"): double-clicked it installs itself
//! (no window, no terminal), puts "Pick Jam" on the desktop, lets the jam page start it with one click
//! (the pickjam:// link) and opens the app window. Later starts keep it fresh from the library.

use anyhow::Result;
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

/// Where the friend's Pick Jam lives (the library through Pick's tunnel).
#[allow(dead_code)]
pub const APP_URL: &str = "https://jam.neverlast.cz/";

/// An engine already answers on this machine's control port.
pub fn running() -> bool {
    let a: SocketAddr = crate::control::DEFAULT_ADDR.parse().unwrap_or_else(|_| SocketAddr::from(([127, 0, 0, 1], 9877)));
    TcpStream::connect_timeout(&a, Duration::from_millis(400)).is_ok()
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::os::windows::process::CommandExt;
    use std::path::PathBuf;
    use std::process::{Command, Stdio};

    const NO_WINDOW: u32 = 0x0800_0000; // CREATE_NO_WINDOW
    const DETACHED: u32 = 0x0000_0008; // DETACHED_PROCESS

    fn home() -> PathBuf {
        PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into())).join("PickJam")
    }

    pub fn installed_exe() -> PathBuf {
        home().join("pick-jam-engine.exe")
    }

    /// Starts `exe args` with no window, not waiting for it.
    fn spawn(exe: &std::path::Path, args: &[&str]) -> Result<()> {
        Command::new(exe).args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
            .creation_flags(NO_WINDOW | DETACHED).spawn()?;
        Ok(())
    }

    fn powershell(script: &str) -> Result<()> {
        let ps = PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into()))
            .join(r"System32\WindowsPowerShell\v1.0\powershell.exe");
        Command::new(ps).args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
            .creation_flags(NO_WINDOW).status()?;
        Ok(())
    }

    /// Copies itself home, writes the start script, the desktop shortcut and the pickjam:// link, then opens.
    pub fn install() -> Result<()> {
        let dir = home();
        std::fs::create_dir_all(&dir)?;
        let me = std::env::current_exe()?;
        let exe = installed_exe();
        if me != exe {
            // an engine from an older install still running would keep its file locked: stop it, then replace it
            let _ = Command::new("taskkill").args(["/F", "/IM", "pick-jam-engine.exe"]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
                .creation_flags(NO_WINDOW).status();
            std::thread::sleep(std::time::Duration::from_millis(600));
            std::fs::copy(&me, &exe)?;
            log("installed");
        }
        let d = dir.display().to_string().replace('\'', "''");
        let e = exe.display().to_string().replace('\'', "''");
        let url = APP_URL;
        // the start script: a fresh engine from the library (only while none runs), the engine, the app window
        let start = format!(r#"$dir = '{d}'
$exe = '{e}'
$url = '{url}'
if (-not (Get-Process pick-jam-engine -ErrorAction SilentlyContinue)) {{
  try {{
    $tmp = "$exe.new"
    Invoke-WebRequest -UseBasicParsing -Uri ($url + 'engine/pick-jam-engine.exe') -OutFile $tmp -TimeoutSec 30
    if ((Get-Item $tmp).Length -gt 100000 -and ((Get-FileHash $tmp).Hash -ne (Get-FileHash $exe).Hash)) {{
      Remove-Item "$exe.old" -ErrorAction SilentlyContinue; Rename-Item $exe "$exe.old"; Move-Item $tmp $exe
    }} else {{ Remove-Item $tmp -ErrorAction SilentlyContinue }}
  }} catch {{ }}
  Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden
}}
$cands = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${{env:ProgramFiles(x86)}}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe", "${{env:ProgramFiles(x86)}}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
$browser = $cands | Where-Object {{ Test-Path -LiteralPath $_ }} | Select-Object -First 1
if ($browser) {{ Start-Process -FilePath $browser -ArgumentList ("--app=" + $url + "app") }} else {{ Start-Process ($url + "app") }}
"#);
        std::fs::write(dir.join("start.ps1"), start)?;
        let ico = dir.join("pick-jam.ico").display().to_string().replace('\'', "''");
        let start_ps1 = dir.join("start.ps1").display().to_string().replace('\'', "''");
        powershell(&format!(r#"
try {{ Invoke-WebRequest -UseBasicParsing -Uri '{url}static/jam-icon.ico' -OutFile '{ico}' -TimeoutSec 20 }} catch {{ }}
$desk = [Environment]::GetFolderPath('Desktop')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\Pick Jam.lnk")
$lnk.TargetPath = '{e}'
$lnk.Arguments = 'open'
$lnk.Description = 'Pick Jam'
if (Test-Path -LiteralPath '{ico}') {{ $lnk.IconLocation = '{ico},0' }}
$lnk.Save()
$k = 'HKCU:\Software\Classes\pickjam'
New-Item -Path "$k\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $k -Name '(default)' -Value 'URL:Pick Jam Engine'
Set-ItemProperty -Path $k -Name 'URL Protocol' -Value ''
Set-ItemProperty -Path "$k\shell\open\command" -Name '(default)' -Value ('"' + '{e}' + '" url "%1"')
"#))?;
        let _ = start_ps1;
        // the engine at once (it waits for the page and for the jam); the app the friend opens from the new icon
        from_link()?;
        message("Pick Jam je nainstalovaný", "Na ploše je ikona Pick Jam — spusť ji.\n\nEngine už běží a čeká na tebe.");
        Ok(())
    }

    /// A small Windows message (the only window it ever shows: after installing).
    fn message(title: &str, text: &str) {
        #[link(name = "user32")]
        extern "system" {
            fn MessageBoxW(hwnd: isize, text: *const u16, caption: *const u16, kind: u32) -> i32;
        }
        let w = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
        let (t, c) = (w(text), w(title));
        unsafe {
            MessageBoxW(0, t.as_ptr(), c.as_ptr(), 0x40); // MB_ICONINFORMATION
        }
    }

    /// A line in %LOCALAPPDATA%\PickJam\pickjam.log (what the icon did, for when "nothing happens").
    fn log(msg: &str) {
        use std::io::Write;
        let _ = std::fs::create_dir_all(home());
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(home().join("pickjam.log")) {
            let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            let _ = writeln!(f, "{t} {msg}");
        }
    }

    /// Chrome (or Edge) on this machine.
    fn browser() -> Option<PathBuf> {
        let env = |k: &str| std::env::var(k).ok().map(PathBuf::from);
        let mut c = Vec::new();
        for (base, rest) in [("ProgramFiles", r"Google\Chrome\Application\chrome.exe"), ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
                             ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"), ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
                             ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe")] {
            if let Some(b) = env(base) {
                c.push(b.join(rest));
            }
        }
        c.into_iter().find(|p| p.is_file())
    }

    /// A fresh engine from the library, only while none runs (the running one cannot be replaced).
    fn update() {
        let exe = installed_exe().display().to_string().replace('\'', "''");
        let url = APP_URL;
        let _ = powershell(&format!(r#"try {{ $exe = '{exe}'; $tmp = "$exe.new"
  Invoke-WebRequest -UseBasicParsing -Uri '{url}engine/pick-jam-engine.exe' -OutFile $tmp -TimeoutSec 20
  if ((Get-Item $tmp).Length -gt 100000 -and ((Get-FileHash $tmp).Hash -ne (Get-FileHash $exe).Hash)) {{
    Remove-Item "$exe.old" -ErrorAction SilentlyContinue; Rename-Item $exe "$exe.old"; Move-Item $tmp $exe
  }} else {{ Remove-Item $tmp -ErrorAction SilentlyContinue }} }} catch {{ }}"#));
    }

    /// The desktop icon: the engine (fresh from the library when none runs), then the app window.
    pub fn open() -> Result<()> {
        log("open");
        if !running() {
            if std::env::current_exe().ok() == Some(installed_exe()) {
                update();
            }
            let exe = if installed_exe().is_file() { installed_exe() } else { std::env::current_exe()? };
            match spawn(&exe, &["serve"]) {
                Ok(()) => log("engine started"),
                Err(e) => log(&format!("engine failed: {e}")),
            }
        }
        let app = format!("{APP_URL}app");
        match browser() {
            Some(b) => {
                let r = Command::new(&b).arg(format!("--app={app}")).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
                    .creation_flags(DETACHED).spawn();
                log(&format!("browser {} -> {}", b.display(), if r.is_ok() { "ok".to_string() } else { format!("{:?}", r.err()) }));
            }
            None => {
                // no Chrome nor Edge: the default browser
                let r = Command::new("cmd").args(["/C", "start", "", &app]).creation_flags(NO_WINDOW).spawn();
                log(&format!("no Chrome/Edge, default browser -> {}", r.is_ok()));
            }
        }
        Ok(())
    }

    /// pickjam://start from the page: the engine, nothing else.
    pub fn from_link() -> Result<()> {
        if !running() {
            let exe = if installed_exe().is_file() { installed_exe() } else { std::env::current_exe()? };
            spawn(&exe, &["serve"])?;
        }
        Ok(())
    }

    /// When started from a terminal, print there (the program itself has no window of its own).
    /// False: no terminal — it was double-clicked (or started by a link or the desktop icon).
    pub fn attach_console() -> bool {
        extern "system" {
            fn AttachConsole(pid: u32) -> i32;
        }
        unsafe { AttachConsole(0xFFFF_FFFF) != 0 }
    }

    /// Double-clicked with nothing to do (no terminal): the downloaded copy installs, the installed one opens.
    pub fn double_clicked() -> Option<Result<()>> {
        let me = std::env::current_exe().ok()?;
        Some(if me == installed_exe() { open() } else { install() })
    }
}

#[cfg(windows)]
pub use win::{attach_console, double_clicked, from_link, install, open};

#[cfg(not(windows))]
pub fn install() -> Result<()> {
    anyhow::bail!("install is for Windows; on a Mac use Pick Jam pro Mac.zip")
}
#[cfg(not(windows))]
pub fn open() -> Result<()> {
    install()
}
#[cfg(not(windows))]
pub fn from_link() -> Result<()> {
    if !running() {
        std::process::Command::new(std::env::current_exe()?).arg("serve")
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn()?;
    }
    Ok(())
}
