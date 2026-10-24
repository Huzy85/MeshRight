// MeshRight 0.3.0 layout: the tool rail, the guided card, the view cube, the
// corner pills, Simple / Advanced modes, the help guide and "Report a problem".
// Everything here drives the same functions as the panel in app.js.

import * as app from './app.js';

const { $, THREE } = app;

// ---------------------------------------------------------------- Simple / Advanced

const MODE_KEY = 'meshright.mode';

function setMode(mode, { remember = true } = {}) {
  if (mode !== 'simple' && mode !== 'advanced') mode = 'simple';
  document.body.dataset.mode = mode;
  for (const b of document.querySelectorAll('#mode-switch [data-mode]')) {
    b.setAttribute('aria-checked', String(b.dataset.mode === mode));
  }
  if (mode === 'advanced') { closeDrawer(); $('panel').scrollTop = 0; }
  if (remember) app.writeStore(MODE_KEY, mode);
  renderCard(app.lastState);
}

$('mode-switch').addEventListener('click', (e) => {
  const b = e.target.closest('[data-mode]');
  if (b) setMode(b.dataset.mode);
});

// ---------------------------------------------------------------- the panel: drawer or dock

const PANEL_W_KEY = 'meshright.panelW';

function isSimple() { return document.body.dataset.mode !== 'advanced'; }

function openDrawer(sectionId, { openDetails = false } = {}) {
  if (isSimple()) document.body.classList.add('drawer-open');
  else document.body.classList.remove('panel-hidden');
  if (sectionId) {
    const el = $(sectionId);
    if (el) {
      if (openDetails && el.tagName === 'DETAILS') el.open = true;
      requestAnimationFrame(() => {
        // Scroll the panel itself, never the page: scrollIntoView would also shove the whole document up.
        const panel = $('panel');
        const top = el.getBoundingClientRect().top - panel.getBoundingClientRect().top + panel.scrollTop - 12;
        panel.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
      });
    }
  }
  updateRail();
}

function closeDrawer() {
  document.body.classList.remove('drawer-open');
  updateRail();
}

function togglePanel() {
  if (isSimple()) {
    if (document.body.classList.contains('drawer-open')) closeDrawer();
    else openDrawer();
  } else {
    document.body.classList.toggle('panel-hidden');
  }
}

$('panel-close').addEventListener('click', closeDrawer);

// Clicking the model while the drawer is open puts it away.
$('viewer').addEventListener('pointerdown', () => {
  if (isSimple() && document.body.classList.contains('drawer-open')) closeDrawer();
}, { capture: true });

// Dock width (Advanced): drag the left edge.
(() => {
  const saved = app.readStore(PANEL_W_KEY, null);
  if (saved) document.documentElement.style.setProperty('--panel-w', `${saved}px`);
  const handle = $('panel-resize');
  let dragging = false;
  handle.addEventListener('pointerdown', (e) => {
    dragging = true;
    document.body.classList.add('resizing');
    try { handle.setPointerCapture(e.pointerId); } catch (_) { /* fine: the window listener still follows the mouse */ }
    e.preventDefault();
  });
  window.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const w = Math.round(Math.min(Math.max(window.innerWidth - e.clientX, 280), Math.min(720, window.innerWidth * 0.6)));
    document.documentElement.style.setProperty('--panel-w', `${w}px`);
    app.writeStore(PANEL_W_KEY, w);
    window.dispatchEvent(new Event('resize'));
  });
  const stop = () => { if (dragging) { dragging = false; document.body.classList.remove('resizing'); window.dispatchEvent(new Event('resize')); } };
  window.addEventListener('pointerup', stop);
  window.addEventListener('pointercancel', stop);
  window.addEventListener('blur', stop);
  handle.addEventListener('dblclick', () => {
    document.documentElement.style.setProperty('--panel-w', '360px');
    app.writeStore(PANEL_W_KEY, 360);
    window.dispatchEvent(new Event('resize'));
  });
})();

// ---------------------------------------------------------------- the tool rail

// Each rail button presses an existing control, so tips, listeners and
// keyboard handling in app.js stay as they are.
const RAIL = {
  fix: () => app.openCleanup(),
  stand: () => app.openUp(),
  size: () => openDrawer('sec-size'),
  measure: () => $('measure-start').click(),
  printer: () => app.openPrinter(),
  erase: () => $('erase-start').click(),
  piece: () => $('piece-start').click(),
  cut: () => $('cut-start').click(),
  hole: () => $('hole-start').click(),
  sculpt: () => $('sculpt-start').click(),
  part: () => $('part-start').click(),
  pick: () => $('pick-start').click(),
  more: () => (document.body.classList.contains('drawer-open') ? closeDrawer() : openDrawer()),
};

$('rail').addEventListener('click', (e) => {
  const b = e.target.closest('[data-do]');
  if (!b || !app.docId) return;
  if (app.busy && b.dataset.do !== 'more' && b.dataset.do !== 'size') return;
  RAIL[b.dataset.do]?.();
  b.blur();  // the tool bar takes over; no focus ring left behind on the rail
});

// Which rail button each tool bar belongs to, so the rail shows which tool is on.
const BAR_TOOL = { 'measure-bar': 'measure', 'piece-bar': 'piece', 'cutter-bar': 'cut', 'hole-bar': 'hole', 'sculpt-bar': 'sculpt', 'pick-bar': 'pick' };
function updateRail() {
  const more = document.querySelector('#rail [data-do="more"]');
  more.classList.toggle('active', document.body.classList.contains('drawer-open'));
  const open = document.querySelector('.pill-bar.top:not([hidden])');
  let tool = open ? BAR_TOOL[open.id] : null;
  if (open && open.id === 'erase-bar') tool = /the part/.test($('erase-text').textContent) ? 'part' : 'erase';
  for (const b of document.querySelectorAll('#rail [data-do]')) {
    if (b.dataset.do !== 'more') b.classList.toggle('active', b.dataset.do === tool);
  }
}
(() => {
  const obs = new MutationObserver(updateRail);
  for (const bar of document.querySelectorAll('.pill-bar.top')) obs.observe(bar, { attributes: true, attributeFilter: ['hidden'] });
  obs.observe($('erase-text'), { childList: true, characterData: true, subtree: true });
})();

