import * as THREE from 'three';
import gsap from 'gsap';

/**
 * Entrances (and exits) for Scar. Each one ends with the robot at `target` (world units, z = 0) facing the user.
 * EntryDirector.play() picks one at random, never one of the last few used, so it stays unpredictable.
 */
export class EntryDirector {
  constructor({ avatarRoot, scene, space, animController, sounds }) {
    this.root = avatarRoot;
    this.scene = scene;
    this.space = space;
    this.legacy = animController;   // the original 8 entries (they land in the centre)
    this.sounds = sounds;
    this.recent = [];
    this.entries = {
      // original set
      quantumWarp: (t, done) => this.legacyEntry('playEntry1', t, done),
      orbitalDrop: (t, done) => this.legacyEntry('playEntry2', t, done),
      diagonalDash: (t, done) => this.legacyEntry('playEntry3', t, done),
      holoUnfold: (t, done) => this.legacyEntry('playEntry4', t, done),
      leftSprint: (t, done) => this.legacyEntry('playSideEntry1', t, done),
      rightSprint: (t, done) => this.legacyEntry('playSideEntry2', t, done),
      topDive: (t, done) => this.legacyEntry('playSideEntry3', t, done),
      rocketUp: (t, done) => this.legacyEntry('playSideEntry4', t, done),
      // new
      portal: (t, done) => this.portal(t, done),
      bouncyDrop: (t, done) => this.bouncyDrop(t, done),
      spiralIn: (t, done) => this.spiralIn(t, done),
      glitchTeleport: (t, done) => this.glitchTeleport(t, done),
      peekaboo: (t, done) => this.peekaboo(t, done),
      cartwheel: (t, done) => this.cartwheel(t, done),
      zoomPast: (t, done) => this.zoomPast(t, done),
    };
  }

  /** A random on-screen landing spot, away from the edges (and usually not dead centre). */
  randomSpot() {
    const { w, h } = this.space.visibleSize(0);
    const fx = Math.random() < 0.3 ? 0 : (Math.random() - 0.5) * 0.6;
    const fy = (Math.random() - 0.5) * 0.35;
    return new THREE.Vector3(fx * w, fy * h, 0);
  }

  play(onDone, name = null) {
    const names = Object.keys(this.entries);
    const pool = names.filter((n) => !this.recent.includes(n));
    const pick = name && this.entries[name] ? name : pool[Math.floor(Math.random() * pool.length)];
    this.recent = [pick, ...this.recent].slice(0, 4);
    this.root.visible = true;
    this.root.rotation.set(0, 0, 0);
    this.root.scale.setScalar(1);
    const target = pick.startsWith('quantum') || pick.startsWith('orbital') || pick.startsWith('diagonal') ||
      pick.startsWith('holo') || pick.endsWith('Sprint') || pick === 'topDive' || pick === 'rocketUp'
      ? new THREE.Vector3(0, 0, 0) : this.randomSpot();
    this.entries[pick](target, () => {
      this.root.rotation.set(0, 0, 0);
      this.root.scale.setScalar(1);
      onDone?.(pick);
    });
    return pick;
  }

  legacyEntry(method, target, done) {
    this.legacy[method](() => done());
  }

