import * as THREE from 'three';

/**
 * 3D Coordinate Ruler & Numbered Axis System
 * Features:
 * - Color-coded Numbered Axes:
 *    * X Axis: Red (#ff3b5c) with tick marks & numbered sprites (-1.0 to +1.0)
 *    * Y Axis: Green (#00f076) with tick marks & numbered sprites (-1.0 to +1.0)
 *    * Z Axis: Blue (#00aaff) with tick marks & numbered sprites (-1.0 to +1.0)
 * - 3 Coordinate Grid Planes:
 *    * XY Plane (Front/Back)
 *    * YZ Plane (Side/Profile)
 *    * ZX Plane (Top/Floor)
 * - Dynamic 3D Target Crosshair with:
 *    * Concentric yellow pulse rings
 *    * Exact Coordinate Badge: [X: ..., Y: ..., Z: ...]
 *    * Real-time projection lines to X, Y, Z axes
 * - Raycast Click-to-Coordinate inspect
 */
export class CoordinateRuler {
  constructor(options = {}) {
    this.options = {
      visible: true,
      range: 1.0,
      step: 0.1,
      numberStep: 0.2,
      showAxes: true,
      showNumbers: true,
      showXY: true,
      showYZ: true,
      showZX: true,
      ...options
    };

    this.group = new THREE.Group();
    this.group.name = "CoordinateRulerSystem";

    this.axesGroup = new THREE.Group();
    this.numbersGroup = new THREE.Group();
    this.planesGroup = new THREE.Group();
    this.targetMarker = new THREE.Group();

    this.group.add(this.axesGroup);
    this.group.add(this.numbersGroup);
    this.group.add(this.planesGroup);
    this.group.add(this.targetMarker);

    this.gridPlanes = {};
    this.targetPos = new THREE.Vector3(-0.22, -0.46, 0.0);

    this.initAxes();
    this.initNumberedSprites();
    this.initGridPlanes();
    this.initTargetCrosshair();
  }