// ---------------------------------------------------------------- the guided card

const card = $('card');
let stepper = null;  // { skipped: Set<code> } while "One at a time" is open
const dismissed = new Set();  // "docId:code" the user said to leave alone
let lastSeen = null;   // { doc, pos, sig } from the previous render, to notice a Clean up that changed nothing
let stuck = false;     // the last step was a Clean up and the same problems are still there

$('card-collapse').addEventListener('click', () => {
  card.classList.toggle('collapsed');
  app.writeStore('meshright.cardCollapsed', card.classList.contains('collapsed'));
});
if (app.readStore('meshright.cardCollapsed', false)) card.classList.add('collapsed');

function button(label, run, { primary = false, title = '' } = {}) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  if (primary) b.className = 'primary';
  if (title) b.title = title;
  b.addEventListener('click', run);
  return b;
}

function plural(n, word) { return `${n} ${word}${n === 1 ? '' : 's'}`; }

function renderCard(state) {
  if (!state || !isSimple()) { card.hidden = true; return; }
  card.hidden = false;

  const ring = $('card-ring');
  ring.style.setProperty('--pct', state.score);
  ring.style.setProperty('--ring',
    state.score >= 90 && state.printable ? 'var(--good-ring)' : state.printable ? 'var(--warn-ring)' : 'var(--bad-ring)');
  $('card-score').textContent = state.score;
  $('card-verdict').textContent = state.verdict;

  const issues = new Map(state.issues.map((i) => [i.code, i]));
  const toFix = state.issues.filter((i) => app.FIX_CODES.has(i.code));
  const stand = app.STAND_CODES.map((c) => issues.get(c)).find(Boolean);
  const tooBig = issues.get('too_big');
  const size = state.stats?.size_mm?.map((v) => app.fmt(v, v >= 100 ? 0 : 1)).join(' × ');

  const sig = toFix.map((i) => `${i.code}:${i.title}`).join('|');
  const pos = state.history?.position || 0;
  const lastStep = state.history?.steps?.[pos - 1];
  if (lastSeen && lastSeen.doc === state.doc_id && lastSeen.sig === sig && toFix.length) {
    if (pos === lastSeen.pos + 1 && /^Clean ?up/i.test(lastStep?.receipt || '')) stuck = true;
  } else {
    stuck = false;
  }
  lastSeen = { doc: state.doc_id, pos, sig };

  const body = $('card-body');
  body.replaceChildren();
  const subBits = [];
  if (toFix.length) subBits.push(plural(toFix.length, 'thing') + ' to fix');
  if (stand) subBits.push('needs standing up');
  if (tooBig) subBits.push('too big for the printer');
  const keptUnits = (issues.get('tiny_model') || issues.get('huge_model'));
  if (keptUnits && dismissed.has(`${state.doc_id}:${keptUnits.code}`)) subBits.push(keptUnits.code === 'tiny_model' ? 'too small to print, size kept' : 'too big to print, size kept');
  if (!subBits.length) subBits.push(app.printerChosen ? `Fits the ${app.printer.name}` : 'Choose your printer to check it fits');
  if (size) subBits.push(`${size} mm`);
  $('card-sub').textContent = subBits.join(' · ');

  if (stepper && toFix.length) {
    renderStepper(body, toFix);
    return;
  }
  stepper = null;

  // Wrong units come before any repair: Clean up works in millimetres, so at 2 mm every tolerance is wrong.
  const units = (issues.get('tiny_model') || issues.get('huge_model'));
  if (units && units.fixes?.length && !dismissed.has(`${state.doc_id}:${units.code}`)) {
    const p = document.createElement('p');
    p.className = 'fine';
    p.textContent = `${units.title}. The file does not say which units it was made in. Pick the right one, or keep this size.`;
    body.append(p);
    units.fixes.forEach((fix, i) => body.append(button(fix.label, () => app.runAction(fix.action, fix.params), { primary: i === 0 })));
    const sp = document.createElement('span'); sp.className = 'spacer'; body.append(sp);
    body.append(button('Size is right', () => { dismissed.add(`${state.doc_id}:${units.code}`); renderCard(app.lastState); }, { title: 'Keep this size and carry on' }));
    return;
  }

  if (toFix.length) {
    const blocked = toFix.some((i) => i.severity === 'error');
    if (stuck) {
      const p = document.createElement('p');
      p.className = 'fine';
      p.textContent = 'Clean up ran, but the same problems are still there. This file may be a loose pile of triangles rather than one surface. Make Solid rebuilds the surface from scratch, which usually works.';
      body.append(p);
      body.append(button('Make Solid', () => app.runAction('make_solid', {}), {
        primary: true,
        title: 'Rebuilds the whole surface as one closed shell. Fine detail may soften a little.',
      }));
    }
    body.append(button('Make it printable', () => app.openCleanup(), {
      primary: !stuck,
      title: 'Shows the plan first: which holes get filled, which bits get removed. Nothing changes until you press Clean up.',
    }));
    body.append(button('One at a time', () => { stepper = { skipped: new Set() }; renderCard(app.lastState); }, {
      title: 'Go through each problem in turn, with its own fix and a Skip button',
    }));
    const sp = document.createElement('span'); sp.className = 'spacer'; body.append(sp);
    body.append(button(blocked ? 'Export anyway' : 'Export as it is', () => app.exportAs('3mf'), {
      title: blocked ? 'Save it with the problems still in. Your slicer may refuse it or print it wrong.' : 'Save it now. The remaining problems are minor.',
    }));
    return;
  }

  if (stand) {
    const fixes = stand.fixes || [];
    if (fixes.length) {
      fixes.forEach((fix, i) => body.append(button(fix.label, () => app.runAction(fix.action, fix.params), { primary: i === 0 })));
    } else {
      body.append(button('Which way is up?', () => app.openUp(), { primary: true, title: 'Shows the model from several likely bottoms. Pick the one that looks upright.' }));
    }
    body.append(button('Pick the bottom', () => $('pick-start').click(), { title: 'Click three or more spots that should touch the bed' }));
    const sp = document.createElement('span'); sp.className = 'spacer'; body.append(sp);
    body.append(button('Leave it', () => app.exportAs('3mf'), { title: 'Export as it is. You can position it in your slicer instead.' }));
    return;
  }

  if (!app.printerChosen) {
    body.append(button('Choose printer', () => app.openPrinter(), { primary: true, title: 'So MeshRight can check it fits on the bed' }));
    const sp = document.createElement('span'); sp.className = 'spacer'; body.append(sp);
    body.append(button('Export 3MF', () => app.exportAs('3mf'), { title: 'Skip the printer check and save it now' }));
    return;
  }

  if (tooBig) {
    tooBig.fixes.forEach((fix, i) => body.append(button(fix.label, () => app.runAction(fix.action, fix.params), { primary: i === 0 })));
    const sp = document.createElement('span'); sp.className = 'spacer'; body.append(sp);
    body.append(button('Export anyway', () => app.exportAs('3mf'), { title: 'Save it at this size. Your slicer will say it does not fit.' }));
    return;
  }

  // Ready.
  const slicer = app.preferredSlicer();
  if (slicer) {
    body.append(button(`Open in ${slicer.name}`, () => app.sendToSlicer(slicer), { primary: true, title: 'Saves a 3MF and opens it in your slicer' }));
    body.append(button('Save 3MF', () => app.exportAs('3mf'), { title: 'Save the file to your Downloads folder instead' }));
  } else {
    body.append(button('Save 3MF', () => app.exportAs('3mf'), { primary: true, title: 'For Bambu Studio, PrusaSlicer and OrcaSlicer. Lands in your Downloads folder.' }));
    body.append(button('Save STL', () => app.exportAs('stl'), { title: 'Works in every slicer' }));
  }
  const sp = document.createElement('span'); sp.className = 'spacer'; body.append(sp);
  body.append(button('More formats', () => { $('export-menu').open = true; }, { title: 'OBJ, PLY, colours, or save your work as a MeshRight project' }));
}

