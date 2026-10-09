// Island sound, synthesised with WebAudio (no files): the sea, wind and rain by weather, birds by
// day and crickets by night, a speech blip per resident (pitch from their voice), and short
// stings for the big moments. Off until the viewer turns it on (browsers need a click first).

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export class IslandAudio {
  constructor() {
    this.on = false;
    this.ctx = null;
    this.scene = { hour: 9, weather: 'clear' };
    this.lastBlip = 0;
  }

  async toggle() {
    if (!this.ctx) this._build();
    this.on = !this.on;
    if (this.on) await this.ctx.resume(); else await this.ctx.suspend();
    this.setScene(this.scene);
    return this.on;
  }

  _noise(seconds, color) {
    const ctx = this.ctx, len = Math.floor(ctx.sampleRate * seconds);
    const buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < len; i++) {
      const white = Math.random() * 2 - 1;
      if (color === 'brown') { last = (last + 0.02 * white) / 1.02; d[i] = last * 3.5; } else d[i] = white;
    }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    return src;
  }

  _layer(color, filterType, freq, q = 0.7) {
    const src = this._noise(3, color), filter = this.ctx.createBiquadFilter(), gain = this.ctx.createGain();
    filter.type = filterType;
    filter.frequency.value = freq;
    filter.Q.value = q;
    gain.gain.value = 0;
    src.connect(filter).connect(gain).connect(this.master);
    src.start();
    return gain;
  }

  _build() {
    const ctx = this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.master = ctx.createGain();
    this.master.gain.value = 0.9;
    this.master.connect(ctx.destination);
    this.sea = this._layer('brown', 'lowpass', 520);
    const lfo = ctx.createOscillator(), lfoGain = ctx.createGain();      // waves: a slow swell
    lfo.frequency.value = 0.09;
    lfoGain.gain.value = 0.07;
    lfo.connect(lfoGain).connect(this.sea.gain);
    lfo.start();
    this.wind = this._layer('white', 'bandpass', 700, 0.5);
    this.rain = this._layer('white', 'highpass', 2600);
    this.fx = ctx.createGain();
    this.fx.gain.value = 0.5;
    this.fx.connect(this.master);
    setInterval(() => this._critters(), 700);
  }

  setScene({ hour, weather }) {
    this.scene = { hour, weather };
    if (!this.ctx) return;
    const t = this.ctx.currentTime, w = weather;
    const ramp = (g, v) => g.gain.setTargetAtTime(this.on ? v : 0, t, 1.2);
    ramp(this.sea, { storm: 0.26, rain: 0.17 }[w] ?? 0.13);
    ramp(this.wind, { storm: 0.2, rain: 0.06, fog: 0.04, cloudy: 0.035 }[w] ?? 0.018);
    ramp(this.rain, { storm: 0.16, rain: 0.1 }[w] ?? 0);
  }

  _tone(freq, { type = 'sine', at = 0, dur = 0.12, vol = 0.08, slide = 0 } = {}) {
    const ctx = this.ctx, t = ctx.currentTime + at;
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    if (slide) osc.frequency.exponentialRampToValueAtTime(Math.max(40, freq + slide), t + dur);
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(vol, t + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(gain).connect(this.fx);
    osc.start(t);
    osc.stop(t + dur + 0.02);
  }

  _critters() {
    if (!this.on) return;
    const { hour, weather } = this.scene;
    if (weather === 'rain' || weather === 'storm') return;
    const day = hour >= 6 && hour < 20;
    if (day && Math.random() < 0.18) {                     // a gull or a songbird
      const base = 2200 + Math.random() * 1400;
      for (let i = 0; i < 2 + Math.floor(Math.random() * 3); i++)
        this._tone(base, { at: i * 0.11, dur: 0.08, vol: 0.025, slide: 600 * (Math.random() - 0.3) });
    } else if (!day && Math.random() < 0.5) {             // crickets
      for (let i = 0; i < 3; i++) this._tone(4600, { type: 'square', at: i * 0.05, dur: 0.025, vol: 0.006 });
    }
  }

  blip(voice, text) {        // speech: one blip every few letters, in the resident's own pitch
    if (!this.on || !text) return;
    const v = voice || { pitch: 220, wave: 'triangle', rate: 1 };
    const n = clamp(Math.ceil(text.length / 5), 3, 16), gap = 0.075 / (v.rate || 1);
    const start = Math.max(0, this.lastBlip - this.ctx.currentTime);
    for (let i = 0; i < n; i++) {
      const wobble = 0.9 + Math.random() * 0.25;
      this._tone(v.pitch * wobble, { type: v.wave, at: start + i * gap, dur: 0.055, vol: v.wave === 'square' || v.wave === 'sawtooth' ? 0.018 : 0.035 });
    }
    this.lastBlip = this.ctx.currentTime + start + n * gap;
  }

  sting(kind) {
    if (!this.on) return;
    const seq = {
      secret: [[523, 0], [415, 0.14], [349, 0.28], [311, 0.5]],        // a falling minor line
      shift: [[392, 0], [523, 0.1], [659, 0.2]],                        // a rising chime
      recap: [[262, 0], [330, 0.08], [392, 0.16], [523, 0.32]],
      event: [[660, 0], [660, 0.35]],                                   // a bell, twice
      bond: [[587, 0], [784, 0.12]],
      unbond: [[440, 0], [370, 0.16]],
    }[kind];
    if (!seq) return;
    for (const [f, at] of seq) this._tone(f, { type: kind === 'event' ? 'sine' : 'triangle', at, dur: kind === 'event' ? 0.9 : 0.4, vol: 0.07 });
  }
}
