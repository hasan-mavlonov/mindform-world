import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const canvas = document.querySelector('#world');
const container = document.querySelector('#world-container');
const scene = new THREE.Scene();
scene.background = new THREE.Color('#9ccbc0');
scene.fog = new THREE.Fog('#9ccbc0', 23, 58);

const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 130);
camera.position.set(19, 21, 23);
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.3;
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI / 2.2;
controls.minDistance = 10;
controls.maxDistance = 56;
controls.target.set(0, 0, 0);

scene.add(new THREE.HemisphereLight('#e9f9ff', '#5b7b63', 2.1));
const sun = new THREE.DirectionalLight('#fff7dc', 3.0);
sun.position.set(-12, 23, 13);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = -24; sun.shadow.camera.right = 24;
sun.shadow.camera.top = 24; sun.shadow.camera.bottom = -24;
sun.shadow.bias = -0.0003;
scene.add(sun);

const material = (color, roughness = 0.93) => new THREE.MeshStandardMaterial({ color, roughness });
const groundMat = material('#79a985');
const ground = new THREE.Mesh(new THREE.PlaneGeometry(45, 39), groundMat);
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
ground.position.y = -0.03;
scene.add(ground);
const island = new THREE.Mesh(new THREE.BoxGeometry(45, 0.9, 39), material('#54775b'));
island.position.y = -0.50;
island.receiveShadow = true;
scene.add(island);
const lowerIsland = new THREE.Mesh(new THREE.BoxGeometry(46.5, 0.4, 40.5), material('#294f4e'));
lowerIsland.position.y = -1.12;
scene.add(lowerIsland);

const stone = material('#d8d8c0');
const wood = material('#91634b');
const roof = material('#cd826b');
const leaves = [material('#3c9871'), material('#54a77f'), material('#72b085'), material('#4a8d70')];
const pathMat = material('#c5c3a5');
const mesh = (geometry, mat, x, y, z, cast = true) => {
  const obj = new THREE.Mesh(geometry, mat);
  obj.position.set(x, y, z);
  obj.castShadow = cast;
  obj.receiveShadow = true;
  scene.add(obj);
  return obj;
};
const cylinder = (top, bottom, height, mat, x, y, z, sides = 8) => mesh(new THREE.CylinderGeometry(top, bottom, height, sides), mat, x, y, z);
const box = (w, h, d, mat, x, y, z) => mesh(new THREE.BoxGeometry(w, h, d), mat, x, y, z);

function drawPath(ax, az, bx, bz, width = 0.9) {
  const dx = bx - ax, dz = bz - az;
  const length = Math.hypot(dx, dz);
  const road = box(width, 0.025, length, pathMat, (ax + bx) / 2, 0.016, (az + bz) / 2);
  road.rotation.y = Math.atan2(dx, dz);
}
for (const [x, z] of [[-9,-5],[9,-5],[-9,6],[9,6],[0,9]]) drawPath(0, 0, x, z);

function makeLabel(text, x, y, z, scale = 2.6) {
  const c = document.createElement('canvas');
  c.width = 512; c.height = 96;
  const ctx = c.getContext('2d');
  ctx.fillStyle = 'rgba(16,48,42,.87)';
  ctx.beginPath(); ctx.roundRect(4, 4, 504, 88, 28); ctx.fill();
  ctx.font = 'bold 37px system-ui, sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillStyle = '#e9fff0'; ctx.fillText(text.toUpperCase(), 256, 50);
  const texture = new THREE.CanvasTexture(c);
  const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthWrite: false }));
  label.position.set(x, y, z); label.scale.set(scale, scale * .19, 1);
  scene.add(label);
  return label;
}

function tree(x,z,s=1,kind=0) {
  cylinder(.14*s,.23*s,1.25*s,wood,x,.62*s,z,7);
  if(kind%2===0){
    const cone = mesh(new THREE.ConeGeometry(.88*s,2.15*s,7),leaves[kind%leaves.length],x,2.02*s,z);
    cone.rotation.y = (x+z)*.18;
  } else {
    for (const [a,b,c,r] of [[0,1.65,0,.74],[-.44,1.9,0,.62],[.36,1.96,.21,.64]]) {
      mesh(new THREE.IcosahedronGeometry(r*s,0),leaves[(kind+1)%leaves.length],x+a*s,b*s,z+c*s);
    }
  }
}

