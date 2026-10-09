// Halcyon Isle in 3D: the island, its places, day/night + weather, and the residents.
// Pure presentation -- everything it shows comes from the server's state.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = t => t * t * (3 - 2 * t);

// Seeded random so the scenery is the same on every load.
function rng(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

const mats = new Map();
function mat(color, opts = {}) {
  const key = color + JSON.stringify(opts);
  if (!mats.has(key)) mats.set(key, new THREE.MeshStandardMaterial({ color, roughness: 0.9, ...opts }));
  return mats.get(key);
}

const FOOT = { plaza: 4.6, cafe: 3.4, market: 3.2, library: 3.8, town_hall: 3.6, clinic: 3.0, lighthouse: 3.6,
  workshop: 4.2, dock: 3.0, rowing_club: 4.0, beach: 4.5, cliffs: 5.5, greenhouse: 5.2 };

const SKY = [   // hour -> sky color
  [0, '#0b1630'], [5, '#16223f'], [6, '#e59a7d'], [7.5, '#a9d3e3'], [12, '#8fd0e6'],
  [17, '#a2cfe0'], [19.3, '#f0a070'], [20.5, '#40386a'], [22, '#101a3a'], [24, '#0b1630'],
];
function keyframe(frames, h) {
  for (let i = 0; i < frames.length - 1; i++) {
    const [h0, c0] = frames[i], [h1, c1] = frames[i + 1];
    if (h >= h0 && h <= h1) return new THREE.Color(c0).lerp(new THREE.Color(c1), (h - h0) / (h1 - h0 || 1));
  }
  return new THREE.Color(frames[0][1]);
}

export class IslandScene {
  constructor(canvas, overlay) {
    this.canvas = canvas;
    this.overlay = overlay;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 400);
    this.camera.position.set(34, 36, 46);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI / 2.15;
    this.controls.minDistance = 8;
    this.controls.maxDistance = 120;
    this.controls.target.set(0, 0, 2);

    this.figures = new Map();      // resident id -> figure
    this.cottages = new Map();     // resident id -> cottage
    this.places = new Map();       // place id -> {x, z, label el, ring}
    this.bubbles = [];
    this.maxBubbles = 4;
    this.pops = [];
    this.selected = null;
    this.camMode = 'orbit';
    this.lobby = false;
    this.hour = 9;
    this.weather = 'clear';
    this.clockMs = performance.now();
    this.ferryDocked = false;
    this.bonfireBoost = 0;
    this.lightningAt = 0;
    this.cinema = { next: 0, focus: null };
    this.pickHandlers = [];
    this.windowMats = [];

    this._lights();
    this._water();
    this._rain();
    this._stars();
    this._picking();
    window.addEventListener('resize', () => this.resize());
    this.resize();
    this.clock = new THREE.Clock();
    this.renderer.setAnimationLoop(() => this._frame());
  }

  resize() {
    const w = this.canvas.clientWidth || window.innerWidth, h = this.canvas.clientHeight || window.innerHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // ------------------------------------------------------------------ build
  _lights() {
    this.hemi = new THREE.HemisphereLight('#e9f9ff', '#4b6b55', 2.0);
    this.scene.add(this.hemi);
    this.sun = new THREE.DirectionalLight('#fff3d6', 3.0);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    Object.assign(this.sun.shadow.camera, { left: -38, right: 38, top: 38, bottom: -38, near: 1, far: 140 });
    this.sun.shadow.bias = -0.0004;
    this.scene.add(this.sun, this.sun.target);
    this.moon = new THREE.DirectionalLight('#9bb8ff', 0.0);
    this.moon.position.set(-30, 50, -20);
    this.scene.add(this.moon);
    this.scene.fog = new THREE.Fog('#9ccbe0', 70, 210);
    this.scene.background = new THREE.Color('#9ccbe0');
  }

  _water() {
    const geo = new THREE.PlaneGeometry(300, 300, 96, 96);
    geo.rotateX(-Math.PI / 2);
    this.waterGeo = geo;
    this.waterBase = Float32Array.from(geo.attributes.position.array);
    // Waves only out at sea: under and right around the island the water lies still, so storm
    // crests can never poke up through the ground.
    this.waveAmp = new Float32Array(geo.attributes.position.count);
    for (let i = 0; i < this.waveAmp.length; i++) {
      const d = Math.hypot(this.waterBase[i * 3], this.waterBase[i * 3 + 2]);
      this.waveAmp[i] = clamp((d - 26) / 10, 0, 1);
    }
    this.water = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: '#3f9fb4', roughness: 0.35, metalness: 0.08, flatShading: true }));
    this.water.position.y = -0.75;
    this.water.receiveShadow = true;
    this.scene.add(this.water);
  }

  _rain() {
    const n = 2600;
    const pos = new Float32Array(n * 6);
    const r = rng(77);
    for (let i = 0; i < n; i++) {
      const x = (r() - 0.5) * 90, y = r() * 40, z = (r() - 0.5) * 90;
      pos.set([x, y, z, x + 0.08, y - 0.9, z], i * 6);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.rain = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: '#cfe2ff', transparent: true, opacity: 0.45 }));
    this.rain.visible = false;
    this.scene.add(this.rain);
  }

  _stars() {
    const n = 900, pos = new Float32Array(n * 3), r = rng(5);
    for (let i = 0; i < n; i++) {
      const th = r() * TAU, ph = Math.acos(r() * 0.95);
      pos.set([Math.sin(ph) * Math.cos(th) * 180, Math.cos(ph) * 180, Math.sin(ph) * Math.sin(th) * 180], i * 3);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.stars = new THREE.Points(geo, new THREE.PointsMaterial({ color: '#ffffff', size: 0.9, transparent: true, opacity: 0, fog: false }));
    this.scene.add(this.stars);
  }

  add(obj, x = 0, y = 0, z = 0, { cast = true, receive = true } = {}) {
    obj.position.set(x, y, z);
    obj.traverse(o => { if (o.isMesh) { o.castShadow = cast; o.receiveShadow = receive; } });
    (this.root || this.scene).add(obj);
    return obj;
  }

  mesh(geo, material, x, y, z, parent) {
    const m = new THREE.Mesh(geo, material);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    (parent || this.root || this.scene).add(m);
    return m;
  }

  build(map) {
    if (this.built) return;
    this.built = true;
    this.map = map;
    this.homeSpots = map.home_slots || [];
    this.root = new THREE.Group();
    this.scene.add(this.root);
    const R = map.radius;
    this._terrain(R, map);
    const hub = map.locations.find(l => l.id === map.hub);
    this.roadSegs = [];
    this.ringR = map.ring_radius;
    const ring = new THREE.Mesh(new THREE.RingGeometry(this.ringR - 0.62, this.ringR + 0.62, 128), mat('#d4ccad'));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.03;
    ring.receiveShadow = true;
    this.root.add(ring);
    for (let k = 0; k < 4; k++) {                        // the plaza's four spokes
      const a = k * Math.PI / 2;
      this._road(Math.cos(a) * 4.4, Math.sin(a) * 4.4, Math.cos(a) * this.ringR, Math.sin(a) * this.ringR, 1.15);
    }
    for (const loc of map.locations) {
      if (loc.id === map.hub) continue;
      if (map.inner.includes(loc.id)) this._road(hub.x, hub.z, loc.x, loc.z, 1.1);
      else this._spur(loc.x, loc.z, FOOT[loc.kind] ?? 3);
    }
    for (const loc of map.locations) this._place(loc, hub);
    this._scenery(map);
  }

  _terrain(R, map) {
    const r = rng(1234);
    const phases = [r() * TAU, r() * TAU, r() * TAU];
    const base = a => R + 1.4 * Math.sin(3 * a + phases[0]) + 0.9 * Math.sin(5 * a + phases[1]) + 0.5 * Math.sin(9 * a + phases[2]);
    // Coastal places (lighthouse, cliffs, beach...) always get land under them: a smooth bulge
    // wherever the noisy coastline would cut through a footprint.
    const bumps = map.locations.map(l => {
      const a = Math.atan2(l.z, l.x), need = Math.hypot(l.x, l.z) + (FOOT[l.kind] ?? 3) * 0.75 + 1.0;
      return { a, extra: Math.max(0, need - base(a)) };
    }).filter(b => b.extra > 0);
    const radiusAt = a => base(a) + bumps.reduce((s, b) => {
      const d = Math.atan2(Math.sin(a - b.a), Math.cos(a - b.a));
      return s + b.extra * Math.exp(-((d / 0.2) ** 2));
    }, 0);
    const shape = (extra) => {
      const s = new THREE.Shape();
      for (let i = 0; i <= 160; i++) {
        const a = (i / 160) * TAU, rad = radiusAt(a) + extra;
        const x = Math.cos(a) * rad, z = Math.sin(a) * rad;
        i ? s.lineTo(x, -z) : s.moveTo(x, -z);
      }
      return s;
    };
    const slab = (extra, depth, color, y) => {
      const geo = new THREE.ExtrudeGeometry(shape(extra), { depth, bevelEnabled: true, bevelThickness: 0.35, bevelSize: 0.6, bevelSegments: 2, curveSegments: 4 });
      geo.rotateX(-Math.PI / 2);
      const m = new THREE.Mesh(geo, mat(color));
      m.position.y = y - depth - 0.35;            // bevelThickness lifts the top face too
      m.receiveShadow = true;
      this.root.add(m);
      return m;
    };
    slab(2.6, 2.2, '#e8d7a8', -0.3);        // sand
    slab(0, 2.0, '#79b083', 0.0);           // grass
    this.radiusAt = radiusAt;
  }

  _spur(x, z, foot) {
    const r = Math.hypot(x, z), a = Math.atan2(z, x), end = Math.max(this.ringR + 0.4, r - foot * 0.7);
    return this._road(Math.cos(a) * this.ringR, Math.sin(a) * this.ringR, Math.cos(a) * end, Math.sin(a) * end, 1.0);
  }

  _road(ax, az, bx, bz, width = 1.0) {
    (this.roadSegs = this.roadSegs || []).push([ax, az, bx, bz]);
    const dx = bx - ax, dz = bz - az, len = Math.hypot(dx, dz);
    const road = this.mesh(new THREE.BoxGeometry(width, 0.04, len), mat('#d4ccad'), (ax + bx) / 2, 0.03, (az + bz) / 2);
    road.rotation.y = Math.atan2(dx, dz);
    road.castShadow = false;
    return road;
  }

  _label(text, x, y, z, cls = 'place3d') {
    const el = document.createElement('div');
    el.className = cls;
    el.textContent = text;
    this.overlay.append(el);
    return { el, pos: new THREE.Vector3(x, y, z) };
  }

  _windowMat() {
    const m = new THREE.MeshStandardMaterial({ color: '#a9d8e6', emissive: '#ffcf7a', emissiveIntensity: 0, roughness: 0.3 });
    this.windowMats.push(m);
    return m;
  }

  _house(g, w, h, d, wall, roof, { gable = true, door = '#7b5640' } = {}) {
    this.mesh(new THREE.BoxGeometry(w, h, d), mat(wall), 0, h / 2, 0, g);
    if (gable) {
      const s = new THREE.Shape();
      s.moveTo(-w / 2 - 0.25, 0); s.lineTo(w / 2 + 0.25, 0); s.lineTo(0, h * 0.55); s.lineTo(-w / 2 - 0.25, 0);
      const geo = new THREE.ExtrudeGeometry(s, { depth: d + 0.5, bevelEnabled: false });
      geo.translate(0, 0, -(d + 0.5) / 2);
      this.mesh(geo, mat(roof), 0, h, 0, g);
    } else {
      this.mesh(new THREE.BoxGeometry(w + 0.3, 0.25, d + 0.3), mat(roof), 0, h + 0.12, 0, g);
    }
    this.mesh(new THREE.BoxGeometry(0.8, 1.4, 0.08), mat(door), 0, 0.7, d / 2 + 0.03, g);
    const win = this._windowMat();
    for (const sx of [-w / 3, w / 3]) this.mesh(new THREE.BoxGeometry(0.6, 0.6, 0.08), win, sx, h * 0.58, d / 2 + 0.03, g);
  }

  _place(loc, hub) {
    const g = new THREE.Group();
    g.position.set(loc.x, 0, loc.z);
    g.rotation.y = loc.id === hub.id ? 0 : Math.atan2(hub.x - loc.x, hub.z - loc.z);   // face the plaza
    this.root.add(g);
    const builder = this['_' + loc.kind];
    if (builder) builder.call(this, g, loc);
    g.traverse(o => { if (o.isMesh) { o.castShadow = !o.userData.noShadow; o.receiveShadow = true; } });
    const ringMat = new THREE.MeshBasicMaterial({ color: '#ffd27a', transparent: true, opacity: 0 });
    const ring = new THREE.Mesh(new THREE.TorusGeometry(4.2, 0.09, 6, 48), ringMat);
    ring.rotation.x = Math.PI / 2;
    ring.position.set(loc.x, 0.12, loc.z);
    this.root.add(ring);
    const labelY = { lighthouse: 11.5, town_hall: 8.6, cliffs: 6.5 }[loc.kind] ?? 5.6;
    this.places.set(loc.id, { ...loc, label: this._label(loc.name, loc.x, labelY, loc.z), ring, group: g });
  }

  // --- places --------------------------------------------------------------------------
  _plaza(g) {
    this.mesh(new THREE.CylinderGeometry(4.6, 4.6, 0.1, 40), mat('#d9d4bd'), 0, 0.05, 0, g);
    this.mesh(new THREE.CylinderGeometry(1.25, 1.25, 0.45, 24), mat('#cfcab4'), 0, 0.3, 0, g);
    this.water2 = this.mesh(new THREE.CylinderGeometry(1.08, 1.08, 0.03, 24), mat('#77c7d7', { roughness: 0.2 }), 0, 0.53, 0, g);
    this.mesh(new THREE.CylinderGeometry(0.17, 0.22, 1.2, 10), mat('#cfcab4'), 0, 0.9, 0, g);
    this.mesh(new THREE.SphereGeometry(0.34, 12, 8), mat('#e5e4cb'), 0, 1.6, 0, g);
    for (let i = 0; i < 4; i++) this._bench(g, Math.cos(i * TAU / 4 + 0.8) * 3.3, Math.sin(i * TAU / 4 + 0.8) * 3.3, -(i * TAU / 4 + 0.8) + Math.PI / 2);
    this.lamps = this.lamps || [];
    for (let i = 0; i < 4; i++) this._lamp(g, Math.cos(i * TAU / 4) * 4.3, Math.sin(i * TAU / 4) * 4.3);
  }

  _bench(g, x, z, rot) {
    const b = new THREE.Group();
    this.mesh(new THREE.BoxGeometry(1.6, 0.12, 0.45), mat('#91634b'), 0, 0.5, 0, b);
    this.mesh(new THREE.BoxGeometry(1.6, 0.5, 0.1), mat('#91634b'), 0, 0.8, -0.22, b);
    for (const s of [-0.65, 0.65]) this.mesh(new THREE.BoxGeometry(0.1, 0.5, 0.4), mat('#4c4c4c'), s, 0.25, 0, b);
    b.position.set(x, 0, z);
    b.rotation.y = rot;
    g.add(b);
  }

  _lamp(g, x, z) {
    this.mesh(new THREE.CylinderGeometry(0.06, 0.08, 2.6, 6), mat('#2f3b3a'), x, 1.3, z, g);
    const bulb = this.mesh(new THREE.SphereGeometry(0.2, 10, 8), new THREE.MeshStandardMaterial({ color: '#fff4cf', emissive: '#ffd27a', emissiveIntensity: 0 }), x, 2.7, z, g);
    this.lamps = this.lamps || [];
    this.lamps.push(bulb.material);
  }

  _cafe(g) {
    this._house(g, 5.2, 3.0, 4.0, '#f3e3c7', '#c9705a');
    const stripes = ['#e7685e', '#fff6e8'];
    for (let i = 0; i < 8; i++) {
      const s = this.mesh(new THREE.BoxGeometry(0.62, 0.06, 1.4), mat(stripes[i % 2]), -2.2 + i * 0.63, 2.35, 2.6, g);
      s.rotation.x = 0.35;
    }
    for (const x of [-1.6, 1.6]) {
      this.mesh(new THREE.CylinderGeometry(0.5, 0.5, 0.08, 14), mat('#f6f1e4'), x, 0.85, 4.3, g);
      this.mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.85, 6), mat('#555'), x, 0.42, 4.3, g);
      this.mesh(new THREE.ConeGeometry(1.0, 0.5, 8), mat(x < 0 ? '#e7685e' : '#ffd27a'), x, 2.0, 4.3, g);
      this.mesh(new THREE.CylinderGeometry(0.04, 0.04, 1.3, 6), mat('#555'), x, 1.4, 4.3, g);
    }
  }

  _market(g) {
    const colors = ['#e7685e', '#5fb3d9', '#ffd27a', '#8ad98f'];
    for (let i = 0; i < 4; i++) {
      const x = (i % 2 ? 1.45 : -1.45), s = new THREE.Group();
      for (const [px, pz] of [[-1, -0.7], [1, -0.7], [-1, 0.7], [1, 0.7]]) this.mesh(new THREE.BoxGeometry(0.1, 2, 0.1), mat('#7b5640'), px, 1, pz, s);
      this.mesh(new THREE.BoxGeometry(2.2, 0.8, 1.2), mat('#a87953'), 0, 0.4, 0, s);
      const top = this.mesh(new THREE.BoxGeometry(2.5, 0.08, 1.9), mat(colors[i]), 0, 2.05, 0, s);
      top.rotation.x = -0.18;
      for (let j = 0; j < 3; j++) this.mesh(new THREE.SphereGeometry(0.18, 8, 6), mat(['#ff9f43', '#e74c3c', '#f6e58d'][j]), -0.6 + j * 0.6, 0.92, 0, s);
      s.position.set(x, 0, i < 2 ? 0.8 : -1.8);
      g.add(s);
    }
  }

  _library(g) {
    this.mesh(new THREE.BoxGeometry(6.2, 3.6, 4.4), mat('#d8cfba'), 0, 1.8, 0, g);
    for (let i = 0; i < 4; i++) this.mesh(new THREE.CylinderGeometry(0.22, 0.25, 3.2, 10), mat('#eee8d8'), -2.1 + i * 1.4, 1.6, 2.6, g);
    const s = new THREE.Shape();
    s.moveTo(-3.5, 0); s.lineTo(3.5, 0); s.lineTo(0, 1.4); s.lineTo(-3.5, 0);
    const geo = new THREE.ExtrudeGeometry(s, { depth: 5.6, bevelEnabled: false });
    geo.translate(0, 0, -2.4);
    this.mesh(geo, mat('#bfb39b'), 0, 3.6, 0, g);
    this.mesh(new THREE.BoxGeometry(5, 0.3, 1.2), mat('#cfc6b0'), 0, 0.15, 3.0, g);
    const win = this._windowMat();
    for (const x of [-2, 0, 2]) this.mesh(new THREE.BoxGeometry(0.7, 1.3, 0.08), win, x, 2.1, 2.23, g);
  }

  _town_hall(g) {
    this._house(g, 6, 3.4, 4.6, '#e9dcc6', '#6d8d96');
    this.mesh(new THREE.BoxGeometry(1.7, 3.6, 1.7), mat('#e3d4b8'), 0, 5.2, 0, g);
    this.mesh(new THREE.ConeGeometry(1.4, 1.6, 4), mat('#6d8d96'), 0, 7.8, 0, g).rotation.y = Math.PI / 4;
    this.mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.08, 20), mat('#fffaf0'), 0, 5.8, 0.88, g).rotation.x = Math.PI / 2;
    this.clockHand = this.mesh(new THREE.BoxGeometry(0.06, 0.45, 0.04), mat('#222'), 0, 5.8, 0.95, g);
    this.clockHand.geometry.translate(0, 0.2, 0);
    this.mesh(new THREE.CylinderGeometry(0.04, 0.04, 2, 6), mat('#555'), 1.6, 4.4, 1.6, g);
    this.flag = this.mesh(new THREE.BoxGeometry(0.9, 0.5, 0.03), mat('#a8f5b8'), 2.05, 5.1, 1.6, g);
  }

  _clinic(g) {
    this._house(g, 4.6, 2.6, 3.6, '#f7f7f2', '#d6d6cf', { gable: false, door: '#8fb3c4' });
    this.mesh(new THREE.BoxGeometry(0.9, 0.25, 0.08), mat('#e74c3c'), 0, 3.3, 1.85, g);
    this.mesh(new THREE.BoxGeometry(0.25, 0.9, 0.08), mat('#e74c3c'), 0, 3.3, 1.85, g);
  }

  _workshop(g) {
    this._house(g, 6, 3.2, 4.6, '#8a6a52', '#5c4636', { door: '#4a3a2c' });
    this.mesh(new THREE.BoxGeometry(0.6, 2, 0.6), mat('#6b6b6b'), 2, 4.4, -1, g);
    const hull = this.mesh(new THREE.SphereGeometry(1, 14, 10), mat('#2f6f8f'), -1.5, 0.9, 4.2, g);
    hull.scale.set(0.9, 0.5, 2.4);
    for (const z of [3.2, 5.2]) this.mesh(new THREE.BoxGeometry(1.4, 0.5, 0.3), mat('#7b5640'), -1.5, 0.25, z, g);
    this.smoke = [];
    for (let i = 0; i < 6; i++) {
      const p = this.mesh(new THREE.SphereGeometry(0.3, 8, 6), new THREE.MeshStandardMaterial({ color: '#d8d8d8', transparent: true, opacity: 0.5 }), 2, 5.5 + i, -1, g);
      p.castShadow = false;
      p.userData.noShadow = true;
      this.smoke.push(p);
    }
  }

  _lighthouse(g) {
    for (let i = 0; i < 6; i++) {
      this.mesh(new THREE.CylinderGeometry(1.25 - i * 0.07, 1.32 - i * 0.07, 1.4, 18), mat(i % 2 ? '#f6f3ee' : '#d9473b'), 0, 0.7 + i * 1.4, 0, g);
    }
    this.mesh(new THREE.TorusGeometry(1.15, 0.08, 6, 24), mat('#333'), 0, 8.5, 0, g).rotation.x = Math.PI / 2;
    this.lampRoom = this.mesh(new THREE.CylinderGeometry(0.75, 0.75, 1.1, 14), new THREE.MeshStandardMaterial({ color: '#fff7d1', emissive: '#ffe08a', emissiveIntensity: 0.1, transparent: true, opacity: 0.85 }), 0, 9.1, 0, g);
    this.mesh(new THREE.ConeGeometry(0.95, 0.9, 14), mat('#d9473b'), 0, 10.1, 0, g);
    const beamGeo = new THREE.ConeGeometry(2.4, 26, 20, 1, true);
    beamGeo.translate(0, -13, 0);
    beamGeo.rotateZ(Math.PI / 2);
    this.beam = this.mesh(beamGeo, new THREE.MeshBasicMaterial({ color: '#fff3c4', transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }), 0, 9.1, 0, g);
    this.beam.castShadow = false;
    this.beam.userData.noShadow = true;
    this._house(g, 2.6, 1.8, 2.2, '#f6f3ee', '#d9473b');
    g.children.slice(-5).forEach(o => o.position.x += 3.2);
  }

  _dock(g, loc) {
    // The pier points out to sea (away from the plaza = -z in local space).
    for (let i = 0; i < 9; i++) this.mesh(new THREE.BoxGeometry(2.4, 0.18, 1.3), mat(i % 2 ? '#a07a58' : '#8f6b4c'), 0, 0.25, -2 - i * 1.32, g);
    for (let i = 0; i < 5; i++) for (const x of [-1.2, 1.2]) this.mesh(new THREE.CylinderGeometry(0.12, 0.12, 1.6, 6), mat('#6b4f39'), x, -0.3, -2 - i * 2.8, g);
    const boat = new THREE.Group();
    const hull = this.mesh(new THREE.BoxGeometry(2.4, 1.1, 6.5), mat('#f2f2ee'), 0, 0.2, 0, boat);
    this.mesh(new THREE.BoxGeometry(2.0, 1.3, 2.4), mat('#2f6f8f'), 0, 1.3, 0.6, boat);
    this.mesh(new THREE.CylinderGeometry(0.22, 0.22, 1.2, 8), mat('#d9473b'), 0, 2.4, 0.6, boat);
    hull.castShadow = true;
    boat.position.set(3.4, -0.2, -8);
    g.add(boat);
    this.ferry = { boat, docked: new THREE.Vector3(3.4, -0.2, -8), away: new THREE.Vector3(3.4, -0.2, -70) };
    boat.position.copy(this.ferry.away);
    const skiff = this.mesh(new THREE.SphereGeometry(1, 12, 8), mat('#d9a441'), -2.6, -0.1, -5, g);
    skiff.scale.set(0.7, 0.35, 1.8);
  }

  _rowing_club(g) {
    this._house(g, 6.2, 2.8, 4, '#5f8fa8', '#2f4f63', { door: '#e9e2d0' });
    for (let i = 0; i < 3; i++) {
      const shell = this.mesh(new THREE.BoxGeometry(0.4, 0.22, 6), mat(['#ffd27a', '#e7685e', '#f6f3ee'][i]), 3.6, 0.5 + i * 0.45, 0.5, g);
      shell.rotation.x = 0.02;
    }
    for (const z of [-2, 3]) this.mesh(new THREE.BoxGeometry(0.15, 1.6, 0.15), mat('#555'), 3.6, 0.8, z, g);
  }

  _beach(g) {
    const sand = this.mesh(new THREE.CylinderGeometry(6, 6, 0.06, 32), mat('#f0dfb0'), 0, 0.04, -3, g);
    sand.scale.set(1.45, 1, 0.75);
    for (let i = 0; i < 8; i++) this.mesh(new THREE.DodecahedronGeometry(0.32, 0), mat('#8f8a7c'), Math.cos(i * TAU / 8) * 1.1, 0.15, Math.sin(i * TAU / 8) * 1.1, g);
    for (let i = 0; i < 4; i++) this.mesh(new THREE.BoxGeometry(0.22, 0.22, 1.5), mat('#7b5640'), 0, 0.25, 0, g).rotation.y = i * Math.PI / 4;
    this.flame = this.mesh(new THREE.ConeGeometry(0.45, 1.1, 7), new THREE.MeshStandardMaterial({ color: '#ffb347', emissive: '#ff7a2a', emissiveIntensity: 1.2 }), 0, 0.85, 0, g);
    this.flame.castShadow = false;
    this.flame.userData.noShadow = true;
    this.fireLight = new THREE.PointLight('#ffad5c', 0, 14, 2);
    this.fireLight.position.set(0, 1.4, 0);
    g.add(this.fireLight);
    for (const [x, z, c] of [[-5, -2, '#e7685e'], [5, -1, '#5fb3d9'], [8, -3, '#ffd27a']]) {
      this.mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.2, 6), mat('#eee'), x, 1.1, z, g);
      this.mesh(new THREE.ConeGeometry(1.3, 0.5, 10), mat(c), x, 2.2, z, g);
    }
  }

  _cliffs(g) {
    const hill = this.mesh(new THREE.CylinderGeometry(3.2, 4.8, 3, 9), mat('#6e9e72'), 0, 1.5, -1, g);
    hill.rotation.y = 0.3;
    const r = rng(9);
    for (let i = 0; i < 9; i++) {
      const a = r() * TAU;
      const rock = this.mesh(new THREE.DodecahedronGeometry(0.8 + r() * 1.0, 0), mat('#8c8a82'), Math.cos(a) * 4.4, 0.6 + r() * 0.8, -1 + Math.sin(a) * 4.4, g);
      rock.rotation.set(r(), r(), r());
    }
    this._bench(g, 0, -2.5, Math.PI);
    g.children.slice(-1)[0].position.y = 3;
  }

  _greenhouse(g) {
    const glass = new THREE.MeshStandardMaterial({ color: '#cfeff5', transparent: true, opacity: 0.35, roughness: 0.1 });
    for (const x of [-2.4, 2.4]) {
      this.mesh(new THREE.BoxGeometry(3.8, 2.4, 5.5), glass, x, 1.2, 0, g).userData.noShadow = true;
      const s = new THREE.Shape();
      s.moveTo(-2, 0); s.lineTo(2, 0); s.lineTo(0, 1.2); s.lineTo(-2, 0);
      const geo = new THREE.ExtrudeGeometry(s, { depth: 5.5, bevelEnabled: false });
      geo.translate(0, 0, -2.75);
      this.mesh(geo, glass, x, 2.4, 0, g).userData.noShadow = true;
      for (let i = 0; i < 6; i++) this.mesh(new THREE.IcosahedronGeometry(0.4, 0), mat(['#3c9871', '#54a77f', '#9fd36b'][i % 3]), x - 1 + (i % 2) * 2, 0.5, -2 + Math.floor(i / 2) * 2, g);
    }
    for (let i = 0; i < 3; i++) {
      this.mesh(new THREE.BoxGeometry(1.4, 0.3, 3), mat('#7b5640'), -3 + i * 3, 0.15, 5, g);
      for (let j = 0; j < 4; j++) this.mesh(new THREE.ConeGeometry(0.2, 0.5, 5), mat('#4d8f48'), -3 + i * 3, 0.55, 3.9 + j * 0.75, g);
    }
  }

  _scenery(map) {
    const r = rng(912343);
    const blockers = map.locations.map(l => [l.x, l.z, (FOOT[l.kind] ?? 3) + 1.2]);
    const nearRoad = (x, z) => Math.abs(Math.hypot(x, z) - this.ringR) < 1.8 || this.roadSegs.some(([ax, az, bx, bz]) => {
      const t = clamp(((x - ax) * (bx - ax) + (z - az) * (bz - az)) / ((bx - ax) ** 2 + (bz - az) ** 2 || 1), 0, 1);
      return Math.hypot(x - (ax + t * (bx - ax)), z - (az + t * (bz - az))) < 1.6;
    });
    const leaves = ['#3c9871', '#54a77f', '#72b085', '#4a8d70'];
    let placed = 0;
    for (let i = 0; i < 900 && placed < 150; i++) {
      const a = r() * TAU, d = Math.sqrt(r()) * (map.radius - 2.5);
      const x = Math.cos(a) * d, z = Math.sin(a) * d;
      if (blockers.some(([bx, bz, br]) => Math.hypot(x - bx, z - bz) < br) || nearRoad(x, z)) continue;
      if (Math.hypot(x, z) < 5.5) continue;
      if (this.homeSpots && this.homeSpots.some(([hx, hz]) => Math.hypot(x - hx, z - hz) < 3.2 || this._nearSeg(x, z, hx, hz))) continue;
      const s = 0.7 + r() * 0.8, kind = placed % 3;
      const tree = new THREE.Group();
      this.mesh(new THREE.CylinderGeometry(0.13 * s, 0.22 * s, 1.2 * s, 6), mat('#7d5a43'), 0, 0.6 * s, 0, tree);
      if (kind === 0) this.mesh(new THREE.ConeGeometry(0.9 * s, 2.3 * s, 7), mat(leaves[placed % 4]), 0, 2.0 * s, 0, tree);
      else for (const [ox, oy, oz, rr] of [[0, 1.7, 0, 0.8], [-0.45, 1.95, 0, 0.62], [0.38, 2.0, 0.22, 0.64]])
        this.mesh(new THREE.IcosahedronGeometry(rr * s, 0), mat(leaves[(placed + 1) % 4]), ox * s, oy * s, oz * s, tree);
      tree.position.set(x, 0, z);
      tree.rotation.y = r() * TAU;
      this.root.add(tree);
      placed++;
    }
    for (let i = 0; i < 40; i++) {   // rocks on the shore
      const a = r() * TAU, d = this.radiusAt(a) + 1.2 + r() * 1.5;
      const rock = this.mesh(new THREE.DodecahedronGeometry(0.4 + r() * 0.7, 0), mat('#8c8a82'), Math.cos(a) * d, -0.3, Math.sin(a) * d);
      rock.rotation.set(r(), r(), r());
    }
  }

  _nearSeg(x, z, hx, hz) {             // the (future) spur from the ring to a cottage
    const a = Math.atan2(hz, hx), ax = Math.cos(a) * this.ringR, az = Math.sin(a) * this.ringR;
    const t = clamp(((x - ax) * (hx - ax) + (z - az) * (hz - az)) / ((hx - ax) ** 2 + (hz - az) ** 2 || 1), 0, 1);
    return Math.hypot(x - (ax + t * (hx - ax)), z - (az + t * (hz - az))) < 1.4;
  }

  // --------------------------------------------------------------- residents
  setHomes(residents) {
    for (const r of residents) {
      if (this.cottages.has(r.id)) continue;
      const [x, z] = r.home_xy;
      const g = new THREE.Group();
      g.position.set(x, 0, z);
      g.rotation.y = Math.atan2(-x, -z);           // door toward the ring road
      this.root.add(g);
      const roof = new THREE.Color(r.color).multiplyScalar(0.8).getStyle();
      const before = this.windowMats.length;
      this._house(g, 2.8, 2.1, 2.6, '#f4ecdf', roof);
      this.mesh(new THREE.BoxGeometry(0.4, 1, 0.4), mat('#8c8a82'), 0.8, 2.9, -0.4, g);
      const spur = this._spur(x, z, 2.2);
      g.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      this.cottages.set(r.id, { id: r.id, group: g, spur, windows: this.windowMats.slice(before), label: this._label(`${r.name}'s cottage`, x, 4.2, z, 'place3d') });
    }
  }

  _figure(r) {
    const g = new THREE.Group();
    const suit = mat(r.color), dark = mat('#243a3a'), skin = mat('#f1d3bd');
    const torso = this.mesh(new THREE.CapsuleGeometry(0.3, 0.45, 5, 10), suit, 0, 0.92, 0, g);
    this.mesh(new THREE.SphereGeometry(0.28, 16, 10), skin, 0, 1.65, 0, g);
    this.mesh(new THREE.SphereGeometry(0.295, 16, 8, 0, TAU, 0, Math.PI * 0.45), dark, 0, 1.69, 0, g);
    for (const ex of [-0.1, 0.1]) this.mesh(new THREE.SphereGeometry(0.025, 7, 5), dark, ex, 1.66, 0.266, g);
    const legL = this.mesh(new THREE.CapsuleGeometry(0.085, 0.27, 4, 6), dark, -0.13, 0.34, 0, g);
    const legR = this.mesh(new THREE.CapsuleGeometry(0.085, 0.27, 4, 6), dark, 0.13, 0.34, 0, g);
    const armL = this.mesh(new THREE.CapsuleGeometry(0.07, 0.29, 4, 7), suit, -0.38, 1.05, 0, g);
    const armR = this.mesh(new THREE.CapsuleGeometry(0.07, 0.29, 4, 7), suit, 0.38, 1.05, 0, g);
    armL.rotation.z = -0.18; armR.rotation.z = 0.18;
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.58, 0.06, 6, 28), new THREE.MeshBasicMaterial({ color: '#d9ffda' }));
    ring.rotation.x = Math.PI / 2; ring.position.y = 0.08; ring.visible = false; g.add(ring);
    g.traverse(o => { o.userData.resident = r.id; });
    this.root.add(g);
    const tag = document.createElement('div');
    tag.className = 'tag3d';
    tag.style.background = r.color;
    tag.textContent = r.name;
    this.overlay.append(tag);
    return { id: r.id, group: g, torso, legL, legR, armL, armR, ring, tag, path: null, walkStart: 0, walkDur: 0,
             lastPathKey: '', asleep: false, talking: false, facing: null, color: r.color, name: r.name };
  }

  syncResidents(residents, { walkSeconds = 4 } = {}) {
    this.setHomes(residents);
    const now = performance.now();
    for (const r of residents) {
      let f = this.figures.get(r.id);
      if (!f) { f = this._figure(r); this.figures.set(r.id, f); f.group.position.set(r.x, 0, r.z); }
      const key = JSON.stringify(r.path);
      if (key !== f.lastPathKey) {
        f.lastPathKey = key;
        const pts = (r.path && r.path.length > 1 ? r.path : [[r.x, r.z]]).map(([x, z]) => new THREE.Vector3(x, 0, z));
        pts[0].copy(f.group.position);                          // start from wherever they are drawn now
        const len = pts.reduce((s, p, i) => i ? s + p.distanceTo(pts[i - 1]) : 0, 0);
        f.path = pts;
        f.pathLen = len;
        f.walkStart = now;
        f.walkDur = len < 0.05 ? 0 : clamp(len / 5.5, 1.0, walkSeconds) * 1000;
      }
      f.asleep = r.asleep;
      f.talking = (r.talking_with || []).length > 0;
      f.partner = (r.talking_with || [])[0] || null;
      f.dim = r.asleep;
      f.status = r.doing;
      const cottage = this.cottages.get(r.id);
      if (cottage) cottage.home = r.place === r.home;
    }
  }

  _walk(f, now, t) {
    let moving = false;
    if (f.path && f.path.length > 1 && f.walkDur > 0) {
      const p = clamp((now - f.walkStart) / f.walkDur, 0, 1);
      let target = smooth(p) * f.pathLen;
      for (let i = 1; i < f.path.length; i++) {
        const seg = f.path[i].distanceTo(f.path[i - 1]);
        if (target <= seg || i === f.path.length - 1) {
          const k = seg ? clamp(target / seg, 0, 1) : 1;
          const pos = f.path[i - 1].clone().lerp(f.path[i], k);
          const dx = pos.x - f.group.position.x, dz = pos.z - f.group.position.z;
          if (Math.hypot(dx, dz) > 0.002) f.group.rotation.y = Math.atan2(dx, dz);
          f.group.position.copy(pos);
          break;
        }
        target -= seg;
      }
      moving = p < 1;
    }
    const stride = moving ? Math.sin(t * 11 + f.id.length) * 0.5 : 0;
    for (const [limb, sign] of [[f.legL, 1], [f.legR, -1], [f.armL, -1], [f.armR, 1]]) {
      limb.rotation.x = moving ? stride * sign * 0.7 : limb.rotation.x * 0.85;
    }
    f.torso.position.y = 0.92 + (moving ? Math.abs(Math.sin(t * 11)) * 0.04 : (f.talking ? Math.abs(Math.sin(t * 6)) * 0.02 : 0));
    if (!moving && f.talking && f.partner && this.figures.has(f.partner)) {
      const o = this.figures.get(f.partner).group.position;
      const want = Math.atan2(o.x - f.group.position.x, o.z - f.group.position.z);
      f.group.rotation.y = lerp(f.group.rotation.y, want, 0.08);
    }
    const home = f.asleep && !moving;
    f.group.visible = !home;
    return moving;
  }

  clearResidents() {          // a different world is opening: take the last cast off the island
    for (const f of this.figures.values()) { this.root.remove(f.group); f.tag.remove(); }
    for (const c of this.cottages.values()) {
      this.root.remove(c.group);
      this.root.remove(c.spur);
      c.label.el.remove();
      this.windowMats = this.windowMats.filter(m => !c.windows.includes(m));
    }
    for (const b of [...this.bubbles, ...this.pops]) b.el.remove();
    this.figures.clear();
    this.cottages.clear();
    this.bubbles = [];
    this.pops = [];
    this.selected = null;
    this.cinema = { next: 0, focus: null };
  }

  select(id) {
    this.selected = id;
    for (const [rid, f] of this.figures) f.ring.visible = rid === id;
  }

  bubble(id, text, kind = 'say', toName = '') {
    const f = this.figures.get(id);
    if (!f || !text) return;
    const now = performance.now();
    for (const b of this.bubbles) if (b.id === id) b.expire = Math.min(b.expire, now + 300);
    const live = this.bubbles.filter(b => b.expire > now + 300).sort((a, c) => a.born - c.born);
    for (const b of live.slice(0, Math.max(0, live.length - (this.maxBubbles - 1)))) b.expire = now + 300;
    const el = document.createElement('div');
    el.className = 'bubble' + (kind === 'inner' ? ' inner' : kind === 'letter' ? ' letter' : '');
    const head = document.createElement('span');
    head.className = 'to';
    const dot = document.createElement('i');
    dot.style.background = f.color;
    head.append(dot, kind === 'say' && toName ? `${f.name} → ${toName}` : kind === 'inner' ? `${f.name} (to themselves)` : f.name);
    el.append(head);
    el.append(document.createTextNode(text.length > 190 ? text.slice(0, 187) + '…' : text));
    this.overlay.append(el);
    const life = clamp(2500 + text.length * 45, 3500, 9000);
    this.bubbles.push({ id, el, born: performance.now(), expire: performance.now() + life });
  }

  pop(id, text) {
    const f = this.figures.get(id);
    if (!f) return;
    const el = document.createElement('div');
    el.className = 'pop';
    el.textContent = text;
    this.overlay.append(el);
    this.pops.push({ id, el, expire: performance.now() + 3200 });
  }

  onPick(fn) { this.pickHandlers.push(fn); }

  _picking() {
    const ray = new THREE.Raycaster(), v = new THREE.Vector2();
    let down = null;
    this.canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    this.canvas.addEventListener('pointerup', e => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
      const b = this.canvas.getBoundingClientRect();
      v.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1);
      ray.setFromCamera(v, this.camera);
      const hits = ray.intersectObjects([...this.figures.values()].map(f => f.group), true);
      const id = hits.find(h => h.object.userData.resident)?.object.userData.resident;
      if (id) this.pickHandlers.forEach(fn => fn(id));
    });
  }

  setCameraMode(mode) {
    this.camMode = mode;
    this.controls.autoRotate = mode === 'cinema' || this.lobby;
    this.controls.autoRotateSpeed = this.lobby ? 0.35 : 0.25;
    this.cinema.next = 0;
  }

  setLobby(on) {
    this.lobby = on;
    this.controls.autoRotate = on;
    this.controls.autoRotateSpeed = 0.35;
    this.overlay.style.display = on ? 'none' : '';
  }

  focusOn(id) {             // cinema: cut to whoever just spoke, but never more than every 6 s
    const now = performance.now();
    if (now < (this.cinema.lockUntil || 0) || !this.figures.get(id)?.group.visible) return;
    Object.assign(this.cinema, { focus: id, next: now + 10000, lockUntil: now + 6000 });
  }

  // ------------------------------------------------------------- time & weather
  setTime(minutes, weather, events = []) {
    this.hour = (minutes % 1440) / 60;
    this.weather = weather;
    this.ferryDocked = events.some(e => e.kind === 'ferry' && e.title === 'Ferry arrived' && e.active);
    this.bonfireBoost = events.some(e => e.place === 'beach' && e.active) ? 1 : 0;
    const active = new Set(events.filter(e => e.active && e.place).map(e => e.place));
    for (const [id, p] of this.places) {
      p.eventActive = active.has(id);
      p.label.el.classList.toggle('event', p.eventActive);
    }
  }

  _sky(t) {
    const h = this.hour;
    const day = clamp(Math.sin(Math.PI * (h - 6) / 14), 0, 1);          // 0 at night, 1 at noon
    const dusk = clamp(1 - Math.abs(h - 19.7) / 1.3, 0, 1) + clamp(1 - Math.abs(h - 6.4) / 1.0, 0, 1);
    const w = this.weather;
    const gloom = { clear: 0, cloudy: 0.35, rain: 0.55, storm: 0.78, fog: 0.4 }[w] ?? 0;
    let sky = keyframe(SKY, h);
    sky.lerp(new THREE.Color(day > 0.2 ? '#8e9aa3' : '#1a1f2c'), gloom * 0.75);
    let flash = 0;
    if (w === 'storm' && t > this.lightningAt) {
      this.lightningAt = t + 4 + Math.random() * 9;
      this.flashUntil = t + 0.18;
    }
    if (this.flashUntil && t < this.flashUntil) flash = 1;
    this.scene.background.copy(sky);
    if (flash) this.scene.background.lerp(new THREE.Color('#e8eeff'), 0.6);
    this.scene.fog.color.copy(this.scene.background);
    const fogNear = w === 'fog' ? 12 : w === 'storm' ? 35 : 70, fogFar = w === 'fog' ? 70 : w === 'storm' ? 140 : 210;
    this.scene.fog.near = lerp(this.scene.fog.near, fogNear, 0.02);
    this.scene.fog.far = lerp(this.scene.fog.far, fogFar, 0.02);
    const az = Math.PI * (h - 6) / 14;
    this.sun.position.set(Math.cos(az) * 60, 10 + day * 60, -20 + Math.sin(az) * 25);
    this.sun.intensity = 3.0 * day * (1 - gloom * 0.8) + flash * 2;
    this.sun.color.set(dusk > 0.3 ? '#ffb27a' : '#fff3d6');
    this.hemi.intensity = 0.45 + 1.6 * day * (1 - gloom * 0.5) + flash * 2;
    this.hemi.color.set(day > 0.15 ? '#e9f9ff' : '#8aa0d8');
    this.moon.intensity = (1 - day) * 0.55;
    const dark = 1 - clamp(day * 1.6 * (1 - gloom * 0.6), 0, 1);
    this.stars.material.opacity = clamp(dark * (1 - gloom) - 0.15, 0, 1);
    this.water.material.color.set(day > 0.2 ? (gloom > 0.5 ? '#4a7f8c' : '#3f9fb4') : '#1d3f55');
    for (const m of this.lamps || []) m.emissiveIntensity = dark > 0.45 ? 1.6 : 0;
    for (const m of this.windowMats) m.emissiveIntensity = dark > 0.5 ? 0.9 : 0;
    for (const c of this.cottages.values()) for (const m of c.windows) m.emissiveIntensity = dark > 0.4 && c.home && h < 23 && h >= 7 ? 1.1 : 0;
    if (this.beam) {
      const on = dark > 0.45 || w === 'fog' || w === 'storm';
      this.beam.material.opacity = on ? 0.16 : 0;
      this.beam.rotation.y = t * 0.7;
      this.lampRoom.material.emissiveIntensity = on ? 2.2 : 0.1;
    }
    if (this.flame) {
      const evening = (h >= 19 && h < 23) || this.bonfireBoost;
      const lit = evening && w !== 'storm' && w !== 'rain';
      this.flame.visible = lit;
      this.flame.scale.y = 0.8 + Math.sin(t * 12) * 0.2;
      this.fireLight.intensity = lit ? (14 + Math.sin(t * 17) * 3) * (0.6 + 0.6 * dark) : 0;
    }
    this.rain.visible = w === 'rain' || w === 'storm';
    if (this.rain.visible) {
      const speed = w === 'storm' ? 55 : 32;
      const pos = this.rain.geometry.attributes.position.array, dt = Math.min(0.05, this.dt || 0.016);
      const drift = w === 'storm' ? 6 : 1;
      const count = w === 'storm' ? pos.length : pos.length * 0.55;
      for (let i = 0; i < count; i += 6) {
        pos[i + 1] -= speed * dt; pos[i + 4] -= speed * dt; pos[i] += drift * dt; pos[i + 3] += drift * dt;
        if (pos[i + 4] < -0.5) { pos[i + 1] += 40; pos[i + 4] += 40; pos[i] -= drift * 0.7; pos[i + 3] -= drift * 0.7; }
      }
      for (let i = Math.floor(count / 6) * 6; i < pos.length; i += 6) { pos[i + 1] = -10; pos[i + 4] = -10; }
      this.rain.geometry.attributes.position.needsUpdate = true;
    }
  }

  _waves(t) {
    const p = this.waterGeo.attributes.position.array, base = this.waterBase;
    const amp = { storm: 0.7, rain: 0.35 }[this.weather] ?? 0.18;
    for (let i = 0, v = 0; i < p.length; i += 3, v++) {
      const x = base[i], z = base[i + 2], a = amp * this.waveAmp[v];
      p[i + 1] = Math.sin(x * 0.18 + t * 1.1) * a + Math.cos(z * 0.15 + t * 0.9) * a * 0.8;
    }
    this.waterGeo.attributes.position.needsUpdate = true;
    if ((this._waveFrame = (this._waveFrame || 0) + 1) % 3 === 0) this.waterGeo.computeVertexNormals();
  }

  // ------------------------------------------------------------------- frame
  _project(v, el, yOffset = 0) {
    const p = v.clone();
    p.y += yOffset;
    p.project(this.camera);
    const visible = p.z < 1 && p.x > -1.2 && p.x < 1.2 && p.y > -1.2 && p.y < 1.2;
    el.style.display = visible ? '' : 'none';
    if (!visible) return;
    const w = this.overlay.clientWidth, h = this.overlay.clientHeight;
    el.style.left = `${(p.x * 0.5 + 0.5) * w}px`;
    el.style.top = `${(-p.y * 0.5 + 0.5) * h}px`;
  }

  _camera(now) {
    if (this.lobby) return;
    let focusId = null;
    if (this.camMode === 'follow') focusId = this.selected;
    if (this.camMode === 'cinema') {
      if (now > this.cinema.next || !this.cinema.focus) {
        const ids = [...this.figures.keys()].filter(id => this.figures.get(id).group.visible);
        if (!this.cinema.focus || now > this.cinema.next) {
          this.cinema.focus = ids.length ? ids[Math.floor(Math.random() * ids.length)] : null;
          this.cinema.next = now + 10000;
        }
      }
      focusId = this.cinema.focus;
    }
    const f = focusId && this.figures.get(focusId);
    if (f && f.group.visible) {
      const target = f.group.position.clone().setY(1.2);
      const delta = target.clone().sub(this.controls.target).multiplyScalar(this.camMode === 'cinema' ? 0.025 : 0.08);
      this.controls.target.add(delta);
      this.camera.position.add(delta);
      if (this.camMode === 'cinema') {
        const dist = this.camera.position.distanceTo(this.controls.target);
        if (dist > 22) this.camera.position.lerp(this.controls.target, 0.01);
      }
    }
  }

  _frame() {
    const dt = this.clock.getDelta();
    this.dt = dt;
    const t = this.clock.elapsedTime, now = performance.now();
    this._sky(t);
    this._waves(t);
    if (this.water2) this.water2.material.color.setHSL(0.52, 0.45, 0.6 + Math.sin(t * 1.5) * 0.03);
    if (this.clockHand) this.clockHand.rotation.z = -this.hour / 12 * TAU;
    if (this.flag) this.flag.rotation.y = Math.sin(t * 2.2) * 0.25;
    if (this.smoke) this.smoke.forEach((p, i) => { const k = (t * 0.35 + i / 6) % 1; p.position.y = 4.8 + k * 4; p.material.opacity = 0.45 * (1 - k); p.scale.setScalar(0.6 + k * 1.4); });
    if (this.ferry) this.ferry.boat.position.lerp(this.ferryDocked ? this.ferry.docked : this.ferry.away, 0.004);
    for (const p of this.places.values()) {
      p.ring.material.opacity = p.eventActive ? 0.35 + Math.sin(t * 3) * 0.25 : 0;
      p.ring.scale.setScalar(1 + (p.eventActive ? Math.sin(t * 3) * 0.04 : 0));
    }
    for (const f of this.figures.values()) this._walk(f, now, t);
    this._camera(now);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    if (this.lobby) return;
    // overlay
    for (const p of this.places.values()) this._project(p.label.pos, p.label.el);
    for (const c of this.cottages.values()) {
      const near = c.id === this.selected || this.camera.position.distanceTo(c.label.pos) < 34;
      if (near) this._project(c.label.pos, c.label.el); else c.label.el.style.display = 'none';
    }
    for (const f of this.figures.values()) {
      if (!f.group.visible) { f.tag.style.display = 'none'; continue; }
      this._project(f.group.position, f.tag, 2.35);
    }
    this.bubbles = this.bubbles.filter(b => {
      const f = this.figures.get(b.id);
      if (!f || now > b.expire + 450) { b.el.remove(); return false; }
      if (now > b.expire) b.el.classList.add('fade');
      const anchor = f.group.visible ? f.group.position : (this.cottages.get(b.id)?.group.position || f.group.position);
      this._project(anchor, b.el, f.group.visible ? 2.75 : 3.6);
      return true;
    });
    // Bubbles over people standing together would cover each other: stack them upward.
    const placed = [];
    for (const b of [...this.bubbles].sort((a, c) => a.born - c.born)) {
      if (b.el.style.display === 'none') continue;
      let x = parseFloat(b.el.style.left), y = parseFloat(b.el.style.top);
      const w = b.el.offsetWidth, h = b.el.offsetHeight;
      for (let guard = 0; guard < 6; guard++) {
        const hit = placed.find(p => Math.abs(p.x - x) < (p.w + w) / 2 && y > p.y - p.h - 6 && y - h < p.y + 6);
        if (!hit) break;
        y = hit.y - hit.h - 8;
      }
      b.el.style.top = `${y}px`;
      if (y - h < 64) { b.el.style.display = 'none'; continue; }     // would hide under the top bar
      placed.push({ x, y, w, h });
    }
    this.pops = this.pops.filter(p => {
      const f = this.figures.get(p.id);
      if (!f || now > p.expire) { p.el.remove(); return false; }
      this._project(f.group.position, p.el, 2.0);
      return true;
    });
  }
}
