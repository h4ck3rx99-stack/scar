import * as THREE from 'three';

/**
 * EyeVisorSystem: Dynamic Expressive Digital Eyes
 * Features:
 * - 6 Expressive Moods: 'normal', 'happy', 'scared', 'angry', 'sad', 'curious', 'dizzy'
 * - Natural Blinking Cycle (Every 3-5 seconds with occasional double-blinks)
 * - Gaze Tracking (Eyes follow cursor position on screen)
 * - Shivering effect when scared
 * - High-resolution luminous emissive visor display
 */
export class EyeVisorSystem {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 512;
    this.canvas.height = 256;
    this.ctx = this.canvas.getContext('2d');

    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.minFilter = THREE.LinearFilter;
    this.texture.magFilter = THREE.LinearFilter;

    // Emissive Visor Mesh attached to the robot's face
    const geo = new THREE.PlaneGeometry(0.38, 0.20);
    const mat = new THREE.MeshBasicMaterial({
      map: this.texture,
      transparent: true,
      opacity: 0.96,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      depthWrite: false
    });
    this.mesh = new THREE.Mesh(geo, mat);
    this.mesh.name = "RobotEyeVisor";
    // Positioned right over the face visor of the robot
    this.mesh.position.set(-0.04, 0.36, 0.48);
    this.mesh.rotation.y = 0;

    // State
    this.currentExpression = 'normal';
    this.targetExpression = 'normal';
    this.blinkProgress = 0; // 0 = fully open, 1 = fully closed
    this.isBlinking = false;
    this.blinkTimer = 0;
    this.nextBlinkTime = 2.5 + Math.random() * 3.0;

    // Gaze direction (-1 to 1)
    this.gazeX = 0;
    this.gazeY = 0;
    this.targetGazeX = 0;
    this.targetGazeY = 0;

    // Scared shiver offset
    this.shiverX = 0;
    this.shiverY = 0;

    // Spiral angle for dizzy
    this.spiralAngle = 0;

