import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// Routing: no hash = model browser; #<id> = projects/<id>/; ?project=path/to/parts.json also works
const params = new URLSearchParams(location.search);
const hashId = decodeURIComponent(location.hash.slice(1));
const PROJECT_URL = params.get('project') || (hashId ? `projects/${hashId}/parts.json` : null);
const MODE = PROJECT_URL ? 'viewer' : 'browser';
const base = PROJECT_URL && PROJECT_URL.includes('/') ? PROJECT_URL.slice(0, PROJECT_URL.lastIndexOf('/') + 1) : '';
window.addEventListener('hashchange', () => location.reload());

const state = {
  data: null, parts: new Map(), meshes: new Map(), extras: [], figures: new Map(),
  selected: null, group: 'all', showSet: true, xray: false, sheet: null, pins: new Map(), measure: false,
};
const sheetList = a => { // [1,2,3,7] -> "1–3, 7"
  const out = []; for (let i = 0; i < a.length; i++) { let j = i; while (j + 1 < a.length && a[j + 1] === a[j] + 1) j++;
    out.push(j > i ? `${a[i]}–${a[j]}` : `${a[i]}`); i = j; } return out.join(', ');
};
const groupName = g => state.data?.groups?.find(x => x.id === g)?.name ?? `Group ${g}`;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---------- 3D setup ----------
const viewport = document.getElementById('viewport');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.85; // the 3D world is 15 % darker (measurements are exempt)
viewport.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#d1cfcb'); // #f6f4ef, 15 % darker
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
const sun = new THREE.DirectionalLight('#ffffff', 1.4);
sun.position.set(0.3, 0.6, 0.4);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(35, 1, 0.0005, 50);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.zoomToCursor = true;

const model = new THREE.Group();
scene.add(model);

const PLATE = '#f4f4f1';
const HI = new THREE.Color('#b0241f');
let partsBox = new THREE.Box3(), homeView = null, grid = null;

function resize() {
  const { clientWidth: w, clientHeight: h } = viewport;
  renderer.setSize(w, h, false);
  renderer.domElement.style.width = w + 'px';
  renderer.domElement.style.height = h + 'px';
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewport);

function fitCamera() {
  const size = partsBox.getSize(new THREE.Vector3());
  const center = partsBox.getCenter(new THREE.Vector3());
  const r = size.length() / 2;
  const dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.1;
  const dir = new THREE.Vector3(0.9, 0.7, 1.2).normalize();
  camera.position.copy(center).addScaledVector(dir, dist);
  camera.near = dist / 200; camera.far = dist * 100; camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.minDistance = r * 0.05; controls.maxDistance = dist * 5;
  controls.update();
  homeView = { pos: camera.position.clone(), target: center.clone(), fov: camera.fov };
}

// ---------- eased camera moves (ease in-out, 0.5 s) ----------
const MOVE_MS = 500;      // Reset view
const PREVIS_MS = 800;    // back to the pre-vis angle
const easeInOut = t => t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
let tween = null;
function moveCamera(pos, target, fov = camera.fov, instant = false, ms = MOVE_MS) {
  if (instant) {
    camera.position.copy(pos); controls.target.copy(target);
    camera.fov = fov; camera.updateProjectionMatrix(); controls.update(); tween = null; return;
  }
  // orbit around the moving target instead of sliding in a straight line: smoother, no swing through the model
  const s0 = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
  const s1 = new THREE.Spherical().setFromVector3(pos.clone().sub(target));
  let dTheta = s1.theta - s0.theta;
  dTheta = ((dTheta + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI; // shortest way round
  tween = { s0, s1, dTheta, t0: controls.target.clone(), t1: target.clone(), f0: camera.fov, f1: fov,
            start: performance.now(), ms };
}
function stepTween(now) {
  if (!tween) return;
  const k = Math.min((now - tween.start) / tween.ms, 1), e = easeInOut(k);
  controls.target.lerpVectors(tween.t0, tween.t1, e);
  const sp = new THREE.Spherical(
    Math.exp(Math.log(tween.s0.radius) + (Math.log(tween.s1.radius) - Math.log(tween.s0.radius)) * e), // even zoom speed
    tween.s0.phi + (tween.s1.phi - tween.s0.phi) * e,
    tween.s0.theta + tween.dTheta * e);
  camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(sp));
  camera.fov = tween.f0 + (tween.f1 - tween.f0) * e; camera.updateProjectionMatrix();
  if (k === 1) tween = null;
}
controls.addEventListener('start', () => { tween = null; }); // grabbing the view stops a move

// Blender cameras from the .glb: look along the camera, aim at the point nearest the model center
function cameraView(c) {
  const pos = new THREE.Vector3(...c.position), fwd = new THREE.Vector3(...c.forward).normalize();
  const center = partsBox.getCenter(new THREE.Vector3());
  const dist = Math.max(center.clone().sub(pos).dot(fwd), partsBox.getSize(new THREE.Vector3()).length() * 0.1);
  return { pos, target: pos.clone().addScaledVector(fwd, dist), fov: c.fov || camera.fov };
}
function goToCamera(i, instant = false) {
  const c = state.data.cameras?.[i]; if (!c) return;
  const v = cameraView(c); moveCamera(v.pos, v.target, v.fov, instant, PREVIS_MS);
}

function addGround() {
  // grid step: a round number (1 cm, 10 cm, 1 m...) so there are ~20-60 lines across the parts
  const size = partsBox.getSize(new THREE.Vector3());
  const span0 = Math.max(size.x, size.z) * 3;
  const step = 10 ** Math.floor(Math.log10(span0 / 20));
  const span = Math.ceil(span0 / step) * step;
  grid = new THREE.GridHelper(span, Math.round(span / step), '#c9c4b8', '#e2ded5');
  const c = partsBox.getCenter(new THREE.Vector3());
  grid.position.set(c.x, partsBox.min.y - step * 0.001, c.z);
  scene.add(grid);
}

const setMaterial = name => /mirr/i.test(name)
  ? new THREE.MeshStandardMaterial({ color: '#c9d6df', metalness: 0.9, roughness: 0.08, transparent: true, opacity: 0.55, side: THREE.DoubleSide })
  : new THREE.MeshStandardMaterial({ color: '#d8d4cc', roughness: 0.95, transparent: true, opacity: /ceil/i.test(name) ? 0.35 : 0.9, side: THREE.FrontSide });

async function loadModel(url) {
  const gltf = await new GLTFLoader().loadAsync(url);
  gltf.scene.updateMatrixWorld(true);
  const sceneIds = new Map((state.data.scene || []).map(s => [s.id, s]));
  const figIds = new Map((state.data.figures || []).map(f => [f.id, f]));
  const hwIds = new Map((state.data.hardware || []).map(h => [h.id, h]));
  const meshes = [];
  gltf.scene.traverse(o => { if (o.isMesh) meshes.push(o); });
  for (const o of meshes) {
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    const id = o.name, pid = o.parent?.name;
    const part = state.parts.get(id) || state.parts.get(pid);
    const set = sceneIds.get(id) || sceneIds.get(pid);
    const fig = figIds.get(id) || figIds.get(pid);
    const hw = hwIds.get(id) || hwIds.get(pid);
    if (hw) { // pin joints / hinges: grey, a bit see-through, stay put when exploding
      o.material = new THREE.MeshStandardMaterial({ color: '#8a8f96', roughness: 0.5, metalness: 0.3,
        transparent: true, opacity: 0.6, depthWrite: false, emissive: '#000000' });
      o.renderOrder = 2;
      o.userData = { hw };
      state.pins.set(hw.id, o);
    } else if (part) {
      o.material = new THREE.MeshStandardMaterial({ flatShading: true, color: PLATE, roughness: 0.85, transparent: true });
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 25),
        new THREE.LineBasicMaterial({ color: '#7d8186', transparent: true }));
      o.add(edges);
      o.userData = { id: part.id, edges };
      state.meshes.set(part.id, o);
    } else if (fig) {
      o.material = new THREE.MeshStandardMaterial({ color: '#b8a690', roughness: 0.7 });
      o.userData = { figure: fig };
      state.figures.set(fig.id, o); state.extras.push(o);
    } else {
      o.material = setMaterial(set?.name || id);
      o.userData = { set: true };
      state.extras.push(o);
    }
  }
  model.add(gltf.scene);

  partsBox = new THREE.Box3();
  for (const m of [...state.meshes.values(), ...state.figures.values()]) partsBox.expandByObject(m);
  if (partsBox.isEmpty()) partsBox.setFromObject(model);

  // explode: each part moves away from the center of its own group (box)
  const groups = new Map();
  for (const m of state.meshes.values()) {
    const g = state.parts.get(m.userData.id).group ?? 1;
    if (!groups.has(g)) groups.set(g, new THREE.Box3());
    groups.get(g).expandByObject(m);
  }
  for (const m of state.meshes.values()) {
    const gb = groups.get(state.parts.get(m.userData.id).group ?? 1);
    const c = gb.getCenter(new THREE.Vector3());
    const pc = new THREE.Box3().setFromObject(m).getCenter(new THREE.Vector3());
    const dir = pc.sub(c);
    m.userData.home = m.position.clone();
    m.userData.reach = gb.getSize(new THREE.Vector3()).length() * 0.25;
    const inv = new THREE.Matrix4().copy(m.parent.matrixWorld).invert();
    const p0 = new THREE.Vector3().applyMatrix4(inv);
    m.userData.dir = dir.lengthSq() < 1e-12 ? new THREE.Vector3() : dir.normalize().applyMatrix4(inv).sub(p0).normalize();
  }
  addGround(); fitCamera(); refreshVisibility(); buildMeasures();
}

