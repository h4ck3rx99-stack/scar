/**
 * Small halo around the robot showing what the voice loop is doing:
 * listening (pulsing cyan), thinking (spinning amber), follow-up (cyan ring draining over 5 s), offline (grey).
 */
export class StatusRing {
  constructor() {
    this.el = document.createElement('div');
    this.el.className = 'status-ring';
    this.label = document.createElement('div');
    this.label.className = 'status-label';
    document.body.append(this.el, this.label);
    this.state = 'idle';
    this.followLeft = 0;
    this.followTotal = 5000;
  }

  set(state, { transcript, followLeft } = {}) {
    this.state = state;
    this.el.dataset.state = state;
    this.label.dataset.state = state;
    if (state === 'follow_up' && followLeft !== undefined) {
      if (followLeft > this.followTotal || this.followLeft === 0) this.followTotal = Math.max(followLeft, 1000);
      this.followLeft = followLeft;
      this.el.style.setProperty('--progress', `${Math.max(0, Math.min(1, followLeft / this.followTotal)) * 360}deg`);
    } else if (state !== 'follow_up') {
      this.followLeft = 0;
    }
    const text = {
      listening: 'Listening…',
      thinking: transcript ? `“${transcript}”` : 'Thinking…',
      follow_up: 'Go ahead…',
      offline: 'Brain offline',
    }[state] || '';
    this.label.textContent = text;
    this.label.classList.toggle('visible', Boolean(text));
    this.el.classList.toggle('visible', ['listening', 'thinking', 'follow_up', 'offline'].includes(state));
  }

  /** x, y = avatar centre in client px; size = avatar height in px. */
  place(x, y, size) {
    const d = Math.round(size * 1.05);
    this.el.style.width = this.el.style.height = `${d}px`;
    this.el.style.transform = `translate(${Math.round(x - d / 2)}px, ${Math.round(y - d / 2)}px)`;
    const ly = Math.min(window.innerHeight - 40, y + size * 0.62);
    this.label.style.transform = `translate(${Math.round(x)}px, ${Math.round(ly)}px) translateX(-50%)`;
  }
}
