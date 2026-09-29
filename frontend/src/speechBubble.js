import * as THREE from 'three';
import { soundEngine } from './soundEngine.js';

/**
 * Holographic Floating Speech Bubble & Companion Voice
 * Renders speech balloons over the 3D robot for:
 * - Wellness notifications (Drink water, rest eyes, stretch)
 * - Peeking banter ("Psst... what're you doing?")
 * - 10 Mischievous voice lines
 */
export class SpeechBubble {
  constructor(camera, avatarRoot) {
    this.camera = camera;
    this.avatarRoot = avatarRoot;
    this.container = null;
    this.timeout = null;
    this.headOffset = 0.85; // world units above the robot's centre; main.js keeps it in sync with the robot's size
    this.ttsEnabled = false; // Strictly OFF by default - no robotic ghost voice!
    this.speechSynth = window.speechSynthesis || null;
    if (this.speechSynth) {
      try {
        this.speechSynth.cancel();
      } catch (e) {}
    }

    this.initDOM();
  }

  initDOM() {
    this.container = document.createElement('div');
    this.container.className = 'robot-speech-bubble';
    this.container.innerHTML = `
      <div class="bubble-arrow"></div>
      <div class="bubble-content">
        <span class="bubble-avatar">🤖</span>
        <span class="bubble-text"></span>
      </div>
    `;
    document.body.appendChild(this.container);
  }

  show(text, options = {}) {
    const {
      duration = 4500,
      chime = true,
      speak = false, // Spoken robotic voice OFF
      type = 'normal' // 'normal', 'wellness', 'mischief'
    } = options;

    if (!this.container) return;

    clearTimeout(this.timeout);

    // Update style based on type
    this.container.className = `robot-speech-bubble visible ${type}`;
    const textEl = this.container.querySelector('.bubble-text');
    textEl.textContent = text;

    // Play pleasant chime or boop
    if (chime) {
      if (type === 'wellness') {
        soundEngine.playChime();
      } else {
        soundEngine.playBoop();
      }
    }

    // Optional Browser Speech Synthesis (friendly robotic voice)
    if (speak && this.ttsEnabled && this.speechSynth) {
      try {
        this.speechSynth.cancel();
        // Strip emojis for clean TTS
        const cleanText = text.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}]/gu, '');
        const utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.rate = 1.15;
        utterance.pitch = 1.35; // slightly high, robotic
        this.speechSynth.speak(utterance);
      } catch (e) {
        // Speech synth not allowed or unsupported
      }
    }

    this.updatePosition();

    this.timeout = setTimeout(() => {
      this.hide();
    }, duration);
  }

  hide() {
    if (this.container) {
      this.container.classList.remove('visible');
    }
  }

  stopAllSpeech() {
    this.ttsEnabled = false;
    clearTimeout(this.timeout);
    if (this.speechSynth) {
      try {
        this.speechSynth.cancel();
      } catch (e) {}
    }
    this.hide();
  }

  updatePosition() {
    if (!this.container || !this.avatarRoot || !this.camera) return;

    // Get avatar position in screen space
    const worldPos = new THREE.Vector3();
    this.avatarRoot.getWorldPosition(worldPos);
    worldPos.y += this.headOffset * this.avatarRoot.scale.y;

    const screenPos = worldPos.clone().project(this.camera);

    const x = (screenPos.x * 0.5 + 0.5) * window.innerWidth;
    const y = (-screenPos.y * 0.5 + 0.5) * window.innerHeight;

    // Clamp inside viewport
    const clampedX = Math.max(90, Math.min(window.innerWidth - 180, x));
    const clampedY = Math.max(70, Math.min(window.innerHeight - 80, y));

    this.container.style.left = `${clampedX}px`;
    this.container.style.top = `${clampedY}px`;
  }
}