function applyExplode(t) {
  for (const m of state.meshes.values()) {
    const scale = m.parent.getWorldScale(new THREE.Vector3()).x || 1;
    m.position.copy(m.userData.home).addScaledVector(m.userData.dir, m.userData.reach * t / scale);
  }
  for (const ms of measures) if (ms.part) {
    const m = state.meshes.get(ms.part);
    if (m) ms.group.position.copy(m.position).sub(m.userData.home);
  }
}

const inFilter = p => state.group === 'all' || p.group === state.group;

function refresh3D() {
  for (const [id, m] of state.meshes) {
    const p = state.parts.get(id);
    const sel = state.selected === id;
    const visible = inFilter(p);
    m.material.emissive.copy(sel ? HI : new THREE.Color(0));
    m.material.emissiveIntensity = sel ? 0.55 : 0;
    m.material.opacity = !visible ? 0.12 : (state.xray && !sel ? 0.3 : 1);
    m.material.depthWrite = visible && (!state.xray || sel);
    m.userData.edges.material.color.set(sel ? '#8e1b1b' : '#7d8186');
    m.userData.edges.material.opacity = visible ? 1 : 0.15;
  }
}

function refreshVisibility() {
  for (const o of state.extras) o.visible = state.showSet;
  if (grid) grid.visible = !state.showSet || !(state.data.scene || []).length;
}

// click-to-select (ignore drags)
const ray = new THREE.Raycaster(); let down = null;
renderer.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', e => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
  const r = renderer.domElement.getBoundingClientRect();
  ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
  const targets = [...state.meshes.values()].filter(m => inFilter(state.parts.get(m.userData.id)));
  if (state.showSet) targets.push(...state.figures.values());
  targets.push(...state.pins.values());
  const hit = ray.intersectObjects(targets, false)[0];
  if (hit?.object.userData.figure) return showFigure(hit.object.userData.figure);
  if (hit?.object.userData.hw) return showHardware(hit.object.userData.hw.type, hit.object.userData.hw);
  select(hit ? hit.object.userData.id : null);
});

if (MODE === 'viewer') renderer.setAnimationLoop(now => { stepTween(now); controls.update(); updateMeasures(); renderer.render(scene, camera); });

// ---------- 3D measurements: spotlight opacity + blur ----------
// Every plate edge gets a dimension line + label. Only what the viewer can see counts. A spotlight map
// (assets/spotlight-map.png, stretched over the view) decides the zones:
//   inside the spotlight: the 6 nearest visible go 100 % -> 85 %, the rest 85 % -> 25 % by depth
//   spotlight edge: a soft curve from 85 % to 25 %;  outside: 25 % -> 0 % as the map goes black
// Below 85 % opacity, labels (and lines, if Line blur is on) blur more as they fade.
const measures = [];
const measureRoot = new THREE.Group(); measureRoot.visible = false; scene.add(measureRoot);
const MCOL = '#1d5d93', MSEL = '#8e1b1b';
const S_IN = 0.85;                              // spotlight map value from which you're "inside"
const OP_SHARP_END = 0.85, OP_MIN = 0.3;        // 6th nearest = 85 %; nothing ever goes below 30 %
const MAX_BLUR_PX = 4;                          // label blur at 30 % (screen pixels); lines never blur
const BLUR_LEVELS = [0, 0.15, 0.3, 0.5, 0.75, 1]; // pre-rendered label blur steps (fraction of max)
const LINE_COPIES = [];                         // lines only fade (no blur copies)
const LINE_W = [1];

