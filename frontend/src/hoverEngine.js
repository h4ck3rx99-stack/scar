import * as THREE from 'three';

/**
 * HoverEngine (Disabled / Clean Mode)
 * All hover meshes, rings, glow discs, and spark/fire particles removed as requested.
 * Maintains safe API interface for animation triggers without rendering visual artifacts.
 */
export class HoverEngine {
  constructor(scene, options = {}) {
    this.scene = scene;
    this.options = { ...options };
    this.group = new THREE.Group();
    this.group.name = "HoverEngineGroup";
    this.group.visible = false; // Completely hidden
  }

  setColorMode() {}
  setPosition(x, y, z) {
    this.group.position.set(x, y, z);
  }
  setRotation(rx, ry, rz) {
    this.group.rotation.set(rx, ry, rz);
  }
  setIntensity() {}
  attachTo(parentMesh) {
    if (this.group.parent) {
      this.group.parent.remove(this.group);
    }
    parentMesh.add(this.group);
  }
  update() {}
}
