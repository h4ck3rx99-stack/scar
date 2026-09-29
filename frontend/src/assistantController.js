import * as THREE from 'three';
import gsap from 'gsap';
import { ClothCard } from './clothProxy.js';
import { soundEngine } from './soundEngine.js';

const LOCAL_SLEEP_MS = 180000; // used only while the brain is offline; otherwise the brain decides

/**
 * The avatar's "Jarvis" behaviour layer: turns brain events into motion.
 * modes: entry | roam | attentive | flying | tapping | carrying | to_sleep | sleeping | waking
 */
export class AssistantController {
  constructor(ctx) {
    // ctx: { scene, camera, space, avatarRoot, roamEngine, animController, mischiefEngine,
    //        speechBubble, eyeVisor, statusRing, bridge, avatarHeightPx(), setTheme(mode) }
    Object.assign(this, ctx);
    this.mode = 'entry';
    this.voice = 'idle';
    this.level = 0;
    this.card = null;
    this.sleepEdge = null;
    this.breathTween = null;
    this.lastLocalActivity = performance.now();
    this.idleTimer = null;
  }

  /** True while this controller owns the avatar's transform (roam/mischief must not move it). */
  get busy() {
    return ['entry', 'flying', 'tapping', 'carrying', 'to_sleep', 'sleeping', 'waking'].includes(this.mode);
  }

  get frameRate() {
    if (this.mode === 'sleeping') return 12;
    if (this.mode === 'roam' && this.voice === 'idle') return 40;
    return 60;
  }

  bind() {
    const b = this.bridge;
    b.on('summon', (m) => this.summon(m));
    b.on('voice_state', (m) => this.onVoiceState(m));
    b.on('say', (m) => this.speechBubble.show(m.text, {
      chime: false, type: 'normal', duration: Math.min(9000, Math.max(2500, 1200 + m.text.length * 55)),
    }));
    b.on('level', (m) => { this.level = m.value; });
    b.on('fly_to', (m) => this.flyTo(m));
    b.on('proxy_start', (m) => this.proxyStart(m));
    b.on('proxy_end', (m) => this.proxyEnd(m));
    b.on('theme', (m) => this.setTheme(m.mode));
    b.on('_connected', () => this.statusRing.set(this.voice));
    b.on('_disconnected', () => { this.voice = 'idle'; this.statusRing.set('offline'); });
    this.statusRing.set('offline');
  }

  // ------------------------------------------------------------------ helpers
  kill() {
    clearTimeout(this.resumeTimer);
    gsap.killTweensOf(this.avatarRoot.position);
    gsap.killTweensOf(this.avatarRoot.rotation);
    gsap.killTweensOf(this.avatarRoot.scale);
    this.animController.stopCurrent();
    if (this.mischiefEngine.isBusy) this.mischiefEngine.isBusy = false;
    if (this.breathTween) { this.breathTween.kill(); this.breathTween = null; }
    this.avatarRoot.scale.setScalar(1);
  }

  /** Stop wandering and hover in place right here (re-anchors the idle hover to the current spot). */
  hold() {
    this.roamEngine.setEnabled(false);
    this.mischiefEngine.enabled = false;
  }

  pauseRoam() {
    this.roamEngine.setEnabled(false);
    this.mischiefEngine.enabled = false;
  }

  resumeRoam(delay = 0) {
    clearTimeout(this.resumeTimer);
    this.resumeTimer = setTimeout(() => {
      if (this.busy) return;
      if (!['idle'].includes(this.voice)) { this.mode = 'attentive'; return; }
      this.mode = 'roam';
      this.eyeVisor.setExpression('normal');
      this.roamEngine.currentPos.copy(this.avatarRoot.position);
      this.roamEngine.setEnabled(true);
      this.mischiefEngine.enabled = true;
    }, delay);
  }

  faceUser(duration = 0.45) {
    gsap.to(this.avatarRoot.rotation, { x: 0, y: 0, z: 0, duration, ease: 'power2.out' });
  }

  noteActivity() {
    this.lastLocalActivity = performance.now();
  }

