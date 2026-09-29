import * as THREE from 'three';
import gsap from 'gsap';
import { soundEngine } from './soundEngine.js';

/**
 * Mischief & Naughty Action Engine
 * Implements 10 hilarious, interactive mischievous behaviors + tab peeking
 * that trigger dynamically while roaming or on demand.
 */
export class MischiefEngine {
  constructor(avatarRoot, camera, hoverEngine, speechBubble, roamEngine) {
    this.avatarRoot = avatarRoot;
    this.camera = camera;
    this.hoverEngine = hoverEngine;
    this.speechBubble = speechBubble;
    this.roamEngine = roamEngine;

    this.isBusy = false;
    this.enabled = true;
    this.allowed = true;  // the dock's "Playful" switch
    this.mischiefTimer = null;
    this.mouseScreenPos = { x: 0, y: 0 };

    this.initMouseTracker();
    this.startScheduler();
  }

  initMouseTracker() {
    window.addEventListener('mousemove', (e) => {
      // Normalized device coordinates (-1 to +1)
      this.mouseScreenPos.x = (e.clientX / window.innerWidth) * 2 - 1;
      this.mouseScreenPos.y = -(e.clientY / window.innerHeight) * 2 + 1;
    });
  }

  startScheduler() {
    this.stopScheduler();
    const nextInterval = () => 90000 + Math.random() * 150000; // every 1.5-4 minutes, never on a fixed beat

    const scheduleNext = () => {
      this.mischiefTimer = setTimeout(() => {
        if (this.allowed && this.enabled && this.roamEngine.enabled && !this.isBusy) {
          this.triggerRandomMischief();
        }
        scheduleNext();
      }, nextInterval());
    };

    scheduleNext();
  }

  stopScheduler() {
    if (this.mischiefTimer) {
      clearTimeout(this.mischiefTimer);
      this.mischiefTimer = null;
    }
  }

  triggerRandomMischief() {
    if (this.isBusy) return;
    // All ten originals, in random order (the on-screen guard in main.js keeps them inside the screen).
    const actions = [
      'screenTap', 'cursorStalker', 'upsideDownAcrobat', 'sneakPeek', 'jetpackHiccup',
      'dizzyDonuts', 'fakePowerDown', 'laserScannerPrank', 'screenShakeQuake', 'tabSpySnicker',
    ].filter((a) => a !== this.lastAction);
    const picked = actions[Math.floor(Math.random() * actions.length)];
    this.lastAction = picked;
    this.playMischief(picked);
  }

  playMischief(actionName) {
    if (this.isBusy) return;
    this.isBusy = true;
    const wasRoaming = this.roamEngine.enabled;
    this.roamEngine.setEnabled(false); // Pause regular roam for the stunt

    const finish = () => {
      this.isBusy = false;
      if (wasRoaming) {
        this.roamEngine.setEnabled(true);
      }
    };

    switch (actionName) {
      case 'screenTap':
        this.actionScreenTap(finish);
        break;
      case 'cursorStalker':
        this.actionCursorStalker(finish);
        break;
      case 'upsideDownAcrobat':
        this.actionUpsideDownAcrobat(finish);
        break;
      case 'sneakPeek':
        this.actionSneakPeek(finish);
        break;
      case 'jetpackHiccup':
        this.actionJetpackHiccup(finish);
        break;
      case 'dizzyDonuts':
        this.actionDizzyDonuts(finish);
        break;
      case 'fakePowerDown':
        this.actionFakePowerDown(finish);
        break;
      case 'laserScannerPrank':
        this.actionLaserScannerPrank(finish);
        break;
      case 'screenShakeQuake':
        this.actionScreenShakeQuake(finish);
        break;
      case 'tabSpySnicker':
        this.actionTabSpySnicker(finish);
        break;
      default:
        finish();
    }
  }

  // 1. Screen Tap ("Tink Tink! Are you watching me?")
  actionScreenTap(onComplete) {
    const tl = gsap.timeline({ onComplete });
    this.speechBubble.show("Tink! Tink! Hey! Anyone in there? 🖐️", { chime: false, type: 'mischief' });

    tl.to(this.avatarRoot.position, {
      x: 0,
      y: 0,
      z: 3.5, // Darts right up close to camera glass
      duration: 1.2,
      ease: 'power2.out'
    });

    // 3 distinct taps against the screen
    for (let i = 0; i < 3; i++) {
      tl.to(this.avatarRoot.position, {
        z: 4.0,
        duration: 0.1,
        ease: 'power3.in',
        onStart: () => soundEngine.playGlassTap()
      });
      tl.to(this.avatarRoot.position, {
        z: 3.6,
        duration: 0.15,
        ease: 'power2.out'
      });
      tl.to({}, { duration: 0.2 }); // brief pause
    }

    tl.to(this.avatarRoot.position, {
      x: (Math.random() - 0.5) * 2,
      y: (Math.random() - 0.5) * 1.5,
      z: 0,
      duration: 1.4,
      ease: 'power2.inOut'
    });
  }

