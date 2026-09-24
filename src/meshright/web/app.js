import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { STLLoader } from 'three/addons/STLLoader.js';
import { TransformControls } from 'three/addons/TransformControls.js';

const $ = (id) => document.getElementById(id);
const viewerEl = $('viewer');

// ---------------------------------------------------------------- 3D scene

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.localClippingEnabled = true;
renderer.setPixelRatio(window.devicePixelRatio);
viewerEl.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
camera.position.set(200, 160, 200);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(1, 2, 1.5);
camera.add(sun);
scene.add(camera);

// Print files use Z-up; three.js uses Y-up. Everything model-related lives in
// modelRoot, which is rotated so the model's Z axis points up on screen.
// The model is shown where it really is: the grid is the print bed at Z = 0.
const modelRoot = new THREE.Group();
modelRoot.rotation.x = -Math.PI / 2;
scene.add(modelRoot);

let content = null;   // model + problem highlights, in the file's own coordinates
let grid = null;
let problemLines = null;
let homeView = null;
let lastSpan = null;
let currentGeometry = null;  // the model's triangles, in its own coordinates

const frontMaterial = new THREE.MeshStandardMaterial({
  color: 0x9fb4d0, roughness: 0.65, metalness: 0.05, flatShading: true,
  polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
});
// Rough spots view: colours per triangle (see applyRoughness).
const roughMaterial = new THREE.MeshStandardMaterial({
  vertexColors: true, roughness: 0.7, flatShading: true,
  polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
});
// What changed since the original file (see applyChanges).
const changeMaterial = new THREE.MeshStandardMaterial({
  vertexColors: true, roughness: 0.7, flatShading: true,
  polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -3,
});
// The model's own colours (colour scans), drawn over the plain surface.
const colourMaterial = new THREE.MeshStandardMaterial({
  vertexColors: true, roughness: 0.75, flatShading: true,
  polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
});
// Back faces are drawn in a warning colour: seen through holes or on
// triangles that point the wrong way.
const backMaterial = new THREE.MeshStandardMaterial({
  color: 0xb85c5c, roughness: 0.9, flatShading: true, side: THREE.BackSide,
  polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
});

function resize() {
  const { clientWidth: w, clientHeight: h } = viewerEl;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewerEl);
resize();

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
  if (typeof placeMeasureLabel === 'function') placeMeasureLabel();
});

function clearModel() {
  if (content) {
    content.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    modelRoot.remove(content);
  }
  if (grid) { scene.remove(grid); grid.geometry.dispose(); }
  content = grid = problemLines = null;
}

function meshFromGeometry(geometry) {
  const group = new THREE.Group();
  group.add(new THREE.Mesh(geometry, frontMaterial));
  group.add(new THREE.Mesh(geometry, backMaterial));
  return group;
}

function makeBedGrid(box, span) {
  // Print-bed style grid centred on the origin: 10 mm squares for typical
  // models, coarser for big ones, always wide enough to reach the model.
  const reach = Math.max(Math.abs(box.min.x), Math.abs(box.max.x), Math.abs(box.min.y), Math.abs(box.max.y));
  let cell = Math.pow(10, Math.max(0, Math.floor(Math.log10(span / 5))));
  let cells = Math.max(10, Math.ceil((reach * 2.4) / cell));
  while (cells > 400) { cell *= 10; cells = Math.max(10, Math.ceil((reach * 2.4) / cell)); }
  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const g = new THREE.GridHelper(cells * cell, cells, dark ? 0x5a5a5e : 0x9a9aa2, dark ? 0x323236 : 0xd2d2d7);
  g.material.transparent = true;
  g.material.opacity = 0.6;
  return g;
}

// keepView: after an edit, keep looking from the same direction instead of
// jumping back to the starting view.
function showModel(model, { keepView = false } = {}) {
  clearModel();
  content = new THREE.Group();
  content.add(model);
  modelRoot.add(content);
  currentGeometry = model.children[0].geometry;
  pickMarkers = null;

  const box = new THREE.Box3().setFromObject(model.children[0]);  // model coordinates, Z up
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.x, size.y, size.z, 1e-6);
  grid = makeBedGrid(box, span);
  scene.add(grid);

  const worldBox = new THREE.Box3().setFromObject(content);
  const target = worldBox.getCenter(new THREE.Vector3());
  camera.near = span / 1000;
  camera.far = span * 100 + target.length() * 4;
  camera.updateProjectionMatrix();

  const homeOffset = new THREE.Vector3(1, 0.75, 1).normalize().multiplyScalar(span * 1.9);
  homeView = { target, position: target.clone().add(homeOffset) };

  if (keepView && lastSpan) {
    const offset = camera.position.clone().sub(controls.target).multiplyScalar(span / lastSpan);
    controls.target.copy(target);
    camera.position.copy(target.clone().add(offset));
    controls.update();
  } else {
    resetView();
  }
  lastSpan = span;
  cutRange = [worldBox.min.y - span * 0.01, worldBox.max.y + span * 0.01];
  applyWireframe();
  applyCut();
  if (typeof applyCoin === 'function') applyCoin();
  overhangShade = null;
  roughShade = null;
  colourShade = null;
  changeShade = null;
  if (typeof applyChanges === 'function') applyChanges();
  if (typeof applyOverhangs === 'function') applyOverhangs();
  if (typeof applyColours === 'function') applyColours();
  if (typeof applyRoughness === 'function') applyRoughness();
}

function resetView() {
  if (!homeView) return;
  controls.target.copy(homeView.target);
  camera.position.copy(homeView.position);
  controls.update();
}

function addProblemLines(highlights) {
  if (!content) return;
  problemLines = new THREE.Group();
  const layers = [
    [highlights.open_edges, 0xef4444],
    [highlights.non_manifold_edges, 0xf97316],
    [highlights.crossing_edges, 0xa855f7],
  ];
  for (const [coords, color] of layers) {
    if (!coords || coords.length === 0) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(coords, 3));
    const lines = new THREE.LineSegments(
      geometry,
      new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.95 }),
    );
    lines.renderOrder = 10;
    problemLines.add(lines);
  }
  problemLines.visible = $('toggle-problems').checked;
  content.add(problemLines);
}

// Cut view: hide everything above a horizontal plane. The inside shows in the
// dark "inside" colour, so walls, voids and hidden shells stand out.
const cutPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
let cutRange = [0, 1];

function applyCut() {
  const on = $('toggle-cut').checked && content;
  $('cut-height').hidden = !on;
  const planes = on ? [cutPlane] : [];
  frontMaterial.clippingPlanes = planes;
  backMaterial.clippingPlanes = planes;
  roughMaterial.clippingPlanes = planes;
  changeMaterial.clippingPlanes = planes;
  colourMaterial.clippingPlanes = planes;
  if (on) {
    const share = Number($('cut-height').value) / 1000;
    cutPlane.constant = cutRange[0] + (cutRange[1] - cutRange[0]) * share;
  }
}

function applyWireframe() {
  const on = $('toggle-wireframe').checked;
  frontMaterial.wireframe = on;
  backMaterial.wireframe = on;
}

$('toggle-problems').addEventListener('change', (e) => {
  if (problemLines) problemLines.visible = e.target.checked;
});
$('toggle-wireframe').addEventListener('change', applyWireframe);
$('toggle-cut').addEventListener('change', applyCut);
$('cut-height').addEventListener('input', applyCut);
$('reset-view').addEventListener('click', resetView);

// ---------------------------------------------------------------- side panel

function showPanel(which) {
  for (const id of ['panel-empty', 'panel-busy', 'panel-error', 'panel-report']) {
    $(id).hidden = id !== which;
  }
}

function showError(message) {
  $('error-text').textContent = message;
  showPanel('panel-error');
}

let statusTimer = null;
function showStatus(message, kind = 'info') {
  const el = $('status');
  el.textContent = message;
  el.className = kind;
  el.hidden = false;
  clearTimeout(statusTimer);
  // "busy" stays until the work finishes and the next message replaces it.
  if (kind !== 'busy') statusTimer = setTimeout(() => { el.hidden = true; }, kind === 'error' ? 8000 : 6000);
}

const fmt = (n, digits = 1) => Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });

function renderIssues(issues) {
  const list = $('issues');
  list.replaceChildren();
  if (issues.length === 0) {
    const li = document.createElement('li');
    li.className = 'none';
    li.innerHTML = '<strong>No problems found</strong>';
    list.append(li);
  }
  for (const issue of issues) {
    const li = document.createElement('li');
    li.className = issue.severity;
    const title = document.createElement('strong');
    title.textContent = issue.title;
    const detail = document.createElement('p');
    detail.textContent = issue.detail;
    li.append(title, detail);
    // Cleanup has its own big button below the list, so only other fixes
    // (such as unit fixes or Put on bed) appear next to a problem.
    const quickFixes = (issue.fixes || []).filter((f) => !f.preview);
    if (quickFixes.length) {
      const fixes = document.createElement('div');
      fixes.className = 'fixes';
      for (const fix of quickFixes) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = fix.label;
        button.addEventListener('click', () => runAction(fix.action, fix.params));
        fixes.append(button);
      }
      li.append(fixes);
    }
    list.append(li);
  }
}

function renderStats(s) {
  const rows = [
    ['Size (mm)', s.size_mm.map((v) => fmt(v)).join(' × ')],
    ['Triangles', fmt(s.triangles, 0)],
    ['Separate pieces', fmt(s.pieces, 0)],
    ['Closed (watertight)', s.watertight ? 'Yes' : 'No'],
    ['Volume', s.volume_mm3 == null ? 'n/a (not closed)' : `${fmt(s.volume_mm3 / 1000, 2)} cm³`],
    ['Filament if solid', s.filament_g == null ? 'n/a (not closed)' : `up to ${fmt(s.filament_g, 0)} g ${s.material}`],
  ];
  const stats = $('stats');
  stats.replaceChildren();
  for (const [label, value] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    stats.append(dt, dd);
  }
  ['x', 'y', 'z'].forEach((axis, i) => {
    const input = $(`size-${axis}`);
    if (document.activeElement !== input) input.value = Number(s.size_mm[i].toFixed(2));
  });
}

function renderHistory(history) {
  const list = $('history');
  list.replaceChildren();
  const first = document.createElement('li');
  first.textContent = 'Opened the file';
  first.className = history.position === 0 ? 'current' : '';
  if (history.dropped) first.textContent += ` (${history.dropped} older steps not kept)`;
  list.append(first);
  history.steps.forEach((step, i) => {
    const li = document.createElement('li');
    li.textContent = step.receipt;
    if (i + 1 === history.position) li.className = 'current';
    else if (i >= history.position) li.className = 'undone';
    list.append(li);
  });
  $('undo').disabled = !history.can_undo;
  $('redo').disabled = !history.can_redo;
  $('revert').disabled = history.position === 0;
}

function renderReport(state) {
  const ring = $('score-ring');
  ring.style.setProperty('--pct', state.score);
  ring.style.setProperty('--ring',
    state.score >= 90 && state.printable ? 'var(--good)' : state.printable ? 'var(--ok)' : 'var(--bad)');
  $('score-value').textContent = state.score;
  $('verdict').textContent = state.verdict;
  $('file-name').textContent = state.name;
  renderIssues(state.issues);
  renderChecklist(state);
  renderStats(state.stats);
  renderHistory(state.history);
  showPanel('panel-report');
}

// ---------------------------------------------------------------- guided checklist

const FIX_CODES = new Set([
  'heavy_mesh', 'holes', 'non_manifold', 'self_intersections', 'inside_out', 'inconsistent_winding',
  'floating_debris', 'duplicate_faces', 'degenerate_faces', 'crossing_not_checked',
]);
const STAND_CODES = ['may_tip_over', 'small_contact', 'off_bed'];