function house(x,z,color,roofColor, label) {
  const wall = material(color);
  const r = material(roofColor);
  box(3.3,2.4,3.3,wall,x,1.2,z);
  const top = mesh(new THREE.ConeGeometry(2.75,1.75,4),r,x,3.16,z);
  top.rotation.y = Math.PI / 4;
  const door = box(.85,1.45,.08,wood,x,.73,z+1.7);
  const glass = material('#b4e9df');
  box(.65,.7,.075,glass,x-1.0,1.66,z+1.7);
  box(.65,.7,.075,glass,x+1.0,1.66,z+1.7);
  cylinder(.14,.20,.28,material('#edd29c'),x,.17,z+2.1);
  makeLabel(label,x,4.4,z,3.2);
  return { top, door };
}
house(-8.5,-4.5,'#e6cab0','#9a6865','LIBRARY');
house(8.5,-4.5,'#c4ccba','#65888e','WORKSHOP');

// Town square
cylinder(3.65,3.65,.11,material('#c6d5be'),0,.06,0,32);
cylinder(2.9,2.9,.10,material('#e5e1cd'),0,.12,0,32);
const fountain = cylinder(1.05,1.05,.37,stone,0,.28,0,20);
cylinder(.89,.89,.025,material('#77c7d7',.24),0,.48,0,20);
cylinder(.16,.20,1.05,stone,0,.79,0,12);
mesh(new THREE.SphereGeometry(.30,12,8),material('#e5e4cb'),0,1.47,0);
makeLabel('TOWN SQUARE',0,2.55,0,3.4);

// Garden: raised beds and flowers
for (let i=0;i<4;i++) {
  const xx=-10.7+(i%2)*2.7,zz=4.8+Math.floor(i/2)*2.4;
  box(2.0,.25,1.35,wood,xx,.13,zz);
  box(1.75,.04,1.1,material('#72523e'),xx,.28,zz);
  for (let j=0;j<6;j++) {
    const px=xx-.65+(j%3)*.65,pz=zz-.32+Math.floor(j/3)*.65;
    cylinder(.035,.045,.35,material('#4d8f48'),px,.48,pz,5);
    mesh(new THREE.IcosahedronGeometry(.14,0),material(['#fbabbe','#f3e7a5','#e8a2da'][j%3]),px,.7,pz);
  }
}
makeLabel('GARDEN',-8.5,2.7,5.5,2.25);

// Lake and stones (stylized concentric puddle shapes)
const lake = cylinder(4.15,4.15,.055,material('#d3d4b4'),8,0.06,6,48);
lake.scale.set(1.12,1,.78);
const water = cylinder(3.88,3.88,.065,material('#51a9b6',.21),8,.10,6,48);
water.scale.set(1.12,1,.78);
const waterRing = mesh(new THREE.TorusGeometry(3.3,.055,8,70),material('#d7f6e9'),8,.15,6,false);
waterRing.rotation.x = Math.PI/2;
waterRing.scale.y = .75;
makeLabel('LAKESIDE',8,2.7,6,2.8);
for(let i=0;i<6;i++) {
  const a=i*Math.PI/3;
  const rock=mesh(new THREE.DodecahedronGeometry(.27+(i%3)*.11,0),stone,8+Math.cos(a)*4.5,.24,6+Math.sin(a)*3.25);
  rock.scale.y=.65;
}

// Campfire
for (let i=0;i<8;i++) {
  const a=i*Math.PI/4;
  mesh(new THREE.DodecahedronGeometry(.4,0),stone,Math.cos(a)*1.15,.18,8.5+Math.sin(a)*1.15);
}
for(let i=0;i<4;i++) {
  const a=i*Math.PI/4;
  const log=box(.24,.27,1.65,wood,0,.28,8.5);
  log.rotation.y=a;
}
const flame = mesh(new THREE.ConeGeometry(.42,.95,6),material('#ffc66a'),0,.85,8.5);
const flameCore=mesh(new THREE.ConeGeometry(.22,.61,6),material('#ff865d'),0,.83,8.5);
const fireGlow = new THREE.PointLight('#ffc176',13,9,2);
fireGlow.position.set(0,1.1,8.5);scene.add(fireGlow);
makeLabel('CAMPFIRE',0,2.7,8.5,2.8);