    this.renderCanvas();
  }

  attachTo(parentGroup) {
    if (this.mesh.parent) {
      this.mesh.parent.remove(this.mesh);
    }
    parentGroup.add(this.mesh);
  }

  setExpression(name) {
    this.currentExpression = name;
    this.renderCanvas();
  }

  setGaze(x, y) {
    this.targetGazeX = Math.max(-1, Math.min(1, x));
    this.targetGazeY = Math.max(-1, Math.min(1, y));
  }

  triggerBlink() {
    this.isBlinking = true;
    this.blinkProgress = 0;
  }

  update(delta, time) {
    // 1. Blinking Logic
    this.blinkTimer += delta;
    if (this.blinkTimer >= this.nextBlinkTime && !this.isBlinking) {
      this.triggerBlink();
      this.blinkTimer = 0;
      this.nextBlinkTime = 2.5 + Math.random() * 3.5;
    }

    if (this.isBlinking) {
      this.blinkProgress += delta * 12.0; // Fast blink
      if (this.blinkProgress >= 2.0) {
        this.blinkProgress = 0;
        this.isBlinking = false;
      }
    }

    // 2. Smooth Gaze Interpolation
    this.gazeX += (this.targetGazeX - this.gazeX) * 0.15;
    this.gazeY += (this.targetGazeY - this.gazeY) * 0.15;

    // 3. Scared Shiver
    if (this.currentExpression === 'scared') {
      this.shiverX = (Math.random() - 0.5) * 6;
      this.shiverY = (Math.random() - 0.5) * 6;
    } else {
      this.shiverX = 0;
      this.shiverY = 0;
    }

    // 4. Dizzy Spiral Rotation
    if (this.currentExpression === 'dizzy') {
      this.spiralAngle += delta * 8.0;
    }

    this.renderCanvas();
    this.texture.needsUpdate = true;
  }

  renderCanvas() {
    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;

    ctx.clearRect(0, 0, w, h);

    // Subtle dark visor backing
    ctx.fillStyle = 'rgba(5, 10, 18, 0.4)';
    ctx.beginPath();
    ctx.roundRect(40, 30, w - 80, h - 60, 30);
    ctx.fill();

    // Eye centers
    const leftX = 180 + this.gazeX * 24 + this.shiverX;
    const rightX = 332 + this.gazeX * 24 + this.shiverX;
    const eyeY = 128 - this.gazeY * 18 + this.shiverY;

    // Compute blink factor (0 = open, 1 = shut)
    let blink = 0;
    if (this.isBlinking) {
      blink = this.blinkProgress <= 1.0 ? this.blinkProgress : 2.0 - this.blinkProgress;
    }

    // Draw Left and Right Eyes
    this.drawEye(ctx, leftX, eyeY, false, blink);
    this.drawEye(ctx, rightX, eyeY, true, blink);
  }

  drawEye(ctx, cx, cy, isRight, blinkFactor) {
    ctx.save();
    ctx.translate(cx, cy);

    const expr = this.currentExpression;
    const scaleY = Math.max(0.04, 1.0 - blinkFactor * 0.96);

    // Color palettes by expression
    let mainColor = '#00f0ff'; // Neon Cyan default
    let glowColor = 'rgba(0, 240, 255, 0.6)';

    if (expr === 'happy') {
      mainColor = '#00ffaa';
      glowColor = 'rgba(0, 255, 170, 0.7)';
    } else if (expr === 'scared') {
      mainColor = '#ffffff';
      glowColor = 'rgba(255, 230, 80, 0.85)';
    } else if (expr === 'angry') {
      mainColor = '#ff3344';
      glowColor = 'rgba(255, 51, 68, 0.75)';
    } else if (expr === 'sad') {
      mainColor = '#3399ff';
      glowColor = 'rgba(51, 153, 255, 0.65)';
    } else if (expr === 'curious') {
      mainColor = '#ffea00';
      glowColor = 'rgba(255, 234, 0, 0.65)';
    } else if (expr === 'dizzy') {
      mainColor = '#cc44ff';
      glowColor = 'rgba(204, 68, 255, 0.7)';
    }

    ctx.shadowColor = glowColor;
    ctx.shadowBlur = 18;
    ctx.fillStyle = mainColor;
    ctx.strokeStyle = mainColor;

    ctx.scale(1.0, scaleY);

    switch (expr) {
      case 'happy': {
        // Cheerful curved upward arc (^_^)
        ctx.lineWidth = 14;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(0, 8, 38, Math.PI * 1.15, Math.PI * 1.85, false);
        ctx.stroke();

        // Cheek blush dot
        ctx.fillStyle = 'rgba(255, 100, 150, 0.5)';
        ctx.beginPath();
        ctx.arc(isRight ? 26 : -26, 32, 12, 0, Math.PI * 2);
        ctx.fill();
        break;
      }

      case 'scared': {
        // Enormous wide circular shocked eyes (O_O)
        ctx.lineWidth = 10;
        ctx.beginPath();
        ctx.arc(0, 0, 48, 0, Math.PI * 2);
        ctx.stroke();

        // Small trembling inner pupil
        ctx.beginPath();
        ctx.arc((Math.random() - 0.5) * 6, (Math.random() - 0.5) * 6, 14, 0, Math.PI * 2);
        ctx.fill();

        // Sweat droplet on outer side
        if (isRight) {
          ctx.fillStyle = '#00d4ff';
          ctx.beginPath();
          ctx.arc(58, -35, 8, 0, Math.PI * 2);
          ctx.fill();
        }
        break;
      }

      case 'angry': {
        // Sharp inward slanted wedge eyes (>_<)
        const slant = isRight ? -0.32 : 0.32;
        ctx.rotate(slant);
        ctx.beginPath();
        ctx.roundRect(-42, -18, 84, 36, 14);
        ctx.fill();

        // Sharp pupil slit
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(0, 0, 8, 0, Math.PI * 2);
        ctx.fill();
        break;
      }

      case 'sad': {
        // Drooping teary curved eyes (T_T)
        ctx.lineWidth = 12;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(0, -6, 36, Math.PI * 0.15, Math.PI * 0.85, false);
        ctx.stroke();

        // Tear drop
        ctx.fillStyle = '#00d4ff';
        ctx.beginPath();
        ctx.arc(isRight ? 18 : -18, 28, 9, 0, Math.PI * 2);
        ctx.fill();
        break;
      }

      case 'curious': {
        // One eye large and wide, other eye slightly squinted (o_O)
        const eyeSize = isRight ? 42 : 28;
        ctx.beginPath();
        ctx.arc(0, 0, eyeSize, 0, Math.PI * 2);
        ctx.fill();

        // Pupil highlight
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(-8, -8, 8, 0, Math.PI * 2);
        ctx.fill();
        break;
      }

      case 'dizzy': {
        // Hypnotic rotating spiral (@_@)
        ctx.lineWidth = 7;
        ctx.beginPath();
        for (let a = 0; a < Math.PI * 5; a += 0.15) {
          const r = a * 6;
          const x = Math.cos(a + this.spiralAngle) * r;
          const y = Math.sin(a + this.spiralAngle) * r;
          if (a === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        break;
      }

      case 'normal':
      default: {
        // Sleek futuristic robotic eye visor
        ctx.beginPath();
        ctx.roundRect(-36, -24, 72, 48, 18);
        ctx.fill();

        // Glowing white inner pupil looking at gaze
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(this.gazeX * 10, this.gazeY * 8, 12, 0, Math.PI * 2);
        ctx.fill();

        // Subtle tech bracket
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(0, 0, 42, isRight ? -Math.PI * 0.3 : Math.PI * 0.7, isRight ? Math.PI * 0.3 : Math.PI * 1.3);
        ctx.stroke();
        break;
      }
    }

    ctx.restore();
  }
}