function checklistRow(state, number, title, detail, button) {
  const li = document.createElement('li');
  li.className = state;
  const mark = document.createElement('span');
  mark.className = 'mark';
  mark.textContent = state === 'done' ? '✓' : state === 'todo' || state === 'blocked' ? '!' : String(number);
  const text = document.createElement('div');
  const strong = document.createElement('strong');
  strong.textContent = title;
  const small = document.createElement('small');
  small.textContent = detail;
  text.append(strong, small);
  li.append(mark, text);
  // One button, or several stacked (for example Shrink or Split).
  const buttons = (Array.isArray(button) ? button : [button]).filter(Boolean).map((spec) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = spec.primary ? 'primary' : '';
    b.textContent = spec.label;
    b.addEventListener('click', spec.run);
    return b;
  });
  if (buttons.length === 1) li.append(buttons[0]);
  else if (buttons.length) {
    const stack = document.createElement('div');
    stack.className = 'buttons';
    stack.append(...buttons);
    li.append(stack);
  }
  return li;
}

function renderChecklist(state) {
  const issues = new Map(state.issues.map((i) => [i.code, i]));
  const list = $('checklist');
  list.replaceChildren();

  // 1. Fix problems
  const toFix = state.issues.filter((i) => FIX_CODES.has(i.code));
  const fixBlocked = toFix.some((i) => i.severity === 'error');
  list.append(checklistRow(
    toFix.length ? (fixBlocked ? 'blocked' : 'todo') : 'done', 1, 'Fix problems',
    toFix.length ? `${toFix.length} thing${toFix.length === 1 ? '' : 's'} to fix` : 'No problems left',
    { label: 'Clean up…', primary: fixBlocked, run: openCleanup },
  ));

  // 2. Stand it up
  const stand = STAND_CODES.map((c) => issues.get(c)).find(Boolean);
  const standFixes = (stand && stand.fixes) || [];
  list.append(checklistRow(
    stand ? 'todo' : 'done', 2, 'Stand it up',
    stand ? stand.title : 'Sits steadily on the bed',
    standFixes.length
      ? standFixes.map((fix) => ({ label: fix.label, run: () => runAction(fix.action, fix.params) }))
      : { label: 'Which way is up?', run: openUp },
  ));
  if (!stand) list.lastChild.classList.add('keep-button');

  // 3. Fits your printer
  const tooBig = issues.get('too_big');
  if (!printerChosen) {
    list.append(checklistRow('todo', 3, 'Fits your printer', 'Choose your printer to check', { label: 'Choose', run: openPrinter }));
  } else if (tooBig) {
    // Shrink it, or split it into parts that each fit (keeps the full size).
    const buttons = tooBig.fixes.map((fix, i) => ({
      label: fix.label, primary: i === 0, run: () => runAction(fix.action, fix.params),
    }));
    list.append(checklistRow('blocked', 3, 'Fits your printer', `Too big for the ${printer.name}: shrink it, or split it into parts`, buttons));
  } else {
    list.append(checklistRow('done', 3, 'Fits your printer', `Fits the ${printer.name}`));
  }

  // 4. Export
  const ready = [...list.children].every((li) => li.classList.contains('done'));
  list.append(checklistRow(ready ? 'ready' : '', 4, 'Export for your slicer',
    ready ? 'Ready to print. Save it and open it in your slicer.' : 'You can export at any time',
    preferredSlicer()
      ? { label: `Open in ${preferredSlicer().name}`, primary: ready, run: () => sendToSlicer(preferredSlicer()) }
      : { label: 'Export 3MF', primary: ready, run: () => exportAs('3mf') }));
}

// ---------------------------------------------------------------- send to slicer

let slicers = [];

// The slicer made by the printer's maker when it is installed, otherwise
// OrcaSlicer (it knows most printers), otherwise any slicer found.
function preferredSlicer() {
  if (!slicers.length) return null;
  const brands = { bambu: /bambu/i, prusa: /prusa/i, creality: /creality/i };
  if (printerChosen && printer) {
    const match = slicers.find((s) => brands[s.id]?.test(printer.name));
    if (match) return match;
  }
  return slicers.find((s) => s.id === 'orca') || slicers[0];
}

async function sendToSlicer(slicer) {
  if (!docId || busy) return;
  $('export-menu').open = false;
  showStatus(`Opening in ${slicer.name}…`, 'busy');
  try {
    const result = await request(`api/doc/${docId}/send-to-slicer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slicer: slicer.id }),
    });
    showStatus(result.message);
  } catch (err) {
    showStatus(err.message, 'error');
  }
}

async function loadSlicers() {
  try {
    slicers = await (await fetch('api/slicers')).json();
  } catch {
    slicers = [];
  }
  const holder = $('slicer-items');
  holder.replaceChildren();
  for (const slicer of slicers) {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'menuitem');
    button.append(`Open in ${slicer.name}`);
    const small = document.createElement('small');
    small.textContent = 'Saves a 3MF and opens it there';
    button.append(small);
    button.addEventListener('click', () => sendToSlicer(slicer));
    holder.append(button);
  }
  holder.hidden = slicers.length === 0;
}
loadSlicers();

// ---------------------------------------------------------------- printer

let printer = null;
let printerChosen = false;
let presets = [];
let materials = [];

function showPrinterName() {
  $('printer-name').textContent = printerChosen ? printer.name : 'Choose printer';
}

async function loadSettings() {
  try {
    const data = await request('api/settings');
    printer = data.printer;
    printerChosen = data.chosen;
    presets = data.presets;
    materials = data.materials;
    showPrinterName();
    if (!printerChosen) openPrinter();
  } catch (err) {
    showStatus(err.message, 'error');
  }
}

function fillPrinterFields(p) {
  $('bed-x').value = p.bed_x;
  $('bed-y').value = p.bed_y;
  $('bed-z').value = p.bed_z;
  $('nozzle').value = p.nozzle_mm;
  $('material').value = p.material;
}

function openPrinter() {
  const select = $('printer-preset');
  select.replaceChildren();
  presets.forEach((p, i) => select.append(new Option(p.name, String(i))));
  select.append(new Option('Something else (enter the sizes)', 'other'));
  $('material').replaceChildren(...materials.map((m) => new Option(m, m)));
  const match = presets.findIndex((p) => printerChosen && p.name === printer.name);
  if (printerChosen) {
    select.value = match >= 0 ? String(match) : 'other';
    fillPrinterFields(printer);
  } else {
    select.value = '0';
    fillPrinterFields(presets[0]);
  }
  $('printer-error').hidden = true;
  $('printer-dialog').showModal();
}

$('printer-preset').addEventListener('change', (e) => {
  if (e.target.value !== 'other') fillPrinterFields(presets[Number(e.target.value)]);
});

$('printer-form').addEventListener('submit', async (e) => {
  if (e.submitter?.value !== 'save') return;
  e.preventDefault();
  const choice = $('printer-preset').value;
  const base = choice === 'other' ? { name: 'My printer', technology: 'filament' } : presets[Number(choice)];
  const material = $('material').value;
  const chosen = {
    ...base,
    bed_x: Number($('bed-x').value),
    bed_y: Number($('bed-y').value),
    bed_z: Number($('bed-z').value),
    nozzle_mm: Number($('nozzle').value),
    material,
    technology: material === 'Resin' ? 'resin' : (base.technology === 'resin' ? 'filament' : base.technology),
  };
  try {
    const data = await request('api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ printer: chosen }),
    });
    printer = data.printer;
    printerChosen = data.chosen;
    showPrinterName();
    $('printer-dialog').close();
    showStatus(`Printer set to ${printer.name}`);
    if (docId) renderReport(await request(`api/doc/${docId}/state`));
  } catch (err) {
    $('printer-error').textContent = err.message;
    $('printer-error').hidden = false;
  }
});

$('printer-open').addEventListener('click', openPrinter);
loadSettings();

// ---------------------------------------------------------------- talking to MeshRight

let docId = null;
let busy = false;

async function request(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Something went wrong (error ${res.status}).`);
  return data;
}

// The server reads every supported format, fixes units and orientation, and
// sends back the exact mesh it checked, so the highlights always line up.
async function fetchModel(id) {
  const res = await fetch(`api/doc/${id}/mesh`);
  if (!res.ok) throw new Error('Could not load the model for display.');
  const shown = Number(res.headers.get('X-Shown-Triangles'));
  const total = Number(res.headers.get('X-Total-Triangles'));
  const note = $('display-note');
  note.hidden = !(shown && total && shown < total);
  if (!note.hidden) {
    note.textContent = `Showing a lighter copy for speed (${fmt(shown, 0)} of ${fmt(total, 0)} triangles). Fixes and export use full detail.`;
  }
  return meshFromGeometry(new STLLoader().parse(await res.arrayBuffer()));
}

// Upload with a progress bar: big scans can take a while.
let currentUpload = null;  // the file being opened, so Cancel can stop it

function uploadWithProgress(url, body, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    currentUpload = xhr;
    xhr.open('POST', url);
    xhr.addEventListener('abort', () => {
      const err = new Error('Cancelled');
      err.cancelled = true;
      reject(err);
    });
    xhr.addEventListener('loadend', () => { if (currentUpload === xhr) currentUpload = null; });
    xhr.upload.addEventListener('progress', (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); });
    xhr.upload.addEventListener('load', () => onProgress(1));
    xhr.addEventListener('load', () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `Something went wrong (error ${xhr.status}).`));
    });
    xhr.addEventListener('error', () => reject(new Error('Could not reach MeshRight. Is it still running?')));
    xhr.send(body);
  });
}

let colourShade = null;
let colouredDoc = null;  // the model whose Colours switch was last set

async function showState(state, { keepView }) {
  const model = await fetchModel(state.doc_id);
  // A model with colours shows them, switched on the first time it is seen.
  $('colour-toggle').hidden = !state.has_colour;
  if (state.has_colour && colouredDoc !== state.doc_id) $('toggle-colour').checked = true;
  if (state.has_colour) colouredDoc = state.doc_id;
  if (!state.has_colour) $('toggle-colour').checked = false;
  showModel(model, { keepView });
  addProblemLines(state.highlights);
  renderReport(state);
}

function setBusy(on) {
  busy = on;
  document.body.classList.toggle('busy', on);
}

// Every change goes through here: an action, or undo / redo / revert.
// Leave any clicking mode (picking the bottom, erasing, merging): their
// clicks belong to the model as it was.
function exitModes({ keepBrush = false } = {}) {
  if (sculpting && !keepBrush) stopSculpt();
  if (cutter) stopCutter();
  if (measuring) stopMeasure();
  if (holing) stopHole();
  stopPick();
  stopPiece();
  if (!lassoLayer.hidden) stopErase();
  if (mergeScan) stopMerge();
  if (combineScan) stopCombine();
  $('tip').hidden = true;
}

async function change(path, body, receipt, busyText) {
  if (!docId || busy) return;
  const forDoc = docId;
  // The brush does not depend on the shape, so it stays on between strokes
  // and through undo.
  exitModes({ keepBrush: true });
  setBusy(true);
  if (busyText) showStatus(busyText, 'busy');
  try {
    const state = await request(`api/doc/${docId}/${path}`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (forDoc !== docId) return;  // another file was opened meanwhile
    await showState(state, { keepView: true });
    const text = receipt ? receipt(state) : null;
    if (text) showStatus(text);
  } catch (err) {
    showStatus(err.message, 'error');
  } finally {
    setBusy(false);
  }
}

const BUSY_TEXT = {
  cleanup: 'Cleaning up…',
  make_solid: 'Rebuilding as a solid…',
  merge_scan: 'Lining up and fusing the scans…',
  combine_models: 'Combining…',
  cut_in_two: 'Cutting…',
  hollow: 'Hollowing…',
  make_bust: 'Making the bust…',
  split_to_fit: 'Splitting into parts…',
};

function runAction(action, params = {}) {
  return change('action', { action, params }, (state) => {
    const steps = state.history.steps;
    return steps.length ? steps[state.history.position - 1]?.receipt : null;
  }, BUSY_TEXT[action]);
}

// ---------------------------------------------------------------- which way is up

const thumbMaterials = [
  new THREE.MeshStandardMaterial({ color: 0x9fb4d0, roughness: 0.65, metalness: 0.05, flatShading: true }),
  new THREE.MeshStandardMaterial({ color: 0xb85c5c, roughness: 0.9, flatShading: true, side: THREE.BackSide }),
];
let thumbRenderer = null;

// A small picture of the model resting with `down` pointing at the bed.
function renderThumb(down) {
  const w = 240;
  const h = 180;
  if (!thumbRenderer) {
    thumbRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    thumbRenderer.setPixelRatio(1);
    thumbRenderer.setSize(w, h);
  }
  const thumbScene = new THREE.Scene();
  thumbScene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.6));
  const cam = new THREE.PerspectiveCamera(35, w / h, 0.1, 1e7);
  const light = new THREE.DirectionalLight(0xffffff, 1.4);
  light.position.set(1, 2, 1.5);
  cam.add(light);
  thumbScene.add(cam);

  const root = new THREE.Group();
  root.rotation.x = -Math.PI / 2;
  thumbScene.add(root);
  const turned = new THREE.Group();
  turned.quaternion.setFromUnitVectors(new THREE.Vector3(...down).normalize(), new THREE.Vector3(0, 0, -1));
  for (const material of thumbMaterials) turned.add(new THREE.Mesh(currentGeometry, material));
  const holder = new THREE.Group();
  holder.add(turned);
  const box = new THREE.Box3().setFromObject(turned);
  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());
  holder.position.set(-centre.x, -centre.y, -box.min.z);
  root.add(holder);

  const span = Math.max(size.x, size.y, size.z, 1e-6);
  const bed = new THREE.Mesh(
    new THREE.CircleGeometry(span * 0.9, 48),
    new THREE.MeshBasicMaterial({ color: 0x8894a8, transparent: true, opacity: 0.18 }),
  );
  bed.rotation.x = -Math.PI / 2;
  thumbScene.add(bed);

  const target = new THREE.Vector3(0, size.z / 2, 0);
  cam.position.copy(target).add(new THREE.Vector3(1, 0.7, 1).normalize().multiplyScalar(span * 2.1));
  cam.near = span / 1000;
  cam.far = span * 100;
  cam.lookAt(target);
  cam.updateProjectionMatrix();
  thumbRenderer.render(thumbScene, cam);
  bed.geometry.dispose();
  return thumbRenderer.domElement.toDataURL('image/png');
}