  // ------------------------------------------------------------ entries/sleep
  playRandomEntry(index) {
    const entries = ['playEntry1', 'playEntry2', 'playEntry3', 'playEntry4'];
    const pick = Number.isInteger(index) ? index % 4 : Math.floor(Math.random() * 4);
    this.kill();
    this.pauseRoam();
    this.mode = 'entry';
    this.animController[entries[pick]](() => {
      this.avatarRoot.position.z = 0;
      this.hold();
      this.mode = 'attentive';
      this.eyeVisor.setExpression('happy');
      soundEngine.playHappy();
      this.resumeRoam(this.voice === 'idle' ? 1200 : 0);
    });
  }

  summon(m) {
    if (this.mode === 'sleeping' || this.mode === 'to_sleep') this.wake();
    else if (this.mode === 'entry' && !this.animController.isAnimating) this.playRandomEntry(m.entry);
  }

  nearestEdge() {
    const c = this.space.worldToClient(this.avatarRoot.position);
    const d = { left: c.x, right: window.innerWidth - c.x, top: c.y };
    return Object.entries(d).sort((a, b) => a[1] - b[1])[0][0];
  }

  goSleep() {
    if (this.mode === 'sleeping' || this.mode === 'to_sleep' || this.mode === 'carrying') return;
    this.kill();
    this.pauseRoam();
    this.mode = 'to_sleep';
    const edge = this.nearestEdge();
    this.sleepEdge = edge;
    const h = this.avatarHeightPx();
    const c = this.space.worldToClient(this.avatarRoot.position);
    // Park so only ~15% of the robot (its visor edge) peeks in from behind the bezel.
    const spot = {
      left: { x: -h * 0.28, y: THREE.MathUtils.clamp(c.y, h, window.innerHeight - h), ry: 0.9, rz: -0.25 },
      right: { x: window.innerWidth + h * 0.28, y: THREE.MathUtils.clamp(c.y, h, window.innerHeight - h), ry: -0.9, rz: 0.25 },
      top: { x: THREE.MathUtils.clamp(c.x, h, window.innerWidth - h), y: -h * 0.3, ry: 0, rz: Math.PI },
    }[edge];
    const w = this.space.clientToWorld(spot.x, spot.y, 0);
    this.eyeVisor.setExpression('normal');
    const tl = gsap.timeline({
      onComplete: () => {
        this.mode = 'sleeping';
        // Slow "breathing" while asleep.
        this.breathTween = gsap.to(this.avatarRoot.scale, { x: 1.05, y: 1.05, z: 1.05, duration: 2.2, ease: 'sine.inOut', yoyo: true, repeat: -1 });
      },
    });
    tl.to(this.avatarRoot.position, { x: w.x, y: w.y, z: 0, duration: 1.8, ease: 'power2.inOut' }, 0);
    tl.to(this.avatarRoot.rotation, { x: 0, y: spot.ry, z: spot.rz, duration: 1.4, ease: 'power2.inOut' }, 0.3);
  }

  wake() {
    if (this.mode !== 'sleeping' && this.mode !== 'to_sleep') return;
    const edge = this.sleepEdge || 'left';
    this.kill();
    this.mode = 'waking';
    const h = this.avatarHeightPx();
    const c = this.space.worldToClient(this.avatarRoot.position);
    const out = {
      left: { x: h * 1.4, y: c.y }, right: { x: window.innerWidth - h * 1.4, y: c.y }, top: { x: c.x, y: h * 1.3 },
    }[edge];
    const w = this.space.clientToWorld(out.x, out.y, 0);
    soundEngine.playCurious();
    this.eyeVisor.setExpression('happy');
    const tl = gsap.timeline({ onComplete: () => { this.hold(); this.mode = 'attentive'; this.resumeRoam(this.voice === 'idle' ? 1500 : 0); } });
    tl.to(this.avatarRoot.rotation, { x: 0, y: 0, z: 0, duration: 0.35, ease: 'back.out(2)' }, 0);
    tl.to(this.avatarRoot.position, { x: w.x, y: w.y, duration: 0.6, ease: 'back.out(1.6)' }, 0.05);
  }

