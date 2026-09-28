//! Keeps every webview on the app's own pages. Links meant for the web open in the default browser through the
//! opener plugin after validation (app/src/lib/shell.ts); any other navigation (a dropped link, a middle-click,
//! script setting `location`) is refused here, so remote content can never load inside SCAR's windows.
use tauri::plugin::{Builder, TauriPlugin};
use tauri::{Runtime, Url};

/// The app's own origins: `http(s)://tauri.localhost` (Windows), `tauri://localhost`, and the Vite dev server in
/// debug builds.
pub fn nav_allowed(url: &Url, dev: bool) -> bool {
    match url.scheme() {
        "tauri" => url.host_str() == Some("localhost"),
        "http" | "https" => match url.host_str() {
            Some("tauri.localhost") => url.port().is_none(),
            Some("localhost") | Some("127.0.0.1") => dev && url.port() == Some(1420),
            _ => false,
        },
        "about" => url.path() == "blank",
        _ => false,
    }
}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("scar-navguard")
        .on_navigation(|_webview, url| nav_allowed(url, cfg!(debug_assertions)))
        .build()
}

#[cfg(test)]
mod tests {
    use super::nav_allowed;
    use tauri::Url;

    fn ok(u: &str, dev: bool) -> bool {
        nav_allowed(&Url::parse(u).unwrap(), dev)
    }

    #[test]
    fn app_pages_are_allowed() {
        assert!(ok("http://tauri.localhost/index.html", false));
        assert!(ok("https://tauri.localhost/quickbar.html", false));
        assert!(ok("tauri://localhost/indicator.html", false));
        assert!(ok("about:blank", false));
        assert!(ok("http://localhost:1420/", true));
    }

    #[test]
    fn everything_else_is_refused() {
        assert!(!ok("https://example.com/", false));
        assert!(!ok("http://tauri.localhost.evil.com/", false));
        assert!(!ok("http://tauri.localhost:8080/", false));
        assert!(!ok("http://localhost:1420/", false));
        assert!(!ok("http://localhost:3000/", true));
        assert!(!ok("http://127.0.0.1:53211/api/v1/stop-all", true));
        assert!(!ok("file:///C:/Windows/win.ini", false));
        assert!(!ok("javascript:alert(1)", false));
        assert!(!ok("data:text/html,<script>alert(1)</script>", false));
        assert!(!ok("about:srcdoc", false));
    }
}
