//! Where the Python runtime keeps its data and configuration (same rules as scar.config.paths).
use std::path::PathBuf;

pub fn data_dir() -> PathBuf {
    if let Ok(d) = std::env::var("SCAR_DATA_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    PathBuf::from(base).join("SCAR")
}

pub fn config_dir() -> PathBuf {
    if let Ok(d) = std::env::var("SCAR_CONFIG_DIR") {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    let base = std::env::var("APPDATA").unwrap_or_else(|_| ".".into());
    PathBuf::from(base).join("SCAR")
}

/// A boolean from config.toml (`key = true`), for the few settings the shell needs before the runtime is up.
pub fn config_bool(key: &str) -> bool {
    let Ok(text) = std::fs::read_to_string(config_dir().join("config.toml")) else {
        return false;
    };
    text.lines().any(|l| {
        let l = l.trim();
        l.starts_with(key) && l[key.len()..].trim_start().starts_with('=') && l.ends_with("true")
    })
}

/// A string from config.toml (`key = "value"`).
pub fn config_str(key: &str) -> Option<String> {
    let text = std::fs::read_to_string(config_dir().join("config.toml")).ok()?;
    for l in text.lines() {
        let l = l.trim();
        if let Some(rest) = l.strip_prefix(key) {
            let rest = rest.trim_start();
            if let Some(v) = rest.strip_prefix('=') {
                let v = v.trim().trim_matches('"');
                return Some(v.to_string());
            }
        }
    }
    None
}
