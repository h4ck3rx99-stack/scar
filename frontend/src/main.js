import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import gsap from 'gsap';

import { HoverEngine } from './hoverEngine.js';
import { RoamEngine } from './roamEngine.js';
import { AnimationController } from './animationController.js';
import { SpeechBubble } from './speechBubble.js';
import { WellnessEngine } from './wellnessEngine.js';
import { MischiefEngine } from './mischiefEngine.js';
import { soundEngine } from './soundEngine.js';
import { ScarFace } from './scarFace.js';
import { DragController } from './dragController.js';
import { ScreenSpace } from './screenSpace.js';
import { StatusRing } from './statusRing.js';
import { AssistantController } from './assistantController.js';
import { bridge, ipcRenderer } from './bridge.js';

const state = {
  currentMode: 'dark',
  themeOverride: null,   // set by voice ("light mode") until the OS theme changes
  sizeMultiplier: 1.0,   // user preference on top of the automatic size
  baseScale: 0.5,
  roamSpeed: 0.35,
  facingOffset: -Math.PI * 0.35, // 3/4 view showing the visor and jetpack
  isCollapsed: true,
};

if (!navigator.userAgent.toLowerCase().includes('electron')) document.body.classList.add('in-browser');

// 1. Renderer ---------------------------------------------------------------
const container = document.getElementById('canvas-container');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0, 0, 6.2);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
renderer.setSize(window.innerWidth, window.innerHeight);
// A 15 cm robot doesn't need 2x pixels on a full-screen transparent overlay.
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
renderer.setClearColor(0x000000, 0);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

// Metal needs something to reflect: a soft studio environment, baked once.
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.9;

const keyLight = new THREE.DirectionalLight(0xddeeff, 1.6);
keyLight.position.set(5, 8, 6);
const rimLight = new THREE.DirectionalLight(0x77aacc, 1.2);
rimLight.position.set(0, 5, -8);
scene.add(new THREE.AmbientLight(0xffffff, 0.25), keyLight, rimLight);

const space = new ScreenSpace(camera);

// 2. Avatar ------------------------------------------------------------------
const avatarRoot = new THREE.Group();
avatarRoot.name = 'AvatarRoot';
scene.add(avatarRoot);
const modelContainer = new THREE.Group();
avatarRoot.add(modelContainer);

const hoverEngine = new HoverEngine(avatarRoot);
const roamEngine = new RoamEngine(avatarRoot, { speed: state.roamSpeed });
const animController = new AnimationController(avatarRoot, hoverEngine, roamEngine);
const speechBubble = new SpeechBubble(camera, avatarRoot);
const wellnessEngine = new WellnessEngine(speechBubble, { intervalMinutes: 12 });
const mischiefEngine = new MischiefEngine(avatarRoot, camera, hoverEngine, speechBubble, roamEngine);
const eyeVisor = new ScarFace(); // Scar's animated face on the visor
const dragController = new DragController(avatarRoot, eyeVisor, speechBubble, roamEngine, ipcRenderer);
const statusRing = new StatusRing();

const MODEL_HEIGHT = 2.0; // normalizeMesh scales the model's largest dimension (its height) to 2 units

/** Robot height in client px: ~14% of the screen height, kept between 150 and 240 px, times the user's size. */
function targetHeightPx() {
  return THREE.MathUtils.clamp(window.innerHeight * 0.14, 150, 240) * state.sizeMultiplier;
}
function avatarHeightPx() {
  return MODEL_HEIGHT * state.baseScale * space.pxPerWorld(avatarRoot.position.z);
}
function updateScale() {
  if (!window.innerHeight) return;
  state.baseScale = targetHeightPx() / (MODEL_HEIGHT * space.pxPerWorld(0));
  modelContainer.scale.setScalar(state.baseScale);
  speechBubble.headOffset = state.baseScale * 1.08;
  updateRoamBounds();
}
function updateRoamBounds() {
  const { w, h } = space.visibleSize(0);
  const mx = 0.9 * state.baseScale * 1.2;
  const my = 1.0 * state.baseScale * 1.2;
  roamEngine.updateBounds({ minX: -(w / 2 - mx), maxX: w / 2 - mx, minY: -(h / 2 - my), maxY: h / 2 - my, minZ: -0.8, maxZ: 0.8 });
}

const gltfLoader = new GLTFLoader();
gltfLoader.setMeshoptDecoder(MeshoptDecoder);
const loadedModels = { dark: null, light: null };

