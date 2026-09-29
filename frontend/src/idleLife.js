import gsap from 'gsap';
import { soundEngine } from './soundEngine.js';

/**
 * Small unpredictable behaviours while Scar is free (roaming or hovering): it looks around, spins, hops,
 * stretches, dances, glances at your cursor... at random 12-40 s intervals, never the same one twice in a row.
 */
export class IdleLife {
  constructor(ctrl) {
    this.c = ctrl;
    this.next = performance.now() + 8000;
    this.last = null;
    this.mouse = { x: 0.5, y: 0.5 };
    window.addEventListener('mousemove', (e) => { this.mouse = { x: e.clientX / window.innerWidth, y: e.clientY / window.innerHeight }; });
    this.moves = {
      lookAround: (tl, r) => {
        tl.to(r.rotation, { y: 0.7, duration: 0.5, ease: 'sine.inOut' });
        tl.to(r.rotation, { y: -0.7, duration: 0.8, ease: 'sine.inOut' }, '+=0.4');
        tl.to(r.rotation, { y: 0, duration: 0.5, ease: 'sine.inOut' }, '+=0.3');
      },
      happySpin: (tl, r) => {
        tl.call(() => this.c.eyeVisor.setExpression('happy'));
        tl.to(r.rotation, { y: '+=' + Math.PI * 2, duration: 0.9, ease: 'power2.inOut' });
      },
      hop: (tl, r) => {
        for (let i = 0; i < 2; i++) {
          tl.to(r.scale, { x: 1.12, y: 0.88, z: 1.12, duration: 0.08 });
          tl.to(r.position, { y: '+=0.25', duration: 0.18, ease: 'power2.out' });
          tl.to(r.scale, { x: 1, y: 1, z: 1, duration: 0.1 }, '<');
          tl.to(r.position, { y: '-=0.25', duration: 0.18, ease: 'power2.in' });
        }
      },
      wave: (tl, r) => {
        tl.to(r.rotation, { z: 0.3, duration: 0.15, yoyo: true, repeat: 5, ease: 'sine.inOut' });
      },
      stretch: (tl, r) => {
        tl.to(r.scale, { y: 1.18, x: 0.92, z: 0.92, duration: 0.5, ease: 'sine.out' });
        tl.to(r.scale, { y: 1, x: 1, z: 1, duration: 0.6, ease: 'elastic.out(1, 0.4)' }, '+=0.3');
      },
      nod: (tl, r) => {
        tl.to(r.rotation, { x: 0.25, duration: 0.15, yoyo: true, repeat: 3, ease: 'sine.inOut' });
      },
      glanceAtCursor: (tl, r) => {
        tl.call(() => { this.c.eyeVisor.setExpression('curious'); this.c.eyeVisor.setGaze(this.mouse.x * 2 - 1, -(this.mouse.y * 2 - 1)); });
        tl.to(r.rotation, { y: (this.mouse.x - 0.5) * 1.2, x: (this.mouse.y - 0.5) * 0.5, duration: 0.4 });
        tl.to(r.rotation, { y: 0, x: 0, duration: 0.5 }, '+=1.2');
      },
      yawn: (tl, r) => {
        tl.call(() => this.c.eyeVisor.setExpression('sleepy'));
        tl.to(r.scale, { y: 1.08, duration: 0.8, ease: 'sine.inOut' });
        tl.to(r.scale, { y: 1, duration: 0.6 });
        tl.call(() => this.c.eyeVisor.setExpression('normal'), null, '+=0.4');
      },
      dance: (tl, r) => {
        tl.call(() => this.c.eyeVisor.setExpression('love'));
        for (let i = 0; i < 4; i++) {
          tl.to(r.position, { x: `+=${i % 2 ? -0.18 : 0.18}`, duration: 0.2, ease: 'sine.inOut' });
          tl.to(r.rotation, { z: i % 2 ? 0.2 : -0.2, duration: 0.2, ease: 'sine.inOut' }, '<');
        }
        tl.to(r.rotation, { z: 0, duration: 0.2 });
      },
      floatUp: (tl, r) => {
        tl.to(r.position, { y: '+=0.5', duration: 1.2, ease: 'sine.inOut' });
        tl.to(r.rotation, { x: -0.25, duration: 0.6 }, '<');
        tl.to(r.position, { y: '-=0.5', duration: 1.0, ease: 'sine.inOut' }, '+=0.5');
        tl.to(r.rotation, { x: 0, duration: 0.6 }, '<');
      },
    };
  }

  tick(now) {
    const c = this.c;
    if (now < this.next) return;
    this.next = now + 12000 + Math.random() * 28000;
    if (!['roam', 'attentive'].includes(c.mode) || c.voice !== 'idle' || c.mischiefEngine.isBusy) return;
    const names = Object.keys(this.moves).filter((n) => n !== this.last);
    const name = names[Math.floor(Math.random() * names.length)];
    this.last = name;
    const wasRoaming = c.mode === 'roam';
    c.hold();
    c.mode = 'fidget';
    const tl = gsap.timeline({
      onComplete: () => {
        c.avatarRoot.rotation.set(0, 0, 0);
        c.avatarRoot.scale.setScalar(1);
        c.eyeVisor.setExpression('normal');
        c.hold();
        c.mode = 'attentive';
        if (wasRoaming) c.resumeRoam(300);
      },
    });
    if (Math.random() < 0.4) soundEngine.playBoop();
    this.moves[name](tl, c.avatarRoot);
  }
}