let upChoices = [];

async function openUp() {
  if (!docId || busy) return;
  const dialog = $('up-dialog');
  $('up-choices').replaceChildren();
  $('up-intro').textContent = 'Finding likely bottoms…';
  dialog.showModal();
  try {
    upChoices = await request(`api/doc/${docId}/up-candidates`);
  } catch (err) {
    $('up-intro').textContent = err.message;
    return;
  }
  $('up-intro').textContent = 'Pick the picture that shows the model standing the right way up.';
  upChoices.forEach((choice, i) => {
    const button = document.createElement('button');
    button.type = 'submit';
    button.value = String(i);
    const img = document.createElement('img');
    img.alt = '';
    img.src = renderThumb(choice.down);
    const title = document.createElement('strong');
    title.textContent = i === 0 ? 'Best guess' : `Option ${i + 1}`;
    button.append(img, title, document.createTextNode(choice.reason));
    $('up-choices').append(button);
  });
}

$('up-open').addEventListener('click', openUp);
$('up-dialog').addEventListener('close', () => {
  const choice = $('up-dialog').returnValue;
  $('up-dialog').returnValue = '';
  if (choice === 'pick') startPick();
  else if (/^\d+$/.test(choice) && upChoices[Number(choice)]) {
    const [x, y, z] = upChoices[Number(choice)].down;
    runAction('set_bottom', { down_x: x, down_y: y, down_z: z });
  }
});

// Pick the bottom: click points on the model, then lay it flat on them.
let picking = false;
let pickPoints = [];
let pickMarkers = null;
const raycaster = new THREE.Raycaster();
let pointerStart = null;

function updatePickBar() {
  const n = pickPoints.length;
  $('pick-text').textContent = n < 3
    ? `Click ${3 - n} more point${3 - n === 1 ? '' : 's'} on the bottom of the model`
    : `${n} points picked. More points even out scan bumps.`;
  $('pick-done').disabled = n < 3;
  $('pick-undo').disabled = n === 0;
}

function startPick() {
  if (!docId || !content) return;
  stopPiece();
  picking = true;
  pickPoints = [];
  $('status').hidden = true;
  document.body.classList.add('picking');
  $('pick-bar').hidden = false;
  updatePickBar();
}

function stopPick() {
  picking = false;
  document.body.classList.remove('picking');
  $('pick-bar').hidden = true;
  if (pickMarkers && content) content.remove(pickMarkers);
  pickMarkers = null;
  pickPoints = [];
}

function addPickMarker(point) {
  if (!pickMarkers) {
    pickMarkers = new THREE.Group();
    content.add(pickMarkers);
  }
  const radius = (lastSpan || 10) * 0.01;
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0x0071e3, depthTest: false }),
  );
  marker.renderOrder = 20;
  marker.position.copy(point);
  pickMarkers.add(marker);
}

renderer.domElement.addEventListener('pointerdown', (e) => { pointerStart = [e.clientX, e.clientY]; });
let pieceMode = false;

function startPiece() {
  if (!docId || !content || busy) return;
  stopPick();
  pieceMode = true;
  document.body.classList.add('picking');
  $('piece-bar').hidden = false;
  $('status').hidden = true;
}

function stopPiece() {
  pieceMode = false;
  document.body.classList.remove('picking');
  $('piece-bar').hidden = true;
}

// Where on the model (in its own coordinates) a click landed, or null.
function modelPointAt(e) {
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObject(content.children[0], true)[0];
  return hit ? content.worldToLocal(hit.point.clone()) : null;
}

renderer.domElement.addEventListener('pointerup', (e) => {
  if (!pieceMode || !pointerStart || !content) return;
  if (Math.hypot(e.clientX - pointerStart[0], e.clientY - pointerStart[1]) > 5) return;
  const point = modelPointAt(e);
  if (!point) return;
  stopPiece();
  runAction('erase_piece', { point: point.toArray() });
});

renderer.domElement.addEventListener('pointerup', (e) => {
  if (!picking || !pointerStart || !content) return;
  const moved = Math.hypot(e.clientX - pointerStart[0], e.clientY - pointerStart[1]);
  if (moved > 5) return;  // that was a drag to turn the view
  const local = modelPointAt(e);
  if (!local) return;
  pickPoints.push([local.x, local.y, local.z]);
  addPickMarker(local);
  updatePickBar();
});

$('pick-start').addEventListener('click', startPick);
$('thick-open').addEventListener('click', () => { if (docId && !busy) $('thick-dialog').showModal(); });
$('thick-dialog').addEventListener('close', () => {
  const choice = $('thick-dialog').returnValue;
  $('thick-dialog').returnValue = '';
  if (choice === 'apply') runAction('thicken', { thickness_mm: Number($('thickness').value) });
});
$('solid-open').addEventListener('click', () => {
  if (!docId || busy) return;
  openCleanup();
});
$('piece-start').addEventListener('click', startPiece);
$('piece-cancel').addEventListener('click', stopPiece);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && pieceMode) stopPiece(); });
$('pick-cancel').addEventListener('click', stopPick);
$('pick-undo').addEventListener('click', () => {
  pickPoints.pop();
  if (pickMarkers && pickMarkers.children.length) pickMarkers.remove(pickMarkers.children.at(-1));
  updatePickBar();
});
$('pick-done').addEventListener('click', () => {
  const points = pickPoints.slice();
  stopPick();
  runAction('lay_flat', { points });
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && picking) stopPick(); });

// ---------------------------------------------------------------- erase an area

const lassoLayer = $('lasso-layer');
let lasso = [];          // screen points of the loop being drawn, in CSS pixels
let lassoNdc = null;     // the finished loop in -1..1 screen coordinates
let erasePreview = null; // red overlay of the triangles that would go
let drawing = false;
let lassoPurpose = 'erase';  // 'erase', or 'part' (Make a part)
const LASSO_TEXT = {
  erase: { draw: 'Draw a loop around what you want to erase', ready: 'The red area will be erased', button: 'Erase', colour: 0xff3b30, css: '#ff3b30', fill: 'rgba(255, 59, 48, 0.08)' },
  part: { draw: 'Draw a loop around the area the part should fit', ready: 'The blue area becomes the part', button: 'Make part…', colour: 0x0071e3, css: '#0071e3', fill: 'rgba(0, 113, 227, 0.08)' },
};

function viewMatrix() {
  camera.updateMatrixWorld();
  content.updateMatrixWorld();
  return new THREE.Matrix4()
    .multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
    .multiply(content.matrixWorld);
}

function startErase(purpose = 'erase') {
  if (!docId || !content || busy) return;
  exitModes();
  lassoPurpose = purpose === 'part' ? 'part' : 'erase';
  $('erase-do').textContent = LASSO_TEXT[lassoPurpose].button;
  $('erase-do').classList.toggle('danger', lassoPurpose === 'erase');
  controls.enabled = false;
  lassoLayer.hidden = false;
  const rect = viewerEl.getBoundingClientRect();
  lassoLayer.width = rect.width * devicePixelRatio;
  lassoLayer.height = rect.height * devicePixelRatio;
  $('erase-bar').hidden = false;
  $('status').hidden = true;
  resetLasso();
}

function resetLasso() {
  lasso = [];
  lassoNdc = null;
  clearErasePreview();
  lassoLayer.getContext('2d').clearRect(0, 0, lassoLayer.width, lassoLayer.height);
  $('erase-text').textContent = LASSO_TEXT[lassoPurpose].draw;
  $('erase-do').hidden = true;
  $('erase-again').hidden = true;
  lassoLayer.style.pointerEvents = 'auto';
}

function stopErase() {
  controls.enabled = true;
  lassoLayer.hidden = true;
  $('erase-bar').hidden = true;
  clearErasePreview();
  lasso = [];
  lassoNdc = null;
}

function clearErasePreview() {
  if (erasePreview) {
    erasePreview.geometry.dispose();
    erasePreview.parent?.remove(erasePreview);
    erasePreview = null;
  }
}

function drawLasso(closed) {
  const ctx = lassoLayer.getContext('2d');
  ctx.clearRect(0, 0, lassoLayer.width, lassoLayer.height);
  if (lasso.length < 2) return;
  ctx.save();
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.beginPath();
  ctx.moveTo(lasso[0][0], lasso[0][1]);
  for (const [x, y] of lasso.slice(1)) ctx.lineTo(x, y);
  if (closed) ctx.closePath();
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = LASSO_TEXT[lassoPurpose].css;
  ctx.fillStyle = LASSO_TEXT[lassoPurpose].fill;
  if (closed) ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function insideLoop(x, y, loop) {
  let inside = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const [xi, yi] = loop[i];
    const [xj, yj] = loop[j];
    if ((yi > y) !== (yj > y) && x < xi + ((y - yi) * (xj - xi)) / (yj - yi)) inside = !inside;
  }
  return inside;
}

// Show in red which triangles the loop would erase (on the displayed copy).
function previewErase() {
  clearErasePreview();
  const positions = currentGeometry.getAttribute('position').array;
  const m = viewMatrix().elements;
  const eye = content.worldToLocal(camera.position.clone());
  const facingOnly = $('erase-facing').checked;
  const picked = [];
  for (let i = 0; i < positions.length; i += 9) {
    const cx = (positions[i] + positions[i + 3] + positions[i + 6]) / 3;
    const cy = (positions[i + 1] + positions[i + 4] + positions[i + 7]) / 3;
    const cz = (positions[i + 2] + positions[i + 5] + positions[i + 8]) / 3;
    const w = m[3] * cx + m[7] * cy + m[11] * cz + m[15];
    if (w <= 1e-9) continue;
    const sx = (m[0] * cx + m[4] * cy + m[8] * cz + m[12]) / w;
    const sy = (m[1] * cx + m[5] * cy + m[9] * cz + m[13]) / w;
    if (!insideLoop(sx, sy, lassoNdc)) continue;
    if (facingOnly) {
      const ux = positions[i + 3] - positions[i], uy = positions[i + 4] - positions[i + 1], uz = positions[i + 5] - positions[i + 2];
      const vx = positions[i + 6] - positions[i], vy = positions[i + 7] - positions[i + 1], vz = positions[i + 8] - positions[i + 2];
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      if (nx * (eye.x - cx) + ny * (eye.y - cy) + nz * (eye.z - cz) <= 0) continue;
    }
    for (let k = 0; k < 9; k++) picked.push(positions[i + k]);
  }
  const count = picked.length / 9;
  if (!count) {
    $('erase-text').textContent = 'Nothing inside that loop. Draw it around part of the model.';
    $('erase-again').hidden = false;
    return;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(picked, 3));
  erasePreview = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
    color: LASSO_TEXT[lassoPurpose].colour, transparent: true, opacity: 0.55, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  }));
  erasePreview.renderOrder = 5;
  content.add(erasePreview);
  $('erase-text').textContent = LASSO_TEXT[lassoPurpose].ready;
  $('erase-do').hidden = false;
  $('erase-again').hidden = false;
}

