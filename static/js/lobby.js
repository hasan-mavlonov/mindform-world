// The lobby: saved worlds + the three-step wizard (mind -> world -> residents).
import { api, el } from './api.js';

const PALETTE = ['#f4a6c8', '#ffb565', '#84cffa', '#acf2a9', '#d7b4ff', '#ffe08a', '#7fe0d4', '#ff9b8a', '#b9c7ff', '#f6c6a0'];

// A cast with built-in friction, for "Surprise me". Bios carry temperament cues (for MindForm's
// genesis); levels are the same people as sliders (for offline, where genesis is keyword-based).
// Secrets complete "<name> …"; voices are a few words on how they talk (quotes = catchphrases).
const SAMPLE_CAST = [
  { name: 'Aya', job: 'baker', goal: 'leave the island for good',
    secret: "has already bought a one-way ferry ticket and hasn't told anyone",
    voice: 'warm, nervous, apologises too much',
    bio: 'Aya Tanaka, 27, a warm but anxious baker from Osaka who moved to the island after a painful breakup. Generous, eager to please, easily hurt by criticism.',
    identity: { age: '27', gender: 'woman', origin: 'Osaka, Japan', language: 'Japanese', family: 'only child, close to her mother' }, levels: { O: 4, C: 3, E: 4, A: 5, N: 4 } },
  { name: 'Rex', job: 'boatbuilder', goal: 'get off the island on his own terms',
    secret: 'is secretly building a boat to leave the island',
    voice: 'gruff, dry, says "lad"',
    bio: 'Rex Calloway, 34, a blunt, proud boatbuilder who grew up on fishing boats in Hull. Disciplined and stubborn, distrusts newcomers, rarely says sorry.',
    identity: { age: '34', gender: 'man', origin: 'Hull, England', language: 'English', family: 'raised by a fisherman father' }, levels: { O: 2, C: 4, E: 2, A: 1, N: 2 } },
  { name: 'Ines', job: 'nurse', goal: 'keep her job at the clinic',
    secret: "mixed up two patients' charts and is hiding it",
    voice: 'formal, precise, kind underneath',
    bio: 'Ines Varga, 41, the island nurse, from Budapest. Calm and dutiful, quietly exhausted, widowed two years ago. Keeps her worries to herself.',
    identity: { age: '41', gender: 'woman', origin: 'Budapest, Hungary', language: 'Hungarian', family: 'widowed, one adult son' }, levels: { O: 3, C: 5, E: 2, A: 4, N: 3 } },
  { name: 'Leo', job: 'none', goal: 'be liked by everyone on the island',
    secret: 'was expelled from university for cheating, not a dropout like he tells everyone',
    voice: 'cheeky, loud, says "mate" a lot',
    bio: 'Leo Okafor, 22, a restless, outgoing surfer from Lagos who dropped out of university and came to the island on a whim. Funny, impulsive, hates being alone.',
    identity: { age: '22', gender: 'man', origin: 'Lagos, Nigeria', language: 'English, Yoruba', family: 'big family, the youngest' }, levels: { O: 4, C: 1, E: 5, A: 4, N: 3 } },
  { name: 'Mira', job: 'librarian', goal: 'find out who left the note in the old book',
    secret: 'wrote the anonymous note pinned to the Town Hall door',
    voice: 'dreamy, poetic, shy',
    bio: 'Mira Duarte, 29, a shy, observant librarian from Lisbon who writes poems nobody has read. Curious and imaginative, sensitive, avoids confrontation.',
    identity: { age: '29', gender: 'woman', origin: 'Lisbon, Portugal', language: 'Portuguese', religion: 'Catholic', family: 'two older brothers' }, levels: { O: 5, C: 4, E: 1, A: 4, N: 4 } },
  { name: 'Tomasz', job: 'keeper', goal: 'stop the council from automating the lighthouse',
    secret: 'has been leaving the lighthouse lamp off on some nights',
    voice: 'gruff, superstitious, few words',
    bio: 'Tomasz Nowak, 58, the gruff, solitary lighthouse keeper. Superstitious, set in his ways, kind underneath. Has lived on the island for thirty years.',
    identity: { age: '58', gender: 'man', origin: 'Gdansk, Poland', language: 'Polish', religion: 'Catholic', family: 'never married' }, levels: { O: 2, C: 4, E: 1, A: 3, N: 3 } },
  { name: 'Sana', job: 'clerk', goal: 'be elected mayor at the next vote',
    secret: 'forged the mayor\'s signature on a council permit',
    voice: 'confident, polished, a little cutting',
    bio: 'Sana Mirza, 31, the ambitious town clerk from Karachi. Organized, competitive, confident in public, privately afraid of failing.',
    identity: { age: '31', gender: 'woman', origin: 'Karachi, Pakistan', language: 'Urdu, English', religion: 'Muslim', family: 'eldest of four' }, levels: { O: 3, C: 5, E: 4, A: 2, N: 3 } },
  { name: 'Jonah', job: 'fisher', goal: 'pay back the money he owes before anyone finds out',
    secret: 'owes money to dangerous people on the mainland',
    voice: 'easygoing, funny, dodges hard questions',
    bio: 'Jonah Reyes, 25, an easygoing, funny fisher who owes money to the wrong people back on the mainland. Charming, avoids hard conversations.',
    identity: { age: '25', gender: 'man', origin: 'Cebu, Philippines', language: 'Cebuano, English', family: 'sends money home to his mother' }, levels: { O: 3, C: 2, E: 4, A: 4, N: 3 } },
];
const DRAMA_CAST = ['Aya', 'Rex', 'Ines', 'Leo', 'Mira'];

