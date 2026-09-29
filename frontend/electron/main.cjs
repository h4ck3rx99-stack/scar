// Jarvis body: a transparent, click-through, always-on-top overlay covering the primary display.
// Also starts the Python brain (backend/) if it isn't already running, and stops it on quit.
const { app, BrowserWindow, screen, ipcMain, nativeTheme, Tray, Menu, nativeImage, globalShortcut } = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { spawn, execFile } = require('child_process');

app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('disable-http-cache');
// Keep rendering smooth when other windows are focused (the overlay is never "focused").
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-background-timer-throttling');

if (!app.requestSingleInstanceLock()) {
  app.exit(0);
}

const ROOT = path.resolve(__dirname, '..', '..');
const BACKEND_DIR = path.join(ROOT, 'backend');
const WS_PORT = 8765;
const TOKEN_FILE = path.join(process.env.APPDATA || '', 'jarvis', 'token');
const SETTINGS_FILE = path.join(app.getPath('userData'), 'settings.json');

let mainWindow = null;
let currentDisplay = null;   // the display the overlay currently covers
let tray = null;
let backendProc = null;
let quitting = false;

function readSettings() {
  try { return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')); } catch { return {}; }
}
function writeSettings(patch) {
  const next = { ...readSettings(), ...patch };
  fs.mkdirSync(path.dirname(SETTINGS_FILE), { recursive: true });
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(next, null, 2));
  return next;
}

function portOpen(port) {
  return new Promise((resolve) => {
    const s = net.connect({ port, host: '127.0.0.1' }, () => { s.destroy(); resolve(true); });
    s.on('error', () => resolve(false));
    s.setTimeout(400, () => { s.destroy(); resolve(false); });
  });
}

async function startBackend() {
  if (await portOpen(WS_PORT)) return; // already running (e.g. started manually for debugging)
  const py = path.join(BACKEND_DIR, '.venv', 'Scripts', 'pythonw.exe');
  if (!fs.existsSync(py)) {
    console.error('backend venv missing:', py);
    return;
  }
  backendProc = spawn(py, ['-m', 'jarvis'], { cwd: BACKEND_DIR, windowsHide: true, stdio: 'ignore' });
  backendProc.on('exit', (code) => {
    backendProc = null;
    if (!quitting) console.error('backend exited with', code);
  });
}

function stopBackend() {
  if (!backendProc) return;
  // Kill only the tree we started (pythonw + its llama-server child), never other apps.
  try { execFile('taskkill', ['/PID', String(backendProc.pid), '/T', '/F']); } catch {}
  backendProc = null;
}

function readToken() {
  try { return fs.readFileSync(TOKEN_FILE, 'utf8').trim(); } catch { return null; }
}