lassoLayer.addEventListener('pointerdown', (e) => {
  if (lassoNdc) return;
  drawing = true;
  lassoLayer.setPointerCapture(e.pointerId);
  const rect = lassoLayer.getBoundingClientRect();
  lasso = [[e.clientX - rect.left, e.clientY - rect.top]];
});
lassoLayer.addEventListener('pointermove', (e) => {
  if (!drawing) return;
  const rect = lassoLayer.getBoundingClientRect();
  const last = lasso.at(-1);
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  if (Math.hypot(x - last[0], y - last[1]) > 3) {
    lasso.push([x, y]);
    drawLasso(false);
  }
});
lassoLayer.addEventListener('pointerup', () => {
  if (!drawing) return;
  drawing = false;
  if (lasso.length < 3) {
    resetLasso();
    return;
  }
  drawLasso(true);
  const rect = lassoLayer.getBoundingClientRect();
  // Keep at most 1,500 points (the server accepts 2,000): plenty for any loop.
  const step = Math.ceil(lasso.length / 1500);
  lassoNdc = lasso.filter((_, i) => i % step === 0)
    .map(([x, y]) => [(x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1]);
  lassoLayer.style.pointerEvents = 'none';  // the loop is done; let the bar take clicks
  previewErase();
});

$('erase-facing').addEventListener('change', () => { if (lassoNdc) previewErase(); });
$('erase-start').addEventListener('click', () => startErase('erase'));
$('part-start').addEventListener('click', () => startErase('part'));
$('erase-again').addEventListener('click', resetLasso);
$('erase-cancel').addEventListener('click', stopErase);
let partSelection = null;  // the loop, kept while the part sheet is open

$('erase-do').addEventListener('click', () => {
  const params = {
    lasso: lassoNdc.flat(),
    view: Array.from(viewMatrix().elements),
    eye: content.worldToLocal(camera.position.clone()).toArray(),
    facing_only: $('erase-facing').checked,
  };
  if (lassoPurpose === 'part') {
    partSelection = params;
    $('part-dialog').showModal();
    return;
  }
  stopErase();
  runAction('erase_area', params);
});

for (const button of document.querySelectorAll('#part-side [data-side]')) {
  button.addEventListener('click', () => {
    for (const b of document.querySelectorAll('#part-side [data-side]')) b.setAttribute('aria-checked', String(b === button));
    $('part-gap-field').hidden = button.dataset.side !== 'outside';
  });
}

$('part-dialog').addEventListener('close', async () => {
  const choice = $('part-dialog').returnValue;
  $('part-dialog').returnValue = '';
  if (choice !== 'apply' || !partSelection) return;
  const params = {
    ...partSelection,
    thickness_mm: Number($('part-thickness').value) || 3,
    side: document.querySelector('#part-side [aria-checked="true"]').dataset.side,
    gap_mm: Number($('part-gap').value) || 0,
  };
  partSelection = null;
  stopErase();
  setBusy(true);
  showStatus('Making the part…', 'busy');
  try {
    const state = await request(`api/doc/${docId}/new-model`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'part_from_area', params }),
    });
    docId = state.doc_id;
    addTab(state.doc_id, state.name);
    await showState(state, { keepView: false });
    showStatus(`${state.notice}. It opened as a new model; the original is in the other tab.`);
  } catch (err) {
    showStatus(err.message, 'error');
  } finally {
    setBusy(false);
  }
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !lassoLayer.hidden) stopErase(); });

// ---------------------------------------------------------------- best way to print

let bestOptions = [];

async function openBest() {
  if (!docId || busy) return;
  $('best-choices').replaceChildren();
  $('best-intro').textContent = 'Trying different ways to place it…';
  $('best-dialog').showModal();
  let advice;
  try {
    advice = await request(`api/doc/${docId}/orientations`);
  } catch (err) {
    $('best-intro').textContent = err.message;
    return;
  }
  bestOptions = advice.options;
  const now = advice.current;
  $('best-intro').textContent = `As it is now: about ${fmt(now.support_cm3, 1)} cm³ of support, ${fmt(now.height_mm, 0)} mm tall. Pick a way to place it:`;
  bestOptions.forEach((option, i) => {
    const button = document.createElement('button');
    button.type = 'submit';
    button.value = `best-${i}`;
    const img = document.createElement('img');
    img.alt = '';
    img.src = renderThumb(option.down);
    const title = document.createElement('strong');
    title.textContent = option.title;
    const numbers = document.createElement('span');
    numbers.className = 'numbers';
    numbers.textContent = `Support ≈ ${fmt(option.support_cm3, 1)} cm³ · ${fmt(option.height_mm, 0)} mm tall`;
    button.append(img, title, numbers, document.createTextNode(option.why));
    $('best-choices').append(button);
  });
}

$('best-open').addEventListener('click', openBest);
$('best-dialog').addEventListener('close', () => {
  const choice = $('best-dialog').returnValue;
  $('best-dialog').returnValue = '';
  const match = /^best-(\d+)$/.exec(choice);
  if (match && bestOptions[Number(match[1])]) {
    const [x, y, z] = bestOptions[Number(match[1])].down;
    runAction('set_bottom', { down_x: x, down_y: y, down_z: z });
  }
});

// Overhangs: shade surfaces facing down more steeply than 45° (they need support).
let overhangShade = null;

function applyOverhangs() {
  if (overhangShade) {
    overhangShade.geometry.dispose();
    overhangShade.parent?.remove(overhangShade);
    overhangShade = null;
  }
  if (!$('toggle-overhang').checked || !content || !currentGeometry) return;
  const p = currentGeometry.getAttribute('position').array;
  let low = Infinity;
  for (let i = 2; i < p.length; i += 3) low = Math.min(low, p[i]);
  const picked = [];
  const limit = -Math.cos(Math.PI / 4);
  for (let i = 0; i < p.length; i += 9) {
    const ux = p[i + 3] - p[i], uy = p[i + 4] - p[i + 1], uz = p[i + 5] - p[i + 2];
    const vx = p[i + 6] - p[i], vy = p[i + 7] - p[i + 1], vz = p[i + 8] - p[i + 2];
    const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz) || 1;
    if (nz / len > limit) continue;
    if (Math.min(p[i + 2], p[i + 5], p[i + 8]) <= low + 0.3) continue;  // resting on the bed
    for (let k = 0; k < 9; k++) picked.push(p[i + k]);
  }
  if (!picked.length) {
    showStatus('Nothing overhangs: this prints without supports as it is.');
    return;
  }
  let area = 0;
  for (let i = 0; i < picked.length; i += 9) {
    const ux = picked[i + 3] - picked[i], uy = picked[i + 4] - picked[i + 1], uz = picked[i + 5] - picked[i + 2];
    const vx = picked[i + 6] - picked[i], vy = picked[i + 7] - picked[i + 1], vz = picked[i + 8] - picked[i + 2];
    area += Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
  }
  showStatus(`About ${fmt(area / 100, 1)} cm² overhangs and will need support (shown in orange; look from below). Try "Best way to print…".`);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(picked, 3));
  overhangShade = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
    color: 0xff9f0a, transparent: true, opacity: 0.6, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  }));
  overhangShade.renderOrder = 4;
  content.add(overhangShade);
}

$('toggle-overhang').addEventListener('change', () => {
  applyOverhangs();
  if (!$('toggle-overhang').checked) $('status').hidden = true;
});

// Rough spots: colour the surface green where it is clean and red where it
// is rough (scanner noise). Worked out by the server for the shown triangles.
let roughShade = null;

function roughColour(level) {
  // green → yellow → red
  const t = level / 255;
  const green = new THREE.Color(0x34c759), yellow = new THREE.Color(0xffcc00), red = new THREE.Color(0xff3b30);
  return t < 0.5 ? green.lerp(yellow, t * 2) : yellow.lerp(red, (t - 0.5) * 2);
}

async function applyRoughness() {
  if (roughShade) {
    roughShade.geometry.dispose();
    roughShade.parent?.remove(roughShade);
    roughShade = null;
  }
  if (!$('toggle-rough').checked || !content || !currentGeometry) return;
  const geometry = currentGeometry;
  const forDoc = docId;
  let data;
  try {
    data = await request(`api/doc/${docId}/roughness`);
  } catch (err) {
    showStatus(err.message, 'error');
    return;
  }
  if (forDoc !== docId || geometry !== currentGeometry || !$('toggle-rough').checked || roughShade) return;
  const levels = Uint8Array.from(atob(data.levels), (c) => c.charCodeAt(0));
  const position = geometry.getAttribute('position');
  if (levels.length * 3 !== position.count) return;
  const colours = new Float32Array(position.count * 3);
  const cache = new Map();
  for (let f = 0; f < levels.length; f++) {
    let c = cache.get(levels[f]);
    if (!c) { c = roughColour(levels[f]); cache.set(levels[f], c); }
    for (let k = 0; k < 3; k++) colours.set([c.r, c.g, c.b], (f * 3 + k) * 3);
  }
  const shade = new THREE.BufferGeometry();
  shade.setAttribute('position', position.clone());
  shade.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3));
  shade.computeVertexNormals();
  roughShade = new THREE.Mesh(shade, roughMaterial);
  roughShade.renderOrder = 3;
  content.add(roughShade);
  const share = Math.round(data.rough_share * 100);
  if (toolActive()) return;  // brushing: the colours say enough
  showStatus(share > 0
    ? `About ${share}% of the surface is rough (red). The Smooth brush in Sculpt calms rough areas.`
    : 'The surface looks clean: nothing rough.');
}

async function applyColours() {
  if (colourShade) {
    colourShade.geometry.dispose();
    colourShade.parent?.remove(colourShade);
    colourShade = null;
  }
  if (!$('toggle-colour').checked || $('colour-toggle').hidden || !content || !currentGeometry) return;
  const geometry = currentGeometry;
  const forDoc = docId;
  let bytes;
  try {
    const res = await fetch(`api/doc/${docId}/colours`);
    if (!res.ok) return;
    bytes = new Uint8Array(await res.arrayBuffer());
  } catch {
    return;
  }
  if (forDoc !== docId || geometry !== currentGeometry || !$('toggle-colour').checked || colourShade) return;
  const position = geometry.getAttribute('position');
  if (bytes.length !== position.count * 3) return;
  // Scan colours are sRGB; three.js works in linear light, so convert them
  // or they would look washed out.
  const linear = new Float32Array(256);
  for (let v = 0; v < 256; v++) {
    const c = v / 255;
    linear[v] = c < 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }
  const colours = new Float32Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) colours[i] = linear[bytes[i]];
  const shade = new THREE.BufferGeometry();
  shade.setAttribute('position', position.clone());
  shade.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3));
  colourShade = new THREE.Mesh(shade, colourMaterial);
  colourShade.renderOrder = 2;
  content.add(colourShade);
}

$('toggle-colour').addEventListener('change', applyColours);

// A coin on the bed beside the model, so its real size is easy to picture.
const COIN_MM = { diameter: 23.25, thickness: 2.33 };
const coinMaterial = new THREE.MeshStandardMaterial({ color: 0xf2cf66, metalness: 0.25, roughness: 0.4 });
let coin = null;

function applyCoin() {
  if (coin) { coin.geometry.dispose(); coin.parent?.remove(coin); coin = null; }
  if (!$('toggle-coin').checked || !content || !currentGeometry) return;
  const box = new THREE.Box3().setFromBufferAttribute(currentGeometry.getAttribute('position'));
  const r = COIN_MM.diameter / 2;
  // three.js cylinders stand along Y; turn it so it lies flat (model Z is up).
  coin = new THREE.Mesh(new THREE.CylinderGeometry(r, r, COIN_MM.thickness, 64), coinMaterial);
  coin.rotation.x = Math.PI / 2;
  coin.position.set(box.max.x + Math.max(8, r), (box.min.y + box.max.y) / 2, COIN_MM.thickness / 2);
  modelRoot.add(coin);
}

