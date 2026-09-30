/**
 * Scar's Settings panel (right-click the robot → ⚙ Settings, or say "open your settings").
 * Every control sends its value to the brain, which saves it and sends the full set back, so the panel,
 * the dock and voice commands ("make yourself bigger") always agree.
 */
const FIELDS = [
  { group: 'Look & feel' },
  { key: 'size', label: 'Size', type: 'range', min: 0.5, max: 2, step: 0.05, fmt: (v) => `${(+v).toFixed(2)}×` },
  { key: 'roam_speed', label: 'Roaming speed', type: 'range', min: 0.3, max: 2, step: 0.1, fmt: (v) => `${(+v).toFixed(1)}×` },
  { key: 'pranks', label: 'Playful pranks', type: 'toggle' },
  { key: 'idle_moves', label: 'Idle moves', type: 'toggle' },
  { key: 'sounds', label: 'Sound effects', type: 'toggle' },
  { key: 'bubbles', label: 'Speech bubbles', type: 'toggle' },
  { group: 'Voice' },
  { key: 'voice_volume', label: 'Voice volume', type: 'range', min: 20, max: 200, step: 10, fmt: (v) => `${Math.round(v)}%` },
  { key: 'voice_speed', label: 'Speaking speed', type: 'range', min: 0.7, max: 1.6, step: 0.05, fmt: (v) => `${(+v).toFixed(2)}×` },
  { key: 'personality', label: 'Personality', type: 'select', options: ['normal', 'funny', 'formal', 'brief', 'detailed', 'sarcastic', 'calm'] },
  { group: 'Listening' },
  { key: 'always_listen', label: 'Always listening', type: 'toggle', hint: 'Act on clear commands without the name' },
  { key: 'follow_up_seconds', label: 'Follow-up window', type: 'range', min: 2, max: 30, step: 1, fmt: (v) => `${Math.round(v)} s` },
  { key: 'sleep_minutes', label: 'Nap after', type: 'range', min: 1, max: 60, step: 1, fmt: (v) => `${Math.round(v)} min` },
  { key: 'only_my_voice', label: 'Only my voice', type: 'toggle', hint: 'After "learn my voice"' },
  { key: 'suggestions', label: 'Suggestions', type: 'toggle' },
  { group: 'Other' },
  { key: 'clipboard_history', label: 'Clipboard history', type: 'toggle', hint: 'Remembers recent copies (in memory only)' },
  { key: 'evening_recap', label: 'Offer evening recap', type: 'toggle' },
  { key: 'recap_hour', label: 'Recap time', type: 'range', min: 16, max: 23, step: 1, fmt: (v) => `${Math.round(v) - 12} PM` },
  { key: 'city', label: 'Home city', type: 'text', placeholder: 'from your IP' },
];

export class SettingsPanel {
  constructor({ send, onOpenChange }) {
    this.send = send;                 // (values) => bool
    this.onOpenChange = onOpenChange; // (open) => void
    this.values = {};
    this.inputs = {};
    this.el = document.createElement('div');
    this.el.id = 'settings-panel';
    this.el.className = 'settings-panel';
    this.el.innerHTML = `
      <div class="menu-header"><span>⚙ SCAR SETTINGS</span><button class="menu-close-x" data-close>✕</button></div>
      <div class="settings-body"></div>
      <div class="settings-foot">
        <span class="settings-tip">Tip: you can also just say "make yourself bigger" or "be funnier".</span>
        <button class="ctx-item" data-reset>↺ Reset to defaults</button>
      </div>`;
    document.body.appendChild(this.el);
    const body = this.el.querySelector('.settings-body');
    for (const f of FIELDS) {
      if (f.group) {
        body.insertAdjacentHTML('beforeend', `<div class="menu-group-label">${f.group}</div>`);
        continue;
      }
      const row = document.createElement('label');
      row.className = `settings-row ${f.type}`;
      row.title = f.hint || '';
      row.innerHTML = `<span class="settings-label">${f.label}</span>`;
      let input;
      if (f.type === 'range') {
        input = Object.assign(document.createElement('input'), { type: 'range', min: f.min, max: f.max, step: f.step });
        const out = document.createElement('span');
        out.className = 'settings-val';
        input.oninput = () => { out.textContent = f.fmt(input.value); };
        input.onchange = () => this.change(f.key, +input.value);
        row.append(input, out);
        input._out = out;
      } else if (f.type === 'toggle') {
        input = Object.assign(document.createElement('input'), { type: 'checkbox' });
        input.onchange = () => this.change(f.key, input.checked);
        const sw = document.createElement('span');
        sw.className = 'settings-switch';
        row.append(input, sw);
      } else if (f.type === 'select') {
        input = document.createElement('select');
        for (const o of f.options) input.add(new Option(o[0].toUpperCase() + o.slice(1), o));
        input.onchange = () => this.change(f.key, input.value);
        row.append(input);
      } else {
        input = Object.assign(document.createElement('input'), { type: 'text', placeholder: f.placeholder || '' });
        input.onkeydown = (e) => { if (e.key === 'Enter') input.blur(); e.stopPropagation(); };
        input.onchange = () => this.change(f.key, input.value.trim());
        row.append(input);
      }
      input._field = f;
      this.inputs[f.key] = input;
      body.append(row);
    }
    this.el.querySelector('[data-close]').onclick = () => this.close();
    this.el.querySelector('[data-reset]').onclick = () => this.send({ __reset: true });
  }

  get isOpen() { return this.el.classList.contains('visible'); }

  open(x, y) {
    const w = 280;
    const h = Math.min(window.innerHeight - 20, 560);
    this.el.style.left = `${Math.min(window.innerWidth - w - 10, Math.max(10, x ?? window.innerWidth / 2 - w / 2))}px`;
    this.el.style.top = `${Math.min(window.innerHeight - h - 10, Math.max(10, y ?? 60))}px`;
    this.el.style.maxHeight = `${h}px`;
    this.el.classList.add('visible');
    this.onOpenChange?.(true);
  }

  close() {
    this.el.classList.remove('visible');
    this.onOpenChange?.(false);
  }

  change(key, value) {
    this.values[key] = value;
    if (!this.send({ [key]: value })) this.el.querySelector('.settings-tip').textContent = 'Brain offline: saved on this screen only.';
  }

  /** New values from the brain (or local fallback): refresh the controls without re-sending. */
  fill(values) {
    Object.assign(this.values, values);
    for (const [key, input] of Object.entries(this.inputs)) {
      if (!(key in values) || document.activeElement === input) continue;
      const v = values[key];
      if (input.type === 'checkbox') input.checked = Boolean(v);
      else input.value = v ?? '';
      if (input._out) input._out.textContent = input._field.fmt(v);
    }
  }
}
