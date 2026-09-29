import * as THREE from 'three';

/**
 * Scar's face: an animated screen fitted exactly onto the robot's glass visor.
 *
 * fitToModel() finds the visor automatically: it casts a grid of rays at the robot from the viewer's side,
 * samples the base-colour texture at each hit, keeps the dark "glass" hits in the upper half, and then
 * builds the face mesh by projecting a grid onto that surface (a decal that follows the visor's curve).
 *
 * Expressions: normal, happy, sad, angry, scared, curious, dizzy, love, listening, thinking, sleepy.
 * The mouth moves with the TTS loudness while talking (setTalk).
 */
// Visor rectangle measured on the light model (fractions of the bounding box), for when detection fails.
const VISOR_FALLBACK = { fx0: 0.285, fx1: 0.82, fy0: 0.475, fy1: 0.632 };
const W = 512;
const H = 288;

const PALETTE = {
  normal: '#39e6ff', happy: '#3dffb4', scared: '#ffffff', angry: '#ff3b4e', sad: '#4aa3ff',
  curious: '#ffe14a', dizzy: '#c86bff', love: '#ff5fa2', listening: '#39e6ff', thinking: '#ffc53d', sleepy: '#5f8dff',
};

export class ScarFace {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = W;
    this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d');
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.anisotropy = 4;