  // 2. Cursor Stalker ("Boop! Tag, you're it!")
  actionCursorStalker(onComplete) {
    const tl = gsap.timeline({ onComplete });
    this.speechBubble.show("Boop! Tag, you're it! 😜", { chime: false, type: 'mischief' });

    // Target near the user's current mouse position in 3D scene space
    const targetX = this.mouseScreenPos.x * 2.8;
    const targetY = this.mouseScreenPos.y * 1.8;

    tl.to(this.avatarRoot.position, {
      x: targetX,
      y: targetY,
      z: 0.5,
      duration: 1.0,
      ease: 'back.out(1.5)',
      onStart: () => soundEngine.playBoop()
    });

    // Quick little playful nudge
    tl.to(this.avatarRoot.rotation, {
      z: 0.25,
      duration: 0.15,
      yoyo: true,
      repeat: 3
    });

    // Zip away laughing
    tl.to(this.avatarRoot.position, {
      x: -targetX * 0.8,
      y: targetY + 0.6,
      z: -0.5,
      duration: 1.2,
      ease: 'power3.out'
    });
  }

  // 3. Upside-Down Acrobat ("Anti-Gravity inverted mode!")
  actionUpsideDownAcrobat(onComplete) {
    const tl = gsap.timeline({ onComplete });
    this.speechBubble.show("Look! Anti-gravity inverted mode! 🙃", { chime: false, type: 'mischief' });

    tl.to(this.avatarRoot.rotation, {
      z: Math.PI, // 180° flip
      duration: 0.8,
      ease: 'back.out(1.7)'
    });

    // Hover upside down casually while moving sideways
    tl.to(this.avatarRoot.position, {
      x: "+=1.5",
      y: "+=0.3",
      duration: 2.0,
      ease: 'sine.inOut'
    });

    // Flip back
    tl.to(this.avatarRoot.rotation, {
      z: 0,
      duration: 0.7,
      ease: 'elastic.out(1, 0.4)'
    });
  }

  // 4. Sneak Peek ("Peek-a-boo from behind the taskbar/bezel!")
  actionSneakPeek(onComplete) {
    const tl = gsap.timeline({ onComplete });

    // Duck below screen bottom
    tl.to(this.avatarRoot.position, {
      x: 2.0,
      y: -3.8, // hidden below screen
      duration: 1.0,
      ease: 'power2.in'
    });

    tl.to({}, { duration: 1.5 }); // hide for 1.5s

    // Slowly peek eyes and forehead out
    tl.to(this.avatarRoot.position, {
      y: -2.3, // peek just top of head
      duration: 1.2,
      ease: 'power2.out',
      onStart: () => {
        this.speechBubble.show("Psst... peek-a-boo! Did you miss me? 👀", { type: 'mischief' });
      }
    });

    // Wiggle left and right curiously
    tl.to(this.avatarRoot.rotation, {
      y: "+=0.4",
      duration: 0.3,
      yoyo: true,
      repeat: 3
    });

    tl.to(this.avatarRoot.position, {
      y: 0,
      x: 0,
      duration: 1.2,
      ease: 'power2.out'
    });
  }

  // 5. Jetpack Hiccup ("Engine sneeze / backfire!")
  actionJetpackHiccup(onComplete) {
    const tl = gsap.timeline({ onComplete });
    this.speechBubble.show("Achoo! Jetpack backfire! 💨", { chime: false, type: 'mischief' });

    soundEngine.playHiccup();

    // Shudder in place
    tl.to(this.avatarRoot.position, {
      x: "+=0.15",
      y: "-=0.1",
      duration: 0.08,
      yoyo: true,
      repeat: 6
    });

    // Wild erratic wobble spiral
    tl.to(this.avatarRoot.rotation, {
      z: 0.8,
      x: -0.4,
      duration: 0.5,
      ease: 'power2.in'
    });

    tl.to(this.avatarRoot.position, {
      x: "-=1.8",
      y: "+=0.8",
      duration: 0.8,
      ease: 'power1.out'
    });

    // Settle and stabilize
    tl.to(this.avatarRoot.rotation, {
      x: 0,
      z: 0,
      duration: 0.8,
      ease: 'elastic.out(1, 0.4)'
    });
  }

