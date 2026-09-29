import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';

import { HoverEngine } from './hoverEngine.js';
import { RoamEngine } from './roamEngine.js';
import { AnimationController } from './animationController.js';
import { SpeechBubble } from './speechBubble.js';
import { WellnessEngine } from './wellnessEngine.js';
import { MischiefEngine } from './mischiefEngine.js';
import { soundEngine } from './soundEngine.js';
import { EyeVisorSystem } from './eyeVisorSystem.js';
import { DragController } from './dragController.js';
import gsap from 'gsap';

// Electron IPC Integration
let ipcRenderer = null;
try {
  if (typeof window !== 'undefined' && window.require) {
    const electron = window.require('electron');
    ipcRenderer = electron.ipcRenderer;
  }
} catch (e) {}

// Application State
const state = {
  currentMode: 'dark',
  windowMode: 'fullscreen', // Full desktop overlay: travels freely across entire monitor
  baseScale: 0.5, // Natural scale for desktop roaming
  roamSpeed: 0.35, // Gentle, relaxed slow floating pacing
  isRoaming: true, // Start roaming automatically
  facingOffset: -Math.PI * 0.35, // 3/4 perspective showing visor and jetpack
  isCollapsed: true
};

// Check if running in browser or Electron
const isElectron = navigator.userAgent.toLowerCase().includes('electron');
if (!isElectron) {
  document.body.classList.add('in-browser');
}

// 1. Scene, Camera, Transparent WebGL Renderer Setup
const container = document.getElementById('canvas-container');
const scene = new THREE.Scene();

const camera = new THREE.PerspectiveCamera(
  45,
  window.innerWidth / window.innerHeight,
  0.1,
  100
);
camera.position.set(0, 0, 6.2);

const renderer = new THREE.WebGLRenderer({
  antialias: true,
  alpha: true,
  powerPreference: 'high-performance'
});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x000000, 0); // 100% transparent background
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.3;
container.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enabled = false; // Disabled so clicks drag the robot without spinning camera

// 2. Cinematic Lighting
const ambientLight = new THREE.AmbientLight(0xffffff, 0.95);
scene.add(ambientLight);

const keyLight = new THREE.DirectionalLight(0xddeeff, 2.5);
keyLight.position.set(5, 8, 6);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight(0x77aaff, 1.2);
fillLight.position.set(-6, -2, 4);
scene.add(fillLight);

const rimLight = new THREE.DirectionalLight(0x77aacc, 1.3);
rimLight.position.set(0, 5, -8);
scene.add(rimLight);

// 3. Avatar Hierarchy
const avatarRoot = new THREE.Group();
avatarRoot.name = "AvatarRoot";
scene.add(avatarRoot);

const modelContainer = new THREE.Group();
modelContainer.name = "ModelContainer";
avatarRoot.add(modelContainer);

// 4. Yellow Jetpack Hover Engine (Silent/Clean)
const hoverEngine = new HoverEngine(avatarRoot);

// 5. Gentle Roam Engine (Full Screen Wandering)
const roamEngine = new RoamEngine(avatarRoot, {
  speed: state.roamSpeed,
  bounds: { minX: -3.8, maxX: 3.8, minY: -2.0, maxY: 2.0, minZ: -1.0, maxZ: 1.0 }
});

function updateRoamBounds() {
  const aspect = window.innerWidth / window.innerHeight;
  const dist = camera.position.z;
  const vFOV = THREE.MathUtils.degToRad(camera.fov);
  const visibleH = 2 * Math.tan(vFOV / 2) * dist;
  const visibleW = visibleH * aspect;

  const marginX = 0.9 * state.baseScale;
  const marginY = 0.8 * state.baseScale;

  roamEngine.updateBounds({
    minX: -(visibleW / 2 - marginX),
    maxX: (visibleW / 2 - marginX),
    minY: -(visibleH / 2 - marginY),
    maxY: (visibleH / 2 - marginY),
    minZ: -0.8,
    maxZ: 0.8
  });
}
updateRoamBounds();

const animController = new AnimationController(avatarRoot, hoverEngine, roamEngine);

// 6. Holographic Speech Bubble, Wellness Engine & Mischief Engine
const speechBubble = new SpeechBubble(camera, avatarRoot);
const wellnessEngine = new WellnessEngine(speechBubble, { intervalMinutes: 12 });
const mischiefEngine = new MischiefEngine(avatarRoot, camera, hoverEngine, speechBubble, roamEngine);

