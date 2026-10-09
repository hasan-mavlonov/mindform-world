// MindForm World client: lobby <-> world view, polling, the HUD, god mode.
import { IslandScene } from './scene.js';
import { Lobby } from './lobby.js';
import { api, el } from './api.js';

const $ = sel => document.querySelector(sel);
const scene = new IslandScene($('#world'), $('#overlay'));
window.mindformScene = scene;   // handy from the dev console (camera, figures)
const app = {
  worldId: null, state: null, seq: 0, first: true, timer: null, selected: null, filter: 'all',
  residents: new Map(), inspectorTurns: -1, story: [], god: { kind: 'whisper' }, polls: 0,
};

const lobby = new Lobby({ onOpen: id => openWorld(id) });

// ------------------------------------------------------------------ routing
async function boot() {
  try {
    const status = await api('/api/status');
    app.version = status.app_version;
    scene.build(status.map);
    app.map = status.map;
    await lobby.load();
  } catch (e) {
    $('#wizard-error').textContent = 'Could not reach the server: ' + e.message;
  }
  const m = location.hash.match(/^#\/world\/(.+)$/);
  if (m) openWorld(decodeURIComponent(m[1]));
  else showLobby();
}

function showLobby() {
  clearTimeout(app.timer);
  app.worldId = null;
  location.hash = '';
  document.body.classList.remove('recording');
  $('#lobby').classList.remove('hidden');
  $('#hud').classList.add('hidden');
  scene.setLobby(true);
  scene.camera.position.set(40, 42, 52);
  scene.controls.target.set(0, 0, 2);
  lobby.loadSaved().catch(() => {});
}

function openWorld(id) {
  clearTimeout(app.timer);
  Object.assign(app, { worldId: id, state: null, seq: 0, first: true, selected: null, inspectorTurns: -1, story: [], polls: 0, warnedLLM: false });
  location.hash = `#/world/${encodeURIComponent(id)}`;
  $('#lobby').classList.add('hidden');
  $('#hud').classList.remove('hidden');
  $('#story').replaceChildren();
  $('#res-list').replaceChildren();
  resRows.clear();
  show.queue = [];
  show.nextAt = 0;
  scene.clearResidents();
  $('#inspector').replaceChildren(el('p', { class: 'muted center' }, 'Click a resident on the island or in the list.'));
  scene.setLobby(false);
  scene.camera.position.set(26, 40, 50);
  scene.controls.target.set(1.5, 0, 1);
  scene.setCameraMode('orbit');
  setActive('#c-cam', 'orbit');
  poll();
}

// ------------------------------------------------------------------ polling
async function poll() {
  const id = app.worldId;
  if (!id) return;
  try {
    const state = await api(`/api/worlds/${encodeURIComponent(id)}/state?since=${app.seq}`);
    if (id !== app.worldId) return;
    $('#error-banner').classList.add('hidden');
    apply(state);
  } catch (e) {
    if (/not found/i.test(e.message)) { showLobby(); return; }
    showError('Connection problem: ' + e.message);
  }
  app.timer = setTimeout(poll, 900);
}

function showError(text) {
  const b = $('#error-banner');
  b.textContent = text;
  b.classList.remove('hidden');
}

const pad = n => String(n).padStart(2, '0');

function apply(s) {
  if (s.app_version && app.version && s.app_version !== app.version) {
    location.reload();                              // the server was upgraded under this tab
    return;
  }
  app.state = s;
  app.polls++;
  for (const r of s.residents) app.residents.set(r.id, r);
  // top bar
  $('#clock-time').textContent = `Day ${s.day} · ${pad(s.hour)}:${pad(s.minute)}`;
  $('#clock-sub').textContent = `${s.daypart} · ${s.weather_text}`;
  $('#badge-mind').textContent = `${s.setup.version_name} · ${s.setup.mind_llm ? 'LLM' : 'offline'}`;
  const brain = $('#badge-brain');
  const failing = s.setup.world_brain === 'llm' && s.llm.failures > 0;
  brain.textContent = `world: ${s.setup.world_brain}` + (s.setup.world_brain === 'llm' ? ` · ${s.llm.calls} calls` : '') +
    (failing ? ` · ${s.llm.failures} failed` : '');
  brain.classList.toggle('warn', failing);
  brain.title = failing ? `Last LLM error: ${s.llm.last_error} -- those calls fell back to rules.` : '';
  if (failing && !app.warnedLLM) {
    app.warnedLLM = true;
    toast('World LLM failing', `${s.llm.last_error || 'error'} -- planning and narration fall back to rules. Check the API key.`);
  }
  const phase = $('#badge-phase');
  phase.textContent = s.status === 'creating' ? 'creating' : s.busy ? s.phase : (s.running ? 'running' : 'paused');
  phase.classList.toggle('busy', s.busy || s.status === 'creating');
  $('#record-badge').replaceChildren(el('b', {}, s.name), el('small', {}, `Day ${s.day} · ${pad(s.hour)}:${pad(s.minute)} · ${s.weather_text}`));
  // creating / errors
  $('#creating').classList.toggle('hidden', s.status !== 'creating');
  if (s.status === 'creating') {
    const p = s.progress || {};
    $('#creating-text').textContent = p.total ? `${p.current || '…'} (${Math.min(p.done + 1, p.total)}/${p.total})` : 'Starting the mind…';
  }
  if (s.status === 'error' || s.error) showError(s.error || 'The world hit an error.');
  // controls
  $('#c-play').textContent = s.running ? '❚❚ Pause' : '▶ Play';
  $('#c-play').disabled = s.status !== 'ready';
  $('#c-step').disabled = s.running || s.busy || s.status !== 'ready';
  setActive('#c-speed', String(s.speed));
  // 3D. The feed goes first: anyone whose earlier scene is still playing keeps their place
  // until it has played (then they walk on to where the server already has them).
  scene.setTime(s.clock, s.weather, s.events);
  ingestFeed(s.feed);
  const queuedFrom = show.queue.length ? Math.min(...show.queue.map(i => i.t)) : Infinity;
  const hold = new Set(s.residents.filter(r => r.path_t > queuedFrom).map(r => r.id));
  const walk = s.speed > 0 ? (s.walk_seconds || 5) / s.speed * 0.95 : 1.2;
  scene.syncResidents(s.residents, { walkSeconds: Math.max(1.2, walk), hold });
  if (app.first) for (const r of s.residents) if (r.mood) scene.setMood(r.id, r.mood);
  renderResidents(s);
  renderEvents(s);
  app.seq = s.feed_seq;
  app.first = false;
  if (app.selected) {
    const r = app.residents.get(app.selected);
    if (r && (r.turns !== app.inspectorTurns || app.polls % 6 === 0)) renderInspector(app.selected);
  }
}

// ------------------------------------------------------------------ panels
const NEED_ICON = { autonomy: '⟡', competence: '◆', relatedness: '♡' };

function moodColor(r) {
  const v = r.last_appraisal ? r.last_appraisal.valence : 0;
  return v > 0.15 ? '#8fe39a' : v < -0.15 ? '#ff8f8a' : '#c9d6d1';
}

const resRows = new Map();   // resident id -> row elements, updated in place (a rebuilt row eats clicks)

function renderResidents(s) {
  $('#res-count').textContent = `${s.residents.length}`;
  const box = $('#res-list');
  s.residents.forEach((r, i) => {
    let row = resRows.get(r.id);
    if (!row || !box.contains(row.el)) {
      const mood = el('span', { class: 'mood', title: 'how the last experience read to them' });
      const avatar = el('div', { class: 'avatar', style: `background:${r.color}` }, r.name[0], mood);
      const doing = el('small'), why = el('small'), need = el('span', { class: 'res-need' }), name = el('b', {}, r.name);
      const btn = el('button', { class: 'res', onclick: () => select(r.id) }, avatar,
        el('div', { class: 'res-text' }, name, doing, why), need);
      row = { el: btn, avatar, mood, doing, why, need, name };
      resRows.set(r.id, row);
    }
    if (box.children[i] !== row.el) box.insertBefore(row.el, box.children[i] || null);
    row.el.classList.toggle('active', r.id === app.selected);
    row.avatar.classList.toggle('asleep', r.asleep);
    row.mood.style.background = moodColor(r);
    const felt = scene.figures.get(r.id)?.mood;      // as the show has played it so far
    row.name.textContent = felt && felt.strength >= 0.25 && !['calm', 'thoughtful'].includes(felt.key) ? `${r.name} ${felt.emoji}` : r.name;
    row.doing.textContent = `${r.doing} · ${r.place_name}`;
    row.why.textContent = r.intent && !r.asleep ? `↳ ${r.intent}` : '';
    const top = r.state.top_need;
    row.need.textContent = top ? `${NEED_ICON[top.key] || ''} ${Math.round(top.tension * 100)}` : '';
    row.need.title = top ? `loudest need: ${top.name}` : '';
  });
  while (box.children.length > s.residents.length) box.lastChild.remove();
}

function renderEvents(s) {
  const box = $('#event-list');
  const items = s.events.map(e => el('div', { class: 'ev' + (e.active ? '' : ' later') }, e.title,
    el('small', {}, `${e.place_name}${e.active ? '' : ` · from ${pad(Math.floor((e.start % 1440) / 60))}:${pad(e.start % 60)}`}`)));
  box.replaceChildren(...(items.length ? items : [el('p', { class: 'muted', style: 'margin:0 4px;font-size:11.5px' }, 'Nothing unusual. Yet.')]));
}

const FILTERS = {
  all: ['say', 'reaction', 'emotion', 'bond', 'formation', 'event', 'letter', 'weather', 'inject', 'outcome', 'system', 'born'],
  speech: ['say'], inner: ['reaction'], feelings: ['emotion', 'bond', 'formation'],
  events: ['event', 'letter', 'weather', 'inject'],
};

function nameOf(id) { return app.residents.get(id)?.name || id || ''; }
function colorOf(id) { return app.residents.get(id)?.color || '#888'; }

function storyRow(it) {
  const time = el('time', {}, it.time.replace(/^Day (\d+), /, 'D$1 '));
  const dot = el('span', { class: 'dot', style: `background:${it.actor ? colorOf(it.actor) : 'transparent'}` });
  let body;
  if (it.kind === 'say') {
    body = el('div', {}, el('span', { class: 'who' }, nameOf(it.actor)), ` → ${it.to_name}: `, el('q', {}, it.text),
      it.brushoff ? el('small', { class: 'src' }, '(they walked off)') : null,
      el('small', { class: 'src' }, it.source === 'mind' ? 'MindForm' : it.source === 'llm' ? 'opener · llm' : 'opener · rules'));
  } else if (it.kind === 'reaction') {
    body = el('div', {}, el('span', { class: 'who' }, nameOf(it.actor)), ': “', it.text, '”');
  } else if (it.kind === 'outcome') {
    body = el('div', {}, el('span', { class: 'who' }, nameOf(it.actor)), ' · ', it.text);
  } else if (it.kind === 'event') {
    body = el('div', {}, el('b', {}, `${it.title || 'Event'}`), el('small', { class: 'src' }, it.place_name || ''), el('div', {}, it.text));
  } else if (it.kind === 'emotion') {
    body = el('div', {}, `${it.emoji} `, el('span', { class: 'who' }, nameOf(it.actor)), ` felt ${it.label}`,
      el('small', { class: 'src' }, `MindForm valence ${it.valence >= 0 ? '+' : ''}${(it.valence || 0).toFixed(2)}`));
  } else if (it.kind === 'bond') {
    body = el('div', {}, it.warmer ? '💞 ' : '💔 ', it.text);
  } else if (it.kind === 'letter') {
    body = el('div', {}, '✉ ', el('b', {}, `Letter for ${nameOf(it.actor)}: `), it.text);
  } else if (it.kind === 'inject') {
    body = el('div', {}, '⚡ ', el('b', {}, `Whisper → ${nameOf(it.actor)}: `), it.text);
  } else {
    body = el('div', {}, it.text);
  }
  return el('div', { class: `st ${it.kind}`, dataset: { kind: it.kind } }, time, dot, body);
}

function renderStory() {                         // full redraw: only when the filter changes
  const kinds = FILTERS[app.filter];
  $('#story').replaceChildren(...app.story.filter(it => kinds.includes(it.kind)).slice(-250).reverse().map(storyRow));
}

function addStory(it) {
  if (!FILTERS.all.includes(it.kind) || (it.kind === 'emotion' && !it.shown)) return;
  app.story.push(it);
  if (app.story.length > 1500) app.story.splice(0, app.story.length - 1500);
  if (!FILTERS[app.filter].includes(it.kind)) return;
  const box = $('#story');
  box.prepend(storyRow(it));                       // newest on top, older rows untouched
  while (box.children.length > 250) box.lastChild.remove();
}

// ------------------------------------------------------------------ the show
// What happens is played one moment at a time -- about one every 1.5-3 s at 1x (each moment
// carries its own "dwell", and the server waits for the same total before the next beat) -- so
// a viewer can follow it. Speed scales it; a long backlog is caught up faster.
const PRESENT = new Set(['say', 'reaction', 'emotion', 'formation', 'bond', 'letter', 'event', 'weather', 'inject', 'outcome']);
const ARRIVE_FIRST = new Set(['say', 'reaction', 'emotion', 'bond', 'outcome']);
const PLACE_ICON = { cafe: '☕', market: '🧺', library: '📚', town_hall: '🏛️', clinic: '🩺', workshop: '🔨', lighthouse: '🗼',
  dock: '🎣', rowing_club: '🚣', beach: '🏖️', cliffs: '⛰️', greenhouse: '🌱', plaza: '⛲' };
const show = { queue: [], nextAt: 0, waitingSince: 0 };

function ingestFeed(items) {
  for (const it of items) {
    if (app.first || !PRESENT.has(it.kind) || (it.kind === 'outcome' && !it.notable)) addStory(it);   // backlog + quiet facts
    else show.queue.push(it);
  }
}

const speedFactor = () => { const v = app.state ? app.state.speed : 1; return v > 0 ? v : 8; };
const watched = id => id === app.selected || (scene.camMode === 'cinema' && scene.cinema.focus === id);

setInterval(() => {
  if (!app.worldId || !show.queue.length) return;
  const now = performance.now();
  if (now < show.nextAt) return;
  const it = show.queue[0];
  // People speak and react once they have arrived where it happens (never stalling for long).
  if (it.actor && ARRIVE_FIRST.has(it.kind) && scene.isWalking(it.actor, it.t)) {
    show.waitingSince = show.waitingSince || now;
    if (now - show.waitingSince < 8000 / Math.min(speedFactor(), 4)) return;
  }
  show.waitingSince = 0;
  show.queue.shift();
  let dwell = present(it);
  const backlog = show.queue.filter(q => q.dwell > 0).length;   // only moments that take screen time
  if (backlog > 20) dwell *= 0.3; else if (backlog > 10) dwell *= 0.6;
  show.nextAt = now + (dwell / speedFactor()) * 1000;
}, 100);

function takeQueued(kind, actor) {                 // the emotion that goes with a line plays with it
  const i = show.queue.findIndex((q, n) => n < 12 && q.kind === kind && q.actor === actor);
  return i >= 0 ? show.queue.splice(i, 1)[0] : null;
}

function feel(it) {                                 // every reading sets the face; strong ones get the body
  addStory(it);
  if (it.headline) scene.emote(it.actor, it); else scene.setMood(it.actor, it);
}

function present(it) {                              // -> seconds (at 1x) until the next moment
  switch (it.kind) {
    case 'say': {
      addStory(it);
      scene.bubble(it.actor, it.text, 'say', it.to_name);
      scene.focusOn(it.actor);
      caption(it);
      const e = takeQueued('emotion', it.actor);
      if (e) feel(e);
      return it.dwell || 2;
    }
    case 'reaction': {
      addStory(it);
      const e = takeQueued('emotion', it.actor);
      if (e) feel(e);
      if (watched(it.actor)) { scene.bubble(it.actor, it.text, 'inner'); caption(it); return 1.8; }
      return e && e.headline ? (e.dwell || 1.5) : 0.15;
    }
    case 'emotion':
      feel(it);
      if (it.headline) scene.focusOn(it.actor);
      return it.dwell || (it.headline ? 1.5 : 0.05);
    case 'outcome': {                               // a notable result of what they were doing
      addStory(it);
      const icon = PLACE_ICON[it.place] || (String(it.place).startsWith('home:') ? '🏠' : '•');
      scene.bubble(it.actor, `${icon} ${it.text}`, 'deed');
      return it.dwell || 1.4;
    }
    case 'formation':
      addStory(it);
      if (watched(it.actor) || Math.abs(it.delta || 0) >= 0.03) scene.pop(it.actor, '↑ ' + it.text.replace(/^.*? grew /, ''));
      return 0.35;
    case 'bond':
      addStory(it);
      scene.bond(it.actor, it.other, it.warmer);
      scene.focusOn(it.actor);
      return it.dwell || 1.8;
    case 'letter':
      addStory(it);
      scene.bubble(it.actor, '✉ ' + it.text, 'letter');
      toast(`Letter for ${nameOf(it.actor)}`, it.text);
      return it.dwell || 2.6;
    case 'event':
      addStory(it);
      toast(it.title || 'Event', `${it.text}${it.place_name ? ` (${it.place_name})` : ''}`);
      return it.dwell || 2.6;
    case 'weather':
      addStory(it);
      toast('Weather', it.text);
      return it.dwell || 2;
    default:
      addStory(it);
      return 0.3;
  }
}

let captionTimer = null;
function caption(it) {
  const box = $('#caption');
  const show = document.body.classList.contains('recording') || scene.camMode === 'cinema';
  if (!show) { box.classList.add('hidden'); return; }
  const who = el('span', { class: 'who', style: `color:${colorOf(it.actor)}` }, nameOf(it.actor));
  box.replaceChildren(it.kind === 'say' ? el('span', {}, who, ` → ${it.to_name}: “${it.text}”`)
    : el('span', { class: 'inner' }, who, ` (to themselves): “${it.text}”`));
  box.classList.remove('hidden');
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => box.classList.add('hidden'), Math.min(9000, 3000 + it.text.length * 45));
}