  // Create high-resolution billboard text sprite
  createTextSprite(text, color = '#ffffff', bgColor = 'rgba(10, 15, 26, 0.88)', borderColor = color) {
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 100;
    const ctx = canvas.getContext('2d');

    // Rounded badge background
    ctx.fillStyle = bgColor;
    ctx.beginPath();
    ctx.roundRect(10, 10, 236, 80, 18);
    ctx.fill();

    // Border
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 4;
    ctx.stroke();

    // Text
    ctx.fillStyle = color;
    ctx.font = 'bold 36px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 128, 50);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({
      map: texture,
      transparent: true,
      depthTest: false
    });

    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(0.24, 0.095, 1.0);
    return sprite;
  }

  initAxes() {
    const range = this.options.range;
    const step = this.options.step;

    // Line Materials
    const xMat = new THREE.LineBasicMaterial({ color: 0xff3b5c, linewidth: 3 });
    const yMat = new THREE.LineBasicMaterial({ color: 0x00f076, linewidth: 3 });
    const zMat = new THREE.LineBasicMaterial({ color: 0x00aaff, linewidth: 3 });

    // 1. X Axis (Red)
    const xGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-range, 0, 0),
      new THREE.Vector3(range, 0, 0)
    ]);
    this.axesGroup.add(new THREE.Line(xGeo, xMat));

    // 2. Y Axis (Green)
    const yGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, -range, 0),
      new THREE.Vector3(0, range, 0)
    ]);
    this.axesGroup.add(new THREE.Line(yGeo, yMat));

    // 3. Z Axis (Blue)
    const zGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, -range),
      new THREE.Vector3(0, 0, range)
    ]);
    this.axesGroup.add(new THREE.Line(zGeo, zMat));

    // 4. Tick Marks every 0.1 units
    const tickLen = 0.025;
    for (let v = -range; v <= range + 0.001; v += step) {
      const val = parseFloat(v.toFixed(2));
      if (Math.abs(val) < 0.001) continue;

      // X ticks (vertical on Y)
      const xTick = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(val, -tickLen, 0),
        new THREE.Vector3(val, tickLen, 0)
      ]);
      this.axesGroup.add(new THREE.Line(xTick, xMat));

      // Y ticks (horizontal on X)
      const yTick = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-tickLen, val, 0),
        new THREE.Vector3(tickLen, val, 0)
      ]);
      this.axesGroup.add(new THREE.Line(yTick, yMat));

      // Z ticks (vertical on Y)
      const zTick = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, -tickLen, val),
        new THREE.Vector3(0, tickLen, val)
      ]);
      this.axesGroup.add(new THREE.Line(zTick, zMat));
    }

    // 5. Positive & Negative End Labels
    const offset = 0.14;
    const pxLabel = this.createTextSprite('+X (Red)', '#ff3b5c', 'rgba(40,10,15,0.92)');
    pxLabel.position.set(range + offset, 0, 0);
    this.axesGroup.add(pxLabel);

    const nxLabel = this.createTextSprite('-X', '#ff3b5c', 'rgba(40,10,15,0.85)');
    nxLabel.position.set(-range - offset, 0, 0);
    this.axesGroup.add(nxLabel);

    const pyLabel = this.createTextSprite('+Y (Green)', '#00f076', 'rgba(10,40,20,0.92)');
    pyLabel.position.set(0, range + offset, 0);
    this.axesGroup.add(pyLabel);

    const nyLabel = this.createTextSprite('-Y', '#00f076', 'rgba(10,40,20,0.85)');
    nyLabel.position.set(0, -range - offset, 0);
    this.axesGroup.add(nyLabel);

    const pzLabel = this.createTextSprite('+Z (Blue)', '#00aaff', 'rgba(10,25,50,0.92)');
    pzLabel.position.set(0, 0, range + offset);
    this.axesGroup.add(pzLabel);

    const nzLabel = this.createTextSprite('-Z', '#00aaff', 'rgba(10,25,50,0.85)');
    nzLabel.position.set(0, 0, -range - offset);
    this.axesGroup.add(nzLabel);
  }

  initNumberedSprites() {
    const range = this.options.range;
    const numberStep = this.options.numberStep;

    // Origin 0.0
    const originSprite = this.createTextSprite('0.0', '#ffffff', 'rgba(30,30,40,0.9)');
    originSprite.position.set(0.06, 0.06, 0);
    originSprite.scale.set(0.18, 0.075, 1.0);
    this.numbersGroup.add(originSprite);

    for (let v = -range; v <= range + 0.001; v += numberStep) {
      const val = parseFloat(v.toFixed(2));
      if (Math.abs(val) < 0.001) continue;

      const sign = val > 0 ? '+' : '';
      const text = `${sign}${val.toFixed(1)}`;

      // X Axis Number
      const xSprite = this.createTextSprite(text, '#ff3b5c', 'rgba(25, 10, 15, 0.88)');
      xSprite.position.set(val, -0.07, 0);
      this.numbersGroup.add(xSprite);

      // Y Axis Number
      const ySprite = this.createTextSprite(text, '#00f076', 'rgba(10, 30, 18, 0.88)');
      ySprite.position.set(0.08, val, 0);
      this.numbersGroup.add(ySprite);

      // Z Axis Number
      const zSprite = this.createTextSprite(text, '#00aaff', 'rgba(10, 20, 38, 0.88)');
      zSprite.position.set(0, -0.07, val);
      this.numbersGroup.add(zSprite);
    }
  }

  initGridPlanes() {
    const size = 2.0;
    const divisions = 20;

    // 1. ZX Plane (Horizontal floor / top-down plane at Y = 0)
    const zxGrid = new THREE.GridHelper(size, divisions, 0xff3b5c, 0x00aaff);
    zxGrid.position.set(0, 0, 0);
    zxGrid.material.transparent = true;
    zxGrid.material.opacity = 0.28;
    this.planesGroup.add(zxGrid);
    this.gridPlanes.zx = zxGrid;

    const zxLabel = this.createTextSprite('ZX Plane (Floor/Top)', '#e0a020', 'rgba(30,20,5,0.85)');
    zxLabel.position.set(size * 0.5 + 0.1, 0, size * 0.5 + 0.1);
    zxGrid.add(zxLabel);

    // 2. XY Plane (Vertical front-back plane at Z = 0)
    const xyGrid = new THREE.GridHelper(size, divisions, 0xff3b5c, 0x00f076);
    xyGrid.rotation.x = Math.PI / 2;
    xyGrid.position.set(0, 0, 0);
    xyGrid.material.transparent = true;
    xyGrid.material.opacity = 0.28;
    this.planesGroup.add(xyGrid);
    this.gridPlanes.xy = xyGrid;

    const xyLabel = this.createTextSprite('XY Plane (Front/Back)', '#00f076', 'rgba(5,30,15,0.85)');
    xyLabel.position.set(size * 0.5 + 0.1, size * 0.5 + 0.1, 0);
    xyGrid.add(xyLabel);

    // 3. YZ Plane (Vertical side-profile plane at X = 0)
    const yzGrid = new THREE.GridHelper(size, divisions, 0x00f076, 0x00aaff);
    yzGrid.rotation.z = Math.PI / 2;
    yzGrid.position.set(0, 0, 0);
    yzGrid.material.transparent = true;
    yzGrid.material.opacity = 0.28;
    this.planesGroup.add(yzGrid);
    this.gridPlanes.yz = yzGrid;

    const yzLabel = this.createTextSprite('YZ Plane (Side/Profile)', '#00aaff', 'rgba(5,20,35,0.85)');
    yzLabel.position.set(0, size * 0.5 + 0.1, size * 0.5 + 0.1);
    yzGrid.add(yzLabel);
  }

  initTargetCrosshair() {
    this.targetMarker.name = "CoordinateTargetCrosshair";

    // 1. Center pulsing target sphere
    const sphereGeo = new THREE.SphereGeometry(0.028, 16, 16);
    const sphereMat = new THREE.MeshBasicMaterial({
      color: 0xffea00,
      depthTest: false
    });
    this.targetSphere = new THREE.Mesh(sphereGeo, sphereMat);
    this.targetMarker.add(this.targetSphere);

    // 2. Tri-axis targeting rings (XY, XZ, YZ)
    const ringGeo = new THREE.RingGeometry(0.065, 0.08, 32);
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0xffea00,
      side: THREE.DoubleSide,
      depthTest: false,
      transparent: true,
      opacity: 0.95
    });

    this.ringXY = new THREE.Mesh(ringGeo, ringMat);
    this.ringXZ = new THREE.Mesh(ringGeo, ringMat);
    this.ringXZ.rotation.x = Math.PI / 2;
    this.ringYZ = new THREE.Mesh(ringGeo, ringMat);
    this.ringYZ.rotation.y = Math.PI / 2;

    this.targetMarker.add(this.ringXY);
    this.targetMarker.add(this.ringXZ);
    this.targetMarker.add(this.ringYZ);

    // 3. Coordinate label sprite right above the target
    this.targetLabel = null;

    // 4. Real-time projection lines to X, Y, Z axes
    const lineMatX = new THREE.LineBasicMaterial({ color: 0xff3b5c, depthTest: false, transparent: true, opacity: 0.85 });
    const lineMatY = new THREE.LineBasicMaterial({ color: 0x00f076, depthTest: false, transparent: true, opacity: 0.85 });
    const lineMatZ = new THREE.LineBasicMaterial({ color: 0x00aaff, depthTest: false, transparent: true, opacity: 0.85 });

    this.projLineX = new THREE.Line(new THREE.BufferGeometry(), lineMatX);
    this.projLineY = new THREE.Line(new THREE.BufferGeometry(), lineMatY);
    this.projLineZ = new THREE.Line(new THREE.BufferGeometry(), lineMatZ);

    this.group.add(this.projLineX);
    this.group.add(this.projLineY);
    this.group.add(this.projLineZ);

    this.setTargetPosition(this.targetPos.x, this.targetPos.y, this.targetPos.z);
  }

  setTargetPosition(x, y, z) {
    this.targetPos.set(x, y, z);
    this.targetMarker.position.set(x, y, z);

    // Update coordinate badge
    if (this.targetLabel) {
      this.targetMarker.remove(this.targetLabel);
      if (this.targetLabel.material.map) this.targetLabel.material.map.dispose();
      this.targetLabel.material.dispose();
    }

    const badgeText = `X: ${x.toFixed(2)}  Y: ${y.toFixed(2)}  Z: ${z.toFixed(2)}`;
    this.targetLabel = this.createTextSprite(badgeText, '#ffea00', 'rgba(10, 15, 25, 0.95)', '#ffea00');
    this.targetLabel.position.set(0, 0.12, 0);
    this.targetLabel.scale.set(0.38, 0.15, 1.0);
    this.targetMarker.add(this.targetLabel);

    // Update projection lines:
    // Line to X-axis (drops to [x, 0, 0])
    this.projLineX.geometry.dispose();
    this.projLineX.geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(x, y, z),
      new THREE.Vector3(x, 0, 0)
    ]);

    // Line to Y-axis (drops to [0, y, 0])
    this.projLineY.geometry.dispose();
    this.projLineY.geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(x, y, z),
      new THREE.Vector3(0, y, 0)
    ]);

    // Line to Z-axis (drops to [0, 0, z])
    this.projLineZ.geometry.dispose();
    this.projLineZ.geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(x, y, z),
      new THREE.Vector3(0, 0, z)
    ]);
  }

  attachTo(parentGroup) {
    if (this.group.parent) {
      this.group.parent.remove(this.group);
    }
    parentGroup.add(this.group);
  }

  setVisible(visible) {
    this.options.visible = visible;
    this.group.visible = visible;
  }

  setAxesVisible(visible) {
    this.options.showAxes = visible;
    this.axesGroup.visible = visible;
  }

  setNumbersVisible(visible) {
    this.options.showNumbers = visible;
    this.numbersGroup.visible = visible;
  }

  setTargetVisible(visible) {
    this.targetMarker.visible = visible;
    this.projLineX.visible = visible;
    this.projLineY.visible = visible;
    this.projLineZ.visible = visible;
  }

  setGridPlaneVisible(planeName, visible) {
    if (this.gridPlanes[planeName]) {
      this.gridPlanes[planeName].visible = visible;
    }
  }

  toggleGridPlane(planeName) {
    if (this.gridPlanes[planeName]) {
      const nextState = !this.gridPlanes[planeName].visible;
      this.gridPlanes[planeName].visible = nextState;
      return nextState;
    }
    return false;
  }
}