// 7. Expressive Digital Eye Visor System (Blinking, Expressions & Eye Movements)
const eyeVisor = new EyeVisorSystem();

// 8. Desktop Mate Drag & Dangling Physics Controller
const dragController = new DragController(avatarRoot, eyeVisor, speechBubble, roamEngine, ipcRenderer);

// 9. 3D Model Loader
const gltfLoader = new GLTFLoader();
const objLoader = new OBJLoader();
const textureLoader = new THREE.TextureLoader();

const loadedModels = { dark: null, light: null };

function normalizeMesh(object) {
  const box = new THREE.Box3().setFromObject(object);
  const size = new THREE.Vector3();
  box.getSize(size);
  const center = new THREE.Vector3();
  box.getCenter(center);

  object.position.sub(center);

  const maxDim = Math.max(size.x, size.y, size.z);
  const scale = 2.0 / maxDim;
  object.scale.set(scale, scale, scale);

  object.traverse(child => {
    if (child.isMesh) {
      if (child.material) {
        child.material.roughness = 0.35;
        child.material.metalness = 0.85;
        child.material.needsUpdate = true;
      }
    }
  });

  const wrapper = new THREE.Group();
  wrapper.add(object);
  wrapper.rotation.y = state.facingOffset;
  hoverEngine.attachTo(wrapper);
  eyeVisor.attachTo(wrapper); // Attach expressive digital eyes to the front visor!

  return wrapper;
}

async function loadModel(mode) {
  if (loadedModels[mode]) return loadedModels[mode];

  return new Promise((resolve) => {
    const glbPath = `/models/${mode}/model.glb`;
    gltfLoader.load(
      glbPath,
      (gltf) => {
        const normalized = normalizeMesh(gltf.scene);
        loadedModels[mode] = normalized;
        resolve(normalized);
      },
      undefined,
      () => {
        const basePath = `/models/${mode}/`;
        const baseColorTex = textureLoader.load(`${basePath}basecolor_opt.jpg`);
        const normalTex = textureLoader.load(`${basePath}normal_opt.jpg`);

        objLoader.load(`${basePath}model_clean.obj`, (obj) => {
          const mat = new THREE.MeshStandardMaterial({
            map: baseColorTex,
            normalMap: normalTex,
            metalness: mode === 'dark' ? 0.9 : 0.6,
            roughness: 0.35
          });
          obj.traverse(c => { if (c.isMesh) c.material = mat; });
          const normalized = normalizeMesh(obj);
          loadedModels[mode] = normalized;
          resolve(normalized);
        });
      }
    );
  });
}

async function setModelMode(mode) {
  state.currentMode = mode;
  modelContainer.clear();

  const model = await loadModel(mode);
  modelContainer.add(model);

  if (mode === 'dark') {
    rimLight.color.set(0x77aacc);
  } else {
    rimLight.color.set(0xccddee);
  }

  updateScale();
}

function updateScale() {
  avatarRoot.scale.set(state.baseScale, state.baseScale, state.baseScale);
}

function showToast(text) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add('visible');
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => toast.classList.remove('visible'), 2400);
}

