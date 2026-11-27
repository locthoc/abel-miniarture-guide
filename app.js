import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// Which project to load: ?project=path/to/parts.json (defaults to ./parts.json)
const params = new URLSearchParams(location.search);
const PROJECT_URL = params.get('project') || 'parts.json';
const base = PROJECT_URL.includes('/') ? PROJECT_URL.slice(0, PROJECT_URL.lastIndexOf('/') + 1) : '';

const state = {
  data: null, parts: new Map(), meshes: new Map(), extras: [], figures: new Map(),
  selected: null, group: 'all', showSet: true, xray: false, sheet: null,
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
viewport.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#f6f4ef');
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
const HI = new THREE.Color('#ff6a2b');
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
  homeView = { pos: camera.position.clone(), target: center.clone() };
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
  const meshes = [];
  gltf.scene.traverse(o => { if (o.isMesh) meshes.push(o); });
  for (const o of meshes) {
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    const id = o.name, pid = o.parent?.name;
    const part = state.parts.get(id) || state.parts.get(pid);
    const set = sceneIds.get(id) || sceneIds.get(pid);
    const fig = figIds.get(id) || figIds.get(pid);
    if (part) {
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
  addGround(); fitCamera(); refreshVisibility();
}

function applyExplode(t) {
  for (const m of state.meshes.values()) {
    const scale = m.parent.getWorldScale(new THREE.Vector3()).x || 1;
    m.position.copy(m.userData.home).addScaledVector(m.userData.dir, m.userData.reach * t / scale);
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
    m.userData.edges.material.color.set(sel ? '#c2410c' : '#7d8186');
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
  const hit = ray.intersectObjects(targets, false)[0];
  if (hit?.object.userData.figure) return showFigure(hit.object.userData.figure);
  select(hit ? hit.object.userData.id : null);
});

renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });

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
  const dl = document.getElementById('dl-svg');
  dl.href = base + sheet.file; dl.download = sheet.file.split('/').pop();
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
  const p = id && state.parts.get(id);
  if (p && p.sheets?.length && !p.sheets.includes(state.sheet?.index)) showSheet(p.sheets[0]);
  refresh3D(); refresh2D();
  if (!p) { info.textContent = 'Select a part in either view.'; return; }
  const angles = [...new Set((p.bevels || p.angles || []).filter(a => a != null))].map(a => a + '°').join(', ');
  info.innerHTML = `<b>#${p.no} ${esc(p.name)}</b> · ${esc(groupName(p.group))} · ${p.thickness} mm foamex · ${p.w} × ${p.h} mm · `
    + (p.miters ? `${p.miters} mitered edge${p.miters === 1 ? '' : 's'} (${angles})` : 'straight cuts')
    + (p.holes ? ` · ${p.holes} cutout${p.holes === 1 ? '' : 's'}` : '')
    + (p.note ? ` · ${esc(p.note)}` : '')
    + (p.pieces > 1 ? ` · ${p.pieces} panels` : '')
    + (p.sheets?.length ? ` · sheet ${sheetList(p.sheets)} of ${state.data.sheets.length}` : '');
}

function showFigure(f) {
  select(null);
  info.innerHTML = `<b>Figure</b> · ${esc(f.name)} · ${f.height} mm tall (reference only, not cut)`;
}

function setGroup(g) {
  state.group = g;
  document.querySelectorAll('.chip[data-g]').forEach(c => c.setAttribute('aria-pressed', String(c.dataset.g === String(g))));
  if (state.selected && !inFilter(state.parts.get(state.selected))) select(null);
  refresh3D(); refresh2D();
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

  const guide = document.querySelector('.nav-guide'), gt = document.getElementById('guide-toggle');
  let guideOn = true;
  try { guideOn = localStorage.getItem('foamex-guide') !== 'off'; } catch {}
  const setGuide = on => {
    guideOn = on; if (guide) guide.hidden = !on;
    gt.textContent = on ? 'Hide controls' : 'Show controls'; gt.setAttribute('aria-pressed', String(on));
    try { localStorage.setItem('foamex-guide', on ? 'on' : 'off'); } catch {}
  };
  gt.addEventListener('click', () => setGuide(!guideOn));
  setGuide(guideOn);

  document.getElementById('parts-body').innerHTML = d.parts.map(p =>
    `<tr data-part="${esc(p.id)}"><td class="num">${p.no}</td><td>${esc(p.name)}${p.pieces > 1 ? ` <span class="pcs">${p.pieces} panels</span>` : ''}</td>
     <td>${esc(groupName(p.group))}</td><td class="num">${p.w} × ${p.h}</td><td class="num">${p.miters ?? 0}</td>
     <td class="num">${p.sheets?.length ? sheetList(p.sheets) : '—'}</td></tr>`).join('');
  document.querySelectorAll('#parts-body tr').forEach(tr => tr.addEventListener('click', () => select(tr.dataset.part)));

  const w = d.warnings || [];
  const box = document.getElementById('warnings');
  if (w.length) {
    box.hidden = false;
    box.innerHTML = `<summary>${w.length} note${w.length === 1 ? '' : 's'} from the conversion</summary><ul>${w.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  }

  document.getElementById('explode').addEventListener('input', e => applyExplode(+e.target.value));
  document.getElementById('reset-view').addEventListener('click', () => {
    if (!homeView) return;
    camera.position.copy(homeView.pos); controls.target.copy(homeView.target); controls.update();
  });
  document.getElementById('print').addEventListener('click', () => {
    if (!state.sheet) return;
    const { w: sw, h: sh } = state.sheet;
    document.getElementById('page-size').textContent =
      `@page{size:${sw}mm ${sh}mm;margin:0}@media print{#print-area svg{width:${sw}mm;height:${sh}mm}}`;
    document.getElementById('print-area').innerHTML = svgCache.get(state.sheet.file); // original black-on-white
    window.print();
  });
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

// ---------- boot ----------
(async () => {
  try {
    const data = normalize(await (await fetch(PROJECT_URL)).json());
    state.data = data;
    data.parts.forEach(p => state.parts.set(p.id, p));
    buildUI();
    await showSheet(data.sheets[0].index);
    await loadModel(base + data.model);
    refresh3D();
  } catch (err) {
    console.error(err);
    info.textContent = 'Could not load the project. If you opened index.html directly, run a local server (see README).';
  }
})();
