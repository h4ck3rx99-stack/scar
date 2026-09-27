//! Minimal HTTP/1.1 client for the runtime's loopback API (health check, shutdown, tray actions).
//! Only ever talks to 127.0.0.1; the token authenticates every request.
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

pub struct Response {
    pub status: u16,
    #[allow(dead_code)] // the shell acts on the status; the body is kept for debugging
    pub body: String,
}

pub fn request(port: u16, method: &str, path: &str, token: Option<&str>, body: Option<&str>, timeout: Duration) -> Result<Response, String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, timeout).map_err(|e| e.to_string())?;
    s.set_read_timeout(Some(timeout)).ok();
    s.set_write_timeout(Some(timeout)).ok();
    let body = body.unwrap_or("");
    let mut req = format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nContent-Length: {}\r\n", body.len());
    if !body.is_empty() {
        req.push_str("Content-Type: application/json\r\n");
    }
    if let Some(t) = token {
        req.push_str(&format!("Authorization: Bearer {t}\r\n"));
    }
    req.push_str("\r\n");
    req.push_str(body);
    s.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
    let mut buf = Vec::new();
    s.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&buf).to_string();
    let status = text.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
    let body = text.split_once("\r\n\r\n").map(|(_, b)| b.to_string()).unwrap_or_default();
    Ok(Response { status, body })
}