// 10. Bind Top-Right Aside Dock UI Elements (Only: Size, Mute, Rest, Roam Off)
function setupUI() {
  const asideDock = document.getElementById('aside-column');
  const asideTrigger = document.getElementById('aside-toggle-btn');

  // Toggle Aside Column collapse/expand
  if (asideTrigger && asideDock) {
    asideTrigger.onclick = () => {
      state.isCollapsed = !state.isCollapsed;
      asideDock.classList.toggle('collapsed', state.isCollapsed);
    };
  }

  // 1. Robot Size Slider
  const scaleSlider = document.getElementById('scale-slider');
  const scaleVal = document.getElementById('scale-val');
  if (scaleSlider) {
    scaleSlider.value = state.baseScale;
    scaleSlider.oninput = (e) => {
      state.baseScale = parseFloat(e.target.value);
      if (scaleVal) scaleVal.textContent = `${state.baseScale.toFixed(2)}x`;
      updateScale();
      updateRoamBounds();
    };
  }

  // 2. Mute Toggle Button
  const audioBtn = document.getElementById('audio-toggle-btn');
  if (audioBtn) {
    audioBtn.onclick = () => {
      soundEngine.muted = !soundEngine.muted;
      audioBtn.textContent = soundEngine.muted ? "🔇 Sound: OFF (Muted)" : "🔊 Sound: ON";
      showToast(soundEngine.muted ? "Sound Muted" : "Sound Enabled");
    };
  }

  // 3. Rest Button (Take a Rest / Drink Water)
  const restBtn = document.getElementById('btn-trigger-wellness');
  if (restBtn) {
    restBtn.onclick = () => {
      wellnessEngine.triggerRandomReminder();
    };
  }

  // 4. Roam Off / On Toggle Button
  const roamBtn = document.getElementById('roam-toggle-btn');
  if (roamBtn) {
    roamBtn.onclick = () => {
      state.isRoaming = !state.isRoaming;
      roamEngine.setEnabled(state.isRoaming);
      roamBtn.textContent = state.isRoaming
        ? "🌐 Roam: ON (Click for Roam Off)"
        : "🛑 Roam: OFF (Click for Roam On)";
      roamBtn.classList.toggle('primary', state.isRoaming);
      showToast(state.isRoaming ? "Roaming: ON" : "Roam: OFF (Resting in place)");
    };
  }

  // 5. Desktop Mate Right-Click Context Menu
  setupContextMenu();
}

function setupContextMenu() {
  const menu = document.getElementById('character-context-menu');
  const closeBtn = document.getElementById('menu-close-btn');

  if (!menu) return;

  const hideContextMenu = () => {
    menu.classList.remove('visible');
    if (ipcRenderer) {
      ipcRenderer.send('set-ignore-mouse-events', true, { forward: true });
      isMouseIgnored = true;
    }
  };

  if (closeBtn) {
    closeBtn.onclick = hideContextMenu;
  }

  // Mood buttons
  document.querySelectorAll('.mood-btn').forEach(btn => {
    btn.onclick = () => {
      const mood = btn.getAttribute('data-mood');
      eyeVisor.setExpression(mood);

      if (mood === 'happy') soundEngine.playHappy();
      else if (mood === 'scared') soundEngine.playScared();
      else if (mood === 'angry') soundEngine.playAngry();
      else if (mood === 'sad') soundEngine.playSad();
      else if (mood === 'curious') soundEngine.playCurious();
      else soundEngine.playBoop();

      showToast(`Mood: ${mood.toUpperCase()}`);
      hideContextMenu();
    };
  });

  // Roam Toggle
  const ctxRoam = document.getElementById('ctx-roam-btn');
  if (ctxRoam) {
    ctxRoam.onclick = () => {
      state.isRoaming = !state.isRoaming;
      roamEngine.setEnabled(state.isRoaming);
      ctxRoam.textContent = state.isRoaming ? "🌐 Roam: ON" : "🛑 Roam: OFF";
      showToast(state.isRoaming ? "Roam: ON" : "Roam: OFF");
      hideContextMenu();
    };
  }

  // Rest
  const ctxRest = document.getElementById('ctx-rest-btn');
  if (ctxRest) {
    ctxRest.onclick = () => {
      wellnessEngine.triggerRandomReminder();
      hideContextMenu();
    };
  }

  // Sound
  const ctxSound = document.getElementById('ctx-sound-btn');
  if (ctxSound) {
    ctxSound.onclick = () => {
      soundEngine.muted = !soundEngine.muted;
      ctxSound.textContent = soundEngine.muted ? "🔇 Sound: OFF" : "🔊 Sound: ON";
      showToast(soundEngine.muted ? "Muted" : "Sound Enabled");
      hideContextMenu();
    };
  }

  // Quit
  const ctxQuit = document.getElementById('ctx-quit-btn');
  if (ctxQuit) {
    ctxQuit.onclick = () => {
      speechBubble.stopAllSpeech();
      if (ipcRenderer) {
        ipcRenderer.send('close-app');
      } else {
        window.close();
      }
    };
  }

  window.addEventListener('beforeunload', () => {
    speechBubble.stopAllSpeech();
  });

  // Close context menu on outside click
  window.addEventListener('click', (e) => {
    if (!e.target.closest('#character-context-menu')) {
      hideContextMenu();
    }
  });
}