function renderStepper(body, toFix) {
  const todo = toFix.filter((i) => !stepper.skipped.has(i.code));
  if (!todo.length) {
    const p = document.createElement('p');
    p.className = 'fine';
    p.textContent = 'You skipped the rest. Clean up can still fix them all at once.';
    body.append(p);
    body.append(button('Make it printable', () => app.openCleanup(), { primary: true }));
    body.append(button('Back', () => { stepper = null; renderCard(app.lastState); }));
    return;
  }
  const issue = todo[0];
  const n = toFix.length - todo.length + 1;
  const row = document.createElement('div');
  row.className = 'step';
  const text = document.createElement('div');
  text.className = 'text';
  const strong = document.createElement('strong');
  strong.textContent = `${n} of ${toFix.length}: ${issue.title}`;
  const small = document.createElement('small');
  small.textContent = issue.detail || issue.text || issue.description || '';
  text.append(strong, small);
  row.append(text);
  const fixes = issue.fixes || [];
  if (fixes.length) {
    fixes.slice(0, 2).forEach((fix, i) => row.append(button(fix.label, () => app.runAction(fix.action, fix.params), { primary: i === 0 })));
  } else {
    row.append(button('Fix with Clean up', () => app.openCleanup(), { primary: true, title: 'This one is fixed as part of Clean up' }));
  }
  row.append(button('Skip', () => { stepper.skipped.add(issue.code); renderCard(app.lastState); }));
  body.append(row);
  body.append(button('All at once instead', () => { stepper = null; renderCard(app.lastState); }, { title: 'Back to the single Make it printable button' }));
}

// ---------------------------------------------------------------- corner pills

function renderCorners(state) {
  if (!state) return;
  const s = state.stats || {};
  $('corner-size').textContent = s.size_mm ? s.size_mm.map((v) => app.fmt(v, v >= 100 ? 0 : 1)).join(' × ') + ' mm' : '';
  $('corner-tris').textContent = s.triangles ? app.fmt(s.triangles, 0) + ' triangles' : '';
  const steps = state.history?.position || 0;
  $('history-count').textContent = steps ? `History · ${steps}` : 'History';
}

$('history-open').addEventListener('click', () => openDrawer('history-disclosure', { openDetails: true }));

app.stateListeners.push((state) => {
  renderCard(state);
  renderCorners(state);
  if (!state) { stepper = null; closeDrawer(); }
});

// ---------------------------------------------------------------- busy and error while opening (Simple mode has no panel)

(() => {
  const pill = document.createElement('div');
  pill.id = 'open-pill';
  pill.className = 'pill-bar';
  pill.hidden = true;
  pill.style.alignItems = 'center';
  pill.style.padding = '6px 6px 6px 16px';
  pill.style.zIndex = '6';
  const text = document.createElement('span');
  text.style.fontWeight = '500';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = 'Cancel';
  cancel.addEventListener('click', () => $('open-cancel').click());
  const report = document.createElement('button');
  report.type = 'button';
  report.id = 'report-open-pill';
  report.textContent = 'Report a problem';
  report.title = 'Send us what happened. You can choose to include the file so we can see why it failed.';
  report.addEventListener('click', openReport);
  pill.append(text, cancel, report);
  $('viewer').append(pill);

  const sync = () => {
    const busy = !$('panel-busy').hidden;
    const error = !$('panel-error').hidden;
    if (isSimple() && busy) {
      text.textContent = $('busy-text').textContent;
      cancel.hidden = false;
      report.hidden = true;
      pill.hidden = false;
    } else if (isSimple() && error && !app.docId) {
      text.textContent = $('error-text').textContent;
      cancel.hidden = true;
      report.hidden = false;
      pill.hidden = false;
    } else {
      pill.hidden = true;
    }
    card.hidden = card.hidden || busy;
  };
  const obs = new MutationObserver(sync);
  for (const id of ['panel-busy', 'panel-error']) obs.observe($(id), { attributes: true, attributeFilter: ['hidden'] });
  obs.observe($('busy-text'), { childList: true, characterData: true, subtree: true });
  obs.observe(document.body, { attributes: true, attributeFilter: ['data-mode'] });
})();

// ---------------------------------------------------------------- view options popover

