import * as THREE from 'three';

/**
 * Procedural Jetpack Thruster Flame System
 * Creates dual high-velocity rocket exhaust plumes with:
 * - Inner plasma core (intense blue/white-hot or orange core)
 * - Outer turbulent flame envelope with additive blending
 * - Particle spark & ember emitter trailing behind in velocity direction
 * - Dynamic flickering point light casting light onto robot back
 */
export class JetpackFire {
  constructor(scene, options = {}) {
    this.scene = scene;
    this.options = {
      colorMode: options.colorMode || 'orange', // 'orange' | 'cyan' | 'purple'
      scale: options.scale || 1.0,
      intensity: options.intensity || 1.0,
      ...options
    };

    this.group = new THREE.Group();
    this.group.name = "JetpackFireGroup";

    // Flame color palettes
    this.palettes = {
      orange: {
        inner: new THREE.Color(0xffeedd),
        mid: new THREE.Color(0xff8811),
        outer: new THREE.Color(0xff2200),
        light: 0xff7711
      },
      cyan: {
        inner: new THREE.Color(0xeeffff),
        mid: new THREE.Color(0x00d4ff),
        outer: new THREE.Color(0x0044ff),
        light: 0x00b4ff
      },
      purple: {
        inner: new THREE.Color(0xffe0ff),
        mid: new THREE.Color(0xcc22ff),
        outer: new THREE.Color(0x6600cc),
        light: 0xaa22ff
      }
    };

    this.plumes = [];
    this.particles = null;
    this.particleCount = 90;
    this.particleData = [];

    this.initFlames();
    this.initParticles();
    this.initLight();

    this.scene.add(this.group);
  }

  initFlames() {
    const pal = this.palettes[this.options.colorMode] || this.palettes.orange;

    // Dual thrusters: left and right
    const nozzleOffsets = [
      new THREE.Vector3(-0.32, -0.42, -0.36),
      new THREE.Vector3(0.32, -0.42, -0.36)
    ];

    nozzleOffsets.forEach((pos, idx) => {
      const plumeGroup = new THREE.Group();
      plumeGroup.position.copy(pos);

      // 1. Inner core cone (white-hot plasma)
      const coreGeo = new THREE.ConeGeometry(0.09, 0.48, 16, 1, true);
      coreGeo.translate(0, -0.24, 0);
      coreGeo.rotateX(Math.PI); // Point flame downward/backward
      const coreMat = new THREE.MeshBasicMaterial({
        color: pal.inner,
        transparent: true,
        opacity: 0.95,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      });
      const coreMesh = new THREE.Mesh(coreGeo, coreMat);

      // 2. Middle intense flame
      const midGeo = new THREE.ConeGeometry(0.18, 0.85, 16, 1, true);
      midGeo.translate(0, -0.42, 0);
      midGeo.rotateX(Math.PI);
      const midMat = new THREE.MeshBasicMaterial({
        color: pal.mid,
        transparent: true,
        opacity: 0.75,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      });
      const midMesh = new THREE.Mesh(midGeo, midMat);

      // 3. Outer turbulent flame envelope
      const outerGeo = new THREE.ConeGeometry(0.28, 1.25, 16, 1, true);
      outerGeo.translate(0, -0.62, 0);
      outerGeo.rotateX(Math.PI);
      const outerMat = new THREE.MeshBasicMaterial({
        color: pal.outer,
        transparent: true,
        opacity: 0.45,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      });
      const outerMesh = new THREE.Mesh(outerGeo, outerMat);

      plumeGroup.add(outerMesh);
      plumeGroup.add(midMesh);
      plumeGroup.add(coreMesh);

      this.group.add(plumeGroup);

      this.plumes.push({
        group: plumeGroup,
        core: coreMesh,
        mid: midMesh,
        outer: outerMesh,
        basePos: pos.clone(),
        baseScale: 1.0
      });
    });
  }