// 11. Mouse Pass-Through Management (Zero Desktop Interference)
let isMouseIgnored = true;
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let pointerDownTime = 0;
let pointerDownPos = { x: 0, y: 0 };
let isMouseDownOnRobot = false;

function checkPointerPassthrough(e) {
  if (!ipcRenderer) return;

  // While dragging or mouse button held down on robot, ALWAYS capture mouse!
  if (dragController.isDragging || isMouseDownOnRobot) {
    if (isMouseIgnored) {
      ipcRenderer.send('set-ignore-mouse-events', false);
      isMouseIgnored = false;
    }
    return;
  }

  // Check if hovering over aside column or context menu or toast
  const isOverUI = !!(
    (e.target && e.target.closest && (
      e.target.closest('#aside-column') ||
      e.target.closest('#character-context-menu') ||
      e.target.closest('#toast')
    ))
  );

  // Check if context menu is currently visible
  const menu = document.getElementById('character-context-menu');
  const isMenuOpen = menu && menu.classList.contains('visible');

  // Check if hovering over 3D robot
  let isOverRobot = false;
  if (!isOverUI && !isMenuOpen && modelContainer && modelContainer.children.length > 0) {
    mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const wrapper = modelContainer.children[0];
    const hits = raycaster.intersectObjects(wrapper.children, true);

    const worldPos = new THREE.Vector3();
    avatarRoot.getWorldPosition(worldPos);
    const screenPos = worldPos.project(camera);
    const sx = (screenPos.x * 0.5 + 0.5) * window.innerWidth;
    const sy = (-screenPos.y * 0.5 + 0.5) * window.innerHeight;
    const distPx = Math.hypot(e.clientX - sx, e.clientY - sy);
    const hoverRadius = Math.max(90, 180 * state.baseScale);

    isOverRobot = (hits.length > 0) || (distPx < hoverRadius);
  }

  const shouldCapture = isOverUI || isMenuOpen || isOverRobot;

  if (shouldCapture && isMouseIgnored) {
    ipcRenderer.send('set-ignore-mouse-events', false);
    isMouseIgnored = false;
  } else if (!shouldCapture && !isMouseIgnored) {
    ipcRenderer.send('set-ignore-mouse-events', true, { forward: true });
    isMouseIgnored = true;
  }
}

window.addEventListener('pointerdown', (e) => {
  if (e.button === 2) return; // Right-click handled by contextmenu
  if (e.target.closest('#aside-column') || e.target.closest('#character-context-menu')) return;

  pointerDownTime = performance.now();
  pointerDownPos = { x: e.clientX, y: e.clientY };

  mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);

  let isHit = false;
  if (modelContainer.children.length > 0) {
    const wrapper = modelContainer.children[0];
    const intersects = raycaster.intersectObjects(wrapper.children, true);
    isHit = intersects.length > 0;
  }

  const worldPos = new THREE.Vector3();
  avatarRoot.getWorldPosition(worldPos);
  const screenPos = worldPos.project(camera);
  const sx = (screenPos.x * 0.5 + 0.5) * window.innerWidth;
  const sy = (-screenPos.y * 0.5 + 0.5) * window.innerHeight;
  const distPx = Math.hypot(e.clientX - sx, e.clientY - sy);
  const hitRadius = Math.max(80, 160 * state.baseScale);

  if (isHit || distPx < hitRadius) {
    isMouseDownOnRobot = true;
    if (ipcRenderer && isMouseIgnored) {
      ipcRenderer.send('set-ignore-mouse-events', false);
      isMouseIgnored = false;
    }
  }
});

window.addEventListener('pointermove', (e) => {
  // Gaze Tracking: Eyes look at cursor!
  const normX = (e.clientX / window.innerWidth) * 2 - 1;
  const normY = -(e.clientY / window.innerHeight) * 2 + 1;
  eyeVisor.setGaze(normX, normY);

  // If holding mouse on robot and moving -> Enter Hand-Holding Dangling Drag!
  if (isMouseDownOnRobot) {
    const dist = Math.hypot(e.clientX - pointerDownPos.x, e.clientY - pointerDownPos.y);
    if (dist > 6 && !dragController.isDragging) {
      dragController.startDrag(e.screenX, e.screenY, e.clientX, e.clientY, camera);
    }
    if (dragController.isDragging) {
      dragController.updateDrag(e.screenX, e.screenY, e.clientX, e.clientY, camera);
    }
  }

  checkPointerPassthrough(e);
});