// Fence segments and benches
function bench(x,z,rot=0){
  const obj = new THREE.Group();
  const plank=new THREE.Mesh(new THREE.BoxGeometry(1.8,.16,.52),wood);
  plank.position.y=.62;obj.add(plank);
  const back=new THREE.Mesh(new THREE.BoxGeometry(1.8,.65,.15),wood);
  back.position.set(0,.90,-.29);obj.add(back);
  for(const i of [-.65,.65]){
    const leg=new THREE.Mesh(new THREE.BoxGeometry(.17,.54,.33),material('#5e5752'));
    leg.position.set(i,.29,0);obj.add(leg);
  }
  obj.position.set(x,0,z);obj.rotation.y=rot;
  scene.add(obj);
}
bench(3,3,-.8);bench(-3,3,.8);bench(-3,-3,2.5);

// Seeded scenery so it looks the same on every load
let s=912343;
const rand=()=>{s=(s*1664525+1013904223)>>>0;return s/4294967296;};
for(let i=0;i<75;i++){
  const x=-20+rand()*40,z=-17+rand()*34;
  const nearRoad=Math.abs(x)<3.2&&Math.abs(z)<3.2;
  const nearLake=Math.hypot((x-8)/1.12,(z-6)/.78)<5.3;
  const buildings=(Math.abs(x+8.5)<3.2&&Math.abs(z+4.5)<3.2)||(Math.abs(x-8.5)<3.2&&Math.abs(z+4.5)<3.2);
  const garden=Math.abs(x+8.5)<4&&Math.abs(z-5.5)<3.8;
  const fire=Math.hypot(x,z-8.5)<2.8;
  if(nearRoad||nearLake||buildings||garden||fire)continue;
  tree(x,z,.6+rand()*.75,i);
}
for(let i=0;i<160;i++) {
  const x=-20+rand()*40,z=-17+rand()*34;
  if(Math.hypot(x,z)<3.7||Math.hypot(x-8,z-6)<5||Math.abs(x+8.5)<2.5&&Math.abs(z+4.5)<2.5)continue;
  const grass=mesh(new THREE.ConeGeometry(.055,.15,4),material(i%4===0?'#e8dba9':'#91bd80'),x,.075,z,false);
  grass.rotation.y=rand()*6;
}

const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
const figures = new Map();
let selected = null;
let latest = null;
let running = true;
let speed = 1;
let fetching = false;
let tickTimer;

function makeAgent(agent){
  const group=new THREE.Group();
  const suit = material(agent.color);
  const dark = material('#243a3a');
  const face = material('#f3d9c5');
  const torso=new THREE.Mesh(new THREE.CapsuleGeometry(.29,.45,5,10),suit);
  torso.position.y=.91;group.add(torso);
  const head=new THREE.Mesh(new THREE.SphereGeometry(.28,16,10),face);
  head.position.y=1.64;group.add(head);
  const hair=new THREE.Mesh(new THREE.SphereGeometry(.29,16,8,0,Math.PI*2,0,Math.PI*.44),dark);
  hair.position.y=1.68;group.add(hair);
  for(let ex of [-.10,.10]){
    const eye=new THREE.Mesh(new THREE.SphereGeometry(.024,7,5),dark);
    eye.position.set(ex,1.65,.266);group.add(eye);
  }
  const leftLeg=new THREE.Mesh(new THREE.CapsuleGeometry(.085,.27,4,6),dark);
  leftLeg.position.set(-.13,.34,0);group.add(leftLeg);
  const rightLeg=new THREE.Mesh(new THREE.CapsuleGeometry(.085,.27,4,6),dark);
  rightLeg.position.set(.13,.34,0);group.add(rightLeg);
  const leftArm=new THREE.Mesh(new THREE.CapsuleGeometry(.07,.29,4,7),suit);
  leftArm.position.set(-.37,1.05,0);leftArm.rotation.z=-.18;group.add(leftArm);
  const rightArm=new THREE.Mesh(new THREE.CapsuleGeometry(.07,.29,4,7),suit);
  rightArm.position.set(.37,1.05,0);rightArm.rotation.z=.18;group.add(rightArm);
  const shadow=new THREE.Mesh(new THREE.CircleGeometry(.45,24),new THREE.MeshBasicMaterial({color:'#1f4c3b',transparent:true,opacity:.22,depthWrite:false}));
  shadow.rotation.x=-Math.PI/2;shadow.position.y=.015;group.add(shadow);
  const ring=new THREE.Mesh(new THREE.TorusGeometry(.54,.055,6,24),new THREE.MeshBasicMaterial({color:'#d9ffda'}));
  ring.rotation.x=Math.PI/2;ring.position.y=.09;ring.visible=false;group.add(ring);
  for(const m of group.children){m.userData.agentId=agent.id;if(m.isMesh){m.castShadow=true;}}
  group.position.set(agent.x,0,agent.z);
  scene.add(group);
  const nameTag=makeLabel(agent.name,agent.x,2.27,agent.z,1.6);
  const figure={group,torso,leftLeg,rightLeg,leftArm,rightArm,ring,nameTag,target:new THREE.Vector3(agent.x,0,agent.z)};
  figures.set(agent.id,figure);
  return figure;
}

