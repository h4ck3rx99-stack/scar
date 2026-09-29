import * as THREE from 'three';

/**
 * Maps between physical screen pixels (what the brain sends), overlay client pixels (CSS px), and
 * world units on a z-plane of the perspective camera. The overlay covers one display at a time; physical ->
 * client subtracts that display's physical origin and divides by its scale factor.
 */
export class ScreenSpace {
  constructor(camera) {
    this.camera = camera;
    this.scale = window.devicePixelRatio || 1;
    this.origin = { x: 0, y: 0 };
  }

  setScale(scaleFactor) {
    this.scale = scaleFactor || window.devicePixelRatio || 1;
  }

  /** display: { physOrigin: {x, y}, scaleFactor, bounds: {width, height} } from Electron. */
  setDisplay(display) {
    if (!display) return;
    this.setScale(display.scaleFactor);
    this.origin = display.physOrigin || { x: 0, y: 0 };
    this.size = display.bounds ? { w: display.bounds.width * this.scale, h: display.bounds.height * this.scale } : null;
  }

  /** Is a physical point on the display the overlay is currently covering? */
  containsPhys(x, y) {
    const w = this.size ? this.size.w : window.innerWidth * this.scale;
    const h = this.size ? this.size.h : window.innerHeight * this.scale;
    return x >= this.origin.x && y >= this.origin.y && x < this.origin.x + w && y < this.origin.y + h;
  }

  visibleSize(z = 0) {
    const dist = this.camera.position.z - z;
    const h = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * dist;
    return { w: h * this.camera.aspect, h };
  }

  pxPerWorld(z = 0) {
    return window.innerHeight / this.visibleSize(z).h;
  }

  physToClient(x, y) {
    return { x: (x - this.origin.x) / this.scale, y: (y - this.origin.y) / this.scale };
  }

  clientToWorld(cx, cy, z = 0) {
    const { w, h } = this.visibleSize(z);
    return new THREE.Vector3((cx / window.innerWidth - 0.5) * w, -(cy / window.innerHeight - 0.5) * h, z);
  }

  physToWorld(x, y, z = 0) {
    const c = this.physToClient(x, y);
    return this.clientToWorld(c.x, c.y, z);
  }

  worldToClient(v) {
    const p = v.clone().project(this.camera);
    return { x: (p.x * 0.5 + 0.5) * window.innerWidth, y: (-p.y * 0.5 + 0.5) * window.innerHeight };
  }

  /** Physical bounds {x,y,width,height} -> world-space centre and size on plane z. */
  boundsToWorld(b, z = 0) {
    const tl = this.physToWorld(b.x, b.y, z);
    const br = this.physToWorld(b.x + b.width, b.y + b.height, z);
    return {
      center: new THREE.Vector3((tl.x + br.x) / 2, (tl.y + br.y) / 2, z),
      width: br.x - tl.x,
      height: tl.y - br.y,
    };
  }
}
