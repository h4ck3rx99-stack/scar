import * as THREE from 'three';

/**
 * Calm roaming: glide to a spot, slow down on arrival, hover there for a while, pick another spot.
 *
 * Everything is time-based (units per second), so it moves the same at 12, 40 or 144 fps. The robot stays
 * at a constant depth (constant size), keeps facing the user, and only leans a little into its motion.
 * Spots are chosen mostly around the edges of the screen, so it doesn't park over whatever you're working on.
 */
export class RoamEngine {
  constructor(targetObject, options = {}) {
    this.targetObject = targetObject;
    this.options = {
      bounds: { minX: -3.8, maxX: 3.8, minY: -2.0, maxY: 2.0, minZ: 0, maxZ: 0 },
      speed: 0.35,
      ...options,
    };
    this.enabled = false;
    this.currentPos = new THREE.Vector3();
    this.targetPos = new THREE.Vector3();
    this.velocity = new THREE.Vector3();
    this.anchor = new THREE.Vector3();   // where it hovers when not roaming
    this.dwell = 0;                      // seconds left hovering at the current spot
    this.arrived = true;
    this.seed = Math.random() * 1000;
  }

  updateBounds(bounds) {
    this.options.bounds = { ...this.options.bounds, ...bounds };
  }

  setSpeed(val) {
    this.options.speed = Math.max(0.05, Math.min(2.0, val));
  }

  setEnabled(enable) {
    this.enabled = enable;
    this.currentPos.copy(this.targetObject.position);
    this.anchor.copy(this.targetObject.position);
    this.velocity.set(0, 0, 0);
    if (enable) {
      // Hover a moment where we are before setting off.
      this.targetPos.copy(this.targetObject.position);
      this.arrived = true;
      this.dwell = 1.5 + Math.random() * 2;
    } else {
      this.targetPos.copy(this.targetObject.position);
    }
  }

  /** Mostly around the edges (70%), sometimes anywhere. */
  pickNewWaypoint() {
    const b = this.options.bounds;
    const w = b.maxX - b.minX;
    const h = b.maxY - b.minY;
    let x, y;
    if (Math.random() < 0.7) {
      const side = Math.floor(Math.random() * 4);
      const edge = 0.18;
      if (side === 0) { x = b.minX + Math.random() * w * edge; y = b.minY + Math.random() * h; }
      else if (side === 1) { x = b.maxX - Math.random() * w * edge; y = b.minY + Math.random() * h; }
      else if (side === 2) { x = b.minX + Math.random() * w; y = b.maxY - Math.random() * h * edge; }
      else { x = b.minX + Math.random() * w; y = b.minY + Math.random() * h * edge; }
    } else {
      x = b.minX + Math.random() * w;
      y = b.minY + Math.random() * h;
    }
    // Don't pick a spot right next to where we already are.
    if (Math.hypot(x - this.currentPos.x, y - this.currentPos.y) < w * 0.15) {
      x = THREE.MathUtils.clamp(this.currentPos.x + (x > this.currentPos.x ? 1 : -1) * w * 0.3, b.minX, b.maxX);
    }
    this.targetPos.set(x, y, 0);
    this.arrived = false;
  }

  update(delta, time) {
    const obj = this.targetObject;
    const t = time + this.seed;
    const dt = Math.min(delta, 0.1);
    // Gentle hover bob, always on.
    const bobY = Math.sin(t * 1.6) * 0.035 + Math.sin(t * 0.7) * 0.015;

    if (!this.enabled) {
      obj.position.x = THREE.MathUtils.damp(obj.position.x, this.anchor.x, 4, dt);
      obj.position.y = THREE.MathUtils.damp(obj.position.y, this.anchor.y + bobY, 4, dt);
      obj.position.z = THREE.MathUtils.damp(obj.position.z, this.anchor.z, 4, dt);
      this._settleRotation(dt, 0, t);
      return 1.0;
    }

    if (this.arrived) {
      this.dwell -= dt;
      if (this.dwell <= 0) this.pickNewWaypoint();
    }

    // "Arrive" steering: accelerate towards the spot, cruise, brake smoothly near it.
    const maxSpeed = this.options.speed * 2.2;          // world units per second
    const toTarget = new THREE.Vector3().subVectors(this.targetPos, this.currentPos);
    const dist = toTarget.length();
    const slowRadius = 1.2;
    const desiredSpeed = dist < slowRadius ? maxSpeed * (dist / slowRadius) : maxSpeed;
    const desired = dist > 1e-4 ? toTarget.multiplyScalar(desiredSpeed / dist) : toTarget.set(0, 0, 0);
    const steer = desired.sub(this.velocity);
    const maxAccel = maxSpeed * 1.5;
    if (steer.length() > maxAccel * dt) steer.setLength(maxAccel * dt);
    this.velocity.add(steer);
    this.currentPos.addScaledVector(this.velocity, dt);
    this.currentPos.z = THREE.MathUtils.damp(this.currentPos.z, 0, 3, dt);

    if (!this.arrived && dist < 0.05 && this.velocity.length() < 0.08) {
      this.arrived = true;
      this.dwell = 4 + Math.random() * 7;   // hover 4-11 s before moving on
    }

    obj.position.set(this.currentPos.x, this.currentPos.y + bobY, this.currentPos.z);
    this._settleRotation(dt, this.velocity.x / Math.max(maxSpeed, 1e-3), t);
    return 1.0 + this.velocity.length() / Math.max(maxSpeed, 1e-3);
  }

  /** Face the user; lean a little into horizontal motion. */
  _settleRotation(dt, vxNorm, t) {
    const obj = this.targetObject;
    const lean = THREE.MathUtils.clamp(vxNorm, -1, 1);
    obj.rotation.z = THREE.MathUtils.damp(obj.rotation.z, -lean * 0.14 + Math.sin(t * 1.1) * 0.012, 5, dt);
    obj.rotation.y = THREE.MathUtils.damp(obj.rotation.y, lean * 0.22, 4, dt);
    obj.rotation.x = THREE.MathUtils.damp(obj.rotation.x, Math.abs(lean) * 0.06, 4, dt);
  }
}