function selectAgent(id){
  selected=id;
  for(const [key,f] of figures)f.ring.visible=key===selected;
  renderPanels();
}

function renderPanels(){
  if(!latest)return;
  const agents=latest.agents;
  document.querySelector('#tick').textContent=String(latest.tick).padStart(4,'0');
  document.querySelector('#agent-count').textContent=String(agents.length).padStart(2,'0');
  document.querySelector('#resident-count').textContent=String(agents.length).padStart(2,'0')+' ONLINE';
  document.querySelector('#visit-count').textContent=String(agents.reduce((n,a)=>n+a.completed_visits,0)).padStart(2,'0');
  document.querySelector('#event-count').textContent=latest.events.length+' EVENTS';
  const list=document.querySelector('#agent-list');list.replaceChildren();
  for(const a of agents){
    const button=document.createElement('button');button.type='button';button.className='agent-row'+(selected===a.id?' active':'');
    const avatar=document.createElement('div');avatar.className='avatar';avatar.style.background=a.color;avatar.textContent=a.name[0];
    const content=document.createElement('div');content.className='agent-summary';
    const name=document.createElement('b');name.textContent=a.name;
    const desc=document.createElement('small');desc.textContent=a.role+' · '+a.mood;
    content.append(name,desc);
    const dot=document.createElement('span');dot.className='agent-meta';dot.textContent='●';
    button.append(avatar,content,dot);button.addEventListener('click',()=>selectAgent(a.id));list.append(button);
  }
  const inspector=document.querySelector('#inspector');
  const a=agents.find(p=>p.id===selected);
  if(a){
    inspector.replaceChildren();
    const wrapper=document.createElement('div');
    const traitRows=Object.entries(a.traits).map(([k,v])=>`<div class="trait-line"><div class="trait-title"><span>${k}</span><span>${Math.round(v*100)}%</span></div><div class="trait-track"><div class="trait-fill" style="width:${v*100}%"></div></div></div>`).join('');
    // Traits/names are from the locally defined simulation, not untrusted user input.
    wrapper.innerHTML=`<div class="inspect-head"><div class="avatar" style="background:${a.color}">${a.name[0]}</div><div><h2>${a.name}</h2><small>${a.role.toUpperCase()} · ${a.mood.toUpperCase()}</small></div></div><p class="inspect-bio">${a.personality}</p><div class="inspect-current">↗ ${a.activity}<br>Destination: ${a.destination} · Visits: ${a.completed_visits}</div>${traitRows}<div class="memory-title">RECENT MEMORIES</div>`;
    for(const m of a.memories.slice(-3).reverse()){
      const item=document.createElement('div');item.className='memory';item.textContent='• '+m;wrapper.append(item);
    }
    if(!a.memories.length){const item=document.createElement('div');item.className='memory';item.textContent='No memories recorded yet.';wrapper.append(item);}
    inspector.append(wrapper);
  }
  const events=document.querySelector('#events');events.replaceChildren();
  for(const e of latest.events.slice().reverse().slice(0,20)){
    const div=document.createElement('div');div.className='event';
    const time=document.createElement('time');time.textContent='T'+String(e.tick).padStart(3,'0');
    const msg=document.createElement('span');msg.textContent=e.message;
    div.append(time,msg);events.append(div);
  }
}
function ingest(state){
  latest=state;
  document.querySelector('#mode').textContent=state.mode.toUpperCase()+' v0.1';
  for(const a of state.agents){
    const f=figures.get(a.id)||makeAgent(a);
    f.target.set(a.x,0,a.z);
  }
  renderPanels();
}
async function api(path,options={}){
  const response=await fetch(path,options);
  if(!response.ok)throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}