  // -------------------------------------------------------------- voice state
  onVoiceState(m) {
    const prev = this.voice;
    this.voice = m.status;
    this.statusRing.set(m.status, { transcript: m.transcript, followLeft: m.follow_up_ms_left });
    if (m.status === prev && m.status !== 'thinking') return;
    const free = !['flying', 'tapping', 'carrying', 'entry'].includes(this.mode);
    switch (m.status) {
      case 'listening':
        if (this.mode === 'sleeping' || this.mode === 'to_sleep') this.wake();
        if (free && this.mode !== 'waking') {
          this.kill();
          this.pauseRoam();
          this.hold();
          this.mode = 'attentive';
          this.faceUser();
        }
        this.eyeVisor.setExpression('curious');
        break;
      case 'thinking':
        this.eyeVisor.setExpression('normal');
        break;
      case 'speaking':
        this.eyeVisor.setExpression('happy');
        break;
      case 'follow_up':
        this.eyeVisor.setExpression('curious');
        break;
      case 'idle':
        this.eyeVisor.setExpression('normal');
        if (free && this.mode !== 'sleeping') this.resumeRoam(400);
        break;
      case 'sleeping':
        this.goSleep();
        break;
    }
  }

  // ------------------------------------------------------------------ flying
  /** Where the robot should hover so its "hand" (top edge) touches the target, staying on-screen. */
  standOff(tx, ty) {
    const h = this.avatarHeightPx();
    const sx = tx > window.innerWidth / 2 ? -1 : 1;
    const sy = ty < window.innerHeight / 2 ? 1 : -1;
    return { x: tx + sx * h * 0.28, y: ty + sy * h * 0.55 };
  }

  flyPath(dest, duration, onDone) {
    const start = this.avatarRoot.position.clone();
    const mid = start.clone().lerp(dest, 0.5);
    const dist = start.distanceTo(dest);
    mid.y += Math.min(1.2, dist * 0.25);   // arc over, like a jump-jet hop
    mid.z += 0.6;
    const curve = new THREE.QuadraticBezierCurve3(start, mid, dest);
    const prev = start.clone();
    const state = { t: 0 };
    return gsap.to(state, {
      t: 1, duration, ease: 'power2.inOut',
      onUpdate: () => {
        const p = curve.getPoint(state.t);
        const vx = p.x - prev.x;
        this.avatarRoot.position.copy(p);
        // Bank into the direction of travel, then level out on arrival.
        const bank = THREE.MathUtils.clamp(-vx * 6, -0.6, 0.6) * (1 - state.t * state.t);
        this.avatarRoot.rotation.z = THREE.MathUtils.lerp(this.avatarRoot.rotation.z, bank, 0.3);
        this.avatarRoot.rotation.y = THREE.MathUtils.lerp(this.avatarRoot.rotation.y, Math.sign(vx) * 0.5 * (1 - state.t), 0.2);
        prev.copy(p);
      },
      onComplete: onDone,
    });
  }

  flyTo(m) {
    this.noteActivity();
    if (this.mode === 'sleeping' || this.mode === 'to_sleep') {
      this.kill();
      this.avatarRoot.rotation.set(0, 0, 0);
    }
    this.kill();
    this.pauseRoam();
    this.mode = 'flying';
    const t = this.space.physToClient(m.target.x, m.target.y);
    const s = m.purpose === 'grab' ? { x: t.x, y: t.y + this.avatarHeightPx() * 0.55 } : this.standOff(t.x, t.y);
    const dest = this.space.clientToWorld(s.x, s.y, 0.3);
    const targetWorld = this.space.clientToWorld(t.x, t.y, 0.3);
    this.eyeVisor.setExpression('curious');
    this.flyPath(dest, Math.max(0.3, (m.duration_ms || 550) / 1000), () => {
      if (m.purpose === 'grab') {
        this.mode = 'carrying';
        this.bridge.send('destination_reached', { reply_to: m.id });
        // If no card arrives (action cancelled), don't stay frozen.
        this.carryTimeout = setTimeout(() => { if (this.mode === 'carrying' && !this.card) this.finishAction(); }, 2500);
        return;
      }
      this.tap(targetWorld, () => this.bridge.send('destination_reached', { reply_to: m.id }));
    });
  }