// spotlight map -> brightness lookup (0..1). Falls back to a soft ellipse if the image can't load.
let spot = null;
(() => {
  const img = new Image();
  img.onload = () => {
    const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data, lum = new Float32Array(c.width * c.height);
    for (let i = 0; i < lum.length; i++) lum[i] = (d[i * 4] * 0.299 + d[i * 4 + 1] * 0.587 + d[i * 4 + 2] * 0.114) / 255;
    let lo = 1; for (const v of lum) if (v < lo) lo = v;
    spot = { w: c.width, h: c.height, lum, min: Math.min(lo, S_IN - 0.05) };
  };
  img.src = 'assets/spotlight-map.png';
})();
function spotAt(nx, ny) { // NDC -1..1
  if (Math.abs(nx) > 1 || Math.abs(ny) > 1) return 0;
  if (!spot) { const r = Math.hypot(nx / 0.45, ny / 0.8); return Math.max(0, Math.min(1, 1.6 - r * 1.2)); }
  const x = Math.min(spot.w - 1, Math.max(0, Math.round((nx + 1) / 2 * (spot.w - 1))));
  const y = Math.min(spot.h - 1, Math.max(0, Math.round((1 - ny) / 2 * (spot.h - 1))));
  return spot.lum[y * spot.w + x];
}
const smooth = (e0, e1, x) => { const t = Math.min(Math.max((x - e0) / (e1 - e0), 0), 1); return t * t * (3 - 2 * t); };
// opacity from the spotlight value s and the measurement's inside-the-spotlight value
function spotOpacity(s, inner) {
  if (s >= S_IN) return inner;
  // outside the spotlight the map's grey drives it: 85 % at the spotlight edge -> 30 % at the darkest grey
  const lo = spot ? spot.min : 0.5;
  return OP_MIN + (Math.min(inner, OP_SHARP_END) - OP_MIN) * smooth(lo, S_IN, s);
}
const blurOf = op => Math.min(Math.max((OP_SHARP_END - op) / (OP_SHARP_END - OP_MIN), 0), 1); // 0 at >= 85 %, 1 at 30 %

function labelSprite(text) {
  const fs = 44, pad = 14, maxBlurCanvas = 10; // canvas px: labels are drawn ~2.5x screen size, so ~4 px on screen
  const probe = document.createElement('canvas').getContext('2d');
  probe.font = `600 ${fs}px "Barlow Condensed", "Arial Narrow", Arial, sans-serif`;
  const bw = Math.ceil(probe.measureText(text).width) + pad * 2, bh = fs + pad, m = maxBlurCanvas * 2;
  const canFilter = 'filter' in probe;
  const levels = BLUR_LEVELS.map(f => {
    const c = document.createElement('canvas'); c.width = bw + m * 2; c.height = bh + m * 2;
    const g = c.getContext('2d');
    if (canFilter && f > 0) g.filter = `blur(${(f * maxBlurCanvas).toFixed(1)}px)`;
    g.font = `600 ${fs}px "Barlow Condensed", "Arial Narrow", Arial, sans-serif`;
    g.fillStyle = 'rgba(255,255,255,0.92)'; g.beginPath(); g.roundRect(m, m, bw, bh, bh / 2.6); g.fill();
    g.fillStyle = '#123a5c'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, m + bw / 2, m + bh / 2 + 2);
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
    return tex;
  });
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: levels[0], transparent: true, sizeAttenuation: false, depthTest: true, depthWrite: false, toneMapped: false }));
  sp.userData = { aspect: (bw + m * 2) / (bh + m * 2), hScale: (bh + m * 2) / bh, innerAspect: bw / bh, levels };
  sp.renderOrder = 5;
  return sp;
}
const evenMM = mm => 2 * Math.round(mm / 2);         // everything is shown in even millimetres
const fmt = mm => `${evenMM(mm)} mm`;

function addMeasure({ a, b, out, len, part = null, kind = 'part', lift = null, off }) {
  const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b), O = new THREE.Vector3(...out).normalize();
  const L = lift ? new THREE.Vector3(...lift).multiplyScalar(off * 0.15) : new THREE.Vector3();
  const a1 = A.clone().addScaledVector(O, off).add(L), b1 = B.clone().addScaledVector(O, off).add(L);
  const pts = [
    a1, b1,                                                                   // dimension line
    A.clone().addScaledVector(O, off * 0.25).add(L), A.clone().addScaledVector(O, off * 1.3).add(L), // extension lines
    B.clone().addScaledVector(O, off * 0.25).add(L), B.clone().addScaledVector(O, off * 1.3).add(L),
  ];
  const n = pts.length * (1 + LINE_COPIES.length); // centre line + blur copies
  const mat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthTest: true, depthWrite: false, toneMapped: false });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 4), 4)); // rgba per vertex
  const lines = new THREE.LineSegments(geo, mat);
  lines.renderOrder = 4; lines.frustumCulled = false;
  const sp = labelSprite(fmt(len));
  sp.position.copy(a1).lerp(b1, 0.5).addScaledVector(O, off * 0.9);
  const group = new THREE.Group(); group.add(lines, sp);
  measureRoot.add(group);
  const anchor = a1.clone().lerp(b1, 0.5);
  measures.push({ group, mat, sp, part, kind, anchor, geo, pts });
}

function buildMeasures() {
  for (const ms of measures) { measureRoot.remove(ms.group); ms.mat.dispose(); ms.geo.dispose(); ms.sp.userData.levels.forEach(t => t.dispose()); ms.sp.material.dispose(); }
  measures.length = 0;
  const size = partsBox.getSize(new THREE.Vector3()).length();
  const off = size * 0.012;
  for (const p of state.data.parts) for (const e of p.edges || [])
    addMeasure({ ...e, part: p.id, lift: p.normal, off });
  // overall size of what is shown (follows the group filter)
  const box = new THREE.Box3();
  for (const [id, m] of state.meshes) if (inFilter(state.parts.get(id))) box.expandByObject(m);
  if (!box.isEmpty()) {
    const { min, max } = box, o = off * 3;
    addMeasure({ a: [min.x, min.y, max.z], b: [max.x, min.y, max.z], out: [0, 0, 1], len: (max.x - min.x) * 1000, kind: 'overall', off: o });
    addMeasure({ a: [max.x, min.y, min.z], b: [max.x, min.y, max.z], out: [1, 0, 0], len: (max.z - min.z) * 1000, kind: 'overall', off: o });
    addMeasure({ a: [min.x, min.y, max.z], b: [min.x, max.y, max.z], out: [-1, 0, 0], len: (max.y - min.y) * 1000, kind: 'overall', off: o });
  }
  // pin joints: the spacing between the plates they bridge (not the pin's own length)
  for (const h of state.data.hardware || []) {
    if (!h.gap) continue;
    const A = new THREE.Vector3(...h.gap.a), B = new THREE.Vector3(...h.gap.b);
    const axis = B.clone().sub(A).normalize();
    // put the dimension line beside the pin, on the side facing away from the model's middle
    const mid = A.clone().lerp(B, 0.5), c = partsBox.getCenter(new THREE.Vector3());
    let out = mid.clone().sub(c); out.addScaledVector(axis, -out.dot(axis));
    if (out.lengthSq() < 1e-12) out = new THREE.Vector3(1, 0, 0).addScaledVector(axis, -axis.x);
    addMeasure({ a: h.gap.a, b: h.gap.b, out: out.normalize().toArray(), len: h.gap.len, kind: 'pin', off: off * 0.6 });
  }
  // figure height, as a scale reference
  for (const [, fm] of state.figures) {
    const fb = new THREE.Box3().setFromObject(fm), f = fm.userData.figure;
    addMeasure({ a: [fb.max.x, fb.min.y, fb.max.z], b: [fb.max.x, fb.max.y, fb.max.z], out: [1, 0, 0], len: f.height, kind: 'figure', off: off * 1.5 });
  }
  applyExplode(+document.getElementById('explode').value);
}

