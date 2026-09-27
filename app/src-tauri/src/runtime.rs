//! The SCAR runtime (Python) is the single source of truth; this shell attaches to a running runtime (daemon or
//! another app instance's) or starts one as a managed sidecar, restarts it after a crash (bounded), and asks it to
//! stop on Quit unless the user keeps it running in the background.
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

use crate::{http, paths};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const DETACHED_PROCESS: u32 = 0x0000_0008;
const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(600);
const START_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Clone, Serialize, Deserialize)]
pub struct Endpoint {
    pub url: String,
    pub token: String,
}

#[derive(Clone, Serialize)]
pub struct RuntimeInfo {
    pub endpoint: Option<Endpoint>,
    pub state: String, // starting | ready | crashed | missing
    pub message: String,
}

#[derive(Deserialize)]
struct EndpointFile {
    url: String,
    port: u16,
    token: String,
}

struct Inner {
    state: String,
    message: String,
    starting: bool,
    owned_pid: Option<u32>,
    restarts: Vec<Instant>,
    quitting: bool,
}

#[derive(Clone)]
pub struct Manager {
    inner: Arc<Mutex<Inner>>,
}

pub fn read_endpoint() -> Option<(Endpoint, u16)> {
    let text = std::fs::read_to_string(paths::data_dir().join("app-api.json")).ok()?;
    let f: EndpointFile = serde_json::from_str(&text).ok()?;
    Some((Endpoint { url: f.url, token: f.token }, f.port))
}

pub fn healthy(port: u16) -> bool {
    matches!(http::request(port, "GET", "/api/v1/health", None, None, Duration::from_millis(1500)), Ok(r) if r.status == 200)
}

/// POST to the runtime with its token (tray actions, shutdown). Errors are returned as text.
pub fn post(path: &str, body: &str) -> Result<u16, String> {
    let (ep, port) = read_endpoint().ok_or("SCAR's runtime isn't running")?;
    http::request(port, "POST", path, Some(&ep.token), Some(body), Duration::from_secs(5)).map(|r| r.status)
}

/// How to start the runtime: an explicit override, the installed runtime, or (debug builds) the repository's venv.
fn runtime_command() -> Option<(PathBuf, Vec<String>)> {
    let args = vec!["-m".to_string(), "scar".to_string(), "daemon".to_string(), "run".to_string()];
    if let Ok(py) = std::env::var("SCAR_RUNTIME_PYTHON") {
        let p = PathBuf::from(py);
        if p.exists() {
            return Some((p, args));
        }
    }
    let installed = paths::data_dir().join("runtime").join("venv").join("Scripts").join("pythonw.exe");
    if installed.exists() {
        return Some((installed, args));
    }
    #[cfg(debug_assertions)]
    {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
        let dev = repo.join(".venv").join("Scripts").join("pythonw.exe");
        if dev.exists() {
            return Some((dev, args));
        }
    }
    None
}

impl Manager {
    pub fn new() -> Self {
        Manager {
            inner: Arc::new(Mutex::new(Inner {
                state: "starting".into(),
                message: "Starting SCAR…".into(),
                starting: false,
                owned_pid: None,
                restarts: Vec::new(),
                quitting: false,
            })),
        }
    }

    fn set(&self, state: &str, message: &str) {
        let mut g = self.inner.lock().unwrap();
        g.state = state.into();
        g.message = message.into();
    }

    /// Current runtime, starting one if none is running.
    pub fn info(&self, app: &AppHandle) -> RuntimeInfo {
        if let Some((ep, port)) = read_endpoint() {
            if healthy(port) {
                self.set("ready", "");
                return RuntimeInfo { endpoint: Some(ep), state: "ready".into(), message: String::new() };
            }
        }
        self.ensure_started(app);
        let g = self.inner.lock().unwrap();
        RuntimeInfo { endpoint: None, state: g.state.clone(), message: g.message.clone() }
    }

