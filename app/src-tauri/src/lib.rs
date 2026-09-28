//! SCAR desktop shell: windows, tray, Quick Bar hotkey, control indicator and runtime lifecycle.
//! No agent, permission or routing logic lives here: everything goes through the runtime's authenticated API.
mod http;
mod navguard;
mod paths;
mod runtime;

use std::sync::Mutex;

use serde::Serialize;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_notification::NotificationExt;

struct ShellState {
    runtime: runtime::Manager,
    quickbar_key: Mutex<Option<String>>,
}

fn tray_bytes(state: &str) -> &'static [u8] {
    match state {
        "working" => include_bytes!("../icons/tray-working@2x.png"),
        "listening" => include_bytes!("../icons/tray-listening@2x.png"),
        "approval" => include_bytes!("../icons/tray-approval@2x.png"),
        "error" => include_bytes!("../icons/tray-error@2x.png"),
        _ => include_bytes!("../icons/tray-idle@2x.png"),
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn show_quickbar(app: &AppHandle) {
    let Some(w) = app.get_webview_window("quickbar") else { return };
    // centre on the monitor under the mouse, a third of the way down
    if let Ok(cursor) = app.cursor_position() {
        if let Ok(Some(m)) = app.monitor_from_point(cursor.x, cursor.y) {
            let size = w.outer_size().unwrap_or(PhysicalSize::new(680, 88));
            let x = m.position().x + (m.size().width as i32 - size.width as i32) / 2;
            let y = m.position().y + m.size().height as i32 / 4;
            let _ = w.set_position(PhysicalPosition::new(x, y));
        }
    }
    let _ = w.show();
    let _ = w.set_focus();
    let _ = app.emit_to("quickbar", "quickbar-shown", ());
}

#[cfg(windows)]
fn show_without_activation(w: &tauri::WebviewWindow) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, SetWindowPos, ShowWindow, GWL_EXSTYLE, HWND_TOPMOST, SWP_NOACTIVATE,
        SWP_NOMOVE, SWP_NOSIZE, SW_SHOWNOACTIVATE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
    };
    if let Ok(hwnd) = w.hwnd() {
        let h = hwnd.0 as _;
        unsafe {
            let ex = GetWindowLongPtrW(h, GWL_EXSTYLE);
            SetWindowLongPtrW(h, GWL_EXSTYLE, ex | (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) as isize);
            ShowWindow(h, SW_SHOWNOACTIVATE);
            SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        }
    }
}

#[cfg(not(windows))]
fn show_without_activation(w: &tauri::WebviewWindow) {
    let _ = w.show();
}

// ------------------------------------------------------------------ commands

#[tauri::command]
fn runtime_info(app: AppHandle, state: tauri::State<'_, ShellState>) -> runtime::RuntimeInfo {
    state.runtime.info(&app)
}

#[tauri::command]
fn restart_runtime(app: AppHandle, state: tauri::State<'_, ShellState>) {
    let rt = state.runtime.clone();
    std::thread::spawn(move || rt.restart(&app));
}