export class Lobby {
  constructor({ onOpen }) {
    this.onOpen = onOpen;
    this.step = 1;
    this.status = null;
    this.state = { version: null, brain: 'rules', mind: 'offline', drama: '2', voices: 'styled', residents: [] };
    this.$ = sel => document.querySelector(sel);
    this.$('#wiz-next').addEventListener('click', () => this.next());
    this.$('#wiz-back').addEventListener('click', () => this.go(this.step - 1));
    this.$('#w-dice').addEventListener('click', () => { this.$('#w-seed').value = Math.floor(Math.random() * 100000); });
    this.$('#w-pace').addEventListener('input', () => this.paceText());
    this.$('#r-add').addEventListener('click', () => { this.addResident(); });
    this.$('#r-surprise').addEventListener('click', () => this.surprise());
    this.$('#r-drama').addEventListener('click', () => this.dramaCast());
    for (const [sel, key] of [['#w-brain', 'brain'], ['#w-mind', 'mind'], ['#w-drama', 'drama'], ['#w-voices', 'voices']]) {
      this.$(sel).addEventListener('click', e => {
        const b = e.target.closest('button');
        if (!b || b.disabled) return;
        this.state[key] = b.dataset.v;
        this.segs();
      });
    }
  }

  async load() {
    this.status = await api('/api/status');
    const llm = this.status.llm;
    const pill = this.$('#llm-status');
    pill.className = 'pill ' + (llm.available ? 'ok' : 'warn');
    pill.textContent = llm.available ? `LLM ready · ${llm.model}` : 'No LLM key · rules & offline only';
    this.state.brain = llm.available ? 'llm' : 'rules';
    this.state.mind = llm.available ? 'llm' : 'offline';
    const first = this.status.versions.find(v => v.available);
    this.state.version = first ? first.id : null;
    this.renderVersions();
    this.segs();
    this.paceText();
    if (!this.state.residents.length) for (let i = 0; i < 3; i++) this.addResident(false);
    this.renderResidents();
    await this.loadSaved();
  }