  // 6. Dizzy Donuts ("5 fast spins -> room is spinning!")
  actionDizzyDonuts(onComplete) {
    const tl = gsap.timeline({ onComplete });

    soundEngine.playDizzy();

    // Fast 360 spins (5 full rotations = 10 * PI)
    tl.to(this.avatarRoot.rotation, {
      y: "+=" + (Math.PI * 10),
      duration: 1.5,
      ease: 'power2.inOut'
    });

    // Stop and wobble drunkenly
    tl.add(() => {
      this.speechBubble.show("Whoa... the room is spinning... 😵💫", { type: 'mischief' });
    });

    tl.to(this.avatarRoot.rotation, {
      z: 0.35,
      duration: 0.4,
      yoyo: true,
      repeat: 4,
      ease: 'sine.inOut'
    });

    tl.to(this.avatarRoot.rotation, {
      z: 0,
      duration: 0.6,
      ease: 'power2.out'
    });
  }

  // 7. Fake Power Down ("Play dead / battery prank")
  actionFakePowerDown(onComplete) {
    const tl = gsap.timeline({ onComplete });
    this.speechBubble.show("Battery 0.1%... system shut down... 🪫", { chime: false, type: 'mischief' });

    soundEngine.playPowerDownUp();

    // Dim hover engine temporarily
    this.hoverEngine.setIntensity(0.1);

    // Limp fall toward bottom
    tl.to(this.avatarRoot.position, {
      y: -2.8,
      duration: 1.6,
      ease: 'power1.in'
    });

    tl.to(this.avatarRoot.rotation, {
      z: 0.6,
      x: 0.4,
      duration: 1.6
    }, "<");

    // Sudden rocket boost back up!
    tl.to(this.avatarRoot.position, {
      y: 0.5,
      x: 0,
      duration: 0.6,
      ease: 'back.out(2.0)',
      onStart: () => {
        this.hoverEngine.setIntensity(2.4);
        this.speechBubble.show("Haha! Fooled ya! Still at 100%! ⚡", { type: 'mischief' });
      }
    });

    tl.to(this.avatarRoot.rotation, {
      x: 0,
      z: 0,
      duration: 0.5,
      ease: 'power2.out',
      onComplete: () => {
        this.hoverEngine.setIntensity(1.1);
      }
    });
  }

  // 8. Laser Scanner Prank ("Target locked on human!")
  actionLaserScannerPrank(onComplete) {
    const tl = gsap.timeline({ onComplete });
    this.speechBubble.show("Scanning human... Firing tickle laser! 🔫 PEW!", { chime: false, type: 'mischief' });

    tl.to(this.avatarRoot.position, {
      x: 0,
      y: 1.2,
      z: 1.0,
      duration: 0.8,
      ease: 'power2.out'
    });

    // Make rapid firing laser sound bursts
    for (let i = 0; i < 3; i++) {
      tl.add(() => soundEngine.playLaser(), `+=${0.25 * i}`);
      tl.to(this.avatarRoot.position, {
        z: 0.85,
        duration: 0.08,
        yoyo: true,
        repeat: 1
      });
    }

    tl.to(this.avatarRoot.position, {
      y: 0,
      z: 0,
      duration: 1.0,
      ease: 'power2.inOut'
    });
  }

  // 9. Screen Shake Quake ("Mini earthquake slam")
  actionScreenShakeQuake(onComplete) {
    const tl = gsap.timeline({ onComplete });

    // Fly up high
    tl.to(this.avatarRoot.position, {
      y: 2.2,
      duration: 0.9,
      ease: 'power2.out'
    });

    // Superhero ground slam down
    tl.to(this.avatarRoot.position, {
      y: -1.8,
      duration: 0.35,
      ease: 'power3.in',
      onComplete: () => {
        soundEngine.playSlam();
        // Shake body viewport
        document.body.classList.add('screen-shake');
        setTimeout(() => document.body.classList.remove('screen-shake'), 450);
        this.speechBubble.show("BOOM! Earthquake jump! Did you feel that? 💥", { type: 'mischief' });
      }
    });

    tl.to(this.avatarRoot.position, {
      y: 0,
      duration: 1.2,
      ease: 'elastic.out(1, 0.4)'
    });
  }

  // 10. Tab Spy Snicker ("What tab are you hiding over there?")
  actionTabSpySnicker(onComplete) {
    const tl = gsap.timeline({ onComplete });

    // Fly to the left edge like looking behind an open window or browser tab
    tl.to(this.avatarRoot.position, {
      x: -3.0,
      y: 0.5,
      duration: 1.2,
      ease: 'power2.inOut'
    });

    // Tilt head curiously
    tl.to(this.avatarRoot.rotation, {
      y: 0.6,
      z: -0.2,
      duration: 0.5
    });

    tl.add(() => {
      this.speechBubble.show("Hmm... is that YouTube or work? I won't tell anyone... 🤫", { type: 'mischief' });
    });

    tl.to({}, { duration: 2.0 }); // pause and stare

    tl.to(this.avatarRoot.position, {
      x: 0,
      y: 0,
      duration: 1.4,
      ease: 'power2.out'
    });

    tl.to(this.avatarRoot.rotation, {
      y: 0,
      z: 0,
      duration: 0.8
    }, "<");
  }
}