function normalizeMesh(object) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  object.position.sub(center);
  const s = MODEL_HEIGHT / Math.max(size.x, size.y, size.z);
  object.position.multiplyScalar(s);
  object.scale.setScalar(s);
  const wrapper = new THREE.Group();
  wrapper.add(object);
  wrapper.rotation.y = state.facingOffset;
  return wrapper;
}

async function loadModel(mode) {
  if (loadedModels[mode]) return loadedModels[mode];
  const gltf = await gltfLoader.loadAsync(`./models/${mode}/avatar.glb`);
  loadedModels[mode] = normalizeMesh(gltf.scene);
  return loadedModels[mode];
}

function disposeModel(mode) {
  const m = loadedModels[mode];
  if (!m) return;
  m.traverse((c) => {
    if (c.isMesh) {
      c.geometry.dispose();
      for (const key of ['map', 'normalMap', 'metalnessMap', 'roughnessMap']) c.material[key]?.dispose();
      c.material.dispose();
    }
  });
  loadedModels[mode] = null;
}

async function setModelMode(mode) {
  if (mode === state.currentMode && modelContainer.children.length) return;
  const previous = state.currentMode;
  state.currentMode = mode;
  const model = await loadModel(mode);
  modelContainer.clear();
  modelContainer.add(model);
  hoverEngine.attachTo(model);
  eyeVisor.attachTo(model);
  if (!eyeVisor.fitToModel(model, avatarRoot)) console.warn('could not locate the visor; face hidden');
  rimLight.color.set(mode === 'dark' ? 0x77aacc : 0xccddee);
  document.body.classList.toggle('theme-dark', mode === 'dark');
  document.body.classList.toggle('theme-light', mode === 'light');
  // Only the active theme stays in VRAM.
  if (previous !== mode) disposeModel(previous);
  updateScale();
}

const controller = new AssistantController({
  scene, camera, space, avatarRoot, roamEngine, animController, mischiefEngine, speechBubble, eyeVisor,
  statusRing, bridge, avatarHeightPx,
  setTheme: (mode) => { state.themeOverride = mode; setModelMode(mode); },
});
controller.bind();

function showToast(text) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add('visible');
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => toast.classList.remove('visible'), 2400);
}

function saveSettings(patch) {
  if (ipcRenderer) ipcRenderer.invoke('save-settings', patch);
}

// 3. Settings dock + context menu ------------------------------------------
function setupUI() {
  const asideDock = document.getElementById('aside-column');
  document.getElementById('aside-toggle-btn').onclick = () => {
    state.isCollapsed = !state.isCollapsed;
    asideDock.classList.toggle('collapsed', state.isCollapsed);
  };

  const scaleSlider = document.getElementById('scale-slider');
  const scaleVal = document.getElementById('scale-val');
  scaleSlider.value = state.sizeMultiplier;
  scaleVal.textContent = `${state.sizeMultiplier.toFixed(2)}x`;
  scaleSlider.oninput = (e) => {
    state.sizeMultiplier = parseFloat(e.target.value);
    scaleVal.textContent = `${state.sizeMultiplier.toFixed(2)}x`;
    updateScale();
  };
  scaleSlider.onchange = () => saveSettings({ sizeMultiplier: state.sizeMultiplier });

  const audioBtn = document.getElementById('audio-toggle-btn');
  audioBtn.onclick = () => {
    soundEngine.muted = !soundEngine.muted;
    audioBtn.textContent = soundEngine.muted ? '🔇 Effects: OFF' : '🔊 Effects: ON';
  };
  document.getElementById('btn-trigger-wellness').onclick = () => wellnessEngine.triggerRandomReminder();
  const roamBtn = document.getElementById('roam-toggle-btn');
  roamBtn.onclick = () => {
    const on = !roamEngine.enabled;
    roamEngine.setEnabled(on);
    mischiefEngine.enabled = on;
    roamBtn.textContent = on ? '🌐 Roam: ON' : '🛑 Roam: OFF';
    roamBtn.classList.toggle('primary', on);
  };
  document.getElementById('theme-toggle-btn').onclick = () => {
    const next = state.currentMode === 'dark' ? 'light' : 'dark';
    state.themeOverride = next;
    setModelMode(next);
  };
  setupContextMenu();
}