  async loadSaved() {
    const list = this.$('#saved-list');
    const { worlds } = await api('/api/worlds');
    list.replaceChildren();
    if (!worlds.length) { list.append(el('p', { class: 'muted' }, 'No worlds yet. Make one →')); return; }
    for (const w of worlds) {
      const modes = `${w.version_name} · mind ${w.setup.mind_llm ? 'LLM' : 'offline'} · world ${w.setup.world_brain}`;
      list.append(el('div', { class: 'saved-item', onclick: () => this.onOpen(w.id) },
        el('b', {}, w.name),
        el('small', {}, `${w.time_text} · ${w.residents.length} residents`),
        el('small', {}, w.residents.join(', ')),
        el('small', {}, modes + (w.status === 'ready' ? '' : ` · ${w.status}`))));
    }
  }

  get version() { return this.status?.versions.find(v => v.id === this.state.version); }

  renderVersions() {
    const box = this.$('#versions');
    box.replaceChildren();
    for (const v of this.status.versions) {
      const card = el('button', { type: 'button', class: 'version' + (v.id === this.state.version ? ' active' : '') + (v.available ? '' : ' disabled') },
        el('b', {}, v.name), el('div', { class: 'tag' }, v.tagline), el('p', {}, v.description),
        v.available ? null : el('div', { class: 'note' }, v.note));
      card.addEventListener('click', () => {
        if (!v.available) return;
        this.state.version = v.id;
        this.renderVersions();
        this.renderResidents();
      });
      box.append(card);
    }
    box.append(el('div', { class: 'version soon' }, el('b', {}, 'Next MindForm versions'),
      el('div', { class: 'tag' }, 'plug in here'),
      el('p', {}, 'Each version is an adapter (create · experience · state). Add one and it shows up in this list.')));
  }

  segs() {
    const llmOk = this.status?.llm.available;
    const v = this.version;
    for (const [sel, key] of [['#w-brain', 'brain'], ['#w-mind', 'mind'], ['#w-drama', 'drama'], ['#w-voices', 'voices']]) {
      for (const b of this.$(sel).querySelectorAll('button')) {
        b.disabled = (b.dataset.v === 'llm' && !llmOk) || (key === 'mind' && b.dataset.v === 'llm' && v && !v.supports_llm);
        b.classList.toggle('active', b.dataset.v === this.state[key]);
      }
    }
    this.$('#w-brain-hint').textContent = this.state.brain === 'llm'
      ? 'The world model plans what residents do, writes their experiences from the facts, and invents events. Falls back to rules on any failure.'
      : 'Seeded rules: fast, free and reproducible (same seed, same world).';
    this.$('#w-mind-hint').textContent = this.state.mind === 'llm'
      ? 'MindForm reads each experience with its LLM (the path you use in the console).'
      : "MindForm's offline fallbacks: lexicon / trained head appraisal, rule-based voice. Fast, free, cruder.";
    if (!llmOk) this.$('#w-mind-hint').textContent += ' (No API key found: set GEMINI_API_KEY in .env here or in MindForm v0.)';
    this.$('#w-voices-hint').textContent = this.state.voices === 'styled'
      ? 'MindForm decides how each moment lands; every resident says it in their own words (from their traits and voice). No two residents share a phrase, nobody repeats themselves.'
      : "Residents say MindForm's reply word for word (offline v0 replies are formulaic). For experiments on the raw output.";
  }

  paceText() {
    const v = Number(this.$('#w-pace').value);
    this.$('#w-pace-v').textContent = v;
    this.$('#w-pace-min').textContent = Math.round(60 / v);
  }

  go(step) {
    this.step = Math.max(1, Math.min(3, step));
    document.querySelectorAll('.step').forEach(s => s.classList.toggle('hidden', Number(s.dataset.step) !== this.step));
    document.querySelectorAll('.steps li').forEach(li => {
      const n = Number(li.dataset.step);
      li.classList.toggle('active', n === this.step);
      li.classList.toggle('done', n < this.step);
    });
    this.$('#wiz-back').style.visibility = this.step === 1 ? 'hidden' : 'visible';
    this.$('#wiz-next').textContent = this.step === 3 ? 'Create world ✦' : 'Next';
    this.$('#wizard-error').textContent = '';
  }