function toast(title, text) {
  const t = el('div', { class: 'toast' }, el('b', {}, title), text);
  $('#toasts').append(t);
  while ($('#toasts').children.length > 3) $('#toasts').firstChild.remove();
  setTimeout(() => t.remove(), 7000);
}

// ------------------------------------------------------------------ inspector
function select(id) {
  app.selected = id;
  scene.select(id);
  showTab('resident');
  renderInspector(id);
  if (app.state) renderResidents(app.state);
}

function bar(label, value, { min = -1, max = 1, tick = null, need = false, right = '' } = {}) {
  const frac = v => (clamp01((v - min) / (max - min))) * 100;
  const zero = frac(0);
  const fill = el('div', { class: 'fill' });
  if (need || min === 0) fill.setAttribute('style', `left:0;width:${frac(value)}%`);
  else fill.setAttribute('style', `left:${Math.min(zero, frac(value))}%;width:${Math.abs(frac(value) - zero)}%`);
  const b = el('div', { class: 'bar' + (need ? ' need' : '') }, fill, min < 0 ? el('div', { class: 'mid' }) : null,
    tick !== null ? el('div', { class: 'tick', style: `left:${frac(tick)}%`, title: 'baseline' }) : null);
  return el('div', { class: 'bar-row' }, el('div', { class: 'bar-label' }, el('span', {}, label), el('span', {}, right)), b);
}
const clamp01 = v => Math.max(0, Math.min(1, v));