window.addEventListener('pointerup', (e) => {
  if (dragController.isDragging) {
    dragController.endDrag();
    isMouseDownOnRobot = false;
    checkPointerPassthrough(e);
    return;
  }

  const wasOnRobot = isMouseDownOnRobot;
  isMouseDownOnRobot = false;

  if (e.target.closest('#aside-column') || e.target.closest('#character-context-menu')) return;

  const dt = performance.now() - pointerDownTime;
  const dist = Math.hypot(e.clientX - pointerDownPos.x, e.clientY - pointerDownPos.y);
  if (dt > 350 || dist > 8) {
    checkPointerPassthrough(e);
    return; // was a drag
  }

  // Short click on Robot -> Get Scared & Flinch reaction!
  if (wasOnRobot) {
    eyeVisor.setExpression('scared');
    soundEngine.playScared();

    gsap.to(avatarRoot.position, {
      y: "+=0.2",
      z: "-=0.25",
      duration: 0.12,
      yoyo: true,
      repeat: 3,
      ease: "power2.inOut"
    });

    const startledQuotes = [
      "Eep! You startled me! 😲",
      "Whoa! Don't sneak up like that! 🫣",
      "Yikes! My circuits jumped! ⚡",
      "Ah! Don't scare me! 😱",
      "Bzzzt! Scared me for a second! 🤖"
    ];
    const quote = startledQuotes[Math.floor(Math.random() * startledQuotes.length)];
    speechBubble.show(quote, { chime: false, type: 'mischief', duration: 2800 });

    setTimeout(() => {
      roamEngine.setEnabled(true);
      roamEngine.pickNewWaypoint();
      eyeVisor.setExpression('normal');
    }, 1800);
  }

  checkPointerPassthrough(e);
});

// Right-Click Context Menu Trigger (Desktop Mate Style)
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  const menu = document.getElementById('character-context-menu');
  if (!menu) return;

  mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);

  let isHit = false;
  if (modelContainer.children.length > 0) {
    const wrapper = modelContainer.children[0];
    const intersects = raycaster.intersectObjects(wrapper.children, true);
    isHit = intersects.length > 0;
  }

  const worldPos = new THREE.Vector3();
  avatarRoot.getWorldPosition(worldPos);
  const screenPos = worldPos.project(camera);
  const sx = (screenPos.x * 0.5 + 0.5) * window.innerWidth;
  const sy = (-screenPos.y * 0.5 + 0.5) * window.innerHeight;
  const distPx = Math.hypot(e.clientX - sx, e.clientY - sy);
  const hitRadius = Math.max(90, 180 * state.baseScale);

  if (isHit || distPx < hitRadius) {
    const menuX = Math.min(window.innerWidth - 230, Math.max(10, e.clientX));
    const menuY = Math.min(window.innerHeight - 260, Math.max(10, e.clientY));
    menu.style.left = `${menuX}px`;
    menu.style.top = `${menuY}px`;
    menu.classList.add('visible');
    soundEngine.playBoop();

    if (ipcRenderer) {
      ipcRenderer.send('set-ignore-mouse-events', false);
      isMouseIgnored = false;
    }
  }
});

// 12. Window Resize Handling
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  updateRoamBounds();
});

// 13. Render Loop
let lastTime = performance.now();
const startTime = performance.now();

function animate() {
  requestAnimationFrame(animate);

  const now = performance.now();
  const delta = (now - lastTime) / 1000;
  const time = (now - startTime) / 1000;
  lastTime = now;

  // Update Roam Engine
  let motionSpeed = 1.0;
  if (!animController.isAnimating && !mischiefEngine.isBusy && !dragController.isDragging) {
    motionSpeed = roamEngine.update(delta, time);
  }

  // Update Eye Visor System (Blinking, Expressions & Gaze)
  eyeVisor.update(delta, time);

  // Keep Speech Bubble anchored directly above robot's head in 3D
  speechBubble.updatePosition();

  renderer.render(scene, camera);
}

// Boot
setupUI();
setModelMode('dark').then(() => {
  setTimeout(() => {
    animController.playEntry1(() => {
      roamEngine.setEnabled(true);
      speechBubble.show("Hello! I'm your desktop assistant. Roaming is active! 🚀", { type: 'normal' });
    });
  }, 500);
});

animate();