const _v = new THREE.Vector3(), _c = new THREE.Color(), _ndc = new THREE.Vector3(), _dir = new THREE.Vector3();
const _right = new THREE.Vector3(), _up = new THREE.Vector3(), _p = new THREE.Vector3(), _q = new THREE.Vector3(), _perp = new THREE.Vector3();
const SHARP = 6; // the nearest 6 visible measurements inside the spotlight
let fontScale = 1;
const occRay = new THREE.Raycaster();

// Can the viewer see this point right now? On screen, and not behind a plate (x-ray plates don't block).
function seen(p, occluders) {
  _ndc.copy(p).project(camera);
  if (_ndc.z < -1 || _ndc.z > 1 || Math.abs(_ndc.x) > 1 || Math.abs(_ndc.y) > 1) return false;
  if (!occluders.length) return true;
  const dist = camera.position.distanceTo(p);
  occRay.set(camera.position, _dir.copy(p).sub(camera.position).normalize());
  occRay.far = dist * 0.995;
  return occRay.intersectObjects(occluders, false).length === 0;
}
// Blocked if a plate sits between the camera and the point (off-screen points don't count as blocked)
function blocked(p, occluders) {
  if (!occluders.length) return false;
  _ndc.copy(p).project(camera);
  if (_ndc.z < -1 || _ndc.z > 1 || Math.abs(_ndc.x) > 1 || Math.abs(_ndc.y) > 1) return false;
  const dist = camera.position.distanceTo(p);
  occRay.set(camera.position, _dir.copy(p).sub(camera.position).normalize());
  occRay.far = dist * 0.995;
  return occRay.intersectObjects(occluders, false).length > 0;
}
let BLOCK_SHARE = 0.5; // Label density: hide a measurement once this share of it is behind plates (0.01-0.5)
try { const v = +localStorage.getItem('foamex-label-density'); if (v >= 1 && v <= 50) BLOCK_SHARE = v / 100; } catch {}
const FADE_S = 0.15; // hide / show fade when a measurement gets blocked or unblocked
let lastT = performance.now();
const _fwd = new THREE.Vector3(), _s = new THREE.Vector3();
// sample the label box and the whole line: if even one sample is blocked, the measurement is blocked
function measureBlocked(ms, occluders, h) {
  const off = ms.group.position;
  const L = _s.copy(ms.sp.position).add(off);
  const depth = Math.max(_v.copy(L).sub(camera.position).dot(_fwd), 1e-6);
  const hh = h / 2 * depth * 0.95, hw = h * ms.sp.userData.innerAspect / 2 * depth * 0.95;
  const pts = [L.clone()];
  for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]])
    pts.push(L.clone().addScaledVector(_right, sx * hw).addScaledVector(_up, sy * hh));
  const a = ms.pts[0], b = ms.pts[1];
  for (const t of [0, 0.25, 0.5, 0.75, 1]) pts.push(a.clone().lerp(b, t).add(off));
  for (let i = 2; i < 6; i++) pts.push(ms.pts[i].clone().add(off));
  const hidden = pts.filter(p => blocked(p, occluders)).length;
  return hidden / pts.length >= BLOCK_SHARE; // a thin part across it no longer hides it
}
const spotOfPoint = p => { _ndc.copy(p).project(camera); return _ndc.z > 1 ? 0 : spotAt(_ndc.x, _ndc.y); };

function updateMeasures() {
  if (!state.measure) return;
  const fovScale = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  camera.updateMatrixWorld();
  _right.setFromMatrixColumn(camera.matrixWorld, 0); _up.setFromMatrixColumn(camera.matrixWorld, 1);
  const viewH = renderer.domElement.clientHeight || 1;
  const occluders = state.xray ? [] :
    [...state.meshes.values()].filter(m => inFilter(state.parts.get(m.userData.id)));

  const now = performance.now(), dt = Math.min((now - lastT) / 1000, 0.1); lastT = now;
  camera.getWorldDirection(_fwd);
  const hLabel = 0.03 * fontScale * fovScale;
  // 1) what the viewer can see, and where it sits in the spotlight
  const shown = [], inside = [];
  for (const ms of measures) {
    const show = ms.kind === 'overall' || ms.kind === 'pin' ? true
      : ms.kind === 'figure' ? state.showSet
      : inFilter(state.parts.get(ms.part));
    ms.group.visible = show;
    if (!show) continue;
    shown.push(ms);
    const off = ms.group.position;
    const wa = _q.copy(ms.anchor).add(off);
    ms.d = camera.position.distanceTo(wa);
    ms.s = spotOfPoint(wa);
    const onScreen = seen(wa, []) || seen(_v.copy(ms.pts[0]).add(off), []) || seen(_v.copy(ms.pts[1]).add(off), []);
    ms.isBlocked = onScreen && measureBlocked(ms, occluders, hLabel);
    ms.vis = onScreen && !ms.isBlocked;
    // fade toward hidden (blocked) or shown over FADE_S
    const target = ms.isBlocked ? 0 : 1;
    ms.show = ms.show ?? target;
    ms.show += Math.sign(target - ms.show) * Math.min(Math.abs(target - ms.show), dt / FADE_S);
    if (ms.vis && ms.s >= S_IN) inside.push(ms);
  }
  // 2) inside the spotlight: 6 nearest 100 % -> 85 %, the rest 85 % -> 25 % by depth (curve)
  inside.sort((x, y) => x.d - y.d);
  const sharpN = Math.min(SHARP, inside.length), rest = inside.slice(sharpN);
  const dNear = sharpN ? inside[sharpN - 1].d : 0, dFar = rest.length ? rest[rest.length - 1].d : dNear;
  const span = Math.max(dFar - dNear, 1e-9);
  const innerOf = ms => {
    const i = inside.indexOf(ms);
    if (i >= 0 && i < sharpN) return sharpN > 1 ? 1 - (1 - OP_SHARP_END) * (i / (sharpN - 1)) : 1;
    const t = Math.min(Math.max((ms.d - dNear) / span, 0), 1);
    return OP_SHARP_END - (OP_SHARP_END - OP_MIN) * (1 - Math.pow(1 - t, 2)); // ease toward 30 %
  };

  // 3) apply: labels by their own spot value; lines per vertex, so they fade (and blur) along their length
  const blurLines = false; // lines only fade
  for (const ms of shown) {
    const sel = ms.part && ms.part === state.selected;
    const inner = innerOf(ms);
    const opL = sel ? 1 : spotOpacity(ms.s, inner);
    ms.sp.material.opacity = opL * ms.show;
    ms.sp.visible = ms.show > 0.001;
    const lvl = Math.round(blurOf(opL) * (BLUR_LEVELS.length - 1));
    const tex = ms.sp.userData.levels[lvl];
    if (ms.sp.material.map !== tex) { ms.sp.material.map = tex; ms.sp.material.needsUpdate = true; }
    const h = 0.03 * fontScale * fovScale;
    ms.sp.scale.set(h * ms.sp.userData.aspect * ms.sp.userData.hScale, h * ms.sp.userData.hScale, 1);

    _c.set(sel ? MSEL : MCOL);
    const pos = ms.geo.attributes.position, col = ms.geo.attributes.color, np = ms.pts.length, off = ms.group.position;
    const vOp = new Array(np), vBlur = new Array(np);
    for (let i = 0; i < np; i++) {
      const s = spotOfPoint(_p.copy(ms.pts[i]).add(off));
      vOp[i] = sel ? 1 : spotOpacity(s, inner);
      vBlur[i] = blurLines && !sel ? blurOf(vOp[i]) : 0;
    }
    // centre line, then the blur copies shifted sideways on screen
    for (let c = 0; c <= LINE_COPIES.length; c++) {
      const k = c === 0 ? 0 : LINE_COPIES[c - 1];
      const w = blurLines ? LINE_W[c] : (c === 0 ? 1 : 0);
      for (let i = 0; i < np; i += 2) { // segments are vertex pairs
        const p0 = ms.pts[i], p1 = ms.pts[i + 1];
        // screen-space direction of the segment -> perpendicular in world space
        _ndc.copy(p0).add(off).project(camera); const x0 = _ndc.x, y0 = _ndc.y;
        _ndc.copy(p1).add(off).project(camera);
        let dx = (_ndc.x - x0) * camera.aspect, dy = _ndc.y - y0; const dl = Math.hypot(dx, dy) || 1; dx /= dl; dy /= dl;
        _perp.copy(_right).multiplyScalar(-dy).addScaledVector(_up, dx);
        for (const j of [i, i + 1]) {
          const P = _p.copy(ms.pts[j]).add(off);
          const worldPerPx = camera.position.distanceTo(P) * fovScale / viewH;
          const shift = k * vBlur[j] * MAX_BLUR_PX * worldPerPx;
          const v = c * np + j;
          pos.setXYZ(v, ms.pts[j].x + _perp.x * shift, ms.pts[j].y + _perp.y * shift, ms.pts[j].z + _perp.z * shift);
          // blurred copies share the alpha; with no blur only the centre line shows
          const wj = vBlur[j] > 0.001 ? w : (c === 0 ? 1 : 0);
          col.setXYZW(v, _c.r, _c.g, _c.b, Math.min(1, vOp[j] * wj * (vBlur[j] > 0.001 ? 1.8 : 1)) * ms.show);
        }
      }
    }
    pos.needsUpdate = true; col.needsUpdate = true;
  }
}

