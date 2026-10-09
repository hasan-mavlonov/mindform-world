// MindForm World client: lobby <-> world view, polling, the HUD, god mode.
import { IslandScene } from './scene.js';
import { Lobby } from './lobby.js';
import { IslandAudio } from './audio.js';
import { api, el } from './api.js';

const $ = sel => document.querySelector(sel);
const scene = new IslandScene($('#world'), $('#overlay'));
const audio = new IslandAudio();
const prefs = (() => { try { return JSON.parse(localStorage.getItem('mfw-prefs') || '{}'); } catch { return {}; } })();
const savePrefs = () => { try { localStorage.setItem('mfw-prefs', JSON.stringify(prefs)); } catch { /* private window */ } };
window.mindformScene = scene;   // handy from the dev console (camera, figures)
const app = {
  worldId: null, state: null, seq: 0, first: true, timer: null, selected: null, filter: 'all',
  residents: new Map(), inspectorTurns: -1, story: [], god: { kind: 'island', preset: null }, polls: 0, presets: [],
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
  if (failing && !app.warnedLLM && !document.body.classList.contains('creator')) {
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
  audio.setScene({ hour: s.hour, weather: s.weather });
  app.presets = s.presets || app.presets;
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
  // MindForm's per-turn formation notes wobble up and down; the story shows the smoothed trait cards
  // instead (each turn's note is still in the inspector's "formed by the island").
  all: ['say', 'reaction', 'emotion', 'bond', 'shift', 'event', 'letter', 'weather', 'inject', 'outcome',
        'system', 'born', 'secret', 'clue', 'recap'],
  highlights: it => (it.drama || 0) >= 2 || it.kind === 'recap',
  speech: ['say'], inner: ['reaction'], feelings: ['emotion', 'bond', 'shift'],
  secrets: it => ['secret', 'clue'].includes(it.kind) || (it.kind === 'say' && ['confide', 'confront', 'gossip', 'probe', 'ally'].includes(it.intent)),
  events: ['event', 'letter', 'weather', 'inject', 'recap'],
};
const passes = (filter, it) => (typeof FILTERS[filter] === 'function' ? FILTERS[filter](it) : FILTERS[filter].includes(it.kind));
const INTENT_TAG = { gossip: '🗣 gossip', confide: '🤫 confides', confront: '⚔ confronts', probe: '🔎 fishing', ally: '🤝 secret-keeper',
                     pass: 'in passing' };

function nameOf(id) { return app.residents.get(id)?.name || id || ''; }
function colorOf(id) { return app.residents.get(id)?.color || '#888'; }

function storyRow(it) {
  const time = el('time', {}, it.time.replace(/^Day (\d+), /, 'D$1 '));
  const dot = el('span', { class: 'dot', style: `background:${it.actor ? colorOf(it.actor) : 'transparent'}` });
  let body;
  if (it.kind === 'say') {
    const to = it.react ? ' (out loud): ' : ` → ${it.to_name}: `;
    const tag = INTENT_TAG[it.intent] && !it.answer && !it.closing ? el('span', { class: `itag ${it.intent}` }, INTENT_TAG[it.intent]) : null;
    body = el('div', {}, el('span', { class: 'who' }, nameOf(it.actor)), to, el('q', {}, it.text), tag,
      it.brushoff ? el('small', { class: 'src' }, '(they walked off)') : null,
      el('small', { class: 'src dbg' }, { mind: 'MindForm', llm: 'voice · llm', voice: 'voice · rules', rules: 'rules', planner: 'planner' }[it.source] || it.source || ''));
  } else if (it.kind === 'secret') {
    body = el('div', {}, it.mode === 'exposed' ? '📨 ' : '🔍 ', el('b', {}, it.headline || it.text), it.reveal ? el('div', { class: 'reveal' }, it.reveal) : null);
  } else if (it.kind === 'clue') {
    body = el('div', {}, '👀 ', el('span', { class: 'who' }, nameOf(it.actor)), ' noticed: ', it.text);
  } else if (it.kind === 'shift') {
    body = el('div', {}, it.up ? '📈 ' : '📉 ', el('b', {}, it.text), it.pole ? el('small', { class: 'src' }, `more ${it.pole}`) : null);
  } else if (it.kind === 'recap') {
    body = el('div', {}, el('b', {}, `🎬 ${it.title} — Day ${it.day}`), el('ul', { class: 'recap-list' }, (it.bullets || []).map(b => el('li', {}, b))));
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
    body = el('div', {}, it.warmer ? '💞 ' : '💔 ', it.text, it.label ? el('small', { class: 'src' }, it.label) : null);
  } else if (it.kind === 'letter') {
    body = el('div', {}, '✉ ', el('b', {}, `Letter for ${nameOf(it.actor)}: `), it.text);
  } else if (it.kind === 'inject') {
    body = el('div', {}, '⚡ ', el('b', {}, `Whisper → ${nameOf(it.actor)}: `), it.text);
  } else {
    body = el('div', {}, it.text);
  }
  const hl = (it.drama || 0) >= 2 || it.kind === 'recap';
  return el('div', { class: `st ${it.kind}${hl ? ' hl' : ''}`, dataset: { kind: it.kind } }, time, dot, body, hl ? el('span', { class: 'star', title: 'highlight' }, '★') : null);
}

function renderStory() {                         // full redraw: only when the filter changes
  $('#story').replaceChildren(...app.story.filter(it => passes(app.filter, it)).slice(-250).reverse().map(storyRow));
}

function addStory(it) {
  if (!FILTERS.all.includes(it.kind) || (it.kind === 'emotion' && !it.shown) || (it.kind === 'system' && it.quiet)) return;
  app.story.push(it);
  if (app.story.length > 1500) app.story.splice(0, app.story.length - 1500);
  if (!passes(app.filter, it)) return;
  const box = $('#story');
  box.prepend(storyRow(it));                       // newest on top, older rows untouched
  while (box.children.length > 250) box.lastChild.remove();
}

// ------------------------------------------------------------------ the show
// What happens is played one moment at a time -- about one every 1.5-3 s at 1x (each moment
// carries its own "dwell", and the server waits for the same total before the next beat) -- so
// a viewer can follow it. Speed scales it; a long backlog is caught up faster.
const PRESENT = new Set(['say', 'reaction', 'emotion', 'formation', 'bond', 'letter', 'event', 'weather', 'inject', 'outcome',
                         'secret', 'clue', 'shift', 'recap']);
const ARRIVE_FIRST = new Set(['say', 'reaction', 'emotion', 'bond', 'outcome', 'clue']);
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
  if (show.queue.length > 150) {                     // hopelessly behind ("max" speed): skip to the latest moments
    for (const old of show.queue.splice(0, show.queue.length - 40)) {
      if (old.kind === 'emotion') scene.setMood(old.actor, old);
      addStory(old);
    }
  }
  const it = show.queue[0];
  const patience = 8000 / Math.min(speedFactor(), 4);
  if (it.kind === 'say' && it.passing) {
    // Lines in passing are said on the road, at the moment the two cross.
    const walked = scene.walkElapsed(it.actor);
    if (it.at != null && walked < it.at / speedFactor()) {
      show.waitingSince = show.waitingSince || now;
      if (now - show.waitingSince < patience) return;
    }
  } else if (it.actor && ARRIVE_FIRST.has(it.kind) && scene.isWalking(it.actor, it.t)) {
    // People speak and react once they have arrived where it happens (never stalling for long).
    show.waitingSince = show.waitingSince || now;
    if (now - show.waitingSince < patience) return;
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
      scene.bubble(it.actor, it.text, it.react ? 'react' : 'say', it.to_name);
      scene.focusOn(it.actor);
      caption(it);
      audio.blip(voiceOf(it.actor), it.text);
      const e = takeQueued('emotion', it.actor);
      if (e) feel(e);
      return it.dwell || 2;
    }
    case 'secret': {
      addStory(it);
      bigCard('secret', it.mode === 'exposed' ? '📨 Secret out' : '🔍 Secret', it.headline || it.text, it.reveal || '', [it.actor, it.other]);
      audio.sting('secret');
      if (it.actor) scene.focusOn(it.actor);
      return it.dwell || 3;
    }
    case 'clue': {
      addStory(it);
      scene.bubble(it.actor, '👀 ' + it.text, 'clue');
      return 1.6;
    }
    case 'shift': {
      addStory(it);
      if (creatorOn() || watched(it.actor)) {
        bigCard('shift', `${nameOf(it.actor)} · ${it.trait} ${it.up ? '↑' : '↓'}`, it.pole ? `more ${it.pole}` : '', '', [it.actor]);
        audio.sting('shift');
      } else scene.pop(it.actor, `${it.up ? '↑' : '↓'} ${it.trait}`);
      return creatorOn() ? (it.dwell || 2.6) : 0.6;
    }
    case 'recap': {
      addStory(it);
      recapCard(it);
      audio.sting('recap');
      return it.dwell || 7;
    }
    case 'reaction': {
      addStory(it);
      const e = takeQueued('emotion', it.actor);
      if (e) feel(e);
      if (watched(it.actor)) { scene.bubble(it.actor, it.text, 'inner'); caption(it); return 1.8; }
      return e && e.headline ? (e.dwell || 1.5) : 0.15;
    }
    case 'system':
      addStory(it);
      return 0.1;
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
    case 'formation':                              // every small turn is in the story; cards show real change
      addStory(it);
      return 0.05;
    case 'bond':
      addStory(it);
      scene.bond(it.actor, it.other, it.warmer);
      scene.focusOn(it.actor);
      audio.sting(it.warmer ? 'bond' : 'unbond');
      return it.dwell || 1.8;
    case 'letter':
      addStory(it);
      scene.bubble(it.actor, '✉ ' + it.text, 'letter');
      toast(`Letter for ${nameOf(it.actor)}`, it.text);
      return it.dwell || 2.6;
    case 'event':
      addStory(it);
      if ((it.drama || 0) >= 3) { bigCard('event', `⚡ ${it.title || 'Breaking'}`, it.text, it.place_name || '', []); audio.sting('event'); }
      else toast(it.title || 'Event', `${it.text}${it.place_name ? ` (${it.place_name})` : ''}`);
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

const creatorOn = () => document.body.classList.contains('creator');
const voiceOf = id => app.residents.get(id)?.voice?.blip;

function bigCard(kind, title, sub, extra, actors) {
  const box = $('#cards');
  const dots = el('div', { class: 'card-dots' }, (actors || []).filter(Boolean).map(a => el('span', { class: 'card-dot', style: `background:${colorOf(a)}` }, nameOf(a)[0])));
  const card = el('div', { class: `bigcard ${kind}` }, dots, el('div', { class: 'card-title' }, title),
    sub ? el('div', { class: 'card-sub' }, sub) : null, extra ? el('div', { class: 'card-extra' }, extra) : null);
  box.append(card);
  while (box.children.length > 2) box.firstChild.remove();
  const life = Math.max(2600, ((kind === 'secret' ? 4200 : kind === 'event' ? 4000 : 3200) / Math.min(speedFactor(), 3)));
  setTimeout(() => card.classList.add('out'), life);
  setTimeout(() => card.remove(), life + 600);
}

function recapCard(it) {
  const box = $('#cards');
  box.replaceChildren();
  const card = el('div', { class: 'bigcard recap' }, el('div', { class: 'card-kicker' }, `Day ${it.day}`),
    el('div', { class: 'card-title' }, it.title || 'Today on Halcyon Isle'),
    el('ol', {}, (it.bullets || []).map((b, i) => el('li', { style: `animation-delay:${0.5 + i * 0.6}s` }, b))));
  box.append(card);
  const life = 7000 / Math.min(speedFactor(), 3);
  setTimeout(() => card.classList.add('out'), life);
  setTimeout(() => card.remove(), life + 700);
}

let captionTimer = null;
function caption(it) {
  const box = $('#caption');
  const show = document.body.classList.contains('recording') || scene.camMode === 'cinema' || creatorOn();
  if (!show) { box.classList.add('hidden'); return; }
  const who = el('span', { class: 'who', style: `color:${colorOf(it.actor)}` }, nameOf(it.actor));
  box.replaceChildren(it.kind === 'say' ? el('span', {}, who, it.react ? `: “${it.text}”` : ` → ${it.to_name}: “${it.text}”`)
    : el('span', { class: 'inner' }, who, ` (thinking): “${it.text}”`));
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
  if (r.goal) box.append(el('div', { class: 'insp-goal' }, '🎯 ', el('b', {}, 'Wants: '), r.goal));
  if (r.former_job_title) box.append(el('div', { class: 'insp-goal warn' }, `📦 Lost their job (${r.former_job_title})`));
  if (r.secret) {
    const known = r.secret.known_by_names || [];
    box.append(el('div', { class: 'insp-secret' + (r.secret.exposed ? ' exposed' : '') }, '🤫 ', el('b', {}, 'Secret: '), `${r.name} ${r.secret.text}.`,
      el('small', {}, r.secret.exposed ? 'Exposed — the whole island knows.' : known.length ? `Known by: ${known.join(', ')}` : 'Nobody knows. Yet.')));
  }
  if (r.voice?.profile) box.append(el('div', { class: 'insp-voice' }, '🗣 ', r.voice.profile,
    (r.voice.leads || []).length ? el('small', {}, ` · “${r.voice.leads.join('”, “')}”`) : null));
  box.append(el('div', { class: 'insp-now' }, r.asleep ? 'Asleep at home' : `${r.doing} · ${r.place_name}`,
    r.intent && !r.asleep ? el('small', {}, `why: ${r.intent} (${r.plan_source || '—'} planner)`) : null));
  const felt = scene.figures.get(id)?.mood;
  if (felt) box.append(el('div', { class: 'insp-feel' }, `${felt.emoji} ${felt.label}`,
    el('small', {}, ` · MindForm read their last experience at valence ${felt.valence >= 0 ? '+' : ''}${(felt.valence || 0).toFixed(2)}`)));
  const lastSaid = (r.said || []).slice(-1)[0];
  if (lastSaid) box.append(el('p', { class: 'insp-quote' }, lastSaid));
  if (r.reply && r.reply !== lastSaid) box.append(el('p', { class: 'insp-raw' }, el('b', {}, 'MindForm said: '), r.reply));
  box.append(el('button', { class: 'ghost', style: 'width:100%;margin-bottom:4px', onclick: () => openGod('whisper', id) }, `⚡ Whisper to ${r.name}`));

  if ((st.traits || []).length) {
    box.append(el('div', { class: 'sec' }, `MINDFORM · TURN ${st.turn ?? r.turns}`, el('small', { class: 'sec-note' }, ' smoothed; gold tick = where they started')));
    for (const t of st.traits) box.append(bar(t.name, (r.trait_view || {})[t.key] ?? t.value, { tick: t.base, right: t.glyph }));
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
    box.append(el('div', { class: 'sec' }, 'MINDFORM, TURN BY TURN', el('small', { class: 'sec-note' }, ' raw notes; the cards above are smoothed')));
    box.append(el('div', {}, r.formation_log.slice().reverse().map(f => el('span', { class: 'formation-tag', title: f.time }, f.note))));
  }
  if ((r.relationships || []).length) {
    box.append(el('div', { class: 'sec' }, 'RELATIONSHIPS', el('small', { class: 'sec-note' }, ' ♥ affection (MindForm) · 🤝 trust (what happened)')));
    const rels = r.relationships.slice().sort((a, b) => (b.talks - a.talks) || (Math.abs(b.affection) - Math.abs(a.affection)));
    for (const rel of rels) {
      const badges = [rel.knows_their_secret ? el('span', { class: 'badge' }, '🤫 knows their secret') : null,
        !rel.knows_their_secret && rel.suspects >= 0.3 ? el('span', { class: 'badge' }, `🔎 suspects ${Math.round(rel.suspects * 100)}%`) : null];
      box.append(el('div', { class: 'rel two' }, el('div', { class: 'avatar', style: `background:${colorOf(rel.id)}` }, rel.name[0]),
        el('div', {}, el('b', {}, rel.name), ' ', el('span', { class: `rel-label ${rel.label.replace(/\s+/g, '-')}` }, rel.label),
          el('small', {}, `${rel.talks} talks${rel.last ? ' · ' + rel.last : ''}`), ...badges),
        el('div', { class: 'rel-bars' }, bar('♥', rel.affection, { right: rel.affection.toFixed(2) }), bar('🤝', rel.trust, { right: rel.trust.toFixed(2) }))));
    }
  }
  if ((r.knowledge || []).length) {
    box.append(el('div', { class: 'sec' }, 'WHAT THEY KNOW ABOUT OTHERS'));
    for (const k of r.knowledge.slice().reverse()) {
      box.append(el('div', { class: 'know' + (k.secret ? ' secret' : k.clue ? ' clue' : '') }, k.secret ? '🤫 ' : k.clue ? '👀 ' : '🗣 ', k.text));
    }
  }
  if (exps.length) {
    box.append(el('div', { class: 'sec' }, 'LIVED (what MindForm was told)'));
    for (const x of exps.slice().reverse()) {
      const said = x.spoken && x.spoken !== x.reply ? el('span', { class: 'reply said' }, `said: “${x.spoken}”`) : null;
      box.append(el('div', { class: 'exp' }, el('time', {}, `${x.time} · narrated by ${x.narration}`), x.experience,
        x.reply ? el('span', { class: 'reply' }, `MindForm: “${x.reply}”`) : null, said));
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

function setCreator(on) {          // big readable bubbles, no debug chips, trait cards and captions
  document.body.classList.toggle('creator', on);
  $('#c-creator').classList.toggle('on', on);
  scene.setCreator(on);
  prefs.creator = on;
  savePrefs();
}
function setVertical(on) {         // a centred 9:16 frame to screen-record for Reels / Shorts
  document.body.classList.toggle('vertical', on);
  $('#c-vertical').classList.toggle('on', on);
  prefs.vertical = on;
  savePrefs();
  requestAnimationFrame(() => scene.resize());
}
async function toggleSound() {
  const on = await audio.toggle();
  $('#c-sound').textContent = on ? '🔊' : '🔇';
  $('#c-sound').classList.toggle('on', on);
}
$('#c-creator').addEventListener('click', () => setCreator(!creatorOn()));
$('#c-vertical').addEventListener('click', () => setVertical(!document.body.classList.contains('vertical')));
$('#c-sound').addEventListener('click', toggleSound);
setCreator(!!prefs.creator);
setVertical(!!prefs.vertical);

window.addEventListener('keydown', e => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || !app.worldId || $('#god').open) return;
  const k = e.key.toLowerCase();
  if (k === 'h') toggleRecord();
  if (k === 'c') setCreator(!creatorOn());
  if (k === 'v') setVertical(!document.body.classList.contains('vertical'));
  if (k === 'm') toggleSound();
  if (k === 'g') openGod();
  if (e.key === ' ') { e.preventDefault(); $('#c-play').click(); }
});
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
$('#story-filter').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || !FILTERS[b.dataset.f]) return;
  app.filter = b.dataset.f;
  $('#story-filter').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
  renderStory();
});
scene.onPick(id => select(id));

