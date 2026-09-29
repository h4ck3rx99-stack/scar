import * as THREE from 'three';
import gsap from 'gsap';

const SX = 24;          // segments across
const SY = 16;          // segments down
const K_GRAB = 1400;    // spring stiffness at the grab point (nearly pinned)
const K_FAR = 70;       // stiffness at the far corner: low = more lag/stretch
const ZETA = 0.32;      // damping ratio < 1 gives the wobble when it stops
const MAX_STRETCH = 0.14; // max vertex lag as a fraction of the card's shorter side
const LIFT_SCALE = 0.04;  // card grows 4% when "picked up"
const SUBSTEP = 1 / 240;

function shadowTexture() {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 256;
  const g = c.getContext('2d');
  g.filter = 'blur(18px)';
  g.fillStyle = 'rgba(0,0,0,1)';
  g.beginPath();
  g.roundRect(40, 40, 176, 176, 16);
  g.fill();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/**
 * A window screenshot rendered as a soft-body card (the "cloth / rubber tab" effect).
 * The mesh lives in world space on plane z; at rest it covers the real window pixel-for-pixel,
 * so the swap between the real window and the card is invisible.
 */
export class ClothCard {
  constructor(scene, space, texture, bounds, grab, z = -0.25) {
    this.scene = scene;
    this.space = space;
    this.z = z;
    this.lift = 0;
    this.opacity = 1;
    this.done = false;

    const r = space.boundsToWorld(bounds, z);
    this.rect = { cx: r.center.x, cy: r.center.y, w: r.width, h: r.height };
    this.prevRect = { ...this.rect };
    this.rectVel = new THREE.Vector2();
    this.gu = THREE.MathUtils.clamp((grab.x - bounds.x) / bounds.width, 0, 1);
    this.gv = THREE.MathUtils.clamp(1 - (grab.y - bounds.y) / bounds.height, 0, 1);

    this.geometry = new THREE.PlaneGeometry(1, 1, SX, SY);
    const uv = this.geometry.attributes.uv;
    const n = uv.count;
    this.u = new Float32Array(n);
    this.v = new Float32Array(n);
    this.k = new Float32Array(n);
    this.c = new Float32Array(n);
    this.pos = new Float32Array(n * 3);
    this.vel = new Float32Array(n * 3);
    const maxD = Math.max(
      Math.hypot(this.gu, this.gv), Math.hypot(1 - this.gu, this.gv),
      Math.hypot(this.gu, 1 - this.gv), Math.hypot(1 - this.gu, 1 - this.gv));
    for (let i = 0; i < n; i++) {
      this.u[i] = uv.getX(i);
      this.v[i] = uv.getY(i);
      const d = Math.hypot(this.u[i] - this.gu, this.v[i] - this.gv) / maxD;
      const s = d * d * (3 - 2 * d); // smoothstep
      this.k[i] = K_GRAB + (K_FAR - K_GRAB) * s;
      this.c[i] = 2 * Math.sqrt(this.k[i]) * ZETA;
    }
    this._restTargets(this.pos);

    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    // Unlit + not tone-mapped: the card must match the real window's pixels exactly.
    this.material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, toneMapped: false, side: THREE.DoubleSide });
    this.mesh = new THREE.Mesh(this.geometry, this.material);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 1;

    this.shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, opacity: 0, depthWrite: false, toneMapped: false }));
    this.shadow.renderOrder = 0;
    this._writeGeometry();
    scene.add(this.shadow, this.mesh);
  }

  /** Where each vertex wants to be: the (animated) rectangle, scaled up by the lift. */
  _restTargets(out) {
    const { cx, cy, w, h } = this.rect;
    const s = 1 + LIFT_SCALE * this.lift;
    // Scale around the grab point so the hand stays attached while the card grows.
    const gx = cx + (this.gu - 0.5) * w;
    const gy = cy + (this.gv - 0.5) * h;
    for (let i = 0; i < this.u.length; i++) {
      out[i * 3] = gx + (this.u[i] - this.gu) * w * s;
      out[i * 3 + 1] = gy + (this.v[i] - this.gv) * h * s;
      out[i * 3 + 2] = this.z;
    }
  }

  grabWorld() {
    const { cx, cy, w, h } = this.rect;
    return new THREE.Vector3(cx + (this.gu - 0.5) * w, cy + (this.gv - 0.5) * h, this.z);
  }

  /** Animate from current bounds to target physical bounds. Resolves when the card has settled. */
  moveTo(targetBounds, durationMs = 900) {
    const t = this.space.boundsToWorld(targetBounds, this.z);
    return new Promise((resolve) => {
      const tl = gsap.timeline({ onComplete: resolve });
      tl.to(this, { lift: 1, duration: 0.14, ease: 'power2.out' });
      tl.to(this.rect, { cx: t.center.x, cy: t.center.y, w: t.width, h: t.height,
        duration: Math.max(0.35, durationMs / 1000), ease: 'power2.inOut' });
      tl.to(this, { lift: 0, duration: 0.45, ease: 'elastic.out(1, 0.45)' });
      tl.to({}, { duration: 0.15 }); // let the last wobble die down
      this.timeline = tl;
    });
  }

  fadeOut() {
    return new Promise((resolve) => {
      gsap.to(this, { opacity: 0, duration: 0.12, ease: 'power1.out', onComplete: () => { this.dispose(); resolve(); } });
    });
  }

  update(delta) {
    if (this.done) return;
    const dt = Math.min(delta, 0.05);
    // Card velocity drives the bend; low-passed so it doesn't flicker.
    const rv = new THREE.Vector2(this.rect.cx - this.prevRect.cx, this.rect.cy - this.prevRect.cy).divideScalar(Math.max(dt, 1e-3));
    this.rectVel.lerp(rv, 0.25);
    this.prevRect = { ...this.rect };

    const target = new Float32Array(this.pos.length);
    this._restTargets(target);
    const maxLag = MAX_STRETCH * Math.min(this.rect.w, this.rect.h);
    const steps = Math.max(1, Math.ceil(dt / SUBSTEP));
    const h = dt / steps;
    for (let s = 0; s < steps; s++) {
      for (let i = 0; i < this.k.length; i++) {
        const k = this.k[i], c = this.c[i];
        for (let a = 0; a < 2; a++) {
          const j = i * 3 + a;
          const acc = k * (target[j] - this.pos[j]) - c * this.vel[j];
          this.vel[j] += acc * h;
          this.pos[j] += this.vel[j] * h;
          const lag = this.pos[j] - target[j];
          if (lag > maxLag) { this.pos[j] = target[j] + maxLag; this.vel[j] *= 0.5; }
          else if (lag < -maxLag) { this.pos[j] = target[j] - maxLag; this.vel[j] *= 0.5; }
        }
      }
    }
    // Fabric drape: the card bows away from the camera in the middle, more when moving fast.
    const speed = this.rectVel.length();
    const bend = Math.min(0.35, speed * 0.02) + this.lift * 0.05;
    for (let i = 0; i < this.k.length; i++) {
      const far = Math.hypot(this.u[i] - this.gu, this.v[i] - this.gv);
      this.pos[i * 3 + 2] = this.z + this.lift * 0.12 - Math.sin(Math.PI * this.u[i]) * bend * far;
    }
    this._writeGeometry();
  }

  _writeGeometry() {
    const p = this.geometry.attributes.position;
    p.array.set(this.pos);
    p.needsUpdate = true;
    this.material.opacity = this.opacity;

    const { cx, cy, w, h } = this.rect;
    const px = 1 / this.space.pxPerWorld(this.z);
    const grow = 1 + 0.12 * this.lift;
    this.shadow.position.set(cx + 10 * px * this.lift, cy - 18 * px * this.lift, this.z - 0.02);
    this.shadow.scale.set(w * grow * 1.35, h * grow * 1.35, 1);  // texture has blur margins
    this.shadow.material.opacity = 0.45 * this.lift * this.opacity;
  }

  dispose() {
    this.done = true;
    if (this.timeline) this.timeline.kill();
    this.scene.remove(this.mesh, this.shadow);
    this.geometry.dispose();
    this.material.map?.dispose();
    this.material.dispose();
    this.shadow.geometry.dispose();
    this.shadow.material.map.dispose();
    this.shadow.material.dispose();
  }
}