function setMeasure(on) {
  state.measure = on; measureRoot.visible = on;
  const b = document.getElementById('measure-btn');
  if (b) { b.setAttribute('aria-pressed', String(on)); b.textContent = on ? 'Hide measurements' : 'Measurements'; }
  const fw = document.getElementById('mfont-wrap'); if (fw) fw.hidden = !on;
  const dw = document.getElementById('density-wrap'); if (dw) dw.hidden = !on;
}

// ---------- 2D blueprint ----------
const sheetEl = document.getElementById('sheet');
const svgCache = new Map();
const sheetLabel = s => `${s.index}/${state.data.sheets.length}`;

async function showSheet(index) {
  const sheet = state.data.sheets.find(s => s.index === index);
  if (!sheet) return;
  state.sheet = sheet;
  if (!svgCache.has(sheet.file)) svgCache.set(sheet.file, await (await fetch(base + sheet.file)).text());
  sheetEl.innerHTML = svgCache.get(sheet.file);
  const svg = sheetEl.querySelector('svg');
  svg.removeAttribute('width'); svg.removeAttribute('height');
  view.full = svg.viewBox.baseVal ? { x: 0, y: 0, w: svg.viewBox.baseVal.width, h: svg.viewBox.baseVal.height } : null;
  fitView();
  sheetEl.querySelectorAll('.part').forEach(g => g.addEventListener('click', () => { if (!view.dragged) select(g.dataset.part); }));
  document.getElementById('sheet-select').value = String(index);
  refresh2D();
}

// ---------- blueprint zoom & pan (changes the SVG viewBox) ----------
const view = { full: null, vb: null, dragged: false };
function applyView() {
  const svg = sheetEl.querySelector('svg');
  if (svg && view.vb) svg.setAttribute('viewBox', `${view.vb.x} ${view.vb.y} ${view.vb.w} ${view.vb.h}`);
}
function fitView() {
  if (!view.full) return;
  // pad the sheet so it fits the pane with the same aspect ratio
  const r = sheetEl.getBoundingClientRect(), f = view.full, pad = 0.04;
  const aspect = r.width / Math.max(r.height, 1);
  let w = f.w * (1 + pad * 2), h = f.h * (1 + pad * 2);
  if (w / h < aspect) w = h * aspect; else h = w / aspect;
  view.vb = { x: f.x + f.w / 2 - w / 2, y: f.y + f.h / 2 - h / 2, w, h };
  applyView();
}
function zoomAt(factor, cx, cy) { // factor < 1 = zoom in; cx, cy in client px
  if (!view.vb) return;
  const r = sheetEl.getBoundingClientRect();
  const sx = cx == null ? 0.5 : (cx - r.left) / r.width, sy = cy == null ? 0.5 : (cy - r.top) / r.height;
  const vb = view.vb, maxW = view.full.w * 4, minW = view.full.w / 60;
  const w = Math.min(maxW, Math.max(minW, vb.w * factor)); const k = w / vb.w;
  view.vb = { x: vb.x + (vb.w - w) * sx, y: vb.y + (vb.h - vb.h * k) * sy, w, h: vb.h * k };
  applyView();
}
sheetEl.addEventListener('wheel', e => { e.preventDefault(); zoomAt(e.deltaY > 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }, { passive: false });
let drag = null;
sheetEl.addEventListener('pointerdown', e => {
  if (e.button !== 0 || !view.vb) return;
  drag = { x: e.clientX, y: e.clientY, vb: { ...view.vb } }; view.dragged = false;
});
window.addEventListener('pointermove', e => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (!view.dragged && Math.hypot(dx, dy) < 4) return;
  if (!view.dragged) { view.dragged = true; sheetEl.classList.add('dragging'); }
  const r = sheetEl.getBoundingClientRect();
  view.vb = { ...drag.vb, x: drag.vb.x - dx * drag.vb.w / r.width, y: drag.vb.y - dy * drag.vb.h / r.height };
  applyView();
});
window.addEventListener('pointerup', () => {
  if (!drag) return;
  drag = null; sheetEl.classList.remove('dragging');
  setTimeout(() => { view.dragged = false; }, 0); // let the click handler see it first
});
sheetEl.addEventListener('dblclick', fitView);
new ResizeObserver(() => { if (view.vb) fitView(); }).observe(sheetEl);