function createWindow() {
  const display = screen.getPrimaryDisplay();
  const { x, y, width, height } = display.bounds;

  mainWindow = new BrowserWindow({
    x, y, width, height,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    hasShadow: false,
    resizable: false,
    movable: false,
    focusable: true,
    skipTaskbar: true,
    backgroundColor: '#00000000',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      backgroundThrottling: false,
    },
  });
  // 'screen-saver' level keeps the avatar above the taskbar and most fullscreen-ish windows.
  mainWindow.setAlwaysOnTop(true, 'screen-saver');
  mainWindow.setVisibleOnAllWorkspaces(true);
  // Click-through by default; `forward: true` still delivers mouse moves so hover over the robot works.
  mainWindow.setIgnoreMouseEvents(true, { forward: true });

  ipcMain.on('set-ignore-mouse-events', (_e, ignore, options) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    try { mainWindow.setIgnoreMouseEvents(Boolean(ignore), options || { forward: true }); } catch {}
  });
  // Windows silently drops the topmost flag when other apps open or grab focus, which let
  // new windows (e.g. Paint) cover the robot. Re-assert it on blur, on request, and every second.
  const keepOnTop = () => {
    if (!mainWindow || mainWindow.isDestroyed() || !mainWindow.isVisible()) return;
    mainWindow.setAlwaysOnTop(true, 'screen-saver');
    mainWindow.moveTop();
  };
  mainWindow.on('blur', keepOnTop);
  setInterval(keepOnTop, 1000);
  ipcMain.on('raise-overlay', keepOnTop);
  ipcMain.on('focus-overlay', () => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.focus(); });
  // Push-to-talk from anywhere: interrupts Scar if it's talking and starts listening.
  globalShortcut.register('Control+Shift+Space', () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('hotkey-talk');
  });
  ipcMain.on('close-app', () => quit());

  // ---- multi-monitor: the overlay lives on one display at a time and hops to where it's needed.
  currentDisplay = display;
  let followCursor = true;
  const displayInfo = (d) => ({
    id: d.id, bounds: d.bounds, scaleFactor: d.scaleFactor,
    // Physical-pixel origin of this display (what the Python brain's coordinates are relative to).
    physOrigin: screen.dipToScreenPoint ? screen.dipToScreenPoint({ x: d.bounds.x, y: d.bounds.y })
                                        : { x: d.bounds.x * d.scaleFactor, y: d.bounds.y * d.scaleFactor },
  });
  const moveToDisplay = (d, reason) => {
    if (!mainWindow || mainWindow.isDestroyed() || d.id === currentDisplay.id) return false;
    currentDisplay = d;
    mainWindow.setBounds(d.bounds);
    mainWindow.webContents.send('display-changed', { ...displayInfo(d), reason });
    return true;
  };
  ipcMain.handle('move-to-point', (_e, phys) => {
    const dip = screen.screenToDipPoint ? screen.screenToDipPoint(phys) : phys;
    const d = screen.getDisplayNearestPoint({ x: Math.round(dip.x), y: Math.round(dip.y) });
    moveToDisplay(d, 'target');
    return displayInfo(currentDisplay);
  });
  ipcMain.on('follow-cursor', (_e, on) => { followCursor = Boolean(on); });
  setInterval(() => {
    if (!followCursor || screen.getAllDisplays().length < 2) return;
    moveToDisplay(screen.getDisplayNearestPoint(screen.getCursorScreenPoint()), 'cursor');
  }, 1500);

  ipcMain.handle('get-config', () => ({
    token: readToken(),
    wsUrl: `ws://127.0.0.1:${WS_PORT}`,
    display: displayInfo(currentDisplay),
    osTheme: nativeTheme.shouldUseDarkColors ? 'dark' : 'light',
    settings: readSettings(),
  }));
  ipcMain.handle('save-settings', (_e, patch) => writeSettings(patch));

  nativeTheme.on('updated', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('os-theme', nativeTheme.shouldUseDarkColors ? 'dark' : 'light');
    }
  });

  const devUrl = 'http://localhost:5173/';
  const probe = http.get(devUrl, () => mainWindow && mainWindow.loadURL(devUrl));
  probe.on('error', () => mainWindow && mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html')));
  probe.setTimeout(500, () => probe.destroy());

  mainWindow.on('closed', () => { mainWindow = null; quit(); });
}

function createTray() {
  // 16x16 cyan dot, generated so no icon file is needed.
  const size = 16;
  const buf = Buffer.alloc(size * size * 4);
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const d = Math.hypot(px - 7.5, py - 7.5);
      const i = (py * size + px) * 4;
      if (d < 7) { buf[i] = 255; buf[i + 1] = 220; buf[i + 2] = 60; buf[i + 3] = 255; } // BGRA
    }
  }
  tray = new Tray(nativeImage.createFromBitmap(buf, { width: size, height: size }));
  tray.setToolTip('Jarvis');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show / hide Jarvis', click: () => mainWindow && (mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show()) },
    { label: 'Reload', click: () => mainWindow && mainWindow.reload() },
    { type: 'separator' },
    { label: 'Quit Jarvis', click: () => quit() },
  ]));
}

function quit() {
  if (quitting) return;
  quitting = true;
  stopBackend();
  if (mainWindow && !mainWindow.isDestroyed()) { try { mainWindow.destroy(); } catch {} }
  app.exit(0);
}

app.whenReady().then(async () => {
  await startBackend();
  createWindow();
  createTray();
  const refit = () => {
    if (!mainWindow) return;
    // Keep covering the same display (or fall back to the primary one if it was unplugged).
    currentDisplay = screen.getAllDisplays().find((d) => currentDisplay && d.id === currentDisplay.id) || screen.getPrimaryDisplay();
    mainWindow.setBounds(currentDisplay.bounds);
  };
  screen.on('display-metrics-changed', refit);
  screen.on('display-removed', refit);
});

app.on('window-all-closed', () => quit());
app.on('before-quit', () => { quitting = true; stopBackend(); });