$('toggle-coin').addEventListener('change', () => {
  applyCoin();
  try { localStorage.setItem('meshright.coin', $('toggle-coin').checked ? '1' : ''); } catch { /* not saved */ }
});
try { $('toggle-coin').checked = localStorage.getItem('meshright.coin') === '1'; } catch { /* default off */ }

// What changed: green where the surface is as in the original file, red
// where repairs moved it. Uses the same colours as Rough spots.
let changeShade = null;

async function applyChanges() {
  if (changeShade) {
    changeShade.geometry.dispose();
    changeShade.parent?.remove(changeShade);
    changeShade = null;
  }
  if (!$('toggle-changes').checked || !content || !currentGeometry) return;
  const geometry = currentGeometry;
  const forDoc = docId;
  let data;
  try {
    data = await request(`api/doc/${docId}/changes`);
  } catch (err) {
    showStatus(err.message, 'error');
    return;
  }
  if (forDoc !== docId || geometry !== currentGeometry || !$('toggle-changes').checked || changeShade) return;
  const levels = Uint8Array.from(atob(data.levels), (c) => c.charCodeAt(0));
  const position = geometry.getAttribute('position');
  if (levels.length * 3 !== position.count) return;
  const colours = new Float32Array(position.count * 3);
  const palette = new Map();
  for (let f = 0; f < levels.length; f++) {
    let c = palette.get(levels[f]);
    if (!c) { c = roughColour(levels[f]); palette.set(levels[f], c); }
    for (let k = 0; k < 3; k++) colours.set([c.r, c.g, c.b], (f * 3 + k) * 3);
  }
  const shade = new THREE.BufferGeometry();
  shade.setAttribute('position', position.clone());
  shade.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3));
  changeShade = new THREE.Mesh(shade, changeMaterial);
  changeShade.renderOrder = 4;
  content.add(changeShade);
  if (toolActive()) return;
  const moved = Math.round(data.moved_share * 100);
  showStatus(moved > 0
    ? `Compared with the original file: ${moved}% of the surface moved more than 0.1 mm (up to ${fmt(data.largest_mm, 2)} mm).`
    : 'Compared with the original file: the surface has not moved.');
}

$('toggle-changes').addEventListener('change', () => {
  applyChanges();
  if (!$('toggle-changes').checked) $('status').hidden = true;
});

$('toggle-rough').addEventListener('change', () => {
  applyRoughness();
  if (!$('toggle-rough').checked) $('status').hidden = true;
});

// ---------------------------------------------------------------- cut in two

let cutter = null;       // the see-through plane showing where the cut goes
let cutterAxis = 'z';

function cutterRange() {
  const box = new THREE.Box3().setFromBufferAttribute(currentGeometry.getAttribute('position'));
  const i = { x: 0, y: 1, z: 2 }[cutterAxis];
  return [box.min.getComponent(i), box.max.getComponent(i), box];
}

function cutterPosition() {
  const [low, high] = cutterRange();
  return low + (high - low) * Number($('cutter-position').value) / 1000;
}

function placeCutter() {
  if (cutter) {
    cutter.geometry.dispose();
    cutter.parent?.remove(cutter);
  }
  const [, , box] = cutterRange();
  const size = box.getSize(new THREE.Vector3()).multiplyScalar(1.25);
  const centre = box.getCenter(new THREE.Vector3());
  // A plane in model coordinates (Z up), facing along the cut axis.
  const dims = { x: [size.y, size.z], y: [size.x, size.z], z: [size.x, size.y] }[cutterAxis];
  cutter = new THREE.Mesh(
    new THREE.PlaneGeometry(dims[0], dims[1]),
    new THREE.MeshBasicMaterial({ color: 0x0071e3, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false }),
  );
  if (cutterAxis === 'x') cutter.rotation.set(0, Math.PI / 2, 0);
  if (cutterAxis === 'y') cutter.rotation.set(Math.PI / 2, 0, 0);
  const at = cutterPosition();
  cutter.position.set(
    cutterAxis === 'x' ? at : centre.x,
    cutterAxis === 'y' ? at : centre.y,
    cutterAxis === 'z' ? at : centre.z,
  );
  cutter.renderOrder = 6;
  content.add(cutter);
}

function startCutter() {
  if (!docId || !content || busy) return;
  exitModes();
  $('cutter-bar').hidden = false;
  $('status').hidden = true;
  placeCutter();
}

function stopCutter() {
  $('cutter-bar').hidden = true;
  if (cutter) {
    cutter.geometry.dispose();
    cutter.parent?.remove(cutter);
    cutter = null;
  }
}

$('cut-start').addEventListener('click', startCutter);
$('cutter-cancel').addEventListener('click', stopCutter);
$('cutter-position').addEventListener('input', placeCutter);
for (const button of document.querySelectorAll('[data-cut-axis]')) {
  button.addEventListener('click', () => {
    cutterAxis = button.dataset.cutAxis;
    for (const b of document.querySelectorAll('[data-cut-axis]')) b.setAttribute('aria-checked', String(b === button));
    placeCutter();
  });
}
$('cutter-do').addEventListener('click', () => {
  const params = { axis: cutterAxis, position_mm: cutterPosition(), pins: $('cutter-pins').checked, numbers: $('cutter-numbers').checked };
  stopCutter();
  runAction('cut_in_two', params);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && cutter) stopCutter(); });

// ---------------------------------------------------------------- measure, holes, walls

function modelHitAt(e) {
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObject(content.children[0], true)[0];
  if (!hit) return null;
  return { point: content.worldToLocal(hit.point.clone()), normal: hit.face.normal.clone().normalize() };
}

function clickedOnModel(e) {
  return pointerStart && content && Math.hypot(e.clientX - pointerStart[0], e.clientY - pointerStart[1]) <= 5;
}

function dot(point, color, parent = content) {
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry((lastSpan || 10) * 0.01, 16, 12),
    new THREE.MeshBasicMaterial({ color, depthTest: false }),
  );
  marker.renderOrder = 20;
  marker.position.copy(point);
  parent.add(marker);
  return marker;
}

// Measure: two clicks, the distance between them, and "should be" to resize.
let measuring = false;
let measurePoints = [];
let measureGroup = null;
let measureDistance = 0;

function startMeasure() {
  if (!docId || !content || busy) return;
  exitModes();
  measuring = true;
  measurePoints = [];
  measureGroup = new THREE.Group();
  content.add(measureGroup);
  document.body.classList.add('picking');
  $('measure-bar').hidden = false;
  $('measure-set').hidden = true;
  $('measure-apply').hidden = true;
  $('measure-text').textContent = 'Click two points on the model';
  $('status').hidden = true;
}

function stopMeasure() {
  measuring = false;
  measurePoints = [];
  if (measureGroup) measureGroup.parent?.remove(measureGroup);
  measureGroup = null;
  document.body.classList.remove('picking');
  $('measure-bar').hidden = true;
  $('measure-label').hidden = true;
}

function placeMeasureLabel() {
  const label = $('measure-label');
  if (!measuring || measurePoints.length < 2) {
    label.hidden = true;
    return;
  }
  const mid = measurePoints[0].clone().add(measurePoints[1]).multiplyScalar(0.5);
  const world = content.localToWorld(mid).project(camera);
  label.style.left = `${(world.x + 1) / 2 * viewerEl.clientWidth}px`;
  label.style.top = `${(1 - world.y) / 2 * viewerEl.clientHeight}px`;
  label.hidden = false;
}

renderer.domElement.addEventListener('pointerup', (e) => {
  if (!measuring || !clickedOnModel(e)) return;
  const hit = modelHitAt(e);
  if (!hit) return;
  if (measurePoints.length === 2) {
    measurePoints = [];
    measureGroup.clear();
  }
  measurePoints.push(hit.point);
  dot(hit.point, 0x0071e3, measureGroup);
  if (measurePoints.length === 2) {
    const [a, b] = measurePoints;
    measureDistance = a.distanceTo(b);
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([a, b]),
      new THREE.LineBasicMaterial({ color: 0x0071e3, depthTest: false }),
    );
    line.renderOrder = 19;
    measureGroup.add(line);
    $('measure-label').textContent = `${fmt(measureDistance, 2)} mm`;
    $('measure-text').textContent = `${fmt(measureDistance, 2)} mm between the points.`;
    $('measure-target').value = Number(measureDistance.toFixed(2));
    $('measure-set').hidden = false;
    $('measure-apply').hidden = false;
  } else {
    $('measure-text').textContent = 'Now click the second point';
  }
});

$('measure-start').addEventListener('click', startMeasure);
$('measure-cancel').addEventListener('click', stopMeasure);
$('measure-apply').addEventListener('click', () => {
  const target = Number($('measure-target').value);
  if (!(target > 0) || !(measureDistance > 0)) return;
  const factor = target / measureDistance;
  stopMeasure();
  runAction('scale', { factor });
});

// Add a hole: click the surface, choose width and depth.
let holing = false;
let holeSpot = null;
let holePreview = null;

function startHole() {
  if (!docId || !content || busy) return;
  exitModes();
  holing = true;
  holeSpot = null;
  document.body.classList.add('picking');
  $('hole-bar').hidden = false;
  $('hole-do').disabled = true;
  $('hole-text').textContent = 'Click where the hole should go';
  $('status').hidden = true;
}

function clearHolePreview() {
  if (holePreview) {
    holePreview.geometry.dispose();
    holePreview.parent?.remove(holePreview);
    holePreview = null;
  }
}

function stopHole() {
  holing = false;
  holeSpot = null;
  clearHolePreview();
  document.body.classList.remove('picking');
  $('hole-bar').hidden = true;
}

function showHolePreview() {
  clearHolePreview();
  if (!holeSpot) return;
  const diameter = Math.max(Number($('hole-diameter').value) || 3.2, 0.5);
  const depthValue = Number($('hole-depth').value) || 0;
  const depth = depthValue > 0 ? depthValue : (lastSpan || 50);
  const geometry = new THREE.CylinderGeometry(diameter / 2, diameter / 2, depth, 32);
  geometry.translate(0, -depth / 2, 0);  // starts at the surface, goes in
  holePreview = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0xff3b30, transparent: true, opacity: 0.45, depthTest: false }));
  holePreview.renderOrder = 18;
  holePreview.position.copy(holeSpot.point);
  holePreview.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), holeSpot.normal);
  content.add(holePreview);
}

renderer.domElement.addEventListener('pointerup', (e) => {
  if (!holing || !clickedOnModel(e)) return;
  const hit = modelHitAt(e);
  if (!hit) return;
  holeSpot = hit;
  $('hole-do').disabled = false;
  $('hole-text').textContent = 'Here? Adjust the size, or click somewhere else';
  showHolePreview();
});

$('hole-start').addEventListener('click', startHole);
$('hole-cancel').addEventListener('click', stopHole);
$('hole-diameter').addEventListener('input', showHolePreview);
$('hole-depth').addEventListener('input', showHolePreview);
$('hole-do').addEventListener('click', () => {
  if (!holeSpot) return;
  const params = {
    point: holeSpot.point.toArray(),
    direction: holeSpot.normal.clone().negate().toArray(),
    diameter_mm: Number($('hole-diameter').value) || 3.2,
    depth_mm: Number($('hole-depth').value) || 0,
  };
  stopHole();
  runAction('add_hole', params);
});

// Check walls: mark spots thinner than the printer can make.
let wallDots = null;
let wallScale = null;

function clearWallDots() {
  if (wallDots) {
    wallDots.geometry.dispose();
    wallDots.parent?.remove(wallDots);
    wallDots = null;
  }
}