function refresh2D() {
  sheetEl.querySelectorAll('.part').forEach(g => {
    g.classList.toggle('sel', g.dataset.part === state.selected);
    const p = state.parts.get(g.dataset.part);
    g.classList.toggle('faded', !!p && !inFilter(p));
  });
  document.querySelectorAll('#parts-body tr').forEach(tr => {
    tr.classList.toggle('sel', tr.dataset.part === state.selected);
    tr.classList.toggle('dim-row', !inFilter(state.parts.get(tr.dataset.part)));
  });
}

// ---------- shared selection ----------
const info = document.getElementById('info');

function select(id) {
  state.selected = id;
  clearHardware();
  const p = id && state.parts.get(id);
  if (p && p.sheets?.length && !p.sheets.includes(state.sheet?.index)) showSheet(p.sheets[0]);
  refresh3D(); refresh2D();
  if (!p) { info.textContent = 'Select a part in either view.'; return; }
  info.innerHTML = `<b>#${p.no} ${esc(p.name)}</b> · ${esc(groupName(p.group))} · ${p.thickness} mm foamex · ${p.w} × ${p.h} mm · `
    + (p.miters ? `${p.miters} mitered edge${p.miters === 1 ? '' : 's'}` : 'straight cuts')
    + (p.holes ? ` · ${p.holes} cutout${p.holes === 1 ? '' : 's'}` : '')
    + (p.note ? ` · ${esc(p.note)}` : '')
    + (p.pieces > 1 ? ` · ${p.pieces} panels` : '')
    + (p.sheets?.length ? ` · sheet ${sheetList(p.sheets)} of ${state.data.sheets.length}` : '');
}