    pub fn ensure_started(&self, app: &AppHandle) {
        {
            let mut g = self.inner.lock().unwrap();
            if g.starting || g.quitting {
                return;
            }
            g.starting = true;
        }
        let Some((exe, args)) = runtime_command() else {
            let mut g = self.inner.lock().unwrap();
            g.starting = false;
            g.state = "missing".into();
            g.message = "SCAR's runtime isn't installed yet. Reinstall SCAR, or run it from source (see docs/SETUP.md).".into();
            return;
        };
        self.set("starting", "Starting SCAR's runtime…");
        let mut cmd = Command::new(&exe);
        cmd.args(&args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        if let Some(dir) = exe.parent() {
            cmd.current_dir(dir);
        }
        #[cfg(debug_assertions)]
        cmd.env("SCAR_APP_DEV", "1"); // the dev webview loads from http://localhost:1420
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS);
        let child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                let mut g = self.inner.lock().unwrap();
                g.starting = false;
                g.state = "crashed".into();
                g.message = format!("Couldn't start SCAR's runtime: {e}");
                return;
            }
        };
        let pid = child.id();
        self.inner.lock().unwrap().owned_pid = Some(pid);

        // wait for the runtime to publish a healthy endpoint
        let me = self.clone();
        let app2 = app.clone();
        thread::spawn(move || {
            let deadline = Instant::now() + START_TIMEOUT;
            while Instant::now() < deadline {
                if let Some((_, port)) = read_endpoint() {
                    if healthy(port) {
                        {
                            let mut g = me.inner.lock().unwrap();
                            g.starting = false;
                            g.state = "ready".into();
                            g.message.clear();
                        }
                        let _ = app2.emit("runtime-restarted", ());
                        return;
                    }
                }
                thread::sleep(Duration::from_millis(300));
            }
            let mut g = me.inner.lock().unwrap();
            g.starting = false;
            g.state = "crashed".into();
            g.message = "SCAR's runtime didn't start in time. Open Diagnostics or check the logs folder.".into();
        });

        // watchdog: an unexpected exit is reported and the runtime restarted (bounded)
        let me = self.clone();
        let app3 = app.clone();
        thread::spawn(move || {
            let mut child = child;
            let status = child.wait();
            let (quitting, allow) = {
                let mut g = me.inner.lock().unwrap();
                if g.owned_pid != Some(pid) {
                    return;
                }
                g.owned_pid = None;
                g.starting = false;
                let now = Instant::now();
                g.restarts.retain(|t| now.duration_since(*t) < RESTART_WINDOW);
                let allow = g.restarts.len() < MAX_RESTARTS;
                if !g.quitting {
                    g.state = "crashed".into();
                    g.message = match (&status, allow) {
                        (_, true) => "SCAR's runtime stopped unexpectedly. Restarting…".into(),
                        (_, false) => "SCAR's runtime keeps stopping. Open Diagnostics to see why.".into(),
                    };
                    if allow {
                        g.restarts.push(now);
                    }
                }
                (g.quitting, allow)
            };
            if quitting {
                return;
            }
            let _ = app3.emit("runtime-crashed", ());
            if allow {
                thread::sleep(Duration::from_secs(2));
                me.ensure_started(&app3);
            }
        });
    }

    /// Restart on request (the "Restart runtime" button).
    pub fn restart(&self, app: &AppHandle) {
        let _ = post("/api/v1/shutdown", "{}");
        {
            let mut g = self.inner.lock().unwrap();
            g.restarts.clear();
            g.owned_pid = None;
        }
        thread::sleep(Duration::from_millis(1500));
        self.ensure_started(app);
    }

    /// Quit: stop the runtime unless the user keeps it running in the background.
    pub fn shutdown_for_quit(&self) {
        self.inner.lock().unwrap().quitting = true;
        if !paths::config_bool("keep_running_in_background") {
            let _ = post("/api/v1/shutdown", "{}");
        }
    }
}