function setupContextMenu() {
  const menu = document.getElementById('character-context-menu');
  const hide = () => {
    menu.classList.remove('visible');
    setPassthrough(true);
  };
  document.getElementById('menu-close-btn').onclick = hide;

  document.querySelectorAll('.mood-btn').forEach((btn) => {
    btn.onclick = () => {
      const mood = btn.getAttribute('data-mood');
      eyeVisor.setExpression(mood);
      ({ happy: soundEngine.playHappy, scared: soundEngine.playScared, angry: soundEngine.playAngry,
        sad: soundEngine.playSad, curious: soundEngine.playCurious }[mood] || soundEngine.playBoop).call(soundEngine);
      hide();
    };
  });

  const input = document.getElementById('ctx-command');
  input.onkeydown = (e) => {
    if (e.key === 'Enter' && input.value.trim()) {
      if (!bridge.send('text_command', { text: input.value.trim() })) showToast('Brain offline');
      input.value = '';
      hide();
    } else if (e.key === 'Escape') hide();
  };
  document.getElementById('ctx-sleep-btn').onclick = () => { controller.goSleep(); hide(); };
  document.getElementById('ctx-rest-btn').onclick = () => { wellnessEngine.triggerRandomReminder(); hide(); };
  document.getElementById('ctx-quit-btn').onclick = () => {
    speechBubble.stopAllSpeech();
    if (ipcRenderer) ipcRenderer.send('close-app'); else window.close();
  };
  window.addEventListener('click', (e) => { if (!e.target.closest('#character-context-menu')) hide(); });
}

// 4. Mouse: click-through except over the robot / UI -------------------------
let isMouseIgnored = true;
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let pointerDownTime = 0;
let pointerDownPos = { x: 0, y: 0 };
let isMouseDownOnRobot = false;

function setPassthrough(ignore) {
  if (!ipcRenderer || ignore === isMouseIgnored) return;
  ipcRenderer.send('set-ignore-mouse-events', ignore, { forward: true });
  isMouseIgnored = ignore;
}

function isOverRobot(clientX, clientY) {
  if (!modelContainer.children.length) return false;
  mouse.set((clientX / window.innerWidth) * 2 - 1, -(clientY / window.innerHeight) * 2 + 1);
  raycaster.setFromCamera(mouse, camera);
  if (raycaster.intersectObjects(modelContainer.children, true).length) return true;
  const c = space.worldToClient(avatarRoot.getWorldPosition(new THREE.Vector3()));
  return Math.hypot(clientX - c.x, clientY - c.y) < avatarHeightPx() * 0.42;
}

function checkPointerPassthrough(e) {
  if (dragController.isDragging || isMouseDownOnRobot) return setPassthrough(false);
  const overUI = e.target?.closest?.('#aside-column, #character-context-menu, #toast');
  const menuOpen = document.getElementById('character-context-menu').classList.contains('visible');
  setPassthrough(!(overUI || menuOpen || isOverRobot(e.clientX, e.clientY)));
}

window.addEventListener('pointerdown', (e) => {
  if (e.button === 2 || e.target.closest('#aside-column, #character-context-menu')) return;
  pointerDownTime = performance.now();
  pointerDownPos = { x: e.clientX, y: e.clientY };
  if (isOverRobot(e.clientX, e.clientY) && !controller.busy) {
    isMouseDownOnRobot = true;
    setPassthrough(false);
  }
});

window.addEventListener('pointermove', (e) => {
  eyeVisor.setGaze((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
  if (isMouseDownOnRobot) {
    if (Math.hypot(e.clientX - pointerDownPos.x, e.clientY - pointerDownPos.y) > 6 && !dragController.isDragging) {
      dragController.startDrag(e.screenX, e.screenY, e.clientX, e.clientY, camera);
      bridge.send('user_activity');
    }
    if (dragController.isDragging) dragController.updateDrag(e.screenX, e.screenY, e.clientX, e.clientY, camera);
  }
  checkPointerPassthrough(e);
});

window.addEventListener('pointerup', (e) => {
  if (dragController.isDragging) {
    dragController.endDrag();
    isMouseDownOnRobot = false;
    controller.noteActivity();
    checkPointerPassthrough(e);
    return;
  }
  const wasOnRobot = isMouseDownOnRobot;
  isMouseDownOnRobot = false;
  if (e.target.closest('#aside-column, #character-context-menu')) return;
  const quick = performance.now() - pointerDownTime < 350 && Math.hypot(e.clientX - pointerDownPos.x, e.clientY - pointerDownPos.y) < 8;
  if (wasOnRobot && quick) {
    // A tap on the robot = "I want to talk to you" (same as the wake word).
    controller.noteActivity();
    if (bridge.send('avatar_clicked')) {
      eyeVisor.setExpression('curious');
      soundEngine.playBoop();
      gsap.fromTo(avatarRoot.scale, { x: 0.92, y: 1.08, z: 0.92 }, { x: 1, y: 1, z: 1, duration: 0.5, ease: 'elastic.out(1, 0.4)' });
    } else {
      eyeVisor.setExpression('sad');
      speechBubble.show("My brain is offline. Is the backend running?", { chime: false, duration: 3500 });
    }
  }
  checkPointerPassthrough(e);
});

window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (!isOverRobot(e.clientX, e.clientY)) return;
  const menu = document.getElementById('character-context-menu');
  menu.style.left = `${Math.min(window.innerWidth - 250, Math.max(10, e.clientX))}px`;
  menu.style.top = `${Math.min(window.innerHeight - 380, Math.max(10, e.clientY))}px`;
  menu.classList.add('visible');
  soundEngine.playBoop();
  setPassthrough(false);
  if (ipcRenderer) ipcRenderer.send('focus-overlay');
  setTimeout(() => document.getElementById('ctx-command').focus(), 50);
});