function showHardware(type, one) {
  select(null);
  const all = (state.data.hardware || []).filter(h => h.type === type);
  for (const [id, m] of state.pins) m.material.emissive.set(all.some(h => h.id === id) ? '#b0241f' : '#000000');
  document.querySelectorAll('#hw-body tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.type === type));
  const h = one || all[0];
  info.innerHTML = `<b>${esc(type)}</b> · pin joint · ${h.size.map(evenMM).join(' × ')} mm · ${all.length} in this model (not cut from foamex)`;
}
function clearHardware() {
  for (const [, m] of state.pins) m.material.emissive.set('#000000');
  document.querySelectorAll('#hw-body tr').forEach(tr => tr.classList.remove('sel'));
}

function showFigure(f) {
  select(null);
  info.innerHTML = `<b>Figure</b> · ${esc(f.name)} · ${f.height} mm tall (reference only, not cut)`;
}

function setGroup(g) {
  state.group = g;
  document.querySelectorAll('.chip[data-g]').forEach(c => c.setAttribute('aria-pressed', String(c.dataset.g === String(g))));
  if (state.selected && !inFilter(state.parts.get(state.selected))) select(null);
  refresh3D(); refresh2D(); buildMeasures();
}

// ---------- UI ----------
function buildUI() {
  const d = state.data;
  document.getElementById('project').textContent = d.project;
  document.title = `${d.project} · Foamex Viewer`;

  const groups = [...new Set(d.parts.map(p => p.group ?? 1))].sort((a, b) => a - b);
  const chips = document.getElementById('filter-chips');
  chips.innerHTML = (groups.length > 1
    ? [`<button class="chip" data-g="all" aria-pressed="true">All</button>`,
       ...groups.map(g => `<button class="chip" data-g="${g}" aria-pressed="false">${esc(groupName(g))}</button>`)].join('')
    : '')
    + `<button class="chip toggle" id="toggle-xray" aria-pressed="false" title="See through all plates">X-ray</button>`
    + ((d.scene?.length || d.figures?.length) ? `<button class="chip toggle" id="toggle-set" aria-pressed="true">Figures</button>` : '');
  chips.querySelectorAll('.chip[data-g]').forEach(c => c.addEventListener('click', () =>
    setGroup(c.dataset.g === 'all' ? 'all' : +c.dataset.g)));
  document.getElementById('toggle-set')?.addEventListener('click', e => {
    state.showSet = !state.showSet;
    e.currentTarget.setAttribute('aria-pressed', String(state.showSet));
    refreshVisibility();
  });
  document.getElementById('toggle-xray').addEventListener('click', e => {
    state.xray = !state.xray;
    e.currentTarget.setAttribute('aria-pressed', String(state.xray));
    refresh3D();
  });

  const sel = document.getElementById('sheet-select');
  sel.innerHTML = d.sheets.map(s => `<option value="${s.index}">Sheet ${s.index} / ${d.sheets.length}</option>`).join('');
  sel.addEventListener('change', () => showSheet(+sel.value));
  const step = k => { const i = (state.sheet?.index ?? 1) + k; if (i >= 1 && i <= d.sheets.length) showSheet(i); };
  document.getElementById('sheet-prev').addEventListener('click', () => step(-1));
  document.getElementById('sheet-next').addEventListener('click', () => step(1));
  document.getElementById('zoom-in').addEventListener('click', () => zoomAt(1 / 1.4));
  document.getElementById('zoom-out').addEventListener('click', () => zoomAt(1.4));
  document.getElementById('zoom-fit').addEventListener('click', fitView);

  const cams = d.cameras || [];
  const cb = document.getElementById('cam-buttons');
  // always one button: the pre-vis camera from Blender (first camera in the file)
  cb.innerHTML = `<button class="btn small cam" id="previs-btn" ${cams.length ? '' : 'disabled'}
    title="${cams.length ? 'Go to the pre-vis camera angle from Blender' : 'No camera in this model: export the .glb with Include > Cameras'}">View Pre-vis angle</button>`;
  document.getElementById('previs-btn').addEventListener('click', () => goToCamera(0));
  document.getElementById('measure-btn').addEventListener('click', () => setMeasure(!state.measure));


  const hw = d.hardware || [];
  const hwWrap = document.getElementById('hardware');
  if (hw.length) {
    const types = new Map();
    for (const h of hw) {
      const k = h.type + '|' + h.size.map(evenMM).join('x');
      if (!types.has(k)) types.set(k, { type: h.type, size: h.size, n: 0 });
      types.get(k).n++;
    }
    hwWrap.hidden = false;
    hwWrap.querySelector('summary').textContent = `Hardware · pin joints (${hw.length})`;
    document.getElementById('hw-body').innerHTML = [...types.values()].map(t =>
      `<tr data-type="${esc(t.type)}"><td>${esc(t.type)}</td><td class="num">${t.size.map(evenMM).join(' × ')}</td><td class="num">${t.n}</td></tr>`).join('');
    document.querySelectorAll('#hw-body tr').forEach(tr => tr.addEventListener('click', () => showHardware(tr.dataset.type)));
  }

  const ld = document.getElementById('density'), ldv = document.getElementById('density-val');
  ld.value = Math.round(BLOCK_SHARE * 100); ldv.textContent = ld.value + '%';
  ld.addEventListener('input', () => {
    BLOCK_SHARE = +ld.value / 100; ldv.textContent = ld.value + '%';
    try { localStorage.setItem('foamex-label-density', ld.value); } catch {}
  });
  const mf = document.getElementById('mfont');
  mf.addEventListener('input', () => { fontScale = +mf.value; document.getElementById('mfont-val').textContent = Math.round(fontScale * 100) + '%'; });

  document.getElementById('parts-body').innerHTML = d.parts.map(p =>
    `<tr data-part="${esc(p.id)}"><td class="num">${p.no}</td><td>${esc(p.name)}${p.pieces > 1 ? ` <span class="pcs split">split · ${p.pieces} panels</span>` : ''}</td>
     <td>${esc(groupName(p.group))}</td><td class="num">${p.w} × ${p.h}</td><td class="num">${p.miters ?? 0}</td>
     <td class="num">${p.sheets?.length ? sheetList(p.sheets) : '—'}</td></tr>`).join('');
  document.querySelectorAll('#parts-body tr').forEach(tr => tr.addEventListener('click', () => select(tr.dataset.part)));

  const split = d.parts.filter(p => (p.pieces || 1) > 1);
  const sb = document.getElementById('split-banner');
  if (split.length) {
    const [sw, sh] = d.sheet_size || [400, 600];
    sb.hidden = false;
    sb.innerHTML = `<span class="sb-icon" aria-hidden="true">⚠</span><span>${split.length} plate${split.length === 1 ? ' is' : 's are'} bigger than a
      ${sw / 10} × ${sh / 10} cm sheet and ${split.length === 1 ? 'was' : 'were'} split into panels:
      ${split.map(p => `<button class="sb-part" data-part="${esc(p.id)}">${esc(p.name)} (${p.pieces} panels)</button>`).join(', ')}</span>
      <button class="sb-x" aria-label="Dismiss">×</button>`;
    sb.querySelectorAll('.sb-part').forEach(b => b.addEventListener('click', () => select(b.dataset.part)));
    sb.querySelector('.sb-x').addEventListener('click', () => { sb.hidden = true; });
  }
  const w = d.warnings || [];
  const box = document.getElementById('warnings');
  if (w.length) {
    box.hidden = false;
    box.innerHTML = `<summary>${w.length} note${w.length === 1 ? '' : 's'} from the conversion</summary><ul>${w.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  }

  document.getElementById('explode').addEventListener('input', e => applyExplode(+e.target.value));
}

// older projects (like the dummy house) list sheets per thickness without an index
function normalize(d) {
  d.sheets.forEach((s, i) => {
    s.index ??= i + 1;
    if (!s.w) { s.w = 297; s.h = 210; }
    for (const id of s.parts || []) { const p = d.parts.find(p => p.id === id); if (p && p.sheet == null) p.sheet = s.index; }
  });
  d.parts.forEach(p => { p.sheets ??= p.sheet != null ? [p.sheet] : []; p.pieces ??= 1;
  });
  d.parts.forEach(p => { p.group ??= 1; p.miters ??= 0; });
  return d;
}

// ---------- model browser ----------
const RECENT_KEY = 'foamex-recent';
function readRecent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '{}') || {}; } catch { return {}; } }
function markOpened(id) { try { const r = readRecent(); r[id] = Date.now(); localStorage.setItem(RECENT_KEY, JSON.stringify(r)); } catch {} }
async function showBrowser() {
  document.getElementById('browser').hidden = false;
  const grid = document.getElementById('model-grid');
  let list = [];
  try { list = (await (await fetch('projects/projects.json', { cache: 'no-cache' })).json()).projects || []; }
  catch (err) { console.error(err); }
  const render = q => {
    const recent = readRecent();
    const items = list.filter(m => !q || `${m.name} ${m.id} ${m.description || ''}`.toLowerCase().includes(q))
      .sort((a, b) => (recent[b.id] || 0) - (recent[a.id] || 0) || String(b.converted || '').localeCompare(String(a.converted || '')));
    grid.innerHTML = items.length ? items.map(m => {
      const initials = esc((m.name || m.id).replace(/[^A-Za-z0-9]/g, ' ').trim().split(/\s+/).map(w => w[0]).join('').slice(0, 3).toUpperCase() || '?');
      const thumb = m.thumb
        ? `<div class="thumb"><img src="projects/${esc(m.thumb)}" alt="" loading="lazy"></div>`
        : `<div class="thumb placeholder"><span>${initials}</span></div>`;
      const size = m.sheet_size ? `${m.sheet_size[0] / 10}×${m.sheet_size[1] / 10} cm sheets` : '';
      const stats = [`${m.parts} parts`, `${m.sheets} sheet${m.sheets === 1 ? '' : 's'}`, size].filter(Boolean).join(' · ');
      const opened = recent[m.id] ? new Date(recent[m.id]).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
      const meta = [m.scale && m.scale !== 1 ? `scale ${m.scale}` : '', opened ? `last opened on ${opened}` : 'not opened yet'].filter(Boolean).join(' · ');
      return `<a class="model-card${m.pinned ? ' pinned' : ''}" href="#${encodeURIComponent(m.id)}">${thumb}
        <div class="card-body"><div class="pills">${m.label ? `<span class="pill">${esc(m.label)}</span>` : ''}${m.split ? `<span class="pill warn" title="${m.split} plate${m.split === 1 ? '' : 's'} split across sheets">⚠ ${m.split} split</span>` : ''}</div><h3>${esc(m.name)}</h3>
        ${m.description ? `<p>${esc(m.description)}</p>` : ''}
        <p class="card-stats">${esc(stats)}</p>${meta ? `<p>${esc(meta)}</p>` : ''}</div></a>`;
    }).join('') : `<p class="empty">${list.length ? 'No models match.' : 'No models yet. Convert one with tools/convert_glb.py.'}</p>`;
  };
  render('');
  document.getElementById('model-search').addEventListener('input', e => render(e.target.value.trim().toLowerCase()));
}

// ---------- guided tour (projects/<id>/tour.json) ----------
// Shows once per browser (localStorage), skippable, replayable. Steps point at parts of the page and can
// run a small action so the feature is visible: reset | select:<part id> | sheet:<n> | explode:<0..1> | measure:on | previs
const tourEl = document.getElementById('tour'), ring = document.getElementById('tour-ring'), pop = document.getElementById('tour-pop');
let tour = null;
const tourKey = () => `foamex-tour-seen:${hashId || PROJECT_URL}`;
function tourAction(a) {
  if (!a) return;
  const [k, v] = a.split(':');
  if (k === 'reset' && homeView) moveCamera(homeView.pos, homeView.target, homeView.fov);
  if (k === 'select') select(v);
  if (k === 'sheet') showSheet(+v);
  if (k === 'explode') { const ex = document.getElementById('explode'); ex.value = v; applyExplode(+v); }
  if (k === 'measure') setMeasure(v !== 'off');
  if (k === 'previs') goToCamera(0);
}
function tourTargets(sel) { return [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null); }
function placeTour() {
  if (!tour || tourEl.hidden) return;
  const step = tour.steps[tour.i], els = tourTargets(step.target);
  let r = { left: innerWidth / 2 - 100, top: innerHeight / 2 - 60, right: innerWidth / 2 + 100, bottom: innerHeight / 2 + 60 };
  if (els.length) {
    const rs = els.map(e => e.getBoundingClientRect());
    r = { left: Math.min(...rs.map(x => x.left)), top: Math.min(...rs.map(x => x.top)),
          right: Math.max(...rs.map(x => x.right)), bottom: Math.max(...rs.map(x => x.bottom)) };
  }
  const pad = 6;
  Object.assign(ring.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`,
    width: `${r.right - r.left + pad * 2}px`, height: `${r.bottom - r.top + pad * 2}px` });
  // popover: below the target if it fits, else above, else inside near the top; clamped to the window
  const pw = pop.offsetWidth, ph = pop.offsetHeight, gap = 14;
  let top = r.bottom + gap;
  if (top + ph > innerHeight - 10) top = r.top - gap - ph;
  if (top < 10) top = Math.min(r.top + 20, innerHeight - ph - 10);
  const left = Math.min(Math.max((r.left + r.right) / 2 - pw / 2, 10), innerWidth - pw - 10);
  Object.assign(pop.style, { left: `${left}px`, top: `${Math.max(10, top)}px` });
}
function showTourStep(i) {
  tour.i = i;
  const st = tour.steps[i], last = i === tour.steps.length - 1;
  tourAction(st.action);
  pop.innerHTML = `<p class="tour-count">Step ${i + 1} of ${tour.steps.length}</p>
    <h3>${esc(st.title)}</h3><p>${esc(st.text)}</p>
    <div class="tour-btns"><button class="btn" id="tour-skip">${last ? 'Close' : 'Skip tutorial'}</button>
      <span class="tour-gap"></span>
      ${i > 0 ? '<button class="btn" id="tour-back">Back</button>' : ''}
      <button class="btn primary" id="tour-next">${last ? 'Done' : 'Next'}</button></div>`;
  pop.querySelector('#tour-skip').onclick = endTour;
  pop.querySelector('#tour-back')?.addEventListener('click', () => showTourStep(i - 1));
  pop.querySelector('#tour-next').onclick = () => last ? endTour() : showTourStep(i + 1);
  requestAnimationFrame(placeTour);
  setTimeout(placeTour, 350); // after layout changes from the action (e.g. new buttons, sheet swap)
  pop.querySelector('#tour-next').focus({ preventScroll: true });
}
function startTour() { if (!tour) return; tourEl.hidden = false; showTourStep(0); }
function endTour() {
  tourEl.hidden = true;
  try { localStorage.setItem(tourKey(), '1'); } catch {}
  const ex = document.getElementById('explode'); if (+ex.value) { ex.value = 0; applyExplode(0); }
}
window.addEventListener('resize', placeTour);
window.addEventListener('keydown', e => {
  if (tourEl.hidden || !tour) return;
  if (e.key === 'Escape') endTour();
  else if (e.key === 'ArrowRight' || e.key === 'Enter') { e.preventDefault(); tour.i < tour.steps.length - 1 ? showTourStep(tour.i + 1) : endTour(); }
  else if (e.key === 'ArrowLeft' && tour.i > 0) showTourStep(tour.i - 1);
});
async function loadTour() {
  try {
    const r = await fetch(base + 'tour.json', { cache: 'no-cache' });
    if (!r.ok) return;
    const t = await r.json();
    if (!t.steps?.length) return;
    tour = { steps: t.steps, i: 0 };
    const rb = document.getElementById('replay-tutorial'); rb.hidden = false; rb.onclick = startTour;
    let seen = false; try { seen = !!localStorage.getItem(tourKey()); } catch {}
    if (!seen) setTimeout(startTour, 400);
  } catch {}
}

