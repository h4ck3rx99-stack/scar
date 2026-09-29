import * as THREE from 'three';

/**
 * Smooth 3D Roaming Engine
 * Generates organic wandering waypoints within full screen bounds.
 * Defaulted to gentle, relaxed, floaty pacing.
 */
export class RoamEngine {
  constructor(targetObject, options = {}) {
    this.targetObject = targetObject;
    this.options = {
      bounds: {
        minX: -3.8, maxX: 3.8,
        minY: -2.0, maxY: 2.0,
        minZ: -1.0, maxZ: 1.0
      },
      speed: 0.35, // Gentle, slow floating default!
      damping: 0.03,
      ...options
    };

    this.enabled = false;
    this.currentPos = new THREE.Vector3(0, 0, 0);
    this.targetPos = new THREE.Vector3(0, 0, 0);
    this.velocity = new THREE.Vector3(0, 0, 0);
    this.prevPos = new THREE.Vector3(0, 0, 0);

    // Rotations
    this.targetRotation = new THREE.Euler(0, 0, 0, 'YXZ');
    this.currentRotation = new THREE.Euler(0, 0, 0, 'YXZ');

    this.waypointTimer = 0;
    this.waypointInterval = 5.0; // Slower interval between targets

    // Seeded time for organic sinusoids
    this.timeSeed = Math.random() * 1000;
  }

  updateBounds(bounds) {
    this.options.bounds = { ...this.options.bounds, ...bounds };
  }

  setSpeed(val) {
    this.options.speed = Math.max(0.05, Math.min(2.0, val));
  }

  setEnabled(enable) {
    this.enabled = enable;
    if (enable) {
      this.currentPos.copy(this.targetObject.position);
      this.pickNewWaypoint();
    } else {
      // Rest gently in place where it currently is without jumping to center!
      this.targetPos.copy(this.targetObject.position);
      this.currentPos.copy(this.targetObject.position);
      this.velocity.set(0, 0, 0);
    }
  }

  pickNewWaypoint() {
    const b = this.options.bounds;
    // Pick gentle wandering waypoints across the full screen
    this.targetPos.set(
      b.minX + Math.random() * (b.maxX - b.minX),
      b.minY + Math.random() * (b.maxY - b.minY),
      b.minZ + Math.random() * (b.maxZ - b.minZ)
    );
    // 5 to 9 seconds at gentle pacing
    this.waypointInterval = 5.0 + Math.random() * 4.0;
    this.waypointTimer = 0;
  }

  update(delta, time) {
    if (!this.targetObject) return 1.0;

    const t = time + this.timeSeed;
    const speedMult = this.options.speed;

    if (this.enabled) {
      this.waypointTimer += delta;
      if (this.waypointTimer >= this.waypointInterval) {
        this.pickNewWaypoint();
      }

      // Gentle, calm breathing curves
      const noiseX = Math.sin(t * 0.5) * 0.25 + Math.cos(t * 1.1) * 0.12;
      const noiseY = Math.cos(t * 0.6) * 0.20 + Math.sin(t * 1.3) * 0.08;
      const noiseZ = Math.sin(t * 0.4) * 0.15;

      const dynamicTarget = new THREE.Vector3(
        this.targetPos.x + noiseX,
        this.targetPos.y + noiseY,
        this.targetPos.z + noiseZ
      );

      // Spring-damped movement with slow speed scaling
      const toTarget = new THREE.Vector3().subVectors(dynamicTarget, this.currentPos);
      this.velocity.add(toTarget.multiplyScalar(this.options.damping * 0.22 * speedMult));
      this.velocity.multiplyScalar(0.95); // High smoothness friction

      this.prevPos.copy(this.currentPos);
      this.currentPos.add(this.velocity);
      this.targetObject.position.copy(this.currentPos);

      // Gentle banking into flight turns
      const targetRoll = -this.velocity.x * 0.35;
      const targetPitch = this.velocity.y * 0.25;
      const targetYaw = this.velocity.x * 0.18;

      this.targetObject.rotation.z = THREE.MathUtils.lerp(this.targetObject.rotation.z, targetRoll, 0.06);
      this.targetObject.rotation.x = THREE.MathUtils.lerp(this.targetObject.rotation.x, targetPitch, 0.06);
      this.targetObject.rotation.y = THREE.MathUtils.lerp(this.targetObject.rotation.y, targetYaw, 0.06);

      const speed = this.velocity.length() / Math.max(delta, 0.001);
      const thrustIntensity = THREE.MathUtils.clamp(1.0 + speed * 0.25, 0.8, 2.0);
      return thrustIntensity;
    } else {
      // Idle hovering breathing motion when resting in place
      const idleFloatY = Math.sin(t * 1.8) * 0.05;
      const idleFloatX = Math.cos(t * 1.1) * 0.02;
      const idleTiltZ = Math.sin(t * 1.4) * 0.012;

      this.targetObject.position.y = THREE.MathUtils.lerp(this.targetObject.position.y, this.targetPos.y + idleFloatY, 0.06);
      this.targetObject.position.x = THREE.MathUtils.lerp(this.targetObject.position.x, this.targetPos.x + idleFloatX, 0.06);
      this.targetObject.position.z = THREE.MathUtils.lerp(this.targetObject.position.z, this.targetPos.z, 0.06);

      this.targetObject.rotation.z = THREE.MathUtils.lerp(this.targetObject.rotation.z, idleTiltZ, 0.05);
      this.targetObject.rotation.x = THREE.MathUtils.lerp(this.targetObject.rotation.x, 0, 0.05);
      this.targetObject.rotation.y = THREE.MathUtils.lerp(this.targetObject.rotation.y, 0, 0.05);

      return 1.0;
    }
  }
}