let viewW = 0;
let viewH = 0;
/** Keeps camera/renderer/scale in sync with the window. Returns false while the window has no size yet. */
function syncViewport() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  if (!w || !h) return false;
  if (w !== viewW || h !== viewH) {
    viewW = w;
    viewH = h;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    updateScale();
  }
  return true;
}
window.addEventListener('resize', syncViewport);

// 5. Render loop: full rate only when something is happening -----------------
let lastTick = performance.now();
let lastRender = 0;
const startTime = performance.now();
const _tmp = new THREE.Vector3();

function animate(now, manual = false) {
  if (!manual) requestAnimationFrame(animate);
  const frameInterval = 1000 / controller.frameRate;
  if (now - lastRender < frameInterval - 1) return;
  lastRender = now;
  if (!syncViewport()) return;
  const delta = Math.min((now - lastTick) / 1000, 0.1);
  lastTick = now;
  const time = (now - startTime) / 1000;

  if (!controller.busy && !animController.isAnimating && !mischiefEngine.isBusy && !dragController.isDragging) {
    roamEngine.update(delta, time);
  }
  controller.update(delta, time);
  eyeVisor.update(delta);
  speechBubble.updatePosition();
  const c = space.worldToClient(avatarRoot.getWorldPosition(_tmp));
  statusRing.place(c.x, c.y, avatarHeightPx());
  renderer.render(scene, camera);
}

// 6. Boot ---------------------------------------------------------------------
async function boot() {
  const cfg = await bridge.loadConfig();
  if (cfg.display) space.setScale(cfg.display.scaleFactor);
  state.sizeMultiplier = cfg.settings?.sizeMultiplier || 1.0;
  setupUI();
  await setModelMode(cfg.osTheme || 'dark');
  // Standby: no wellness pop-ups or pranks until Scar is called again.
  bridge.on('voice_state', (m) => {
    const standby = m.status === 'standby';
    wellnessEngine.setEnabled(!standby);
    mischiefEngine.enabled = !standby && roamEngine.enabled;
  });
  bridge.on('alert', (m) => { if (controller.mode === 'standby') showToast(m.text); });
  if (ipcRenderer) {
    ipcRenderer.on('os-theme', (_e, mode) => { state.themeOverride = null; setModelMode(mode); });
  }
  avatarRoot.position.set(0, 0, -20); // off-stage until the entry animation
  bridge.connect();
  // If the brain is slow to start (models loading), make an entrance anyway.
  setTimeout(() => { if (controller.mode === 'entry' && !animController.isAnimating) controller.playRandomEntry(); }, 1500);
  requestAnimationFrame(animate);
}

boot();

// Test hooks (browser dev only): lets the harness drive and inspect the scene.
if (!ipcRenderer) {
  // step(ms): advance animations and render without requestAnimationFrame (hidden test browsers pause rAF).
  const step = async (ms = 1000, fps = 30) => {
    for (let t = 0; t < ms; t += 1000 / fps) {
      await new Promise((r) => setTimeout(r, 1000 / fps));
      gsap.ticker.tick();
      lastRender = 0;
      animate(performance.now(), true);
    }
  };
  window.__jarvis = { controller, avatarRoot, space, state, bridge, camera, renderer, setModelMode, step, statusRing, updateScale };
}