#[tauri::command]
fn set_tray_state(app: AppHandle, state: String, tooltip: String) -> Result<(), String> {
    let tray = app.tray_by_id("main").ok_or("no tray")?;
    let img = Image::from_bytes(tray_bytes(&state)).map_err(|e| e.to_string())?;
    tray.set_icon(Some(img)).map_err(|e| e.to_string())?;
    tray.set_tooltip(Some(tooltip)).map_err(|e| e.to_string())
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct IndicatorPayload {
    active: bool,
    text: String,
    kill_key: String,
}

#[tauri::command]
fn set_indicator(app: AppHandle, active: bool, text: String, kill_key: String) -> Result<(), String> {
    let w = app.get_webview_window("indicator").ok_or("no indicator window")?;
    let _ = app.emit_to("indicator", "indicator", IndicatorPayload { active, text, kill_key });
    if active {
        if let Ok(Some(m)) = app.primary_monitor() {
            let size = w.outer_size().unwrap_or(PhysicalSize::new(760, 52));
            let x = m.position().x + (m.size().width as i32 - size.width as i32) / 2;
            let _ = w.set_position(PhysicalPosition::new(x, m.position().y + 8));
        }
        show_without_activation(&w); // never take focus from the window SCAR is typing into
    } else {
        let _ = w.hide();
    }
    Ok(())
}

#[tauri::command]
fn show_main(app: AppHandle, route: Option<String>) {
    show_main_window(&app);
    if let Some(r) = route {
        let _ = app.emit_to("main", "navigate", r);
    }
}

#[tauri::command]
fn hide_quickbar(app: AppHandle) {
    if let Some(w) = app.get_webview_window("quickbar") {
        let _ = w.hide();
    }
}

#[tauri::command]
fn resize_quickbar(app: AppHandle, height: f64) {
    if let Some(w) = app.get_webview_window("quickbar") {
        let h = height.clamp(72.0, 640.0);
        let _ = w.set_size(tauri::LogicalSize::new(680.0, h));
    }
}

#[tauri::command]
fn main_visible(app: AppHandle) -> bool {
    app.get_webview_window("main").map(|w| w.is_visible().unwrap_or(false) && !w.is_minimized().unwrap_or(false)).unwrap_or(false)
}

#[tauri::command]
fn register_quickbar_shortcut(app: AppHandle, state: tauri::State<'_, ShellState>, accelerator: String) -> Result<(), String> {
    let gs = app.global_shortcut();
    let mut current = state.quickbar_key.lock().unwrap();
    if let Some(old) = current.as_ref() {
        if old.eq_ignore_ascii_case(&accelerator) && gs.is_registered(old.as_str()) {
            return Ok(());
        }
        let _ = gs.unregister(old.as_str());
    }
    gs.on_shortcut(accelerator.as_str(), |app, _shortcut, event| {
        if event.state == ShortcutState::Pressed {
            show_quickbar(app);
        }
    })
    .map_err(|e| e.to_string())?;
    *current = Some(accelerator);
    Ok(())
}

#[tauri::command]
fn quit_app(app: AppHandle, state: tauri::State<'_, ShellState>) {
    state.runtime.shutdown_for_quit();
    app.exit(0);
}

// ------------------------------------------------------------------ tray

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open SCAR", true, None::<&str>)?;
    let quick = MenuItem::with_id(app, "quickbar", "Quick Bar", true, None::<&str>)?;
    let mute = MenuItem::with_id(app, "mute", "Pause listening", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Stop All", true, None::<&str>)?;
    let status = MenuItem::with_id(app, "status", "Status", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit SCAR", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &quick, &sep1, &mute, &stop, &status, &sep2, &quit])?;
    TrayIconBuilder::with_id("main")
        .icon(Image::from_bytes(tray_bytes("idle"))?)
        .tooltip("SCAR")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main_window(app),
            "quickbar" => show_quickbar(app),
            "mute" => {
                let _ = runtime::post("/api/v1/voice", r#"{"action":"mute"}"#);
            }
            "stop" => {
                let _ = runtime::post("/api/v1/stop-all", "{}");
            }
            "status" => {
                show_main_window(app);
                let _ = app.emit_to("main", "open-status", ());
            }
            "quit" => {
                if let Some(st) = app.try_state::<ShellState>() {
                    st.runtime.shutdown_for_quit();
                }
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                let app = tray.app_handle();
                match app.get_webview_window("main") {
                    Some(w) if w.is_visible().unwrap_or(false) && !w.is_minimized().unwrap_or(false) => {
                        let _ = w.hide();
                    }
                    _ => show_main_window(app),
                }
            }
        })
        .build(app)?;
    Ok(())
}

fn tray_hint_once(app: &AppHandle) {
    let flag = paths::data_dir().join("app-tray-hint-shown");
    if flag.exists() {
        return;
    }
    let _ = std::fs::create_dir_all(paths::data_dir());
    let _ = std::fs::write(&flag, b"1");
    let _ = app
        .notification()
        .builder()
        .title("SCAR is still running")
        .body("It's in the system tray, so reminders and monitors keep working. Use Quit SCAR in the tray menu to close it.")
        .show();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let start_hidden = std::env::args().any(|a| a == "--minimized") || paths::config_bool("start_minimized");
    tauri::Builder::default()
        .plugin(navguard::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main_window(app)))
        .plugin(tauri_plugin_window_state::Builder::default().with_denylist(&["quickbar", "indicator"]).build())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec!["--minimized"])))
        .manage(ShellState { runtime: runtime::Manager::new(), quickbar_key: Mutex::new(None) })
        .invoke_handler(tauri::generate_handler![
            runtime_info,
            restart_runtime,
            set_tray_state,
            set_indicator,
            show_main,
            hide_quickbar,
            resize_quickbar,
            main_visible,
            register_quickbar_shortcut,
            quit_app
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            build_tray(&handle)?;
            // start (or attach to) the runtime right away; the window shows its state meanwhile
            let rt = app.state::<ShellState>().runtime.clone();
            let h2 = handle.clone();
            std::thread::spawn(move || {
                let _ = rt.info(&h2);
            });
            // the Quick Bar shortcut from settings (the Settings screen re-registers on change)
            let key = paths::config_str("quickbar_hotkey").unwrap_or_else(|| "Ctrl+Alt+Space".into());
            let st = app.state::<ShellState>();
            if let Err(e) = register_quickbar_shortcut(handle.clone(), st, key.clone()) {
                let _ = handle
                    .notification()
                    .builder()
                    .title("Quick Bar shortcut unavailable")
                    .body(format!("Windows wouldn't give SCAR {key} (another app may be using it). Choose another in Settings → App. ({e})"))
                    .show();
            }
            if let Some(main) = app.get_webview_window("main") {
                if !start_hidden {
                    let _ = main.show();
                }
                let h3 = handle.clone();
                main.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        // closing hides to the tray; Quit is in the tray menu
                        api.prevent_close();
                        if let Some(w) = h3.get_webview_window("main") {
                            let _ = w.hide();
                        }
                        tray_hint_once(&h3);
                    }
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running SCAR");
}