  // ------------------------------------------------------------------ new entrances
  portal(t, done) {
    const px = 1 / this.space.pxPerWorld(0);
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(90 * px, 5 * px, 12, 64),
      new THREE.MeshBasicMaterial({ color: 0x39e6ff, transparent: true, opacity: 0, toneMapped: false }));
    ring.position.copy(t).add(new THREE.Vector3(0, 0, -0.3));
    ring.scale.setScalar(0.01);
    this.scene.add(ring);
    this.root.position.copy(t);
    this.root.scale.setScalar(0.01);
    this.root.rotation.set(0, Math.PI * 3, 0);
    const tl = gsap.timeline({ onComplete: () => { this.scene.remove(ring); ring.geometry.dispose(); ring.material.dispose(); done(); } });
    tl.to(ring.material, { opacity: 0.95, duration: 0.2 }, 0);
    tl.to(ring.scale, { x: 1, y: 1, z: 1, duration: 0.45, ease: 'back.out(2)' }, 0);
    tl.to(ring.rotation, { z: Math.PI * 2, duration: 1.4, ease: 'none' }, 0);
    tl.to(this.root.scale, { x: 1, y: 1, z: 1, duration: 0.6, ease: 'back.out(1.8)' }, 0.35);
    tl.to(this.root.rotation, { y: 0, duration: 0.9, ease: 'power3.out' }, 0.35);
    tl.to(ring.scale, { x: 1.6, y: 1.6, z: 1.6, duration: 0.4, ease: 'power2.in' }, 1.0);
    tl.to(ring.material, { opacity: 0, duration: 0.4 }, 1.0);
  }

  bouncyDrop(t, done) {
    const { h } = this.space.visibleSize(0);
    this.root.position.set(t.x, h * 0.75, 0);
    const tl = gsap.timeline({ onComplete: done });
    let height = t.y + h * 0.75;
    tl.to(this.root.position, { y: t.y, duration: 0.55, ease: 'power2.in' });
    for (let i = 0; i < 3; i++) {
      const up = (height - t.y) * (0.28 / (i + 1));
      tl.to(this.root.scale, { x: 1.25, y: 0.75, z: 1.25, duration: 0.07 });
      tl.to(this.root.scale, { x: 1, y: 1, z: 1, duration: 0.12 });
      tl.to(this.root.position, { y: t.y + up, duration: 0.22 - i * 0.04, ease: 'power2.out' }, '<');
      tl.to(this.root.position, { y: t.y, duration: 0.22 - i * 0.04, ease: 'power2.in' });
      height = t.y + up;
    }
    tl.to(this.root.scale, { x: 1.12, y: 0.88, z: 1.12, duration: 0.06 });
    tl.to(this.root.scale, { x: 1, y: 1, z: 1, duration: 0.35, ease: 'elastic.out(1, 0.4)' });
  }

  spiralIn(t, done) {
    const { w, h } = this.space.visibleSize(0);
    const sx = Math.random() < 0.5 ? -1 : 1, sy = Math.random() < 0.5 ? -1 : 1;
    const start = new THREE.Vector3(sx * w * 0.7, sy * h * 0.7, -2);
    const state = { a: 0 };
    const turns = 2.2;
    const tl = gsap.timeline({ onComplete: done });
    tl.to(state, {
      a: 1, duration: 1.5, ease: 'power2.out',
      onUpdate: () => {
        const k = 1 - state.a;
        const ang = state.a * Math.PI * 2 * turns;
        const r = k * Math.hypot(start.x - t.x, start.y - t.y);
        const base = Math.atan2(start.y - t.y, start.x - t.x);
        this.root.position.set(t.x + Math.cos(base + ang) * r, t.y + Math.sin(base + ang) * r, start.z * k);
        this.root.rotation.z = Math.sin(ang) * 0.4 * k;
        this.root.rotation.y = (1 - state.a) * Math.PI * 2;
      },
    });
  }

  glitchTeleport(t, done) {
    const tl = gsap.timeline({ onComplete: () => { this.root.visible = true; done(); } });
    const spots = [this.randomSpot(), this.randomSpot(), this.randomSpot(), t];
    this.root.visible = false;
    spots.forEach((p, i) => {
      tl.call(() => { this.root.visible = true; this.root.position.copy(p); this.root.scale.set(1, 0.05, 1); }, null, i * 0.28);
      tl.to(this.root.scale, { y: 1, duration: 0.08 }, i * 0.28);
      if (i < spots.length - 1) {
        tl.to(this.root.scale, { x: 0.05, duration: 0.06 }, i * 0.28 + 0.16);
        tl.call(() => { this.root.visible = false; this.root.scale.x = 1; }, null, i * 0.28 + 0.22);
      }
    });
    tl.to(this.root.rotation, { z: 0.15, duration: 0.05, yoyo: true, repeat: 3 });
  }

  peekaboo(t, done) {
    const { h } = this.space.visibleSize(0);
    const bottom = -h / 2;
    this.root.position.set(t.x, bottom - 1.2, 0);
    const tl = gsap.timeline({ onComplete: done });
    tl.to(this.root.position, { y: bottom + 0.15, duration: 0.6, ease: 'power2.out' });
    tl.to(this.root.rotation, { y: 0.6, duration: 0.3, ease: 'sine.inOut' });
    tl.to(this.root.rotation, { y: -0.6, duration: 0.5, ease: 'sine.inOut' });
    tl.to(this.root.rotation, { y: 0, duration: 0.3, ease: 'sine.inOut' });
    tl.to(this.root.position, { y: bottom - 0.4, duration: 0.25, ease: 'power2.in' });
    tl.to(this.root.position, { y: t.y, duration: 0.7, ease: 'back.out(1.6)' });
  }

  cartwheel(t, done) {
    const { w } = this.space.visibleSize(0);
    const from = Math.random() < 0.5 ? -1 : 1;
    this.root.position.set(from * (w / 2 + 1.2), t.y, 0);
    const tl = gsap.timeline({ onComplete: done });
    tl.to(this.root.position, { x: t.x, duration: 1.1, ease: 'power2.out' }, 0);
    tl.to(this.root.rotation, { z: -from * Math.PI * 4, duration: 1.1, ease: 'power2.out' }, 0);
    tl.set(this.root.rotation, { z: 0 });
    tl.to(this.root.scale, { x: 1.15, y: 0.85, z: 1.15, duration: 0.08 });
    tl.to(this.root.scale, { x: 1, y: 1, z: 1, duration: 0.4, ease: 'elastic.out(1, 0.4)' });
  }

  zoomPast(t, done) {
    this.root.position.set(t.x * 0.3, t.y * 0.3 + 0.5, 5.5);    // just behind the viewer
    this.root.rotation.set(0.3, Math.PI, 0);
    const tl = gsap.timeline({ onComplete: done });
    tl.to(this.root.position, { x: t.x, y: t.y + 0.4, z: -3, duration: 0.8, ease: 'power2.in' }, 0);
    tl.to(this.root.rotation, { y: Math.PI * 2, x: 0, duration: 0.9, ease: 'power2.inOut' }, 0);
    tl.to(this.root.position, { y: t.y, z: 0, duration: 0.6, ease: 'back.out(1.5)' });
    tl.set(this.root.rotation, { y: 0 });
  }

  // --------------------------------------------------------------------- exits
  /** Random way of leaving the screen (standby). */
  exit(onDone) {
    const { w, h } = this.space.visibleSize(0);
    const p = this.root.position;
    const kinds = ['up', 'shrink', 'dash', 'portal', 'down'];
    const kind = kinds[Math.floor(Math.random() * kinds.length)];
    const tl = gsap.timeline({ onComplete: () => { this.root.visible = false; this.root.scale.setScalar(1); onDone?.(kind); } });
    if (kind === 'up') {
      tl.to(p, { y: p.y - 0.15, duration: 0.2 });
      tl.to(p, { y: h, duration: 0.6, ease: 'power3.in' });
    } else if (kind === 'shrink') {
      tl.to(this.root.rotation, { y: Math.PI * 4, duration: 0.7, ease: 'power2.in' }, 0);
      tl.to(this.root.scale, { x: 0.01, y: 0.01, z: 0.01, duration: 0.7, ease: 'back.in(2)' }, 0);
    } else if (kind === 'dash') {
      const side = p.x > 0 ? 1 : -1;
      tl.to(this.root.rotation, { z: -side * 0.5, duration: 0.2 });
      tl.to(p, { x: side * (w / 2 + 1.5), duration: 0.5, ease: 'power3.in' });
    } else if (kind === 'portal') {
      tl.to(this.root.scale, { x: 1.2, y: 0.05, z: 1.2, duration: 0.25, ease: 'power2.in' });
      tl.to(this.root.scale, { x: 0.01, duration: 0.15 });
    } else {
      tl.to(p, { y: p.y + 0.2, duration: 0.25, ease: 'power2.out' });
      tl.to(p, { y: -h, duration: 0.55, ease: 'power3.in' });
    }
  }
}