    this.material = new THREE.MeshBasicMaterial({
      map: this.texture, transparent: true, toneMapped: false, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4, side: THREE.FrontSide,
    });
    // Placeholder until fitted; kept invisible so nothing floats in the wrong place.
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.4, 0.22), this.material);
    this.mesh.name = 'ScarFace';
    this.mesh.visible = false;
    this.mesh.renderOrder = 5;

    this.currentExpression = 'normal';
    this.blink = 0;
    this.blinking = false;
    this.blinkTimer = 0;
    this.nextBlink = 2.5 + Math.random() * 3;
    this.gazeX = this.gazeY = this.tGazeX = this.tGazeY = 0;
    this.talk = 0;
    this.talkTarget = 0;
    this.time = 0;
    this.redrawAcc = 0;
  }

  // ------------------------------------------------------------------ API
  attachTo(parent) {
    if (this.mesh.parent) this.mesh.parent.remove(this.mesh);
    parent.add(this.mesh);
  }

  setExpression(name) {
    this.currentExpression = PALETTE[name] ? name : 'normal';
  }

  setGaze(x, y) {
    this.tGazeX = THREE.MathUtils.clamp(x, -1, 1);
    this.tGazeY = THREE.MathUtils.clamp(y, -1, 1);
  }

  /** 0..1 voice loudness; 0 closes the mouth. */
  setTalk(level) {
    this.talkTarget = level;
  }

  triggerBlink() {
    this.blinking = true;
    this.blink = 0;
  }

  // ---------------------------------------------------------------- fitting
  /**
   * @param wrapper   the normalized model group the face is parented to
   * @param viewRoot  the avatar root whose local -Z is "towards the viewer"
   */
  fitToModel(wrapper, viewRoot) {
    if (wrapper.userData.faceGeometry) {
      this._useGeometry(wrapper.userData.faceGeometry);
      return true;
    }
    const saved = { p: viewRoot.position.clone(), r: viewRoot.rotation.clone(), s: viewRoot.scale.clone() };
    viewRoot.position.set(0, 0, 0);
    viewRoot.rotation.set(0, 0, 0);
    viewRoot.scale.set(1, 1, 1);
    viewRoot.updateMatrixWorld(true);
    try {
      const geo = this._fit(wrapper);
      if (!geo) return false;
      wrapper.userData.faceGeometry = geo;
      this._useGeometry(geo);
      return true;
    } finally {
      viewRoot.position.copy(saved.p);
      viewRoot.rotation.copy(saved.r);
      viewRoot.scale.copy(saved.s);
      viewRoot.updateMatrixWorld(true);
    }
  }

  _useGeometry(geo) {
    this.mesh.geometry.dispose();
    this.mesh.geometry = geo;
    this.mesh.position.set(0, 0, 0);
    this.mesh.rotation.set(0, 0, 0);
    this.mesh.scale.set(1, 1, 1);
    this.mesh.visible = true;
  }

  _fit(wrapper) {
    const meshes = [];
    wrapper.traverse((c) => { if (c.isMesh && c !== this.mesh) meshes.push(c); });
    if (!meshes.length) return null;
    const box = new THREE.Box3();
    meshes.forEach((m) => box.expandByObject(m));
    const size = box.getSize(new THREE.Vector3());

    // Texture luminance lookup (256x256 is plenty to tell glass from paint).
    const map = meshes[0].material.map;
    let lum = null;
    if (map && map.image) {
      const c = document.createElement('canvas');
      c.width = c.height = 256;
      const g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(map.image, 0, 0, 256, 256);
      const d = g.getImageData(0, 0, 256, 256).data;
      lum = (uv) => {
        const u = ((uv.x % 1) + 1) % 1;
        const v = ((uv.y % 1) + 1) % 1;
        const py = map.flipY ? Math.floor((1 - v) * 255) : Math.floor(v * 255);
        const i = (py * 256 + Math.floor(u * 255)) * 4;
        return (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 255;
      };
    }

    const ray = new THREE.Raycaster();
    const dir = new THREE.Vector3(0, 0, -1);
    const zStart = box.max.z + 1;
    const hitAt = (x, y) => {
      ray.set(new THREE.Vector3(x, y, zStart), dir);
      return ray.intersectObjects(meshes, false)[0] || null;
    };

    // 1. Coarse scan of the upper 60% for dark glass.
    const N = 48;
    const glass = [];
    for (let iy = 0; iy < N; iy++) {
      const y = box.min.y + size.y * (0.4 + 0.6 * (iy + 0.5) / N);
      for (let ix = 0; ix < N; ix++) {
        const x = box.min.x + size.x * (ix + 0.5) / N;
        const h = hitAt(x, y);
        if (!h || !h.uv) continue;
        const l = lum ? lum(h.uv) : 0;
        if (l < 0.11) glass.push(h.point);
      }
    }
    if (glass.length < 20) return null;
    // Keep the biggest blob near the median (robust against dark seams elsewhere).
    const mx = median(glass.map((p) => p.x));
    const my = median(glass.map((p) => p.y));
    const near = glass.filter((p) => Math.abs(p.x - mx) < size.x * 0.35 && Math.abs(p.y - my) < size.y * 0.2);
    const xs = near.map((p) => p.x).sort((a, b) => a - b);
    const ys = near.map((p) => p.y).sort((a, b) => a - b);
    let x0 = pct(xs, 0.04), x1 = pct(xs, 0.96), y0 = pct(ys, 0.06), y1 = pct(ys, 0.94);
    // Inset so the face screen sits inside the visor rim.
    let iw = (x1 - x0) * 0.06, ih = (y1 - y0) * 0.08;
    if ((y1 - y0) < size.y * 0.08 || (x1 - x0) / (y1 - y0) > 3.4) {
      // Glass and paint too similar to tell apart (dark theme): use the visor rectangle measured on the
      // light theme, as fractions of the model's bounding box (both themes share the same design).
      x0 = box.min.x + size.x * VISOR_FALLBACK.fx0;
      x1 = box.min.x + size.x * VISOR_FALLBACK.fx1;
      y0 = box.min.y + size.y * VISOR_FALLBACK.fy0;
      y1 = box.min.y + size.y * VISOR_FALLBACK.fy1;
      iw = ih = 0;
    }

    // 2. Project a grid onto the visor surface -> face geometry in wrapper-local space.
    const GX = 24, GY = 14;
    const inv = wrapper.matrixWorld.clone().invert();
    const positions = [];
    const uvs = [];
    const fallbackZ = median(near.map((p) => p.z));
    for (let j = 0; j <= GY; j++) {
      for (let i = 0; i <= GX; i++) {
        const u = i / GX, v = j / GY;
        const x = x0 + iw + (x1 - x0 - 2 * iw) * u;
        const y = y0 + ih + (y1 - y0 - 2 * ih) * v;
        const h = hitAt(x, y);
        const p = h ? h.point.clone() : new THREE.Vector3(x, y, fallbackZ);
        p.z += 0.004 * size.y; // hover just above the glass
        p.applyMatrix4(inv);
        positions.push(p.x, p.y, p.z);
        uvs.push(u, v);
      }
    }
    const index = [];
    for (let j = 0; j < GY; j++) {
      for (let i = 0; i < GX; i++) {
        const a = j * (GX + 1) + i, b = a + 1, c = a + GX + 1, d = c + 1;
        index.push(a, b, d, a, d, c);
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
    geo.setIndex(index);
    geo.computeBoundingSphere();
    this.aspect = (x1 - x0) / (y1 - y0);
    return geo;
  }

  // ------------------------------------------------------------------ frame
  update(delta) {
    this.time += delta;
    this.blinkTimer += delta;
    if (!this.blinking && this.blinkTimer > this.nextBlink && this.currentExpression !== 'sleepy') {
      this.triggerBlink();
      this.blinkTimer = 0;
      this.nextBlink = 2.2 + Math.random() * 3.8;
      if (Math.random() < 0.15) this.nextBlink = 0.25; // occasional double blink
    }
    if (this.blinking) {
      this.blink += delta * 11;
      if (this.blink >= 2) { this.blink = 0; this.blinking = false; }
    }
    this.gazeX += (this.tGazeX - this.gazeX) * 0.15;
    this.gazeY += (this.tGazeY - this.gazeY) * 0.15;
    // Mouth: fast attack, slower release, like real speech.
    const k = this.talkTarget > this.talk ? 0.55 : 0.22;
    this.talk += (this.talkTarget - this.talk) * k;

    // 30 fps is plenty for the face and halves texture uploads.
    this.redrawAcc += delta;
    if (this.redrawAcc < 1 / 30) return;
    this.redrawAcc = 0;
    this.draw();
    this.texture.needsUpdate = true;
  }

  // ---------------------------------------------------------------- drawing
  draw() {
    const g = this.ctx;
    const e = this.currentExpression;
    const color = PALETTE[e];
    g.clearRect(0, 0, W, H);

    // Glass: opaque so the texture's painted eyes never show through.
    const bg = g.createRadialGradient(W * 0.45, H * 0.35, 20, W / 2, H / 2, W * 0.7);
    bg.addColorStop(0, '#0d1a24');
    bg.addColorStop(1, '#03070b');
    g.fillStyle = bg;
    g.beginPath();
    g.roundRect(0, 0, W, H, 70);
    g.fill();
    // Scanlines + a soft reflection streak.
    g.globalAlpha = 0.06;
    g.fillStyle = color;
    for (let y = 0; y < H; y += 6) g.fillRect(0, y, W, 2);
    g.globalAlpha = 0.07;
    g.fillStyle = '#ffffff';
    g.beginPath();
    g.ellipse(W * 0.3, H * 0.18, W * 0.28, H * 0.07, -0.18, 0, Math.PI * 2);
    g.fill();
    g.globalAlpha = 1;

    const blinkAmt = this.blinking ? (this.blink <= 1 ? this.blink : 2 - this.blink) : 0;
    const gx = this.gazeX * 22;
    const gy = -this.gazeY * 12;
    const eyeY = H * 0.42 + gy;
    const sep = W * 0.2;

    g.save();
    g.shadowColor = color;
    g.shadowBlur = 22;
    g.fillStyle = color;
    g.strokeStyle = color;
    g.lineCap = 'round';
    for (const side of [-1, 1]) this.drawEye(g, W / 2 + side * sep + gx, eyeY, side, blinkAmt, e);
    this.drawMouth(g, W / 2 + gx * 0.5, H * 0.76 + gy * 0.5, e, color);
    g.restore();
  }

  drawEye(g, cx, cy, side, blinkAmt, e) {
    g.save();
    g.translate(cx, cy);
    const t = this.time;
    const sy = Math.max(0.06, 1 - blinkAmt * 0.94);
    switch (e) {
      case 'happy':
        g.lineWidth = 16;
        g.beginPath();
        g.arc(0, 18, 40, Math.PI * 1.15, Math.PI * 1.85);
        g.stroke();
        break;
      case 'love': {
        const s = 1 + Math.sin(t * 6) * 0.08;
        g.scale(s, s * sy);
        g.beginPath();
        g.moveTo(0, 22);
        g.bezierCurveTo(-46, -8, -22, -44, 0, -18);
        g.bezierCurveTo(22, -44, 46, -8, 0, 22);
        g.fill();
        break;
      }
      case 'sad':
        g.scale(1, sy);
        g.beginPath();
        g.roundRect(-34, -16, 68, 38, 18);
        g.fill();
        g.fillStyle = '#03070b';
        g.beginPath();
        g.moveTo(-40, -22);
        g.lineTo(40, -22);
        g.lineTo(side * 40, 4);
        g.closePath();
        g.fill();
        break;
      case 'angry':
        g.scale(1, sy);
        g.beginPath();
        g.roundRect(-36, -18, 72, 40, 14);
        g.fill();
        g.fillStyle = '#03070b';
        g.beginPath();
        g.moveTo(-44, -24);
        g.lineTo(44, -24);
        g.lineTo(-side * 44, 6);
        g.closePath();
        g.fill();
        break;
      case 'scared':
        g.lineWidth = 10;
        g.scale(1, sy);
        g.beginPath();
        g.arc(0, 0, 40, 0, Math.PI * 2);
        g.stroke();
        g.beginPath();
        g.arc((Math.random() - 0.5) * 5, (Math.random() - 0.5) * 5, 11, 0, Math.PI * 2);
        g.fill();
        break;
      case 'curious': {
        const r = side > 0 ? 40 : 26;
        g.scale(1, sy);
        g.beginPath();
        g.arc(0, 0, r, 0, Math.PI * 2);
        g.fill();
        break;
      }
      case 'dizzy':
        g.lineWidth = 7;
        g.beginPath();
        for (let a = 0; a < Math.PI * 5; a += 0.15) {
          const r = a * 5;
          const x = Math.cos(a + t * 8 * side) * r;
          const y = Math.sin(a + t * 8 * side) * r;
          if (a === 0) g.moveTo(x, y); else g.lineTo(x, y);
        }
        g.stroke();
        break;
      case 'sleepy':
        g.lineWidth = 12;
        g.beginPath();
        g.arc(0, -6, 30, Math.PI * 0.2, Math.PI * 0.8);
        g.stroke();
        break;
      case 'thinking': {
        // Eyes glance up and to the side, a slow "processing" pulse.
        const pulse = 0.85 + Math.sin(t * 5) * 0.15;
        g.translate(14, -12);
        g.scale(pulse, pulse * sy);
        g.beginPath();
        g.roundRect(-30, -30, 60, 60, 22);
        g.fill();
        break;
      }
      case 'listening': {
        // Big attentive eyes that gently breathe.
        const s = 1.08 + Math.sin(t * 4) * 0.05;
        g.scale(s, s * sy);
        g.beginPath();
        g.roundRect(-34, -40, 68, 80, 30);
        g.fill();
        g.fillStyle = '#e8fdff';
        g.beginPath();
        g.arc(-10 + this.gazeX * 8, -12, 9, 0, Math.PI * 2);
        g.fill();
        break;
      }
      default:
        g.scale(1, sy);
        g.beginPath();
        g.roundRect(-32, -36, 64, 72, 26);
        g.fill();
        g.fillStyle = '#e8fdff';
        g.beginPath();
        g.arc(-9 + this.gazeX * 8, -12 - this.gazeY * 4, 8, 0, Math.PI * 2);
        g.fill();
    }
    g.restore();
  }

  drawMouth(g, cx, cy, e, color) {
    g.save();
    g.translate(cx, cy);
    g.lineWidth = 9;
    const open = this.talk;
    if (open > 0.04) {
      // Talking: a rounded opening whose size follows the voice.
      const w = 46 + open * 30;
      const h = 8 + open * 46;
      g.beginPath();
      g.roundRect(-w / 2, -h / 2, w, h, Math.min(h / 2, 22));
      g.fill();
      g.fillStyle = '#03070b';
      g.beginPath();
      g.roundRect(-w / 2 + 8, -h / 2 + 7, w - 16, Math.max(0, h - 14), Math.min(h / 2, 16));
      g.fill();
    } else if (e === 'thinking') {
      for (let i = 0; i < 3; i++) {
        const a = 0.35 + 0.65 * Math.max(0, Math.sin(this.time * 6 - i * 0.9));
        g.globalAlpha = a;
        g.beginPath();
        g.arc(-30 + i * 30, 0, 8, 0, Math.PI * 2);
        g.fill();
      }
    } else if (e === 'sad' || e === 'angry') {
      g.beginPath();
      g.arc(0, 26, 30, Math.PI * 1.2, Math.PI * 1.8);
      g.stroke();
    } else if (e === 'scared' || e === 'listening') {
      g.beginPath();
      g.ellipse(0, 0, e === 'scared' ? 14 : 10, e === 'scared' ? 18 : 10, 0, 0, Math.PI * 2);
      g.stroke();
    } else if (e === 'sleepy') {
      g.globalAlpha = 0.6;
      g.beginPath();
      g.moveTo(-16, 0);
      g.lineTo(16, 0);
      g.stroke();
      g.font = 'bold 34px sans-serif';
      g.globalAlpha = 0.5 + 0.5 * Math.sin(this.time * 2);
      g.fillText('z', 110, -60 - (this.time * 10) % 20);
    } else {
      // Resting smile.
      g.beginPath();
      g.arc(0, -22, 34, Math.PI * 0.25, Math.PI * 0.75);
      g.stroke();
    }
    g.restore();
  }
}

function median(a) {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)];
}

function pct(sorted, q) {
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(q * sorted.length)))];
}