async function checkWalls() {
  if (!docId || busy) return;
  exitModes();
  clearWallDots();
  wallScale = null;
  $('walls-scale').hidden = true;
  $('walls-text').textContent = 'Measuring the walls…';
  $('walls-dialog').showModal();
  let result;
  try {
    result = await request(`api/doc/${docId}/wall-thickness?flexible=${cleanupPreset === 'flexible'}`);
  } catch (err) {
    $('walls-text').textContent = err.message;
    return;
  }
  if (result.thin_share === 0) {
    $('walls-text').textContent = `All walls are at least ${fmt(result.thinnest_mm, 2)} mm thick. That is fine for your printer (it needs about ${fmt(result.limit_mm, 1)} mm).`;
    return;
  }
  const points = result.thin_spots.flat();
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  wallDots = new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0xa855f7, size: 6, sizeAttenuation: false, depthTest: false }));
  wallDots.renderOrder = 17;
  content.add(wallDots);
  $('walls-text').textContent =
    `Some walls are thinner than ${fmt(result.limit_mm, 1)} mm (the thinnest is ${fmt(result.thinnest_mm, 2)} mm), about ` +
    `${fmt(result.thin_share * 100, 0)}% of the surface. They are marked in purple and may print weak or not at all.` +
    (result.scale_to_fix && result.scale_to_fix <= 3 ? ` Making the model ${fmt(result.scale_to_fix, 2)}× bigger would fix it.` : ' Make them thicker in your design, or print with a smaller nozzle.');
  if (result.scale_to_fix && result.scale_to_fix <= 3) {
    wallScale = result.scale_to_fix;
    $('walls-scale').textContent = `Scale up ×${fmt(wallScale, 2)}`;
    $('walls-scale').hidden = false;
  }
}

$('walls-check').addEventListener('click', checkWalls);

$('hollow-open').addEventListener('click', () => {
  if (!docId || busy) return;
  exitModes();
  $('hollow-filament').hidden = !(printerChosen && printer.technology === 'filament');
  $('hollow-dialog').showModal();
});
$('hollow-dialog').addEventListener('close', () => {
  if ($('hollow-dialog').returnValue === 'apply') {
    runAction('hollow', { wall_mm: Number($('hollow-wall').value) || 2, drain_holes: $('hollow-drain').checked });
  }
  $('hollow-dialog').returnValue = '';
});
$('bust-open').addEventListener('click', () => {
  if (!docId || busy) return;
  exitModes();
  $('bust-dialog').showModal();
});
for (const button of document.querySelectorAll('#bust-base [data-base]')) {
  button.addEventListener('click', () => {
    for (const b of document.querySelectorAll('#bust-base [data-base]')) b.setAttribute('aria-checked', String(b === button));
  });
}
$('bust-dialog').addEventListener('close', () => {
  if ($('bust-dialog').returnValue === 'apply') {
    const base = document.querySelector('#bust-base [aria-checked="true"]').dataset.base;
    runAction('make_bust', { height_mm: Number($('bust-height').value) || 100, base });
  }
  $('bust-dialog').returnValue = '';
});
$('walls-dialog').addEventListener('close', () => {
  if ($('walls-dialog').returnValue === 'scale' && wallScale) {
    clearWallDots();
    runAction('scale', { factor: wallScale });
  }
  $('walls-dialog').returnValue = '';
});

// ---------------------------------------------------------------- merge another scan

let mergeScan = null;     // { id, name }
let mergeGroup = null;    // the added scan, shown in orange beside the model
let mergeBase = [];       // spots on the model (model coordinates)
let mergeAdded = [];      // matching spots on the added scan (its own coordinates)
let mergeMarkers = null;

const addedMaterial = new THREE.MeshStandardMaterial({ color: 0xf0a04b, roughness: 0.6, flatShading: true, side: THREE.DoubleSide });

function mergeMarker(parent, point, color) {
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry((lastSpan || 10) * 0.012, 16, 12),
    new THREE.MeshBasicMaterial({ color, depthTest: false }),
  );
  marker.renderOrder = 20;
  marker.position.copy(point);
  parent.add(marker);
  return marker;
}

function updateMergeBar() {
  const pairs = Math.min(mergeBase.length, mergeAdded.length);
  const onBase = mergeBase.length === mergeAdded.length;
  const n = pairs + 1;
  $('merge-text').textContent = onBase
    ? (pairs >= 3
      ? `${pairs} pairs matched. Add more for a better fit, or Merge.`
      : `Pair ${n} of 3: click a spot on the blue model`)
    : `Pair ${n}: now click the same spot on the orange scan`;
  $('merge-do').disabled = pairs < 3 || !onBase;
  $('merge-undo').disabled = mergeBase.length === 0;
}

async function startMerge(file) {
  if (!docId || !content || busy) return;
  stopPick();
  setBusy(true);
  showStatus(`Opening ${file.name}…`, 'busy');
  try {
    const body = new FormData();
    body.append('file', file);
    const scan = await uploadWithProgress(`api/doc/${docId}/add-scan`, body, () => {});
    const res = await fetch(`api/scan/${scan.scan_id}/mesh`);
    if (!res.ok) throw new Error('Could not show the added scan.');
    const geometry = new STLLoader().parse(await res.arrayBuffer());
    mergeScan = { id: scan.scan_id, name: scan.name };
    showAddedScan(geometry);
    mergeBase = [];
    mergeAdded = [];
    $('merge-bar').hidden = false;
    $('status').hidden = true;
    showTip('merge', TIPS.merge);
    document.body.classList.add('picking');
    updateMergeBar();
  } catch (err) {
    showStatus(err.message, 'error');
  } finally {
    setBusy(false);
  }
}

// Place the added scan to the right of the model, on the same level, and
// frame both.
function showAddedScan(geometry) {
  clearAddedScan();
  geometry.computeBoundingBox();
  const baseBox = new THREE.Box3().setFromBufferAttribute(currentGeometry.getAttribute('position'));
  const addedBox = geometry.boundingBox;
  const gap = Math.max(baseBox.max.x - baseBox.min.x, 1) * 0.3;
  const inner = new THREE.Mesh(geometry, addedMaterial);
  mergeGroup = new THREE.Group();
  mergeGroup.add(inner);
  mergeGroup.position.set(
    baseBox.max.x + gap - addedBox.min.x,
    (baseBox.min.y + baseBox.max.y) / 2 - (addedBox.min.y + addedBox.max.y) / 2,
    baseBox.min.z - addedBox.min.z,
  );
  content.add(mergeGroup);
  mergeMarkers = new THREE.Group();
  content.add(mergeMarkers);
  frameAll();
}

function clearAddedScan() {
  if (mergeGroup) {
    mergeGroup.children[0].geometry.dispose();
    mergeGroup.parent?.remove(mergeGroup);
  }
  if (mergeMarkers) mergeMarkers.parent?.remove(mergeMarkers);
  mergeGroup = null;
  mergeMarkers = null;
}

function stopMerge() {
  clearAddedScan();
  mergeScan = null;
  $('merge-bar').hidden = true;
  document.body.classList.remove('picking');
}

renderer.domElement.addEventListener('pointerup', (e) => {
  if (!mergeScan || !pointerStart || !content) return;
  if (Math.hypot(e.clientX - pointerStart[0], e.clientY - pointerStart[1]) > 5) return;
  const onBase = mergeBase.length === mergeAdded.length;
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const target = onBase ? content.children[0] : mergeGroup;
  const hit = raycaster.intersectObject(target, true)[0];
  if (!hit) {
    showStatus(onBase ? 'Click on the blue model.' : 'Click on the orange scan.', 'error');
    return;
  }
  const inContent = content.worldToLocal(hit.point.clone());
  if (onBase) {
    mergeBase.push(inContent.toArray());
    mergeMarker(mergeMarkers, inContent, 0x0071e3);
  } else {
    const own = mergeGroup.worldToLocal(hit.point.clone());
    mergeAdded.push(own.toArray());
    mergeMarker(mergeMarkers, inContent, 0xd9730d);
  }
  updateMergeBar();
});

$('merge-input').addEventListener('change', (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (file) startMerge(file);
});
$('merge-cancel').addEventListener('click', stopMerge);
$('merge-undo').addEventListener('click', () => {
  if (mergeAdded.length === mergeBase.length) mergeAdded.pop();
  else mergeBase.pop();
  if (mergeMarkers?.children.length) mergeMarkers.remove(mergeMarkers.children.at(-1));
  updateMergeBar();
});
$('merge-do').addEventListener('click', () => {
  const params = { scan: mergeScan.id, added_points: mergeAdded, base_points: mergeBase };
  stopMerge();
  runAction('merge_scan', params);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && mergeScan) stopMerge(); });

// ---------------------------------------------------------------- sculpt brushes

let sculpting = false;
let sculptBrush = 'smooth';
let stroke = null;        // points of the stroke being drawn, model coordinates
let strokeLine = null;
const brushRing = new THREE.Mesh(
  new THREE.RingGeometry(0.9, 1, 48),
  new THREE.MeshBasicMaterial({ color: 0x0071e3, transparent: true, opacity: 0.85, depthTest: false, side: THREE.DoubleSide }),
);
brushRing.renderOrder = 30;
brushRing.visible = false;

function brushRadius() {
  // The slider goes from a fine 0.5% of the model's size to a broad 15%.
  return (lastSpan || 100) * (0.005 + 0.145 * Number($('sculpt-size').value) / 100);
}

function startSculpt() {
  if (!docId || !content || busy) return;
  exitModes();
  sculpting = true;
  modelRoot.add(brushRing);
  $('sculpt-bar').hidden = false;
  $('status').hidden = true;
}

function stopSculpt() {
  sculpting = false;
  stroke = null;
  brushRing.visible = false;
  brushRing.parent?.remove(brushRing);
  if (strokeLine) { strokeLine.geometry.dispose(); strokeLine.parent?.remove(strokeLine); strokeLine = null; }
  controls.enabled = true;
  $('sculpt-bar').hidden = true;
}

function placeRing(hit) {
  if (!hit) { brushRing.visible = false; return; }
  const r = brushRadius();
  brushRing.visible = true;
  brushRing.scale.setScalar(r);
  brushRing.position.copy(hit.point).addScaledVector(hit.normal, r * 0.02);
  brushRing.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), hit.normal);
}

function drawStroke() {
  if (strokeLine) { strokeLine.geometry.dispose(); strokeLine.parent?.remove(strokeLine); }
  strokeLine = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(stroke),
    new THREE.LineBasicMaterial({ color: 0x0071e3, depthTest: false, transparent: true, opacity: 0.6 }),
  );
  strokeLine.renderOrder = 29;
  modelRoot.add(strokeLine);
}

renderer.domElement.addEventListener('pointerdown', (e) => {
  if (!sculpting || busy || e.button !== 0 || !content) return;
  const hit = modelHitAt(e);
  if (!hit) return;  // off the model: drag turns the view as usual
  controls.enabled = false;
  stroke = [hit.point];
  drawStroke();
});

renderer.domElement.addEventListener('pointermove', (e) => {
  if (!sculpting || !content) return;
  const hit = modelHitAt(e);
  placeRing(hit);
  if (!stroke || !hit) return;
  if (hit.point.distanceTo(stroke.at(-1)) < brushRadius() / 4 || stroke.length >= 2000) return;
  stroke.push(hit.point);
  drawStroke();
});

window.addEventListener('pointerup', () => {
  if (!sculpting || !stroke) return;
  const points = stroke.map((p) => p.toArray());
  stroke = null;
  controls.enabled = true;
  change('action', {
    action: 'sculpt',
    params: { stroke: points, radius_mm: brushRadius(), brush: sculptBrush, strength: $('sculpt-strength').value },
  }, null).then(() => {
    if (strokeLine) { strokeLine.geometry.dispose(); strokeLine.parent?.remove(strokeLine); strokeLine = null; }
  });
});

for (const button of document.querySelectorAll('[data-brush]')) {
  button.addEventListener('click', () => {
    sculptBrush = button.dataset.brush;
    for (const b of document.querySelectorAll('[data-brush]')) b.setAttribute('aria-checked', String(b === button));
  });
}
$('sculpt-start').addEventListener('click', startSculpt);
$('sculpt-done').addEventListener('click', stopSculpt);
document.addEventListener('keydown', (e) => {
  if (!sculpting || e.target.closest?.('input, select, textarea')) return;
  if (e.key === 'Escape') stopSculpt();
  const size = $('sculpt-size');
  if (e.key === '[') size.value = Math.max(0, Number(size.value) - 5);
  if (e.key === ']') size.value = Math.min(100, Number(size.value) + 5);
});

// ---------------------------------------------------------------- combine models