// God mode: one-click presets, or write it yourself
function openGod(kind = app.god.kind, target = null) {
  app.god.kind = kind;
  const residents = app.state?.residents || [];
  const places = app.map?.locations || [];
  $('#god-target').replaceChildren(...residents.map(r => el('option', { value: r.id, selected: r.id === target }, r.name)));
  $('#god-place').replaceChildren(...places.map(l => el('option', { value: l.id }, l.name)));
  $('#god-pplace').replaceChildren(el('option', { value: '' }, 'Anywhere (random)'),
    ...places.filter(l => ['cafe', 'library', 'workshop', 'greenhouse', 'clinic', 'town_hall', 'rowing_club', 'market'].includes(l.id))
      .map(l => el('option', { value: l.id }, l.name)));
  renderPresets(target);
  $('#god-error').textContent = '';
  syncGod();
  $('#god').showModal();
}

function renderPresets(target = null) {
  const box = $('#god-presets');
  const residents = app.state?.residents || [];
  box.replaceChildren(...(app.presets || []).map(p => el('button', {
    type: 'button', class: 'preset' + (app.god.preset === p.id ? ' active' : ''), dataset: { id: p.id },
    onclick: () => { app.god.preset = p.id; renderPresets(target); },
  }, el('span', { class: 'p-icon' }, p.icon), el('b', {}, p.label), el('small', {}, p.sub))));
  const p = (app.presets || []).find(x => x.id === app.god.preset);
  const fits = !p ? [] : p.target === 'secret' ? residents.filter(r => r.secret && !r.secret.exposed)
    : p.target === 'employed' ? residents.filter(r => r.job && r.job !== 'none') : residents;
  $('#god-ptarget-wrap').classList.toggle('hidden', !p || !p.target);
  $('#god-ptarget').replaceChildren(el('option', { value: '' }, 'Anyone (random)'),
    ...fits.map(r => el('option', { value: r.id, selected: r.id === target }, r.name + (p?.target === 'secret' ? ` — ${r.secret.text}` : ''))));
  $('#god-pplace-wrap').classList.toggle('hidden', !p || !p.place);
  $('#god-send').textContent = p ? `${p.icon} Make it happen` : 'Send';
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
    : 'what people can see or hear: "A whale surfaced in the harbor…"';
}
$('#c-god').addEventListener('click', () => openGod(app.god.kind, app.selected));
$('#god-kind').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) { app.god.kind = b.dataset.v; app.god.preset = null; renderPresets(); syncGod(); }
});
$('#god-text').addEventListener('input', () => { if (app.god.preset) { app.god.preset = null; renderPresets(); } });
$('#god-form').addEventListener('submit', async e => {
  if (e.submitter?.value === 'cancel') { app.god.preset = null; return; }
  e.preventDefault();
  try {
    if (app.god.preset) {
      const res = await api(world('/preset'), { method: 'POST', body: { preset: app.god.preset,
        target: $('#god-ptarget').value || undefined, place: $('#god-pplace').value || undefined } });
      toast('⚡ ' + res.event.title, app.state?.running ? 'Everyone will be hit next beat.' : 'Playing one beat now.');
    } else {
      const k = app.god.kind;
      const body = { kind: k === 'whisper' ? 'whisper' : 'event', text: $('#god-text').value, title: $('#god-title').value || undefined,
                     target: k === 'whisper' ? $('#god-target').value : undefined, place: k === 'event' ? $('#god-place').value : undefined,
                     minutes: Number($('#god-min').value) || 120, force: k === 'island' };
      await api(world('/inject'), { method: 'POST', body });
      toast('Sent', k === 'whisper' ? `${nameOf(body.target)} will live it next beat.` : k === 'island' ? 'Everyone will react next beat.' : 'It is happening now.');
      $('#god-text').value = '';
    }
    $('#god').close();
    app.god.preset = null;
    if (!app.state?.running && !app.state?.busy) api(world('/step'), { method: 'POST' }).catch(() => {});   // one click: see it now
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