const viewPop = $('view-pop');
const viewPopBtn = $('view-pop-open');
function setViewPop(open) {
  viewPop.classList.toggle('open', open);
  viewPopBtn.setAttribute('aria-expanded', String(open));
}
viewPopBtn.addEventListener('click', () => setViewPop(!viewPop.classList.contains('open')));
document.addEventListener('pointerdown', (e) => {
  if (viewPop.classList.contains('open') && !e.target.closest('#corner-tr')) setViewPop(false);
});
$('reset-view').addEventListener('click', () => setViewPop(false));

// ---------------------------------------------------------------- view cube

// Model axes: the model is Z up, drawn with modelRoot turned so model +Z is
// three +Y and model +Y is three -Z. Slicer "Front" looks at the model's -Y
// side, which is three +Z. The cube below is axis aligned to three's world.
// turn: extra label rotation if a face ever needs it (checked from views 1 to 6: none do).
const FACES = [
  { dir: [1, 0, 0], label: 'Right', turn: 0 },
  { dir: [-1, 0, 0], label: 'Left', turn: 0 },
  { dir: [0, 1, 0], label: 'Top', turn: 0 },
  { dir: [0, -1, 0], label: 'Bottom', turn: 0 },
  { dir: [0, 0, 1], label: 'Front', turn: 0 },
  { dir: [0, 0, -1], label: 'Back', turn: 0 },
];