let combineScan = null;   // { id, name }
let combineGroup = null;  // the added model, placed with handles
const handles = new TransformControls(camera, renderer.domElement);
handles.setRotationSnap(THREE.MathUtils.degToRad(15));
handles.addEventListener('dragging-changed', (e) => { controls.enabled = !e.value; });
const combineMaterial = new THREE.MeshStandardMaterial({
  color: 0xf0a04b, roughness: 0.6, flatShading: true, side: THREE.DoubleSide, transparent: true, opacity: 1,
});

async function startCombine(file) {
  if (!docId || !content || busy) return;
  exitModes();
  setBusy(true);
  showStatus(`Opening ${file.name}…`, 'busy');
  try {
    const body = new FormData();
    body.append('file', file);
    const added = await uploadWithProgress(`api/doc/${docId}/add-scan`, body, () => {});
    const res = await fetch(`api/scan/${added.scan_id}/mesh`);
    if (!res.ok) throw new Error('Could not show the added model.');
    const geometry = new STLLoader().parse(await res.arrayBuffer());
    combineScan = { id: added.scan_id, name: added.name };
    showCombined(geometry);
    $('combine-bar').hidden = false;
    $('status').hidden = true;
    showTip('combine', TIPS.combine);
  } catch (err) {
    showStatus(err.message, 'error');
  } finally {
    setBusy(false);
  }
}

// Start with the added model in the middle of the model. Its triangles are
// centred in the group, so the handles sit in its middle and it turns about
// its centre.
let combineCentre = new THREE.Vector3();
function showCombined(geometry) {
  geometry.computeBoundingBox();
  combineCentre = geometry.boundingBox.getCenter(new THREE.Vector3());
  geometry.translate(-combineCentre.x, -combineCentre.y, -combineCentre.z);
  const baseBox = new THREE.Box3().setFromBufferAttribute(currentGeometry.getAttribute('position'));
  combineGroup = new THREE.Group();
  combineGroup.add(new THREE.Mesh(geometry, combineMaterial));
  baseBox.getCenter(combineGroup.position);
  content.add(combineGroup);
  handles.setMode('translate');
  for (const b of document.querySelectorAll('[data-combine-mode]')) b.setAttribute('aria-checked', String(b.dataset.combineMode === 'translate'));
  handles.attach(combineGroup);
  scene.add(handles.getHelper());
  updateCombineLook();
  frameAll();
}

// Frame the model and anything shown beside it.
function frameAll() {
  const both = new THREE.Box3().setFromObject(content);
  const centre = both.getCenter(new THREE.Vector3());
  const span = both.getSize(new THREE.Vector3()).length();
  controls.target.copy(centre);
  camera.position.copy(centre).add(new THREE.Vector3(0.3, 0.6, 1).normalize().multiplyScalar(span * 1.2));
  camera.far = Math.max(camera.far, span * 100);
  camera.updateProjectionMatrix();
  controls.update();
}

function updateCombineLook() {
  // Cutting tools are see-through, so the part they cut is visible.
  combineMaterial.opacity = $('combine-how').value === 'join' ? 1 : 0.55;
  combineMaterial.depthWrite = combineMaterial.opacity === 1;
}

function stopCombine() {
  handles.detach();
  handles.getHelper().parent?.remove(handles.getHelper());
  controls.enabled = true;
  if (combineGroup) {
    combineGroup.children[0].geometry.dispose();
    combineGroup.parent?.remove(combineGroup);
  }
  combineGroup = null;
  combineScan = null;
  $('combine-bar').hidden = true;
}

function setCombineMode(mode) {
  if (!combineGroup) return;
  handles.setMode(mode);
  for (const b of document.querySelectorAll('[data-combine-mode]')) b.setAttribute('aria-checked', String(b.dataset.combineMode === mode));
}

for (const button of document.querySelectorAll('[data-combine-mode]')) {
  button.addEventListener('click', () => setCombineMode(button.dataset.combineMode));
}
$('combine-how').addEventListener('change', updateCombineLook);
$('combine-input').addEventListener('change', (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (file) startCombine(file);
});
$('combine-cancel').addEventListener('click', stopCombine);
$('combine-do').addEventListener('click', () => {
  combineGroup.updateMatrix();
  const placement = combineGroup.matrix.clone()
    .multiply(new THREE.Matrix4().makeTranslation(-combineCentre.x, -combineCentre.y, -combineCentre.z));
  const params = { scan: combineScan.id, placement: placement.toArray(), how: $('combine-how').value };
  stopCombine();
  runAction('combine_models', params);
});
document.addEventListener('keydown', (e) => {
  if (!combineScan || e.target.closest?.('input, select, textarea')) return;
  if (e.key === 'Escape') stopCombine();
  const mode = { g: 'translate', r: 'rotate', s: 'scale' }[e.key.toLowerCase()];
  if (mode && !e.metaKey && !e.ctrlKey) setCombineMode(mode);
});

// ---------------------------------------------------------------- cleanup sheet

// Plan steps the user can switch off, and the cleanup option each one maps to.
const PLAN_OPTIONS = {
  remove_debris: 'remove_loose_pieces',
  fill_holes: 'fill',
  fix_crossing: 'fix_crossing',
  reduce: 'reduce',
  close_open_edge: 'close_open_edge',
};

// Repair preset: remembered in this browser for next time.
let cleanupPreset = 'balanced';
try { cleanupPreset = localStorage.getItem('meshright.preset') || 'balanced'; } catch { /* default */ }

function showPreset() {
  for (const b of document.querySelectorAll('#cleanup-preset [data-preset]')) {
    b.setAttribute('aria-checked', String(b.dataset.preset === cleanupPreset));
  }
}

let planRequest = 0;
async function loadPlan() {
  const ask = ++planRequest;
  $('plan-steps').replaceChildren();
  $('plan-intro').textContent = 'Checking what needs doing…';
  $('cleanup-apply').disabled = true;
  try {
    const plan = await request(`api/doc/${docId}/cleanup-plan?preset=${cleanupPreset}`);
    if (ask === planRequest) renderPlan(plan);
  } catch (err) {
    if (ask === planRequest) $('plan-intro').textContent = err.message;
  }
}

async function openCleanup() {
  if (!docId || busy) return;
  showPreset();
  $('cleanup-dialog').showModal();
  loadPlan();
}

for (const button of document.querySelectorAll('#cleanup-preset [data-preset]')) {
  button.addEventListener('click', () => {
    cleanupPreset = button.dataset.preset;
    try { localStorage.setItem('meshright.preset', cleanupPreset); } catch { /* not saved */ }
    showPreset();
    loadPlan();
  });
}

function renderPlan(plan) {
  const list = $('plan-steps');
  list.replaceChildren();
  const doable = plan.steps.filter((s) => s.checked !== false);
  $('plan-intro').textContent = doable.length
    ? 'This is what will change. Untick anything you want to keep as it is.'
    : 'Nothing to clean up. The surface already looks tidy.';
  for (const step of plan.steps) {
    const li = document.createElement('li');
    const option = PLAN_OPTIONS[step.key];
    if (option) {
      const label = document.createElement('label');
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = step.checked !== false;
      box.dataset.option = option;
      label.append(box, document.createTextNode(step.text));
      li.append(label);
    } else {
      const mark = document.createElement('span');
      mark.className = 'done-mark';
      mark.textContent = '✓';
      li.append(mark, document.createTextNode(step.text));
    }
    list.append(li);
  }
  $('cleanup-apply').disabled = plan.steps.length === 0;
}

$('cleanup-dialog').addEventListener('close', () => {
  const choice = $('cleanup-dialog').returnValue;
  $('cleanup-dialog').returnValue = '';
  if (choice === 'apply') {
    const params = { preset: cleanupPreset };
    for (const box of document.querySelectorAll('#plan-steps input[data-option]')) {
      params[box.dataset.option] = box.checked;
    }
    runAction('cleanup', params);
  } else if (choice === 'solid') {
    runAction('make_solid', {});
  }
});

const undo = () => change('undo', null, () => 'Undone');
const redo = () => change('redo', null, () => 'Redone');

let currentLoad = 0;

async function openFile(file) {
  if (!file) return;
  const loadId = ++currentLoad;
  exitModes();
  showPanel('panel-busy');
  const busyText = $('busy-text');
  const mb = file.size / 1024 / 1024;
  // Very big scans need a lot of memory: say so, and point at Cancel.
  const slowNote = mb > 500
    ? ` This is a very big file (${fmt(mb, 0)} MB). It can take a few minutes and needs a lot of memory; if your computer slows down, press Cancel.`
    : mb > 200 ? ` This is a big file (${fmt(mb, 0)} MB), so it can take a minute or two.` : '';

  try {
    const body = new FormData();
    body.append('file', file);
    const state = await uploadWithProgress('api/open', body, (share) => {
      busyText.textContent = share < 1 ? `Opening… ${Math.round(share * 100)}%${slowNote}` : `Checking your model…${slowNote}`;
    });
    if (loadId !== currentLoad) return;  // a newer file was opened meanwhile
    docId = state.doc_id;
    addTab(state.doc_id, state.name);
    $('drop-hint').hidden = true;
    $('view-controls').hidden = false;
    await showState(state, { keepView: false });
    if (state.notice) showStatus(state.notice);
  } catch (err) {
    if (err.cancelled) return;
    if (loadId === currentLoad) showError(err.message);
  }
}

// Stop opening a file: back to the model that was open, or the start screen.
$('open-cancel').addEventListener('click', () => {
  currentLoad++;  // any late answer for the cancelled file is ignored
  currentUpload?.abort();
  if (docId) showPanel('panel-report');
  else {
    showPanel('panel-empty');
    $('drop-hint').hidden = false;
  }
  showStatus('Stopped opening the file.');
});

// ---------------------------------------------------------------- several models open

const MAX_TABS = 4;  // the same as the server keeps in memory
let openDocs = [];   // { id, name }, oldest first

function renderTabs() {
  const nav = $('doc-tabs');
  nav.replaceChildren();
  for (const doc of openDocs) {
    const tab = document.createElement('div');
    tab.className = 'doc-tab' + (doc.id === docId ? ' active' : '');
    tab.title = doc.name;
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = doc.name;
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.setAttribute('aria-label', `Close ${doc.name}`);
    close.addEventListener('click', (e) => { e.stopPropagation(); closeTab(doc.id); });
    tab.append(name, close);
    tab.addEventListener('click', () => switchTo(doc.id));
    nav.append(tab);
  }
  // Like Safari: the tabs only show once there is more than one.
  nav.hidden = openDocs.length < 2;
}

function addTab(id, name) {
  openDocs = openDocs.filter((d) => d.id !== id);
  openDocs.push({ id, name });
  while (openDocs.length > MAX_TABS) {
    const oldest = openDocs.shift();
    fetch(`api/doc/${oldest.id}`, { method: 'DELETE' }).catch(() => {});
    showStatus(`Closed ${oldest.name} to make room: up to ${MAX_TABS} models can be open at once.`);
  }
  renderTabs();
}

async function switchTo(id) {
  if (id === docId || busy) return;
  exitModes();
  const loadId = ++currentLoad;
  try {
    const state = await request(`api/doc/${id}/state`);
    if (loadId !== currentLoad) return;
    docId = id;
    renderTabs();
    await showState(state, { keepView: false });
  } catch (err) {
    openDocs = openDocs.filter((d) => d.id !== id);
    renderTabs();
    showStatus(err.message, 'error');
  }
}

async function closeTab(id) {
  if (busy) return;
  fetch(`api/doc/${id}`, { method: 'DELETE' }).catch(() => {});
  const index = openDocs.findIndex((d) => d.id === id);
  openDocs = openDocs.filter((d) => d.id !== id);
  if (id !== docId) { renderTabs(); return; }
  const next = openDocs[Math.min(index, openDocs.length - 1)];
  if (next) {
    docId = null;
    await switchTo(next.id);
    return;
  }
  // Nothing left open: back to the start screen.
  exitModes();
  docId = null;
  clearModel();
  applyCoin();  // no model: the coin goes too
  $('drop-hint').hidden = false;
  $('view-controls').hidden = true;
  showPanel('panel-empty');
  $('undo').disabled = true;
  $('redo').disabled = true;
  renderTabs();
}

function saveProject() {
  $('export-menu').open = false;
  if (!docId) {
    showStatus('Open a model first.', 'error');
    return;
  }
  const link = document.createElement('a');
  link.href = `api/doc/${docId}/project`;
  link.download = '';
  document.body.append(link);
  link.click();
  link.remove();
  showStatus('Project saved. Open the .meshright file later to carry on.');
}