// ---------- fullscreen panes ----------
function paneFull(pane) { return document.fullscreenElement === pane || pane.classList.contains('maximized'); }
function syncFullButtons() {
  document.querySelectorAll('.full-btn').forEach(b => {
    const on = paneFull(b.closest('.pane'));
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? '✕' : '⛶';
    b.title = on ? 'Exit fullscreen (Esc)' : 'Fullscreen'; b.setAttribute('aria-label', b.title);
  });
  requestAnimationFrame(() => { if (view.vb) fitView(); });
}
async function toggleFull(pane) {
  if (paneFull(pane)) {
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
    pane.classList.remove('maximized');
  } else if (pane.requestFullscreen && document.fullscreenEnabled !== false) {
    try { await pane.requestFullscreen(); } catch { pane.classList.add('maximized'); }
  } else pane.classList.add('maximized');
  syncFullButtons();
}
document.addEventListener('fullscreenchange', syncFullButtons);
window.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.pane.maximized').forEach(p => { p.classList.remove('maximized'); syncFullButtons(); });
});
document.querySelectorAll('.full-btn').forEach(b => b.addEventListener('click', () => toggleFull(b.closest('.pane'))));

// ---------- boot ----------
(async () => {
  if (MODE === 'browser') { showBrowser(); return; }
  document.getElementById('viewer').hidden = false;
  document.getElementById('viewer-controls').hidden = false;
  document.getElementById('info').hidden = false;
  document.getElementById('back').hidden = false;
  try {
    const res = await fetch(PROJECT_URL);
    if (!res.ok && hashId) { // an old link? look the id up in the projects' aliases and redirect
      try {
        const list = (await (await fetch('projects/projects.json', { cache: 'no-cache' })).json()).projects || [];
        const hit = list.find(m => (m.aliases || []).some(a => a.toLowerCase() === hashId.toLowerCase()));
        if (hit) { location.replace('#' + encodeURIComponent(hit.id)); return; }
      } catch {}
    }
    const data = normalize(await res.json());
    if (hashId) markOpened(hashId);
    state.data = data;
    data.parts.forEach(p => state.parts.set(p.id, p));
    buildUI();
    await showSheet(data.sheets[0].index);
    try { await Promise.race([document.fonts.load('600 44px "Barlow Condensed"'), new Promise(r => setTimeout(r, 2500))]); } catch {}
    await loadModel(base + data.model);
    if (data.cameras?.length) goToCamera(0, true); // open from the Blender camera
    loadTour();
    refresh3D();
  } catch (err) {
    console.error(err);
    info.textContent = 'Could not load the project. If you opened index.html directly, run a local server (see README).';
  }
})();