  tap(targetWorld, onContact) {
    this.mode = 'tapping';
    const p = this.avatarRoot.position.clone();
    const toward = targetWorld.clone().sub(p).multiplyScalar(0.35);
    const tl = gsap.timeline({ onComplete: () => this.finishAction(650) });
    tl.to(this.avatarRoot.rotation, { z: -Math.sign(toward.x) * 0.35, x: -0.2, duration: 0.1, ease: 'power2.out' }, 0);
    tl.to(this.avatarRoot.position, {
      x: p.x + toward.x, y: p.y + toward.y, duration: 0.11, ease: 'power3.in',
      onComplete: () => { soundEngine.playGlassTap(); onContact(); },
    }, 0);
    tl.to(this.avatarRoot.position, { x: p.x, y: p.y, duration: 0.25, ease: 'back.out(2)' });
    tl.to(this.avatarRoot.rotation, { x: 0, z: 0, duration: 0.3, ease: 'elastic.out(1, 0.5)' }, '<');
  }

  finishAction(delay = 0) {
    this.hold();
    this.mode = 'attentive';
    this.eyeVisor.setExpression('happy');
    // Drift away from the button so the user can see the result, then carry on.
    setTimeout(() => {
      if (this.mode !== 'attentive') return;
      const c = this.space.worldToClient(this.avatarRoot.position);
      const h = this.avatarHeightPx();
      const nx = THREE.MathUtils.clamp(c.x + (c.x > window.innerWidth / 2 ? -1 : 1) * h * 1.2, h, window.innerWidth - h);
      const ny = THREE.MathUtils.clamp(c.y + h * 0.9, h, window.innerHeight - h);
      const w = this.space.clientToWorld(nx, ny, 0);
      this.mode = 'flying';
      gsap.to(this.avatarRoot.position, { x: w.x, y: w.y, z: 0, duration: 0.9, ease: 'power2.inOut',
        onComplete: () => { this.hold(); this.mode = 'attentive'; this.resumeRoam(300); } });
      this.faceUser(0.6);
    }, delay);
  }

  // ------------------------------------------------------------ cloth proxy
  async proxyStart(m) {
    clearTimeout(this.carryTimeout);
    this.kill();
    this.pauseRoam();
    this.mode = 'carrying';
    let texture;
    try {
      texture = await new THREE.TextureLoader().loadAsync(m.texture_url);
    } catch (e) {
      console.warn('capture load failed', e);
      this.bridge.send('proxy_arrived', { window_id: m.window_id });
      return;
    }
    if (this.card) this.card.dispose();
    const card = new ClothCard(this.scene, this.space, texture, m.bounds, m.grab);
    this.card = card;
    this.bridge.send('proxy_ready', { window_id: m.window_id });
    this.eyeVisor.setExpression('happy');
    // Give the brain a frame to hide the real window before the card starts to move.
    await new Promise((r) => setTimeout(r, 60));
    await card.moveTo(m.target, m.duration_ms || 900);
    this.bridge.send('proxy_arrived', { window_id: m.window_id });
  }

  proxyEnd(m) {
    const card = this.card;
    this.card = null;
    if (card) card.fadeOut();
    this.finishAction(150);
  }

  // ------------------------------------------------------------------ per frame
  update(delta, time) {
    // Speaking: visor pulses with the voice loudness.
    const visor = this.eyeVisor.mesh;
    const target = 1 + (this.voice === 'speaking' ? this.level * 0.35 : 0);
    visor.scale.y = THREE.MathUtils.lerp(visor.scale.y, target, 0.35);

    if (this.card) {
      this.card.update(delta);
      if (this.mode === 'carrying') {
        // Robot hovers just above the card's grab point, like it's holding the title bar.
        const g = this.card.grabWorld();
        const lift = this.avatarHeightPx() * 0.5 / this.space.pxPerWorld(0.3);
        const dest = new THREE.Vector3(g.x, g.y + lift, 0.3);
        this.avatarRoot.position.lerp(dest, 0.35);
        const vx = dest.x - this.avatarRoot.position.x;
        this.avatarRoot.rotation.z = THREE.MathUtils.lerp(this.avatarRoot.rotation.z, THREE.MathUtils.clamp(-vx * 3, -0.4, 0.4), 0.2);
      }
    }

    if (!this.bridge.connected && this.mode === 'roam' && performance.now() - this.lastLocalActivity > LOCAL_SLEEP_MS) {
      this.goSleep();
    }
  }
}