function showError(err){
  const el=document.querySelector('#error');
  el.textContent='Simulation connection error: '+err.message;
  el.classList.remove('hidden');
  console.error(err);
}
async function step(n=1){
  if(fetching)return;
  fetching=true;
  try{ingest(await api('/api/step?n='+n,{method:'POST'}));document.querySelector('#error').classList.add('hidden');}
  catch(err){showError(err);setRunning(false);}
  finally{fetching=false;}
}
function setRunning(value){
  running=value;
  document.querySelector('#toggle').textContent=running?'Ⅱ Pause':'▶ Resume';
  document.querySelector('#run-state').textContent=running?'RUNNING':'PAUSED';
}
function schedule(){
  clearInterval(tickTimer);
  tickTimer=setInterval(()=>{if(running)step(speed);},850);
}
document.querySelector('#toggle').addEventListener('click',()=>setRunning(!running));
document.querySelector('#step').addEventListener('click',()=>step(1));
document.querySelector('#speed').addEventListener('click',()=>{
  speed=speed===1?2:speed===2?5:1;
  document.querySelector('#speed').textContent=speed+'× SPEED';
});
document.querySelector('#reset').addEventListener('click',async()=>{
  if(fetching)return;
  try{ingest(await api('/api/reset',{method:'POST'}));}catch(err){showError(err);}
});
let mouseDown={x:0,y:0};
canvas.addEventListener('pointerdown',e=>{mouseDown={x:e.clientX,y:e.clientY};});
canvas.addEventListener('pointerup',e=>{
  if(Math.hypot(e.clientX-mouseDown.x,e.clientY-mouseDown.y)>5)return;
  const bounds=canvas.getBoundingClientRect();
  mouse.set(((e.clientX-bounds.left)/bounds.width)*2-1,-((e.clientY-bounds.top)/bounds.height)*2+1);
  raycaster.setFromCamera(mouse,camera);
  const picks=raycaster.intersectObjects([...figures.values()].map(f=>f.group),true);
  if(picks.length){
    let obj=picks[0].object;
    while(obj && !obj.userData.agentId)obj=obj.parent;
    if(obj?.userData.agentId)selectAgent(obj.userData.agentId);
  }
});
function resize(){
  const w=container.clientWidth,h=container.clientHeight;
  renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();
}
window.addEventListener('resize',resize);resize();
const clock=new THREE.Clock();
function animate(){
  requestAnimationFrame(animate);
  const t=clock.getElapsedTime();
  controls.update();
  flame.scale.y=.83+Math.sin(t*12)*.19;
  flameCore.scale.y=.7+Math.sin(t*10+1.5)*.18;
  water.material.color.setHSL(.51,.30,.53+Math.sin(t*.7)*.018);
  for(const [key,f] of figures){
    const dx=f.target.x-f.group.position.x,dz=f.target.z-f.group.position.z;
    const moving=Math.hypot(dx,dz)>.03;
    f.group.position.lerp(f.target,0.075);
    if(moving){
      f.group.rotation.y=Math.atan2(dx,dz);
      const stride=Math.sin(t*10+(key.length*2))*.18;
      f.leftLeg.rotation.x=stride;f.rightLeg.rotation.x=-stride;
      f.leftArm.rotation.x=-stride;f.rightArm.rotation.x=stride;
      f.torso.position.y=.91+Math.abs(Math.sin(t*10))*.028;
    }else{
      f.leftLeg.rotation.x*=.89;f.rightLeg.rotation.x*=.89;
      f.leftArm.rotation.x*=.89;f.rightArm.rotation.x*=.89;
    }
    f.nameTag.position.set(f.group.position.x,2.45,f.group.position.z);
    f.ring.rotation.z=t*.15;
  }
  renderer.render(scene,camera);
}
animate();
try{ingest(await api('/api/state'));selectAgent('aya');schedule();}
catch(err){showError(err);}
