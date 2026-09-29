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
    if (wrapper.userData.faceFit) {
      this._useFit(wrapper.userData.faceFit);
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
      const fit = { geo, mask: this.maskCanvas, W: this.W, H: this.H };
      wrapper.userData.faceFit = fit;   // each theme's model keeps its own fit
      this._useFit(fit);
      return true;
    } finally {
      viewRoot.position.copy(saved.p);
      viewRoot.rotation.copy(saved.r);
      viewRoot.scale.copy(saved.s);
      viewRoot.updateMatrixWorld(true);
    }
  }

  _useFit({ geo, mask, W: w, H: h }) {
    this.maskCanvas = mask;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
      // A resized canvas needs a fresh texture object.
      this.texture.dispose();
      this.texture = new THREE.CanvasTexture(this.canvas);
      this.texture.colorSpace = THREE.SRGBColorSpace;
      this.texture.anisotropy = 4;
      this.material.map = this.texture;
    }
    this.W = w;
    this.H = h;
    this.mesh.geometry = geo;
    this.mesh.position.set(0, 0, 0);
    this.mesh.rotation.set(0, 0, 0);
    this.mesh.scale.set(1, 1, 1);
    this.mesh.visible = true;
  }

  /** Visor-shaped alpha mask at the face canvas's resolution, with soft edges. mask rows run bottom-up. */
  _buildMask(mask, mx, my) {
    this.W = 512;
    this.H = Math.max(140, Math.min(420, Math.round(512 * my / mx)));
    const small = document.createElement('canvas');
    small.width = mx;
    small.height = my;
    const sg = small.getContext('2d');
    const img = sg.createImageData(mx, my);
    for (let j = 0; j < my; j++) {
      for (let i = 0; i < mx; i++) {
        const a = mask[j * mx + i] ? 255 : 0;
        const o = ((my - 1 - j) * mx + i) * 4;          // flip: canvas rows run top-down
        img.data[o] = img.data[o + 1] = img.data[o + 2] = 255;
        img.data[o + 3] = a;
      }
    }
    sg.putImageData(img, 0, 0);
    const big = document.createElement('canvas');
    big.width = this.W;
    big.height = this.H;
    const bg = big.getContext('2d');
    bg.imageSmoothingEnabled = true;
    bg.filter = 'blur(4px)';
    bg.drawImage(small, 0, 0, this.W, this.H);
    this.maskCanvas = big;
  }

  _fit(wrapper) {
    const meshes = [];
    wrapper.traverse((c) => { if (c.isMesh && c !== this.mesh) meshes.push(c); });
    if (!meshes.length) return null;
    const box = new THREE.Box3();
    meshes.forEach((m) => box.expandByObject(m));
    const size = box.getSize(new THREE.Vector3());
    const mat = meshes[0].material;

    // The visor glass is the only surface that is both non-metallic AND dark. Either test alone fails on one
    // theme: on the dark robot the metal body is as dark as the glass; on the white robot the paint isn't metal.
    const metal = texSampler(mat.metalnessMap, 2);
    const lum = texSampler(mat.map, -1);
    const isGlass = (uv) => (metal ? metal(uv) < 0.15 && lum(uv) < 0.35 : lum(uv) < 0.11);

    const ray = new THREE.Raycaster();
    const dir = new THREE.Vector3(0, 0, -1);
    const zStart = box.max.z + 1;
    const hitAt = (x, y) => {
      ray.set(new THREE.Vector3(x, y, zStart), dir);
      return ray.intersectObjects(meshes, false)[0] || null;
    };

    // 1. Grid scan of the upper 60% of the robot: which cells are glass?
    const N = 64;
    const gx0 = box.min.x, gy0 = box.min.y + size.y * 0.4, cw = size.x / N, ch = size.y * 0.6 / N;
    const cell = new Uint8Array(N * N);
    for (let iy = 0; iy < N; iy++) {
      for (let ix = 0; ix < N; ix++) {
        const h = hitAt(gx0 + (ix + 0.5) * cw, gy0 + (iy + 0.5) * ch);
        if (h && h.uv && isGlass(h.uv)) cell[iy * N + ix] = 1;
      }
    }
    // 2. The visor is the largest connected glass region.
    const comp = largestComponent(cell, N);
    if (comp.length < 30) return null;
    let cx0 = N, cx1 = 0, cy0 = N, cy1 = 0;
    for (const k of comp) {
      const ix = k % N, iy = (k / N) | 0;
      cx0 = Math.min(cx0, ix); cx1 = Math.max(cx1, ix); cy0 = Math.min(cy0, iy); cy1 = Math.max(cy1, iy);
    }
    const x0 = gx0 + cx0 * cw, x1 = gx0 + (cx1 + 1) * cw, y0 = gy0 + cy0 * ch, y1 = gy0 + (cy1 + 1) * ch;

    // 3. Fine mask of the visor's outline inside that box, eroded a little so the face stays inside the rim.
    const MX = 96, MY = Math.max(24, Math.round(MX * (y1 - y0) / (x1 - x0)));
    const mask = new Uint8Array(MX * MY);
    for (let j = 0; j < MY; j++) {
      for (let i = 0; i < MX; i++) {
        const h = hitAt(x0 + (i + 0.5) / MX * (x1 - x0), y0 + (j + 0.5) / MY * (y1 - y0));
        if (h && h.uv && isGlass(h.uv)) mask[j * MX + i] = 1;
      }
    }
    const kept = largestComponent(mask, MX, MY);
    const solid = new Uint8Array(MX * MY);
    for (const k of kept) solid[k] = 1;
    // The texture's painted eyes aren't "glass", so they'd be holes: fill everything the visor encloses.
    this._buildMask(erode(fillHoles(solid, MX, MY), MX, MY), MX, MY);

    // 4. Project a grid onto the visor surface -> face geometry in wrapper-local space.
    const GX = 40, GY = Math.max(12, Math.round(GX * (y1 - y0) / (x1 - x0)));
    const inv = wrapper.matrixWorld.clone().invert();
    const positions = [];
    const uvs = [];
    let lastZ = box.max.z;
    for (let j = 0; j <= GY; j++) {
      for (let i = 0; i <= GX; i++) {
        const u = i / GX, v = j / GY;
        const x = x0 + (x1 - x0) * u;
        const y = y0 + (y1 - y0) * v;
        const h = hitAt(x, y);
        const p = h ? h.point.clone() : new THREE.Vector3(x, y, lastZ);
        lastZ = p.z;
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
    const CW = this.canvas.width, CH = this.canvas.height;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = 'source-over';
    g.clearRect(0, 0, CW, CH);

    // Glass fills the whole visor, opaque so the texture's painted eyes never show through.
    const bg = g.createRadialGradient(CW * 0.45, CH * 0.35, 10, CW / 2, CH / 2, CW * 0.6);
    bg.addColorStop(0, '#0e1c27');
    bg.addColorStop(1, '#02060a');
    g.fillStyle = bg;
    g.fillRect(0, 0, CW, CH);
    // Scanlines + a soft reflection streak across the glass.
    g.globalAlpha = 0.06;
    g.fillStyle = color;
    for (let y = 0; y < CH; y += 6) g.fillRect(0, y, CW, 2);
    g.globalAlpha = 0.08;
    g.fillStyle = '#ffffff';
    g.beginPath();
    g.ellipse(CW * 0.3, CH * 0.2, CW * 0.26, CH * 0.08, -0.15, 0, Math.PI * 2);
    g.fill();
    g.globalAlpha = 1;

    // The face itself is laid out on a 512x288 design, scaled uniformly into the visor and centred.
    const s = Math.min(CW / W * 1.05, CH / H * 1.25);
    g.save();
    g.translate((CW - W * s) / 2, (CH - H * s) / 2);
    g.scale(s, s);
    const blinkAmt = this.blinking ? (this.blink <= 1 ? this.blink : 2 - this.blink) : 0;
    const gx = this.gazeX * 22;
    const gy = -this.gazeY * 12;
    const eyeY = H * 0.42 + gy;
    const sep = W * 0.2;
    g.shadowColor = color;
    g.shadowBlur = 22;
    g.fillStyle = color;
    g.strokeStyle = color;
    g.lineCap = 'round';
    for (const side of [-1, 1]) this.drawEye(g, W / 2 + side * sep + gx, eyeY, side, blinkAmt, e);
    this.drawMouth(g, W / 2 + gx * 0.5, H * 0.76 + gy * 0.5, e, color);
    g.restore();

    // Cut everything to the visor's real outline (soft edge), so it reads as the visor lighting up.
    if (this.maskCanvas) {
      g.globalCompositeOperation = 'destination-in';
      g.drawImage(this.maskCanvas, 0, 0, CW, CH);
      g.globalCompositeOperation = 'source-over';
    }
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

/** Channel sampler for a texture (channel -1 = luminance). Returns (uv) -> 0..1, or null if no texture. */
function texSampler(tex, channel) {
  if (!tex || !tex.image) return null;
  const S = 256;
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(tex.image, 0, 0, S, S);
  const d = g.getImageData(0, 0, S, S).data;
  return (uv) => {
    const u = ((uv.x % 1) + 1) % 1;
    const v = ((uv.y % 1) + 1) % 1;
    const py = tex.flipY ? Math.floor((1 - v) * (S - 1)) : Math.floor(v * (S - 1));
    const i = (py * S + Math.floor(u * (S - 1))) * 4;
    return channel < 0 ? (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 255 : d[i + channel] / 255;
  };
}

/** Indices of the largest 4-connected set of 1-cells in a w x h grid. */
function largestComponent(grid, w, h = w) {
  const seen = new Uint8Array(grid.length);
  let best = [];
  for (let s = 0; s < grid.length; s++) {
    if (!grid[s] || seen[s]) continue;
    const comp = [];
    const stack = [s];
    seen[s] = 1;
    while (stack.length) {
      const k = stack.pop();
      comp.push(k);
      const x = k % w, y = (k / w) | 0;
      for (const [nx, ny] of [[x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]]) {
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        const n = ny * w + nx;
        if (grid[n] && !seen[n]) { seen[n] = 1; stack.push(n); }
      }
    }
    if (comp.length > best.length) best = comp;
  }
  return best;
}

/** Set every 0-cell that can't reach the border through 0-cells (i.e. enclosed holes) to 1. */
function fillHoles(grid, w, h) {
  const outside = new Uint8Array(grid.length);
  const stack = [];
  for (let x = 0; x < w; x++) stack.push(x, (h - 1) * w + x);
  for (let y = 0; y < h; y++) stack.push(y * w, y * w + w - 1);
  while (stack.length) {
    const k = stack.pop();
    if (grid[k] || outside[k]) continue;
    outside[k] = 1;
    const x = k % w, y = (k / w) | 0;
    if (x > 0) stack.push(k - 1);
    if (x < w - 1) stack.push(k + 1);
    if (y > 0) stack.push(k - w);
    if (y < h - 1) stack.push(k + w);
  }
  const out = new Uint8Array(grid.length);
  for (let k = 0; k < grid.length; k++) out[k] = outside[k] ? 0 : 1;
  return out;
}

function erode(grid, w, h) {
  const out = new Uint8Array(grid.length);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const k = y * w + x;
      out[k] = grid[k] && grid[k - 1] && grid[k + 1] && grid[k - w] && grid[k + w] ? 1 : 0;
    }
  }
  return out;
}

function median(a) {
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)];
}

function pct(sorted, q) {
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(q * sorted.length)))];
}