async function renderInspector(id) {
  const r = app.residents.get(id);
  if (!r) return;
  app.inspectorTurns = r.turns;
  let exps = [];
  try {
    exps = (await api(`/api/worlds/${encodeURIComponent(app.worldId)}/experiences?resident=${encodeURIComponent(id)}&limit=8`)).experiences;
  } catch { /* keep going */ }
  if (app.selected !== id) return;
  const st = r.state || {};
  const ident = r.identity || {};
  const facts = ['age', 'origin', 'gender'].map(k => ident[k]).filter(Boolean).join(' · ');
  const box = el('div');
  box.append(el('div', { class: 'insp-head' }, el('div', { class: 'avatar', style: `background:${r.color}` }, r.name[0]),
    el('div', {}, el('h2', {}, r.name), el('small', {}, [r.job_title, facts].filter(Boolean).join(' · ')))));
  if (r.goal) box.append(el('div', { class: 'muted', style: 'margin-top:10px;font-size:12px' }, `Wants: ${r.goal}`));
  box.append(el('div', { class: 'insp-now' }, r.asleep ? 'Asleep at home' : `${r.doing} · ${r.place_name}`,
    r.intent && !r.asleep ? el('small', {}, `why: ${r.intent} (${r.plan_source || '—'} planner)`) : null));
  const felt = scene.figures.get(id)?.mood;
  if (felt) box.append(el('div', { class: 'insp-feel' }, `${felt.emoji} ${felt.label}`,
    el('small', {}, ` · MindForm read their last experience at valence ${felt.valence >= 0 ? '+' : ''}${(felt.valence || 0).toFixed(2)}`)));
  if (r.reply) box.append(el('p', { class: 'insp-quote' }, r.reply));
  box.append(el('button', { class: 'ghost', style: 'width:100%;margin-bottom:4px', onclick: () => openGod('whisper', id) }, `⚡ Whisper to ${r.name}`));

  if ((st.traits || []).length) {
    box.append(el('div', { class: 'sec' }, `MINDFORM · TURN ${st.turn ?? r.turns}`));
    for (const t of st.traits) box.append(bar(t.name, t.value, { tick: t.base, right: t.glyph }));
    if ((st.needs || []).length) {
      box.append(el('div', { class: 'sec' }, 'NEEDS (how starved)'));
      for (const n of st.needs) box.append(bar(`${NEED_ICON[n.key] || ''} ${n.name}`, n.tension, { min: 0, max: 1, need: true, right: n.tension.toFixed(2) }));
    }
    box.append(el('div', { class: 'sec' }, 'SELF & STANCE'));
    box.append(bar('self-esteem', st.esteem || 0, { right: (st.esteem || 0).toFixed(2) }));
    const kv = el('dl', { class: 'kv' });
    const add = (k, v) => { if (v) kv.append(el('dt', {}, k), el('dd', {}, v)); };
    add('stance', st.stance?.line);
    add('voice', st.voice);
    add('lens', st.lens);
    add('values', (st.values || []).filter(v => v.value > 0.05).slice(0, 3).map(v => v.label).join(', '));
    add('beliefs', (st.beliefs || []).map(b => b.statement).join(' · '));
    add('habits', (st.habits || []).map(h => typeof h === 'string' ? h : h.text || h.label || JSON.stringify(h)).join(' · '));
    add('read via', [st.sources?.appraisal, st.sources?.reply].filter(Boolean).join(' / '));
    box.append(kv);
  } else {
    box.append(el('p', { class: 'muted' }, 'Control resident: a fixed persona with no MindForm inside.'));
  }
  if ((r.formation_log || []).length) {
    box.append(el('div', { class: 'sec' }, 'FORMED BY THE ISLAND'));
    box.append(el('div', {}, r.formation_log.slice().reverse().map(f => el('span', { class: 'formation-tag', title: f.time }, f.note))));
  }
  if ((r.relationships || []).length) {
    box.append(el('div', { class: 'sec' }, 'RELATIONSHIPS'));
    for (const rel of r.relationships) {
      box.append(el('div', { class: 'rel' }, el('div', { class: 'avatar', style: `background:${colorOf(rel.id)}` }, rel.name[0]),
        el('div', {}, rel.name, el('small', {}, `${rel.feeling} · ${rel.talks} talks${rel.last ? ' · ' + rel.last : ''}`)),
        bar('', rel.affinity)));
    }
  }
  if (exps.length) {
    box.append(el('div', { class: 'sec' }, 'LIVED (what MindForm was told)'));
    for (const x of exps.slice().reverse()) {
      box.append(el('div', { class: 'exp' }, el('time', {}, `${x.time} · narrated by ${x.narration}`), x.experience,
        x.reply ? el('span', { class: 'reply' }, `“${x.reply}”`) : null));
    }
  }
  $('#inspector').replaceChildren(box);
}