  initParticles() {
    const pal = this.palettes[this.options.colorMode] || this.palettes.orange;
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(this.particleCount * 3);
    const colors = new Float32Array(this.particleCount * 3);
    const sizes = new Float32Array(this.particleCount);

    for (let i = 0; i < this.particleCount; i++) {
      // Spawn near one of the two nozzles
      const nozzleX = (i % 2 === 0 ? -0.32 : 0.32);
      positions[i * 3 + 0] = nozzleX + (Math.random() - 0.5) * 0.1;
      positions[i * 3 + 1] = -0.45 - Math.random() * 0.3;
      positions[i * 3 + 2] = -0.36 + (Math.random() - 0.5) * 0.1;

      colors[i * 3 + 0] = pal.mid.r;
      colors[i * 3 + 1] = pal.mid.g;
      colors[i * 3 + 2] = pal.mid.b;

      sizes[i] = 4.0 + Math.random() * 6.0;

      this.particleData.push({
        vx: (Math.random() - 0.5) * 0.08,
        vy: -0.15 - Math.random() * 0.25, // shooting down/back
        vz: -0.05 - Math.random() * 0.15,
        life: Math.random(),
        decay: 0.02 + Math.random() * 0.03,
        baseX: nozzleX
      });
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

    // Particle texture (soft radial glow)
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    const grad = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.3, 'rgba(255,180,50,0.8)');
    grad.addColorStop(0.7, 'rgba(255,50,0,0.3)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 64, 64);
    const pTexture = new THREE.CanvasTexture(canvas);

    const mat = new THREE.PointsMaterial({
      size: 0.16,
      map: pTexture,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      vertexColors: true
    });

    this.particles = new THREE.Points(geo, mat);
    this.group.add(this.particles);
  }

  initLight() {
    const pal = this.palettes[this.options.colorMode] || this.palettes.orange;
    this.thrusterLight = new THREE.PointLight(pal.light, 3.5, 4.0);
    this.thrusterLight.position.set(0, -0.6, -0.5);
    this.group.add(this.thrusterLight);
  }

  setColorMode(mode) {
    if (!this.palettes[mode]) return;
    this.options.colorMode = mode;
    const pal = this.palettes[mode];

    this.plumes.forEach(p => {
      p.core.material.color.copy(pal.inner);
      p.mid.material.color.copy(pal.mid);
      p.outer.material.color.copy(pal.outer);
    });

    if (this.thrusterLight) {
      this.thrusterLight.color.set(pal.light);
    }
  }

  setIntensity(multiplier) {
    this.options.intensity = Math.max(0.1, multiplier);
  }

  attachTo(parentMesh) {
    if (this.group.parent) {
      this.group.parent.remove(this.group);
    }
    parentMesh.add(this.group);
  }

  update(time, motionSpeed = 1.0) {
    const intensity = this.options.intensity * motionSpeed;
    const pal = this.palettes[this.options.colorMode] || this.palettes.orange;

    // 1. Plume flickering & turbulence
    this.plumes.forEach((p, idx) => {
      const flicker1 = Math.sin(time * 35 + idx * 4.2) * 0.15;
      const flicker2 = Math.cos(time * 50 + idx * 2.7) * 0.1;
      const totalLen = (1.0 + flicker1 + flicker2) * intensity;

      p.core.scale.set(1.0 + flicker2 * 0.5, totalLen, 1.0 + flicker2 * 0.5);
      p.mid.scale.set(1.0 + flicker1 * 0.4, totalLen * 1.1, 1.0 + flicker1 * 0.4);
      p.outer.scale.set(1.0 + flicker2 * 0.3, totalLen * 1.25, 1.0 + flicker2 * 0.3);

      // Subtle thrust angle turbulence
      p.group.rotation.x = Math.sin(time * 18 + idx) * 0.08;
      p.group.rotation.z = Math.cos(time * 22 + idx) * 0.08;
    });

    // 2. Light flicker
    if (this.thrusterLight) {
      this.thrusterLight.intensity = (3.0 + Math.sin(time * 40) * 1.2) * intensity;
    }

    // 3. Update exhaust particles
    if (this.particles) {
      const posAttr = this.particles.geometry.attributes.position;
      const colAttr = this.particles.geometry.attributes.color;

      for (let i = 0; i < this.particleCount; i++) {
        const pd = this.particleData[i];
        pd.life -= pd.decay;

        if (pd.life <= 0) {
          // Reset particle to nozzle
          pd.life = 1.0;
          posAttr.setXYZ(
            i,
            pd.baseX + (Math.random() - 0.5) * 0.12,
            -0.45,
            -0.36 + (Math.random() - 0.5) * 0.12
          );
          pd.vx = (Math.random() - 0.5) * 0.08;
          pd.vy = (-0.12 - Math.random() * 0.28) * intensity;
          pd.vz = (-0.05 - Math.random() * 0.18) * intensity;
        } else {
          // Move particle
          const px = posAttr.getX(i) + pd.vx;
          const py = posAttr.getY(i) + pd.vy;
          const pz = posAttr.getZ(i) + pd.vz;
          posAttr.setXYZ(i, px, py, pz);

          // Color fade from mid to outer to dark
          const alpha = pd.life;
          colAttr.setXYZ(
            i,
            pal.mid.r * alpha,
            pal.mid.g * (alpha * 0.8),
            pal.mid.b * (alpha * 0.5)
          );
        }
      }
      posAttr.needsUpdate = true;
      colAttr.needsUpdate = true;
    }
  }
}