  next() {
    if (this.step === 1 && !this.state.version) { this.$('#wizard-error').textContent = 'Pick a mind version.'; return; }
    if (this.step < 3) return this.go(this.step + 1);
    this.create();
  }

  // ---- residents -------------------------------------------------------------------
  blankResident(i) {
    return { mode: this.state.mind === 'offline' ? 'manual' : 'bio', name: '', bio: '', identity: {},
             levels: { O: 3, C: 3, E: 3, A: 3, N: 3 }, job: 'none', goal: '', secret: '', voice: '', color: PALETTE[i % PALETTE.length] };
  }

  addResident(render = true) {
    if (this.state.residents.length >= 10) return;
    this.state.residents.push(this.blankResident(this.state.residents.length));
    if (render) this.renderResidents();
  }

  surprise() {
    const pool = [...SAMPLE_CAST].sort(() => Math.random() - 0.5);
    const n = Math.max(3, Math.min(this.state.residents.length || 5, 8));
    const mode = this.state.mind === 'offline' ? 'manual' : 'bio';
    this.state.residents = pool.slice(0, n).map((c, i) => this.fromSample(c, i, mode));
    this.renderResidents();
  }

  dramaCast() {        // the cast the Reels series opens with: everyone is hiding something
    const mode = this.state.mind === 'offline' ? 'manual' : 'bio';
    this.state.residents = DRAMA_CAST.map((n, i) => this.fromSample(SAMPLE_CAST.find(c => c.name === n), i, mode));
    this.state.drama = '3';
    this.segs();
    this.renderResidents();
  }

  fromSample(c, i, mode) {
    return { mode, name: c.name, bio: c.bio, identity: { name: c.name, ...c.identity }, levels: { ...c.levels },
             job: c.job, goal: c.goal, secret: c.secret || '', voice: c.voice || '', color: PALETTE[i % PALETTE.length] };
  }