function showTab(name) {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('hidden', t.dataset.tab !== name));
}

// ------------------------------------------------------------------ controls
function setActive(sel, value) {
  document.querySelectorAll(`${sel} button`).forEach(b => b.classList.toggle('active', b.dataset.v === value));
}

const world = path => `/api/worlds/${encodeURIComponent(app.worldId)}${path}`;

$('#c-lobby').addEventListener('click', async () => {
  if (app.state?.running) await api(world('/run'), { method: 'POST', body: { running: false } }).catch(() => {});
  showLobby();
});
$('#c-play').addEventListener('click', async () => {
  try { await api(world('/run'), { method: 'POST', body: { running: !app.state?.running } }); } catch (e) { showError(e.message); }
});
$('#c-step').addEventListener('click', () => api(world('/step'), { method: 'POST' }).catch(e => showError(e.message)));
$('#c-speed').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) api(world('/speed'), { method: 'POST', body: { speed: Number(b.dataset.v) } }).catch(err => showError(err.message));
});
$('#c-cam').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.v === 'follow' && !app.selected && app.state?.residents.length) select(app.state.residents[0].id);
  scene.setCameraMode(b.dataset.v);
  setActive('#c-cam', b.dataset.v);
});
const toggleRecord = () => document.body.classList.toggle('recording');
$('#c-record').addEventListener('click', toggleRecord);
window.addEventListener('keydown', e => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || !app.worldId) return;
  if (e.key === 'h' || e.key === 'H') toggleRecord();
  if (e.key === ' ') { e.preventDefault(); $('#c-play').click(); }
});
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
$('#story-filter').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  app.filter = b.dataset.f;
  $('#story-filter').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
  renderStory();
});
scene.onPick(id => select(id));

