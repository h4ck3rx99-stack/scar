import * as THREE from 'three';
import gsap from 'gsap';
import { soundEngine } from './soundEngine.js';

/**
 * DragController: Handles Hand-Holding Pick-Up & Dangling Inertia Physics
 * Across the full desktop screen (no small-box clipping).
 * - When clicked & dragged, robot looks like someone is holding its hand in mid-air
 * - Follows cursor anywhere on the screen (top-left to bottom-right)
 * - Torso tilts and dangles like a pendulum with velocity inertia
 * - Gets scared with wide pupils, shivering, and startled voice lines
 * - Smooth drop and bounce recovery on release at the exact dropped position
 */
export class DragController {
  constructor(avatarRoot, eyeVisor, speechBubble, roamEngine, ipcRenderer) {
    this.avatarRoot = avatarRoot;
    this.eyeVisor = eyeVisor;
    this.speechBubble = speechBubble;
    this.roamEngine = roamEngine;
    this.ipcRenderer = ipcRenderer;

    this.isDragging = false;
    this.dragStartTime = 0;
    this.lastScreenPos = { x: 0, y: 0 };
    this.velocity = { x: 0, y: 0 };
    this.grabOffsetX = 0;
    this.grabOffsetY = 0;

    this.scaredLines = [
      "W-whoa! Put me down! 😱",
      "Eeeek! Don't drop me! 💦",
      "Where are we going?! 🚀",
      "Hold on tight! 🌪️",
      "I'm dangling in the air! 🫣"
    ];

    this.relievedLines = [
      "Whew! Safe and sound! 😌",
      "That was a wild ride! ✨",
      "Back on steady ground! 🤖",
      "Thanks for holding me! 😄"
    ];
  }

  clientToWorld(clientX, clientY, camera) {
    if (!camera) return { x: 0, y: 0 };
    const normX = (clientX / window.innerWidth) * 2 - 1;
    const normY = -(clientY / window.innerHeight) * 2 + 1;
    const vFOV = THREE.MathUtils.degToRad(camera.fov);
    const dist = Math.abs(camera.position.z - this.avatarRoot.position.z);
    const visibleH = 2 * Math.tan(vFOV / 2) * dist;
    const visibleW = visibleH * camera.aspect;
    return {
      x: (normX * visibleW) / 2,
      y: (normY * visibleH) / 2
    };
  }

  startDrag(screenX, screenY, clientX, clientY, camera) {
    this.isDragging = true;
    this.dragStartTime = performance.now();
    this.lastScreenPos = { x: screenX, y: screenY };
    this.velocity = { x: 0, y: 0 };

    if (camera && clientX !== undefined && clientY !== undefined) {
      const world = this.clientToWorld(clientX, clientY, camera);
      this.grabOffsetX = this.avatarRoot.position.x - world.x;
      this.grabOffsetY = this.avatarRoot.position.y - world.y;
    } else {
      this.grabOffsetX = 0;
      this.grabOffsetY = 0;
    }

    // Pause autonomous roaming while being held
    this.wasRoaming = this.roamEngine.enabled;
    this.roamEngine.setEnabled(false);

    // 1. Immediately switch to Scared expression
    this.eyeVisor.setExpression('scared');
    soundEngine.playScared();

    // 2. Pick-Up Pose: Held by the hand!
    // Tilts one side up (raised arm) and body hangs down
    gsap.to(this.avatarRoot.rotation, {
      z: 0.38, // Tilt sideways (held by right hand/shoulder)
      x: -0.22, // Lean forward hanging down
      duration: 0.25,
      ease: 'back.out(2.0)'
    });

    const quote = this.scaredLines[Math.floor(Math.random() * this.scaredLines.length)];
    this.speechBubble.show(quote, { chime: false, type: 'mischief', duration: 3500 });
  }

  updateDrag(screenX, screenY, clientX, clientY, camera) {
    if (!this.isDragging) return;

    const deltaX = screenX - this.lastScreenPos.x;
    const deltaY = screenY - this.lastScreenPos.y;

    this.velocity.x = deltaX;
    this.velocity.y = deltaY;
    this.lastScreenPos = { x: screenX, y: screenY };

    // Move smoothly in 3D world coordinates across the full screen (window stays 100% fixed)
    if (camera && clientX !== undefined && clientY !== undefined) {
      const world = this.clientToWorld(clientX, clientY, camera);
      const targetX = world.x + this.grabOffsetX;
      const targetY = world.y + this.grabOffsetY;

      // Fluid interpolation for buttery-smooth 60fps drag
      this.avatarRoot.position.x = THREE.MathUtils.lerp(this.avatarRoot.position.x, targetX, 0.5);
      this.avatarRoot.position.y = THREE.MathUtils.lerp(this.avatarRoot.position.y, targetY, 0.5);
    }

    // Pendulum physics: body sways organically opposite to drag direction
    const targetRoll = 0.38 - THREE.MathUtils.clamp(deltaX * 0.03, -0.5, 0.5);
    const targetPitch = -0.22 + THREE.MathUtils.clamp(deltaY * 0.02, -0.35, 0.35);

    this.avatarRoot.rotation.z = THREE.MathUtils.lerp(this.avatarRoot.rotation.z, targetRoll, 0.25);
    this.avatarRoot.rotation.x = THREE.MathUtils.lerp(this.avatarRoot.rotation.x, targetPitch, 0.25);
  }

  endDrag() {
    if (!this.isDragging) return;
    this.isDragging = false;

    const dropX = this.avatarRoot.position.x;
    const dropY = this.avatarRoot.position.y;
    const dropZ = this.avatarRoot.position.z;

    // 1. Drop & bounce recovery physics at the current dropped position
    const tl = gsap.timeline({
      onComplete: () => {
        if (this.wasRoaming) {
          this.roamEngine.currentPos.set(dropX, dropY, dropZ);
          this.roamEngine.setEnabled(true);
          this.roamEngine.pickNewWaypoint();
        }
      }
    });

    // Soft drop
    tl.to(this.avatarRoot.position, {
      y: dropY - 0.22,
      duration: 0.2,
      ease: 'power2.in'
    });

    // Bounce back up and level out at dropped spot
    tl.to(this.avatarRoot.position, {
      y: dropY,
      duration: 0.45,
      ease: 'elastic.out(1.2, 0.4)'
    });

    tl.to(this.avatarRoot.rotation, {
      x: 0,
      z: 0,
      duration: 0.5,
      ease: 'elastic.out(1.0, 0.4)'
    }, "<");

    // 2. Relieved Happy Expression & sound
    this.eyeVisor.setExpression('happy');
    soundEngine.playHappy();

    const quote = this.relievedLines[Math.floor(Math.random() * this.relievedLines.length)];
    this.speechBubble.show(quote, { chime: false, type: 'normal', duration: 3000 });

    // Return to normal expression after 3 seconds
    setTimeout(() => {
      if (this.eyeVisor.currentExpression === 'happy') {
        this.eyeVisor.setExpression('normal');
      }
    }, 3200);
  }
}