const cube = (() => {
  const canvas = $('cube-canvas');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  } catch {
    $('view-cube').hidden = true;
    return null;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(96, 96, false);
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(28, 1, 0.1, 50);
  const light = new THREE.HemisphereLight(0xffffff, 0x888888, 1.4);
  scene.add(light);

  const colours = () => {
    const cs = getComputedStyle(document.body);
    return {
      face: cs.getPropertyValue('--surface-solid').trim() || '#fff',
      text: cs.getPropertyValue('--text').trim() || '#000',
      edge: cs.getPropertyValue('--secondary').trim() || '#888',
      hot: cs.getPropertyValue('--accent').trim() || '#0071e3',
    };
  };

  function faceTexture(label, c, turn = 0) {
    const size = 128;
    const cv = document.createElement('canvas');
    cv.width = size; cv.height = size;
    const ctx = cv.getContext('2d');
    ctx.fillStyle = c.face;
    ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = c.text;
    ctx.font = `600 ${label.length > 5 ? 26 : 30}px -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.translate(size / 2, size / 2);
    ctx.rotate(turn);
    ctx.fillText(label, 0, 2);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    return tex;
  }

  let materials = [];
  let edges;
  const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), []);
  scene.add(box);

  function build() {
    const c = colours();
    for (const m of materials) { m.map?.dispose(); m.dispose(); }
    materials = FACES.map((f) => new THREE.MeshLambertMaterial({ map: faceTexture(f.label, c, f.turn) }));
    box.material = materials;
    if (edges) { scene.remove(edges); edges.geometry.dispose(); edges.material.dispose(); }
    edges = new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry), new THREE.LineBasicMaterial({ color: new THREE.Color(c.edge), transparent: true, opacity: 0.8 }));
    scene.add(edges);
  }
  build();
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', build);

  // The small camera copies the big one's direction at a fixed distance.
  const lastQ = new THREE.Quaternion();
  let hot = -1;
  const dirScratch = new THREE.Vector3();
  function render(force) {
    if (!force && app.camera.quaternion.equals(lastQ)) return;
    lastQ.copy(app.camera.quaternion);
    dirScratch.copy(app.camera.position).sub(app.controls.target);
    if (dirScratch.lengthSq() < 1e-12) dirScratch.set(1, 0.75, 1);
    dirScratch.normalize().multiplyScalar(3.2);
    cam.position.copy(dirScratch);
    cam.quaternion.copy(app.camera.quaternion);
    renderer.render(scene, cam);
  }
  (function loop() { render(false); requestAnimationFrame(loop); })();

  // Hover and click
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  function pick(e) {
    const r = canvas.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(pointer, cam);
    const hit = ray.intersectObject(box, false)[0];
    return hit ? hit.face.materialIndex : -1;
  }
  function setHot(i) {
    if (i === hot) return;
    const c = colours();
    if (hot >= 0) materials[hot].color.set(0xffffff);
    hot = i;
    if (hot >= 0) materials[hot].color.set(new THREE.Color(c.hot).lerp(new THREE.Color(0xffffff), 0.55));
    canvas.style.cursor = hot >= 0 ? 'pointer' : 'default';
    canvas.title = hot >= 0 ? `Look from the ${FACES[hot].label.toLowerCase()}` : '';
    render(true);
  }
  canvas.addEventListener('pointermove', (e) => setHot(pick(e)));
  canvas.addEventListener('pointerleave', () => setHot(-1));
  canvas.addEventListener('click', (e) => {
    const i = pick(e);
    if (i >= 0 && app.docId) lookFrom(FACES[i].dir);
  });

  return { render, build };
})();

// Turn the big camera to look from a direction, keeping the distance, with a
// short glide. Opposite directions pass over the top instead of through the model.
let glide = null;
function lookFrom(dir, { distance = null, target = null } = {}) {
  if (!app.homeView) return;
  const { camera, controls } = app;
  const tgt = target ? target.clone() : controls.target.clone();
  const start = camera.position.clone().sub(controls.target);
  const dist = distance ?? start.length();
  const from = start.clone().normalize();
  const to = new THREE.Vector3(...dir).normalize();
  let via = null;
  if (from.dot(to) < -0.98) {
    via = Math.abs(to.y) > 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
  }
  const startTarget = controls.target.clone();
  const t0 = performance.now();
  const ms = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 320;
  const token = glide = {};
  function step(now) {
    if (glide !== token) return;
    const t = ms ? Math.min(1, (now - t0) / ms) : 1;
    const e = 1 - Math.pow(1 - t, 3);
    let d;
    if (via) {
      d = e < 0.5 ? from.clone().lerp(via, e * 2) : via.clone().lerp(to, (e - 0.5) * 2);
    } else {
      d = from.clone().lerp(to, e);
    }
    if (d.lengthSq() < 1e-9) d.copy(via || to);
    d.normalize();
    const curTarget = startTarget.clone().lerp(tgt, e);
    controls.target.copy(curTarget);
    camera.position.copy(curTarget).add(d.multiplyScalar(dist));
    controls.update();
    if (t < 1) requestAnimationFrame(step);
    else glide = null;
  }
  requestAnimationFrame(step);
}

function goHome() {
  if (!app.homeView) return;
  const dir = app.homeView.position.clone().sub(app.homeView.target);
  lookFrom(dir.toArray(), { distance: dir.length(), target: app.homeView.target });
}
$('view-home').addEventListener('click', goHome);

const VIEW_KEYS = { 0: 'home', 1: [0, 1, 0], 2: [0, -1, 0], 3: [0, 0, 1], 4: [0, 0, -1], 5: [-1, 0, 0], 6: [1, 0, 0] };

// ---------------------------------------------------------------- keys

document.addEventListener('keydown', (e) => {
  if (e.target.closest?.('input, textarea, select')) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape') {
    if (viewPop.classList.contains('open')) { setViewPop(false); return; }
    if (isSimple() && document.body.classList.contains('drawer-open') && !app.toolActive()) { closeDrawer(); return; }
    return;
  }
  if (!app.docId || app.toolActive()) return;
  if (e.key in VIEW_KEYS) {
    e.preventDefault();
    const v = VIEW_KEYS[e.key];
    if (v === 'home') goHome(); else lookFrom(v);
  } else if (e.key.toLowerCase() === 't') {
    e.preventDefault();
    togglePanel();
  }
});

// ---------------------------------------------------------------- help guide

const GUIDE = [
  ['Getting started', [
    ['What MeshRight does', 'It opens a 3D model, checks whether it will print, and fixes what is wrong: holes, loose bits, inside-out triangles, a model lying on its side or too big for your printer. Then it saves a file your slicer opens without complaint. Nothing leaves your computer.', 'Drop a file anywhere, or press Open.'],
    ['The score', 'A number out of 100 for how ready the model is to print. Green over 90 means print it. Orange means it will print but could be better. Red means your slicer will likely refuse it or print it wrong. Every fix moves the score, so you can see what helped.', 'Bottom card in Simple mode, top of the panel in Advanced.'],
    ['Simple and Advanced', 'Simple shows one card with the next thing to do and a short tool rail. Advanced docks the full panel beside the model with every tool, every number and the history. Switch at the top right at any time; your choice is remembered. Both modes work on the same model.', 'Top right of the header.'],
    ['Try it on a sample', 'With nothing open, the start screen offers a few sample models with typical faults. Open one to see what the check finds and what a fix looks like before you trust it with your own scan.', 'Start screen.'],
    ['Nothing changes by itself', 'Opening a file only checks it. Every fix shows you what it will do first, and every change can be undone with Ctrl+Z or from the History list.', ''],
  ]],
  ['Fixing', [
    ['Make it printable (Clean up)', 'Fills holes, deletes stray bits, turns inside-out triangles the right way and trims models with far too many triangles. You see the plan as a list of ticked steps first; untick any step you do not want. Four presets: Quick print (fewest triangles), Balanced, Keep all detail (resin) and Flexible (TPU, stricter walls).', 'Simple: the card or Fix on the rail. Advanced: Clean up in the checklist.'],
    ['One at a time', 'Goes through each problem in turn with its own fix and a Skip button, for when you want to see what each fix does. Problems with no single fix point you to Clean up.', 'Simple mode card, next to Make it printable.'],
    ['Make Solid', 'Rebuilds the whole model as one sealed solid, like dipping it in wax. Use it when Clean up cannot close every hole, or a scan is a mess of overlapping surfaces. Details finer than about 0.2 mm soften a little.', 'Bottom of the Clean up sheet, or Edit in the panel.'],
    ['Give it thickness', 'For a scan that is only a surface, such as a face mask or a relief, with no back and no inside. Adds a back behind the surface so the slicer sees a solid. 2 mm suits most filament prints; use at least twice your nozzle size.', 'Edit section of the panel.'],
    ['Check walls', 'Finds walls thinner than your nozzle can print and colours them red. Thin walls vanish in the slicer or snap when you pick the print up. The sheet offers to scale the model up until the thinnest wall prints.', 'Edit section of the panel.'],
    ['Problems list', 'Each problem says what it is, why it matters for printing and which fix handles it. Red ones will stop the print; orange ones are worth fixing. Problems is also a view option that draws them on the model: red lines are holes, orange lines are edges shared by too many triangles, purple is where the surface cuts through itself.', 'Advanced panel, or View at the top right.'],
  ]],
  ['Position and size', [
    ['Stand it up', 'Scans often come in lying on their side. Which way is up? shows the model from several likely bottoms; pick the one that looks upright. Pick the bottom lets you click three or more spots that should touch the bed and turns the model so they lie flat. Best way to print tries many positions and ranks them by how much support they need and how steadily they stand.', 'Stand up on the rail, or Position in the panel.'],
    ['Put on bed', 'Sets the model flat on the bed and in the middle, the way it will print. Harmless to press any time.', 'Position in the panel.'],
    ['Spin, tip, roll and mirror', 'Turn the model 90 degrees at a time around any axis. Mirror flips it left to right, for example to make a left-hand version of a right-hand part.', 'Position in the panel.'],
    ['Size', 'Type a width, depth or height in millimetres and the other two follow, so the shape stays the same. Tick the coin to see a 23 mm coin beside the model for scale. If a scan came in at the wrong scale, Measure can resize it from one known distance instead.', 'Size on the rail, or Size in the panel.'],
    ['Measure', 'Click two points on the model to see the distance between them. Type the real distance and MeshRight resizes the whole model so it matches. Press M to start.', 'Measure on the rail.'],
    ['Too big for the printer', 'When you have chosen a printer, MeshRight checks the model fits its bed. If not, Shrink to fit scales it down, and Split into parts cuts it into pieces that each fit, with pins so they glue back together lined up.', 'The card, or Fits your printer in the checklist.'],
    ['Your printer', 'Choose your printer from the list or type the bed size and nozzle. MeshRight uses it to check fit, to judge thin walls, and to remember your pin clearance. You can change it any time; it is stored on this computer only.', 'Printer at the top of the window.'],
  ]],
  ['Editing tools', [
    ['Erase an area', 'Draw a loop around part of the model to delete it, for example the table a scanner picked up or a stray blob. Facing me only leaves the far side of the model alone. Undo brings anything back.', 'Advanced rail, or Edit in the panel.'],
    ['Erase a piece', 'Click any loose piece that is not joined to the model, such as crumbs, a stand or a second object the scanner caught, to delete it.', 'Advanced rail, or Edit in the panel.'],
    ['Cut in two', 'Cuts the model along a line you place, for models taller than your printer or awkward to print whole. Pins and matching holes keep the parts lined up when you glue them, and each part gets its number engraved on a hidden cut face. The Fit setting is the gap around each pin: 0.3 mm suits most printers.', 'Advanced rail, or Edit in the panel.'],
    ['Add a hole', 'Makes a round hole where you click, for a screw, a magnet or a hanging loop. 3.2 mm suits an M3 screw. Depth 0 goes all the way through.', 'Advanced rail, or Edit in the panel.'],
    ['Sculpt', 'Brush the surface: Smooth calms rough areas, Flatten makes an area flat, Push out and Push in raise or lower it. Drag across the model to brush, drag off the model to turn the view. The [ and ] keys change the brush size. Each stroke can be undone.', 'Advanced rail, or Edit in the panel.'],
    ['Make a part', 'Draw a loop around an area and MeshRight builds a solid piece shaped to fit it, such as a pad, a grip or a cover. It opens as a new model; the original stays as it is. Gap for a good fit is the play between the two, 0.2 mm is a sensible start.', 'Advanced rail, or Edit in the panel.'],
    ['Hollow (resin)', 'For resin printers: hollows the inside to save resin, keeping walls of the thickness you choose, with drain holes at the bottom so trapped resin can run out. Filament printers do not need this; the slicer\'s infill already fills the inside sparsely.', 'Edit in the panel.'],
    ['Make a bust', 'For a scan of a head, or head and shoulders. Closes the neck, cuts the ragged bottom off flat, adds a round or square base and sizes it to the height you want. Stand the head upright first.', 'Edit in the panel.'],
    ['Combine', 'Opens a second model in orange that you can move, turn and resize with the handles (G, R and S switch between them). Join adds it on, for a stand or a handle. Cut it away removes its shape from your model. Keep the overlap keeps only where they cross.', 'Edit in the panel.'],
    ['Merge scans', 'Lines up and fuses a scan taken from another side. Click a spot on the blue model, then the same spot on the orange scan. Three pairs is enough; more gives a better fit.', 'Edit in the panel.'],
  ]],
  ['Looking at the model', [
    ['Turning and zooming', 'Drag to turn the view, right-drag or two-finger drag to pan, scroll or pinch to zoom. On a tablet: one finger turns, two fingers pan and zoom.', ''],
    ['The view cube', 'The small cube at the top right turns with the model. Click a face to look from that side, like a slicer\'s view cube. Home goes back to the starting view with the whole model in sight. The number keys do the same: 0 home, 1 top, 2 bottom, 3 front, 4 back, 5 left, 6 right.', 'Top right of the model.'],
    ['View options', 'Problems draws the faults on the model. Wireframe shows the triangles. Overhangs colours the parts that lean out too far to print without support. Rough spots shades the surface green where it is clean and red where it is rough. Cut view slices the model open at a height you choose, to see inside. Colours shows the colours a scanner recorded, when the file has them.', 'View at the top right.'],
    ['Show what changed', 'Colours the model green where it is as in the original file and red where a repair moved the surface by 2 mm or more, so you can see exactly what the fixes touched.', 'History in the panel.'],
  ]],
  ['Saving and slicers', [
    ['Which file to save', '3MF is best for Bambu Studio, PrusaSlicer and OrcaSlicer: it keeps the size and position exact. STL works in every slicer. OBJ and PLY keep colours for other 3D apps. A MeshRight project saves your work, including the history, so you can carry on later.', 'Export at the top of the window, or the card.'],
    ['Open in slicer', 'When MeshRight finds a slicer installed on this computer, the card and the Export menu offer to open the fixed model in it directly. The file is saved to Downloads as well.', 'The card, or Export.'],
    ['3MF in colours', 'For a colour scan and a multi-colour printer: groups the scan\'s colours into 2 to 8 print colours and paints every triangle, so the slicer opens it ready to assign a filament to each colour. Four colours suits most scans.', 'Export menu, when the model has colours.'],
    ['Several files at once', 'Choose or drop several files together to fix them all with the same settings and download the results in one go.', 'Start screen.'],
  ]],
  ['Undo, history and safety', [
    ['Undo and redo', 'Every change can be undone with Ctrl+Z (Cmd+Z on a Mac) and redone with Ctrl+Y. The arrows at the bottom left do the same.', 'Bottom left of the model.'],
    ['History', 'Lists every change so far, newest at the bottom, with what it did in plain words. Back to the original file returns to the file as you opened it, and even that can be undone.', 'History at the bottom left, or in the panel.'],
    ['Your work survives a crash', 'After every step the open model is saved in MeshRight\'s own folder on this computer, the newest ten. If MeshRight or the browser closes unexpectedly, open it again and the file is offered back.', ''],
    ['Privacy', 'Models are checked and fixed on this computer; nothing is uploaded. Ask AI, if you connect one, sees the check report and never the model. Report a problem only sends your model if you tick the box, and then only to Steam Vibe Ltd to reproduce the fault.', ''],
  ]],
  ['When something goes wrong', [
    ['The file will not open', 'Check it is one of the listed formats. A zip or a slicer project file (.3mf from a slicer usually opens; .gcode never will). Very big files can take a few minutes; press Cancel if the computer slows down and try Quick print on a smaller version. If a good file still refuses, press Report a problem on the message and tick the box to include the file, so we can see why.', ''],
    ['Clean up did not fix it', 'Some scans are too broken to patch hole by hole. Try Make Solid, which rebuilds the whole model. For a surface with no inside, use Give it thickness first.', ''],
    ['The model looks wrong after a fix', 'Press Ctrl+Z. Turn on Show what changed under History to see exactly what moved. Then try a gentler preset, or fix one thing at a time.', ''],
    ['Report a problem', 'Press Report a problem at the bottom right of the model, in Help under About, or on the message when a file will not open. MeshRight gathers the last error messages, the steps you took, the version and your browser into a small report file in your Downloads folder and opens an email to support@steamvibe.com. Your model is only included if you tick the box.', 'Bottom right of the model, or Help then About.'],
    ['Ask AI', 'Optional. Connect an AI service you already use, or one that runs on this computer such as Ollama or LM Studio, and ask in your own words: will this print, what is wrong, make it 15 cm tall. It sees the check report, never the model, and only suggests steps; nothing happens until you press Apply.', 'Ask AI at the top of the window.'],
  ]],
];

function renderGuide(query = '') {
  const host = $('help-topics');
  host.replaceChildren();
  const q = query.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  const matches = (text) => !words.length || words.every((w) => text.toLowerCase().includes(w));
  const mark = (text) => {
    if (!words.length) return document.createTextNode(text);
    const frag = document.createDocumentFragment();
    const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig');
    for (const part of text.split(re)) {
      if (!part) continue;
      if (words.includes(part.toLowerCase())) { const m = document.createElement('mark'); m.textContent = part; frag.append(m); } else frag.append(part);
    }
    return frag;
  };
  let shown = 0;
  for (const [group, topics] of GUIDE) {
    const hits = topics.filter(([title, body, where]) => matches(`${title} ${body} ${where}`));
    if (!hits.length) continue;
    const h = document.createElement('h3');
    h.textContent = group;
    host.append(h);
    for (const [title, body, where] of hits) {
      const d = document.createElement('details');
      if (words.length) d.open = true;
      const s = document.createElement('summary');
      s.append(mark(title));
      const b = document.createElement('div');
      b.className = 'body';
      for (const para of body.split('\n')) { const p = document.createElement('p'); p.append(mark(para)); b.append(p); }
      if (where) { const p = document.createElement('p'); p.className = 'where'; p.append('Where: ', mark(where)); b.append(p); }
      d.append(s, b);
      host.append(d);
      shown++;
    }
  }
  $('help-none').hidden = shown > 0;
}
renderGuide();
$('help-search').addEventListener('input', (e) => renderGuide(e.target.value));

function showHelpTab(name) {
  for (const b of document.querySelectorAll('#help-tabs [data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
  for (const t of ['guide', 'keys', 'about']) $(`help-${t}`).hidden = t !== name;
  if (name === 'guide') setTimeout(() => $('help-search').focus(), 50);
}
$('help-tabs').addEventListener('click', (e) => {
  const b = e.target.closest('[data-tab]');
  if (b) showHelpTab(b.dataset.tab);
});

// ---------------------------------------------------------------- report a problem

const dialog = $('report-dialog');
let modelBlob = null;      // the 3MF of the open model, fetched when the box is ticked
let modelFor = null;       // which docId it belongs to

function openReport() {
  $('report-ask').hidden = false;
  $('report-done').hidden = true;
  $('report-error').hidden = true;
  $('report-big').hidden = true;
  $('report-model').checked = false;
  $('report-model-size').textContent = '';
  const consent = dialog.querySelector('.consent');
  const failed = !app.docId && app.lastFailedFile;
  consent.hidden = !app.docId && !failed;
  consent.querySelector('strong').textContent = failed ? 'Also include the file that would not open' : 'Also include my model file';
  consent.querySelector('small').textContent = failed
    ? `${app.lastFailedFile.name}, ${app.lastFailedFile.size < 100 * 1024 ? 'under 0.1' : app.fmt(app.lastFailedFile.size / 1024 / 1024, 1)} MB. Without it we can only guess why it failed. Optional and off until you tick it.`
    : 'Most faults can only be reproduced with the model. It is optional and off until you tick it.';
  $('report-includes').lastElementChild.hidden = !app.docId;
  if (!dialog.open) dialog.showModal();
  setTimeout(() => $('report-note').focus(), 50);
}
$('report-open').addEventListener('click', openReport);
$('report-open-error').addEventListener('click', openReport);
$('error-retry').addEventListener('click', () => $('file-input').click());
$('report-open-about').addEventListener('click', () => { $('help-dialog').close(); openReport(); });

$('report-model').addEventListener('change', async (e) => {
  if (e.target.checked && !app.docId && app.lastFailedFile) {
    const mb = app.lastFailedFile.size / 1024 / 1024;
    $('report-big').hidden = mb <= 20;
    if (mb > 20) $('report-big').textContent = `The file is ${app.fmt(mb, 0)} MB. Many email services refuse attachments over 20 to 25 MB. Send the report without it if the email bounces, and we will reply with a link to upload the file instead.`;
    return;
  }
  if (!e.target.checked || !app.docId) { $('report-big').hidden = true; return; }
  if (modelFor !== app.docId || !modelBlob) {
    $('report-model-size').textContent = '(getting the size…)';
    try {
      const res = await fetch(`api/doc/${app.docId}/export?format=3mf`);
      if (!res.ok) throw new Error('export failed');
      modelBlob = await res.blob();
      modelFor = app.docId;
    } catch {
      modelBlob = null;
      $('report-model-size').textContent = '(could not read the model)';
      return;
    }
  }
  const mb = modelBlob.size / 1024 / 1024;
  $('report-model-size').textContent = `(${mb < 0.1 ? 'under 0.1' : app.fmt(mb, 1)} MB)`;
  $('report-big').hidden = mb <= 20;
  if (mb > 20) {
    $('report-big').textContent = `Your model is ${app.fmt(mb, 0)} MB. Many email services refuse attachments over 20 to 25 MB. Send the report without it if the email bounces, and we will reply with a link to upload the model instead.`;
  }
});

function describeState(state) {
  if (!state) return ['No model open.'];
  const lines = [
    `File: ${state.name}`,
    `Score: ${state.score} (${state.verdict})`,
  ];
  const s = state.stats || {};
  if (s.size_mm) lines.push(`Size mm: ${s.size_mm.map((v) => app.fmt(v, 2)).join(' x ')}`);
  if (s.triangles != null) lines.push(`Triangles: ${s.triangles}`);
  for (const [k, v] of Object.entries(s)) {
    if (k === 'size_mm' || k === 'triangles') continue;
    if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') lines.push(`${k}: ${v}`);
  }
  lines.push('', 'Problems:');
  for (const i of state.issues || []) lines.push(`  [${i.severity}] ${i.code}: ${i.title}`);
  if (!(state.issues || []).length) lines.push('  none');
  lines.push('', 'Steps (History):');
  const h = state.history || { steps: [], position: 0 };
  lines.push('  0. Opened the file');
  (h.steps || []).forEach((step, i) => lines.push(`  ${i + 1}. ${step.receipt}${i >= h.position ? '  (undone)' : ''}`));
  return lines;
}

function buildReport(note) {
  const ua = navigator.userAgent;
  const os = (navigator.userAgentData?.platform) || (/Windows/.test(ua) ? 'Windows' : /Mac/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : 'unknown');
  const lines = [
    'MeshRight problem report',
    `Date: ${new Date().toISOString()}`,
    `MeshRight: ${$('app-version').textContent || 'unknown'}`,
    `Mode: ${document.body.dataset.mode}`,
    `Browser: ${ua}`,
    `OS: ${os}`,
    `Screen: ${window.innerWidth} x ${window.innerHeight}, pixel ratio ${window.devicePixelRatio}`,
    `Printer: ${app.printerChosen ? `${app.printer.name} (${app.printer.bed_x ?? ''}x${app.printer.bed_y ?? ''}x${app.printer.bed_z ?? ''} mm, nozzle ${app.printer.nozzle_mm ?? ''})` : 'not chosen'}`,
    '',
    'What happened (from the user):',
    note ? note.split('\n').map((l) => `  ${l}`).join('\n') : '  (nothing written)',
    '',
    'Last errors shown:',
    ...(app.errorLog.length ? app.errorLog.map((e) => `  ${e.at}  ${e.text}`) : ['  none']),
    '',
    'Model:',
    ...(app.lastState ? describeState(app.lastState) : [app.lastFailedFile ? `No model open. The last file that would not open: ${app.lastFailedFile.name} (${app.lastFailedFile.size} bytes)` : 'No model open.']),
    '',
  ];
  return lines.join('\n');
}

// A minimal zip writer (stored, no compression): enough for a text file and a 3MF.
const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();
function crc32(bytes) {
  let c = -1;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}
function dosTime(d) {
  const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
  const date = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  return { time, date };
}
function makeZip(files) {  // [{ name, bytes: Uint8Array }]
  const enc = new TextEncoder();
  const now = dosTime(new Date());
  const parts = [];
  const central = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.bytes);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true);  // utf-8 names
    local.setUint16(8, 0, true);       // stored
    local.setUint16(10, now.time, true);
    local.setUint16(12, now.date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, f.bytes.length, true);
    local.setUint32(22, f.bytes.length, true);
    local.setUint16(26, name.length, true);
    local.setUint16(28, 0, true);
    parts.push(new Uint8Array(local.buffer), name, f.bytes);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true);
    cd.setUint16(4, 20, true);
    cd.setUint16(6, 20, true);
    cd.setUint16(8, 0x0800, true);
    cd.setUint16(10, 0, true);
    cd.setUint16(12, now.time, true);
    cd.setUint16(14, now.date, true);
    cd.setUint32(16, crc, true);
    cd.setUint32(20, f.bytes.length, true);
    cd.setUint32(24, f.bytes.length, true);
    cd.setUint16(28, name.length, true);
    cd.setUint16(30, 0, true);
    cd.setUint16(32, 0, true);
    cd.setUint16(34, 0, true);
    cd.setUint16(36, 0, true);
    cd.setUint32(38, 0, true);
    cd.setUint32(42, offset, true);
    central.push(new Uint8Array(cd.buffer), name);
    offset += 30 + name.length + f.bytes.length;
  }
  const cdSize = central.reduce((n, p) => n + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(4, 0, true);
  end.setUint16(6, 0, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, cdSize, true);
  end.setUint32(16, offset, true);
  end.setUint16(20, 0, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: 'application/zip' });
}

$('report-send').addEventListener('click', async () => {
  const errorLine = $('report-error');
  errorLine.hidden = true;
  const note = $('report-note').value.trim();
  const failedFile = !app.docId && app.lastFailedFile;
  const includeModel = $('report-model').checked && (app.docId || failedFile);
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const zipName = `meshright-report-${stamp}.zip`;
  try {
    const files = [{ name: 'report.txt', bytes: new TextEncoder().encode(buildReport(note)) }];
    if (includeModel && failedFile) {
      const safe = failedFile.name.replace(/[^\w.-]+/g, '_') || 'file';
      files.push({ name: safe, bytes: new Uint8Array(await failedFile.arrayBuffer()) });
    } else if (includeModel) {
      if (modelFor !== app.docId || !modelBlob) {
        const res = await fetch(`api/doc/${app.docId}/export?format=3mf`);
        if (!res.ok) throw new Error('Could not read the model to include it.');
        modelBlob = await res.blob();
        modelFor = app.docId;
      }
      const base = (app.lastState?.name || 'model').replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '_') || 'model';
      files.push({ name: `${base}-meshright.3mf`, bytes: new Uint8Array(await modelBlob.arrayBuffer()) });
    }
    const zip = makeZip(files);
    const url = URL.createObjectURL(zip);
    const a = document.createElement('a');
    a.href = url;
    a.download = zipName;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);

    const subject = `MeshRight problem: ${(app.lastState?.name || failedFile?.name || 'no model').slice(0, 60)}`;
    const bodyText = [
      note || '(describe what happened here)',
      '',
      `Please attach ${zipName} from your Downloads folder before sending.`,
      includeModel ? (failedFile ? 'It contains the report and the file that would not open.' : 'It contains the report and my model file.') : 'It contains the report only (no model).',
      '',
      `MeshRight ${$('app-version').textContent || ''}`,
    ].join('\n');
    const mailto = `mailto:support@steamvibe.com?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(bodyText)}`;
    $('report-file').textContent = zipName;
    $('report-ask').hidden = true;
    $('report-done').hidden = false;
    setTimeout(() => { window.location.href = mailto; }, 300);
  } catch (err) {
    errorLine.textContent = err.message || 'Could not build the report.';
    errorLine.hidden = false;
  }
});

// ---------------------------------------------------------------- start

setMode(app.readStore(MODE_KEY, 'simple'), { remember: false });
updateRail();
if (app.lastState) { renderCard(app.lastState); renderCorners(app.lastState); }
