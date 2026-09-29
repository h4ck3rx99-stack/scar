import { gsap } from 'gsap';
import * as THREE from 'three';

/**
 * Animation Controller for 3D Assistant
 * Orchestrates:
 * - Entry 1, 2, 3, 4 (Full 3D summons)
 * - Side Entry 1, 2, 3, 4 (Edge screen bezels)
 * - Hover engine energy synchronization
 * - Ensures robot always cleanly faces the user (+Z) upon arrival
 */
export class AnimationController {
  constructor(avatarGroup, hoverEngine, roamEngine) {
    this.avatarGroup = avatarGroup;
    this.hoverEngine = hoverEngine;
    this.roamEngine = roamEngine;
    this.currentTimeline = null;
    this.isAnimating = false;
  }

  stopCurrent() {
    if (this.currentTimeline) {
      this.currentTimeline.kill();
      this.currentTimeline = null;
    }
    this.isAnimating = false;
  }

  /**
   * Entry 1: Quantum Warp & Spin
   * Spawns far in Z-distance, spirals 720° into screen with hover ring energy burst, settles facing user
   */
  playEntry1(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(0, 0, -20);
    this.avatarGroup.rotation.set(0, -Math.PI * 4, 0);
    this.avatarGroup.scale.set(0.01, 0.01, 0.01);
    this.hoverEngine.setIntensity(2.4);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Entry 1 Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      z: 0,
      duration: 1.3,
      ease: 'power3.out'
    }, 0);

    tl.to(this.avatarGroup.rotation, {
      y: 0,
      duration: 1.4,
      ease: 'power2.out'
    }, 0);

    tl.to(this.avatarGroup.scale, {
      x: 1.15, y: 1.15, z: 1.15,
      duration: 1.0,
      ease: 'power2.out'
    }, 0)
    .to(this.avatarGroup.scale, {
      x: 1.0, y: 1.0, z: 1.0,
      duration: 0.5,
      ease: 'elastic.out(1, 0.4)'
    }, 1.0);

    this.currentTimeline = tl;
  }

  /**
   * Entry 2: Orbital Drop & Ground Hover Bounce
   * Dives down from top, anti-gravity repulsors cushion with smooth squash & stretch
   */
  playEntry2(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(0, 13, 0);
    this.avatarGroup.rotation.set(0.25, 0, 0);
    this.avatarGroup.scale.set(1.0, 1.0, 1.0);
    this.hoverEngine.setIntensity(2.5);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Entry 2 Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      y: -0.5,
      duration: 0.75,
      ease: 'power2.in'
    }, 0)
    .to(this.avatarGroup.position, {
      y: 0,
      duration: 0.7,
      ease: 'elastic.out(1.2, 0.45)'
    }, 0.75);

    tl.to(this.avatarGroup.scale, {
      x: 1.25, y: 0.8, z: 1.25,
      duration: 0.16,
      ease: 'power1.inOut'
    }, 0.7)
    .to(this.avatarGroup.scale, {
      x: 1.0, y: 1.0, z: 1.0,
      duration: 0.6,
      ease: 'elastic.out(1, 0.4)'
    }, 0.86);

    tl.to(this.avatarGroup.rotation, {
      x: 0,
      duration: 0.7,
      ease: 'power2.out'
    }, 0.65);

    this.currentTimeline = tl;
  }

  /**
   * Entry 3: Mach 3 Diagonal Dash & Drift
   * Blasts from top-left background, banks to center and faces user
   */
  playEntry3(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(-13, 8, -10);
    this.avatarGroup.rotation.set(-0.2, 0.7, -0.5);
    this.avatarGroup.scale.set(0.4, 0.4, 0.4);
    this.hoverEngine.setIntensity(2.8);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Entry 3 Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      x: 0, y: 0, z: 0,
      duration: 1.1,
      ease: 'power3.out'
    }, 0);

    tl.to(this.avatarGroup.scale, {
      x: 1.0, y: 1.0, z: 1.0,
      duration: 1.0,
      ease: 'power2.out'
    }, 0);

    tl.to(this.avatarGroup.rotation, {
      x: 0.1, y: -0.3, z: 0.3,
      duration: 0.65,
      ease: 'power2.out'
    }, 0)
    .to(this.avatarGroup.rotation, {
      x: 0, y: 0, z: 0,
      duration: 0.6,
      ease: 'elastic.out(1, 0.5)'
    }, 0.65);

    this.currentTimeline = tl;
  }

  /**
   * Entry 4: Holographic Teleport & Core Ignition
   * Vertical energy expansion from center point, settling facing user
   */
  playEntry4(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(0, 0, 0);
    this.avatarGroup.rotation.set(0, Math.PI * 2, 0);
    this.avatarGroup.scale.set(0.01, 3.0, 0.01);
    this.hoverEngine.setIntensity(3.0);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Entry 4 Complete');
      }
    });

    tl.to(this.avatarGroup.scale, {
      x: 1.25, y: 1.25, z: 1.25,
      duration: 0.55,
      ease: 'back.out(2.0)'
    }, 0)
    .to(this.avatarGroup.scale, {
      x: 1.0, y: 1.0, z: 1.0,
      duration: 0.5,
      ease: 'elastic.out(1, 0.4)'
    }, 0.55);

    tl.to(this.avatarGroup.rotation, {
      y: 0,
      duration: 0.85,
      ease: 'power3.out'
    }, 0);

    this.currentTimeline = tl;
  }

  /**
   * Side Entry 1: Left Bezel Sprint
   * Enters from off-screen left monitor bezel, smoothly banks into center facing user
   */
  playSideEntry1(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(-14, 0, 0);
    this.avatarGroup.rotation.set(0, 0.5, -0.4);
    this.avatarGroup.scale.set(1.0, 1.0, 1.0);
    this.hoverEngine.setIntensity(2.4);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Side Entry 1 (Left) Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      x: 0,
      duration: 1.0,
      ease: 'power3.out'
    }, 0);

    tl.to(this.avatarGroup.rotation, {
      x: 0, y: 0, z: 0,
      duration: 1.2,
      ease: 'elastic.out(1, 0.4)'
    }, 0.15);

    this.currentTimeline = tl;
  }

  /**
   * Side Entry 2: Right Bezel Sprint
   * Enters from off-screen right monitor bezel curving in to face user
   */
  playSideEntry2(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(14, 0, 0);
    this.avatarGroup.rotation.set(0, -0.5, 0.4);
    this.avatarGroup.scale.set(1.0, 1.0, 1.0);
    this.hoverEngine.setIntensity(2.4);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Side Entry 2 (Right) Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      x: 0,
      duration: 1.0,
      ease: 'power3.out'
    }, 0);

    tl.to(this.avatarGroup.rotation, {
      x: 0, y: 0, z: 0,
      duration: 1.2,
      ease: 'elastic.out(1, 0.4)'
    }, 0.15);

    this.currentTimeline = tl;
  }

  /**
   * Side Entry 3: Top Bezel Dive
   * Drops from top edge like aerial drone, repulsors cushion to center facing user
   */
  playSideEntry3(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(0, 11, -1);
    this.avatarGroup.rotation.set(0.3, 0, 0);
    this.avatarGroup.scale.set(1.0, 1.0, 1.0);
    this.hoverEngine.setIntensity(2.2);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Side Entry 3 (Top) Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      y: 0, z: 0,
      duration: 1.0,
      ease: 'power3.out'
    }, 0);

    tl.to(this.avatarGroup.rotation, {
      x: 0,
      duration: 1.1,
      ease: 'elastic.out(1, 0.4)'
    }, 0.15);

    this.currentTimeline = tl;
  }

  /**
   * Side Entry 4: Bottom Bezel Rocket Boost
   * Rockets UP from below taskbar with anti-gravity burst, flips into center facing user
   */
  playSideEntry4(onComplete) {
    this.stopCurrent();
    this.isAnimating = true;
    if (this.roamEngine) this.roamEngine.setEnabled(false);

    this.avatarGroup.position.set(0, -11, 0);
    this.avatarGroup.rotation.set(-0.3, 0, 0);
    this.avatarGroup.scale.set(1.0, 1.0, 1.0);
    this.hoverEngine.setIntensity(2.8);

    const tl = gsap.timeline({
      onComplete: () => {
        this.isAnimating = false;
        this.hoverEngine.setIntensity(1.0);
        if (onComplete) onComplete('Side Entry 4 (Bottom) Complete');
      }
    });

    tl.to(this.avatarGroup.position, {
      y: 0,
      duration: 1.1,
      ease: 'power3.out'
    }, 0);

    tl.to(this.avatarGroup.rotation, {
      x: Math.PI * 2,
      duration: 1.1,
      ease: 'power3.out'
    }, 0)
    .set(this.avatarGroup.rotation, { x: 0 });

    this.currentTimeline = tl;
  }
}