// God mode
function openGod(kind = 'whisper', target = null) {
  app.god.kind = kind;
  const residents = app.state?.residents || [];
  $('#god-target').replaceChildren(...residents.map(r => el('option', { value: r.id, selected: r.id === target }, r.name)));
  $('#god-place').replaceChildren(...(app.map?.locations || []).map(l => el('option', { value: l.id }, l.name)));
  $('#god-error').textContent = '';
  syncGod();
  $('#god').showModal();
  $('#god-text').focus();
}
function syncGod() {
  const k = app.god.kind;
  setActive('#god-kind', k);
  $('#god-target-wrap').classList.toggle('hidden', k !== 'whisper');
  $('#god-place-wrap').classList.toggle('hidden', k !== 'event');
  $('#god-title-wrap').classList.toggle('hidden', k === 'whisper');
  $('#god-min-wrap').classList.toggle('hidden', k === 'whisper');
  $('#god-text-hint').textContent = k === 'whisper'
    ? 'first person, as a fact: "I found a letter under my door…" (reaches their mind verbatim)'
    : 'what people there can see or hear: "A stranger is handing out flyers…"';
}
$('#c-god').addEventListener('click', () => openGod(app.god.kind, app.selected));
$('#god-kind').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { app.god.kind = b.dataset.v; syncGod(); } });
$('#god-form').addEventListener('submit', async e => {
  if (e.submitter?.value === 'cancel') return;
  e.preventDefault();
  const k = app.god.kind;
  const body = { kind: k === 'whisper' ? 'whisper' : 'event', text: $('#god-text').value, title: $('#god-title').value || undefined,
                 target: k === 'whisper' ? $('#god-target').value : undefined, place: k === 'event' ? $('#god-place').value : undefined,
                 minutes: Number($('#god-min').value) || 120 };
  try {
    await api(world('/inject'), { method: 'POST', body });
    $('#god').close();
    $('#god-text').value = '';
    toast('Sent', k === 'whisper' ? `${nameOf(body.target)} will live it next beat.` : 'It is happening now.');
  } catch (err) { $('#god-error').textContent = err.message; }
});

// Experiment: export + clone
$('#c-more').addEventListener('click', () => {
  const s = app.state;
  $('#more-export').href = world('/export');
  $('#clone-seed').value = s ? s.setup.seed + 1 : 1;
  $('#clone-brain').value = s?.setup.world_brain || 'rules';
  $('#clone-mind').value = String(!!s?.setup.mind_llm);
  $('#clone-error').textContent = '';
  $('#more').showModal();
});
$('#more-form').addEventListener('submit', async e => {
  if (e.submitter?.value === 'cancel') return;
  e.preventDefault();
  try {
    const { id } = await api(world('/clone'), { method: 'POST', body: {
      seed: Number($('#clone-seed').value), world_brain: $('#clone-brain').value, mind_llm: $('#clone-mind').value === 'true' } });
    $('#more').close();
    openWorld(id);
  } catch (err) { $('#clone-error').textContent = err.message; }
});

boot();
