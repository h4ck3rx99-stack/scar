const { app, BrowserWindow, screen, ipcMain } = require('electron');
const path = require('path');
const http = require('http');

// Suppress cache locking collisions when restarting or running multiple instances
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('disable-http-cache');

// Ensure single instance lock so two copies cannot run simultaneously and collide
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.exit(0);
  process.exit(0);
}

let mainWindow = null;

function forceQuit() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.destroy(); } catch (e) {}
  }
  app.exit(0);
  process.exit(0);
}

function createWindow() {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;

  mainWindow = new BrowserWindow({
    width: Math.round(width),
    height: Math.round(height),
    x: 0,
    y: 0,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    hasShadow: false,
    resizable: false,
    skipTaskbar: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      backgroundThrottling: false
    }
  });

  // Enable initial mouse pass-through for empty areas
  // { forward: true } forwards mouse movement to Chromium so hover detection works!
  mainWindow.setIgnoreMouseEvents(true, { forward: true });

  // Mouse ignore toggle (handles pass-through for transparent screen space)
  ipcMain.on('set-ignore-mouse-events', (event, ignore, options) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    try {
      mainWindow.setIgnoreMouseEvents(Boolean(ignore), options || { forward: true });
    } catch (err) {
      // Safe fallback if window is transitioning
    }
  });

  // Hard quit when user closes the app (permanent shutdown, no zombie speech)
  ipcMain.on('close-app', () => {
    forceQuit();
  });

  // Load URL (Vite dev server or built file)
  const devUrl = 'http://localhost:5173/';
  const checkReq = http.get(devUrl, () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(devUrl);
    }
  });

  checkReq.on('error', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    forceQuit();
  });
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  forceQuit();
});

app.on('before-quit', () => {
  forceQuit();
});