function exportAs(format) {
  $('export-menu').open = false;
  if (!docId) {
    showStatus('Open a model first.', 'error');
    return;
  }
  const link = document.createElement('a');
  link.href = `api/doc/${docId}/export?format=${format}`;
  link.download = '';
  document.body.append(link);
  link.click();
  link.remove();
  showStatus(`Saved as ${format.toUpperCase()}. Check your Downloads folder.`);
}

// ---------------------------------------------------------------- controls

function openFiles(files) {
  const list = [...files];
  if (list.length > 1) openBatch(list);
  else if (list.length === 1) openFile(list[0]);
}

$('file-input').addEventListener('change', (e) => {
  openFiles(e.target.files);
  e.target.value = '';
});

for (const type of ['dragenter', 'dragover']) {
  viewerEl.addEventListener(type, (e) => { e.preventDefault(); viewerEl.classList.add('dragging'); });
}
for (const type of ['dragleave', 'drop']) {
  viewerEl.addEventListener(type, (e) => { e.preventDefault(); viewerEl.classList.remove('dragging'); });
}
viewerEl.addEventListener('drop', (e) => openFiles(e.dataTransfer.files));

// ---------------------------------------------------------------- batch

let batchFiles = [];
let batchId = null;

function openBatch(files) {
  batchFiles = files;
  batchId = null;
  $('batch-title').textContent = `Fix ${files.length} files`;
  $('batch-intro').textContent = 'Each file gets the same fixes. You get the fixed files back in one zip, with a short report.';
  $('batch-options').hidden = false;
  $('batch-results').hidden = true;
  $('batch-run').hidden = false;
  $('batch-run').disabled = false;
  $('batch-download').hidden = true;
  $('batch-cancel').textContent = 'Cancel';
  $('batch-fit').disabled = !printerChosen;
  $('batch-dialog').showModal();
}

$('batch-run').addEventListener('click', async () => {
  const options = { format: $('batch-format').value, preset: $('batch-preset').value };
  for (const box of document.querySelectorAll('#batch-options [data-option]')) options[box.dataset.option] = box.checked && !box.disabled;
  const body = new FormData();
  for (const file of batchFiles) body.append('files', file);
  body.append('options', JSON.stringify(options));
  $('batch-run').disabled = true;
  $('batch-intro').textContent = `Fixing ${batchFiles.length} files… Big scans can take a minute.`;
  try {
    const data = await request('api/batch', { method: 'POST', body });
    batchId = data.batch_id;
    showBatchResults(data.results);
  } catch (err) {
    $('batch-intro').textContent = err.message;
    $('batch-run').disabled = false;
  }
});

function showBatchResults(results) {
  const ready = results.filter((r) => r.ready).length;
  $('batch-intro').textContent = `${ready} of ${results.length} files are ready to print.`;
  $('batch-options').hidden = true;
  const list = $('batch-results');
  list.replaceChildren();
  for (const r of results) {
    const li = document.createElement('li');
    const clean = r.ok && r.ready && r.problems_left.length === 0;
    li.className = !r.ok ? 'failed' : clean ? '' : 'attention';
    const mark = document.createElement('span');
    mark.className = 'mark';
    mark.textContent = clean ? '✓' : '!';
    const text = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = r.name;
    const detail = document.createElement('small');
    detail.textContent = !r.ok
      ? r.error
      : `Score ${r.score_before} → ${r.score_after}` + (r.problems_left.length ? `. Still: ${r.problems_left.join(', ')}` : '');
    text.append(name, detail);
    li.append(mark, text);
    list.append(li);
  }
  list.hidden = false;
  $('batch-run').hidden = true;
  $('batch-download').hidden = !results.some((r) => r.ok);
  $('batch-cancel').textContent = 'Done';
}

$('batch-download').addEventListener('click', () => {
  if (!batchId) return;
  const link = document.createElement('a');
  link.href = `api/batch/${batchId}`;
  link.download = 'meshright-fixed.zip';
  document.body.append(link);
  link.click();
  link.remove();
});

$('undo').addEventListener('click', undo);
$('redo').addEventListener('click', redo);
$('revert').addEventListener('click', () => change('revert', null, () => 'Back to the original file'));

$('save-project').addEventListener('click', saveProject);
for (const button of document.querySelectorAll('#export-menu [data-format]')) {
  button.addEventListener('click', () => exportAs(button.dataset.format));
}
document.addEventListener('click', (e) => {
  if (!$('export-menu').contains(e.target)) $('export-menu').open = false;
});

for (const button of document.querySelectorAll('#panel-report [data-action]')) {
  button.addEventListener('click', () => {
    const { action, axis } = button.dataset;
    runAction(action, axis ? { axis } : {});
  });
}

// Spin / Tip / Roll picks the axis; the two arrows turn 90° either way.
let turnAxis = 'z';
const axisButtons = document.querySelectorAll('.segmented [data-axis]');
for (const button of axisButtons) {
  button.addEventListener('click', () => {
    turnAxis = button.dataset.axis;
    for (const b of axisButtons) b.setAttribute('aria-checked', String(b === button));
  });
}
for (const button of document.querySelectorAll('[data-turn]')) {
  button.addEventListener('click', () => runAction('rotate', { axis: turnAxis, degrees: Number(button.dataset.turn) }));
}

$('size-form').addEventListener('submit', (e) => e.preventDefault());
for (const input of document.querySelectorAll('#size-form input')) {
  input.addEventListener('change', () => {
    const value = Number(input.value);
    if (!(value > 0)) {
      showStatus('Please type a size above 0 mm.', 'error');
      return;
    }
    runAction('resize', { axis: input.dataset.axis, size_mm: value });
  });
}

// ---------------------------------------------------------------- help: samples and first-time tips

async function loadSamples() {
  try {
    const list = await (await fetch('api/samples')).json();
    const holder = $('samples').querySelector('.sample-list');
    for (const item of list) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'soft';
      button.textContent = item.name.replace(/_/g, ' ').replace(/\.stl$/, '').replace(/^./, (c) => c.toUpperCase());
      button.title = item.about;
      button.addEventListener('click', async () => {
        if (busy) return;
        const res = await fetch(`api/samples/${item.id}`);
        if (!res.ok) { showStatus('Could not make that sample.', 'error'); return; }
        openFile(new File([await res.blob()], item.name, { type: 'model/stl' }));
        showTip(`sample-${item.id}`, item.about);
      });
      holder.append(button);
    }
    $('samples').hidden = list.length === 0;
  } catch {
    // Samples are optional; the app works without them.
  }
}
loadSamples();

// A short tip the first time each tool is used. Remembered in this browser
// only; if storage is blocked, tips simply show again.
const TIPS = {
  'erase-start': 'Draw a loop around what to erase. "Facing me only" leaves the far side alone. Undo brings anything back.',
  'part-start': 'Draw a loop around the area the part should fit, for example where a grip or pad will sit. Turn the view first so that area faces you.',
  'piece-start': 'Click any loose piece to erase it, for example a stand or crumbs a scanner picked up.',
  'cut-start': 'Slide to place the cut. Pins keep the parts lined up when you glue them.',
  'hole-start': 'Click where the hole should start. Depth 0 goes all the way through.',
  'measure-start': 'Click two points. If you know the real distance, type it and MeshRight resizes the model to match.',
  'sculpt-start': 'Drag across the model to brush it. Drag off the model to turn the view. [ and ] change the brush size; each stroke can be undone.',
  'combine': 'Drag the arrows to move, or press R to turn and S to resize. "Cut it away" removes its shape from your model.',
  'merge': 'Click a spot on the blue model, then the same spot on the orange scan. Three pairs is enough; more gives a better fit.',
};

function seenTips() {
  try { return JSON.parse(localStorage.getItem('meshright.tips') || '[]'); } catch { return []; }
}

function showTip(key, text) {
  const seen = seenTips();
  if (seen.includes(key)) return;
  $('tip-text').textContent = text;
  $('tip').hidden = false;
  try { localStorage.setItem('meshright.tips', JSON.stringify([...seen, key])); } catch { /* not saved */ }
}

$('tip-close').addEventListener('click', () => { $('tip').hidden = true; });
for (const [id, text] of Object.entries(TIPS)) {
  const button = $(id);
  if (button) button.addEventListener('click', () => { if (docId) showTip(id, text); });
}

// ---------------------------------------------------------------- updates

const DAY_MS = 24 * 60 * 60 * 1000;

function readStore(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
function writeStore(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* not saved */ }
}

function showUpdate(result) {
  const link = $('update-link');
  if (result?.newer) {
    link.href = result.url;
    link.textContent = `MeshRight ${result.latest} is out`;
    link.hidden = false;
    $('update-text').textContent = `· version ${result.latest} is available`;
  } else {
    link.hidden = true;
  }
}

async function checkUpdates({ quiet }) {
  if (!quiet) $('update-text').textContent = '· checking…';
  try {
    const result = await request('api/updates');
    if (result.error) throw new Error(result.error);
    writeStore('meshright.update', { at: Date.now(), result });
    showUpdate(result);
    if (!quiet && !result.newer) $('update-text').textContent = '· you have the latest version';
  } catch (err) {
    if (!quiet) $('update-text').textContent = `· ${err.message}`;
  }
}

(async () => {
  let version = '';
  try {
    version = (await (await fetch('api/health')).json()).version;
    $('app-version').textContent = version;
  } catch { /* shown blank */ }
  const auto = readStore('meshright.updateAuto', true);
  $('update-auto').checked = auto;
  const last = readStore('meshright.update', null);
  if (!auto) return;
  // A saved answer counts for a day, unless MeshRight was updated since.
  if (last && Date.now() - last.at < DAY_MS && last.result?.current === version) showUpdate(last.result);
  else checkUpdates({ quiet: true });
})();

$('update-auto').addEventListener('change', (e) => {
  writeStore('meshright.updateAuto', e.target.checked);
  if (!e.target.checked) $('update-link').hidden = true;
});
$('update-check').addEventListener('click', () => checkUpdates({ quiet: false }));

// ---------------------------------------------------------------- keyboard shortcuts

const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
if (isMac) for (const k of document.querySelectorAll('kbd[data-mod]')) k.textContent = '⌘';
$('undo').title = `Undo (${isMac ? '⌘' : 'Ctrl+'}Z)`;
$('redo').title = `Redo (${isMac ? '⌘' : 'Ctrl+'}Y)`;

function openHelp() {
  if (!$('help-dialog').open) $('help-dialog').showModal();
}
$('help-open').addEventListener('click', openHelp);

function flip(id) {
  const box = $(id);
  if (box.closest('[hidden]')) return;
  box.checked = !box.checked;
  box.dispatchEvent(new Event('change', { bubbles: true }));
}

// A tool bar or a sheet is showing: letter keys belong to it.
function toolActive() {
  return document.querySelector('.pill-bar.top:not([hidden])') || document.querySelector('dialog[open]');
}

document.addEventListener('keydown', (e) => {
  if (e.target.closest?.('input, textarea, select')) return;
  const key = e.key.toLowerCase();
  if (e.ctrlKey || e.metaKey) {
    if (e.altKey) return;
    if (key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (key === 'y' || (key === 'z' && e.shiftKey)) { e.preventDefault(); redo(); }
    else if (key === 'o' && !busy) { e.preventDefault(); $('file-input').click(); }
    else if (key === 'e' && docId) { e.preventDefault(); $('export-menu').open = true; $('export-menu').querySelector('[data-format]').focus(); }
    else if (key === 's' && docId) { e.preventDefault(); $('save-project').click(); }
    return;
  }
  if (e.altKey) return;
  if (e.key === '?') { e.preventDefault(); openHelp(); return; }
  if (e.key === 'Escape') { if (docId) exitModes(); return; }
  if (!docId || toolActive()) return;
  if (key === 'f') $('reset-view').click();
  else if (key === 'w') flip('toggle-wireframe');
  else if (key === 'p') flip('toggle-problems');
  else if (key === 'o') flip('toggle-overhang');
  else if (key === 'q') flip('toggle-rough');
  else if (key === 'c') flip('toggle-cut');
  else if (key === 'm' && !busy) $('measure-start').click();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && (measuring || holing)) exitModes();
});