  renderResidents() {
    const box = this.$('#resident-cards');
    if (!box || !this.status) return;
    box.replaceChildren();
    const schema = this.version?.schema || { identity_fields: [{ key: 'name', label: 'Name' }], trait_questions: [] };
    const jobs = this.status.map.jobs;
    this.$('#r-hint').textContent = `${this.state.residents.length} resident${this.state.residents.length === 1 ? '' : 's'} · born through ${this.version?.name || 'the mind'}'s own creation form. ` +
      (this.state.mind === 'offline' ? 'Offline, a biography is read with a small keyword list -- the sliders set temperament precisely.' : '');
    this.state.residents.forEach((r, i) => {
      const seg = el('div', { class: 'seg tiny' },
        el('button', { type: 'button', class: r.mode === 'bio' ? 'active' : '', onclick: () => { r.mode = 'bio'; this.renderResidents(); } }, 'Biography'),
        el('button', { type: 'button', class: r.mode === 'manual' ? 'active' : '', onclick: () => { r.mode = 'manual'; this.renderResidents(); } }, 'Fields + sliders'));
      const color = el('input', { type: 'color', value: r.color, title: 'Color', oninput: e => { r.color = e.target.value; } });
      const remove = el('button', { type: 'button', class: 'remove', title: 'Remove', onclick: () => { this.state.residents.splice(i, 1); this.renderResidents(); } }, '✕');
      const card = el('div', { class: 'rcard' }, el('div', { class: 'rcard-head' }, color, seg, remove));
      if (r.mode === 'bio') {
        card.append(
          el('label', {}, 'Name', el('input', { value: r.name, maxlength: 60, placeholder: 'Aya', oninput: e => { r.name = e.target.value; } })),
          el('label', {}, 'Biography', el('textarea', { rows: 3, maxlength: 1200, placeholder: 'Aya, 27, a warm but anxious baker from Osaka who…', oninput: e => { r.bio = e.target.value; } }, r.bio)));
      } else {
        const grid = el('div', { class: 'rcard-grid' });
        for (const f of schema.identity_fields) {
          grid.append(el('label', {}, f.label, el('input', { value: r.identity[f.key] || (f.key === 'name' ? r.name : '') || '', maxlength: 120,
            oninput: e => { r.identity[f.key] = e.target.value; if (f.key === 'name') r.name = e.target.value; } })));
        }
        card.append(grid);
        for (const q of schema.trait_questions) {
          const val = el('input', { type: 'range', min: 1, max: 5, value: r.levels[q.key] || 3, oninput: e => { r.levels[q.key] = Number(e.target.value); } });
          card.append(el('div', { class: 'slider-row' }, el('b', {}, q.name), el('span', {}, q.low), val, el('span', {}, q.high)));
        }
        card.append(el('label', {}, 'Background (optional)', el('textarea', { rows: 2, maxlength: 1200, oninput: e => { r.bio = e.target.value; } }, r.bio)));
      }
      const jobSel = el('select', { onchange: e => { r.job = e.target.value; } },
        jobs.map(j => el('option', { value: j.id, selected: j.id === r.job }, j.title + (j.hours ? ` (${j.hours[0]}–${j.hours[1]}h)` : ''))));
      const who = (r.identity.name || r.name || 'They').trim() || 'They';
      card.append(el('div', { class: 'rcard-grid' },
        el('label', {}, 'Life on the island', jobSel),
        el('label', {}, 'What they want (goal)', el('input', { value: r.goal, maxlength: 200, placeholder: 'leave the island for good', oninput: e => { r.goal = e.target.value; } }))));
      card.append(el('label', { class: 'secret-field' }, el('span', {}, '🤫 Secret ', el('small', {}, `finish the sentence: "${who} …" (others can find out)`)),
        el('input', { value: r.secret, maxlength: 200, placeholder: 'is secretly building a boat to leave the island', oninput: e => { r.secret = e.target.value; } })));
      card.append(el('label', {}, el('span', {}, '🗣 Voice ', el('small', {}, 'how they talk; quote catchphrases (optional)')),
        el('input', { value: r.voice, maxlength: 200, placeholder: 'dry, sarcastic, says "mate" a lot', oninput: e => { r.voice = e.target.value; } })));
      box.append(card);
    });
    this.$('#r-add').disabled = this.state.residents.length >= 10;
  }

  async create() {
    const err = this.$('#wizard-error');
    const residents = this.state.residents;
    if (!residents.length) { err.textContent = 'Add at least one resident.'; return; }
    for (const [i, r] of residents.entries()) {
      if (r.mode === 'bio' && !r.bio.trim()) { err.textContent = `Resident ${i + 1}: write a short biography.`; return; }
      if (r.mode === 'manual' && !(r.identity.name || r.name || '').trim()) { err.textContent = `Resident ${i + 1}: give them a name.`; return; }
    }
    const setup = {
      name: this.$('#w-name').value.trim() || 'Halcyon Isle',
      version: this.state.version,
      world_brain: this.state.brain,
      mind_llm: this.state.mind === 'llm',
      seed: Number(this.$('#w-seed').value) || 431,
      experiences_per_hour: Number(this.$('#w-pace').value),
      intensity: Number(this.state.drama),
      voices: this.state.voices,
      characters: residents.map(r => ({
        mode: r.mode, name: r.name.trim(), bio: r.bio.trim(), job: r.job, goal: r.goal.trim(), color: r.color,
        secret: (r.secret || '').trim(), voice: (r.voice || '').trim(),
        identity: r.mode === 'manual' ? { ...r.identity, name: (r.identity.name || r.name).trim() } : {},
        levels: r.mode === 'manual' ? r.levels : {},
      })),
    };
    const btn = this.$('#wiz-next');
    btn.disabled = true;
    err.textContent = '';
    try {
      const { id } = await api('/api/worlds', { method: 'POST', body: setup });
      this.onOpen(id);
    } catch (e) {
      err.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  }
}
