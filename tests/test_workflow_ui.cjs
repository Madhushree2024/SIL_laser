const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function ui() {
  const nodes = new Map(), listeners = new Map();
  function node(id = '') {
    return { id, value: '', disabled: false, dataset: {}, children: [], checked: false,
      textContent: '', innerHTML: '', style: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      focus() { document.activeElement = this; },
      getContext() { return {}; }, closest() { return null; },
      setAttribute(name,value) { this.attributes ||= {};this.attributes[name]=String(value); },
      cloneNode() { const copy=node(this.id);for(const name of ['textContent','href','target','value'])copy[name]=this[name];copy.attributes={...this.attributes};return copy; },
      addEventListener(type, handler) { this[type] = handler; },
      replaceChildren(...children) { this.children = children; if (id === 'preferredTemplate' || id === 'librarySelection') this.value = children[0]?.value || ''; },
      append(...children) { this.children.push(...children); }, prepend(child) { this.children.unshift(child); },
    };
  }
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id); };
  const document = {
    getElementById: get, createElement: () => node(), hasFocus: () => true, hidden: false, activeElement: null,
    baseURI: 'http://127.0.0.1:8001/',
    querySelectorAll: () => [], querySelector: () => node(),
    addEventListener(type, fn) { const set = listeners.get(type) || new Set(); set.add(fn); listeners.set(type, set); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
  };
  const context = vm.createContext({ document, window: { addEventListener() {}, confirm: () => true },
    Option: function(text, value) { this.text = text; this.value = value; },
    setInterval() {}, setTimeout: (fn) => setImmediate(fn), devicePixelRatio: 1,
    URL, FormData: class {}, crypto: { randomUUID: () => 'generated-id' }, console,
  });
  const source = fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8').replace('init().catch((error) => message(error.message, true));', '');
  vm.runInContext(source + '\nglobalThis.appState = state;', context);
  context.appState.user = {id:'test-admin',username:'test-admin',role:'admin',permissions:['read','download','operate','templates','configure','users']};
  context.draw = () => {};
  context.logs = [];
  context.message = (text, error) => context.logs.push({ text, error });
  return { context, get, document, listeners, run: (code) => vm.runInContext(code, context) };
}

function space(changes = {}) {
  return { code: 'Space', target: { closest: () => null }, preventDefault() { this.prevented = true; }, ...changes };
}

test('one active-window Space starts workflow; duplicate registration and teardown', () => {
  const u = ui(); let calls = 0;
  u.context.runWorkflow = () => { calls++; u.context.appState.workflowBusy = true; };
  u.run('registerWorkflowKeys(); registerWorkflowKeys();');
  assert.equal(u.listeners.get('keydown').size, 1);
  const first = space(); u.context.workflowKeydown(first);
  u.context.workflowKeydown(space()); u.context.workflowKeydown(space({ repeat: true }));
  assert.equal(calls, 1); assert.equal(first.prevented, true);
  u.run('unregisterWorkflowKeys();');
  assert.equal(u.listeners.get('keydown').size, 0);
});

test('LaserCAD tab opens once for a saved DXF and ignores SVG or missing jobs',()=>{
 const u=ui(),views=[];u.context.navigateSection=view=>views.push(view);
 const output={path:'layout.dxf',laser_control:{job_id:'job-1'}};
 u.context.openLaserControlForExport(output);u.context.openLaserControlForExport(output);
 u.context.openLaserControlForExport({path:'layout.svg',laser_control:{job_id:'svg'}});
 u.context.openLaserControlForExport({path:'failed.dxf'});
 assert.deepEqual(views,['lasercad']);
});

test('LaserCAD tab opens during workflow handoff before LaserCAD reports completion',async()=>{
 const u=ui(),views=[];let complete;
 u.context.navigateSection=view=>views.push(view);
 for(const name of ['displayWorkflow','renderExportLinks','updateCycleVisuals','showWorkflowPreview'])u.context[name]=()=>{};
 u.context.loadCurrentState=async()=>{};
 u.context.api=async()=>new Promise(resolve=>complete=resolve);
 const output={path:'parallel.dxf',laser_control:{job_id:'parallel'}};
 const pending=u.context.followWorkflow({id:'cycle',busy:true,stage:'Exporting',artifacts:{export:output}},'cycle');
 assert.deepEqual(views,['lasercad']);
 await new Promise(resolve=>setImmediate(resolve));
 complete({id:'cycle',busy:false,stage:'Completed',result:{export:output,nesting:{placements:[]}}});
 await pending;assert.deepEqual(views,['lasercad']);
});

test('LaserCAD tab manual export switches before its pending LaserCAD response resolves',async()=>{
 const u=ui(),views=[];let complete,request;
 u.context.navigateSection=view=>views.push(view);
 u.context.readAlignment=()=>({});u.context.renderExportLinks=()=>{};
 u.context.api=async(url,options)=>{
   if(url==='/api/export'){request=JSON.parse(options.body);return new Promise(resolve=>complete=resolve)}
   assert.equal(url,'/api/laser-control/status');
   return {job:{id:'manual',source_path:'manual.dxf',ui_request_id:request.ui_request_id}};
 };
 const pending=u.context.exportLayout('dxf');
 await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(views,['lasercad']);
 complete({path:'manual.dxf',laser_control:{job_id:'manual'}});
 await pending;assert.deepEqual(views,['lasercad']);
});

test('LaserCAD tab watcher ignores another exports request identity',async()=>{
 const u=ui(),views=[];let polls=0;
 u.context.navigateSection=view=>views.push(view);
 u.context.api=async()=>({job:{id:'other',source_path:'other.dxf',ui_request_id:++polls===1?'unrelated':'expected'}});
 const stop=u.context.watchLaserControlExport('expected');
 await new Promise(resolve=>setImmediate(resolve));assert.equal(views.length,0);
 await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));
 stop();assert.deepEqual(views,['lasercad']);assert.equal(polls,2);
});

test('Laser Control controller heading excludes the SIL CO2 brand',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../frontend/index.html'),'utf8');
 assert.match(html,/<p class="lc-eyebrow">TROCEN AWC7813 CON<\/p>/);
 assert.ok(!html.includes('SIL CO₂ •'));
});

test('Space respects focus, edit modes, native controls, modifiers and IME', () => {
  const u = ui(); let calls = 0; u.context.runWorkflow = () => calls++;
  for (const selector of ['input', 'textarea', 'select', 'button', 'a', 'summary', '[contenteditable]', '[role="textbox"]', '[role="combobox"]', '[role="checkbox"]']) {
    const event = space({ target: { closest: (query) => query.includes(selector.split(']')[0]) ? {} : null } });
    u.context.workflowKeydown(event); assert.equal(event.prevented, undefined, selector);
  }
  for (const field of ['repeat', 'defaultPrevented', 'isComposing', 'ctrlKey', 'altKey', 'metaKey', 'shiftKey']) u.context.workflowKeydown(space({ [field]: true }));
  u.document.hidden = true; u.context.workflowKeydown(space()); u.document.hidden = false;
  u.document.hasFocus = () => false; u.context.workflowKeydown(space()); u.document.hasFocus = () => true;
  u.document.activeElement = { closest: () => ({}) }; u.context.workflowKeydown(space()); u.document.activeElement = null;
  u.context.appState.editBoundary = true; u.context.workflowKeydown(space()); u.context.appState.editBoundary = false;
  u.context.appState.activeView = 'calibration'; u.context.workflowKeydown(space());
  u.context.appState.activeView = 'laser-control'; u.context.workflowKeydown(space());
  assert.equal(calls, 0);
});

test('workflow freezes selection, runs once and shows completion only after export', async () => {
  const u = ui();
  const templates = [{ id: 'stable-42', name: 'Selected', shape: 'square', width_mm: 20, height_mm: 20, enabled: true, quantity: 1, priority: 2 }, { id: 'stable-7', name: 'Other', shape: 'square', width_mm: 30, height_mm: 30, enabled: true, quantity: 1, priority: 1 }];
  u.context.appState.templates = templates; u.context.appState.selectedTemplateId = 'stable-42';
  u.get('workflowFormat').value = 'svg';
  const stages = []; let starts = 0, reads = 0, captured;
  const completed = { id: 'generated-id', busy: false, stage: 'Completed', result: { export: { path: '/tmp/layout.svg', url: '/data/layout.svg' }, nesting: { placements: [{ template_id: 'stable-42' }] } } };
  u.context.api = async (url, options) => {
    if (url === '/api/workflow' && options) { starts++; captured = JSON.parse(options.body); return { id: 'generated-id', busy: true, stage: 'Capturing image' }; }
    reads++; return reads === 1 ? { id: 'generated-id', busy: true, stage: 'Exporting' } : completed;
  };
  const display = u.context.displayWorkflow;
  u.context.displayWorkflow = (status) => { stages.push(status.stage); display(status); if (status.stage === 'Exporting') assert.equal(u.context.logs.some(x => x.text.startsWith('Completed:')), false); };
  u.context.loadCurrentState = async () => {};
  const run = u.context.runWorkflow(); await u.context.runWorkflow();
  assert.equal(u.context.appState.workflowBusy, true);
  await run;
  assert.equal(starts, 1);
  assert.deepEqual(captured.templates.map(x => x.id), ['stable-42']);
  assert.equal(captured.selected_template_id, 'stable-42');
  assert.equal('space_number' in captured, false);
  assert.deepEqual(stages, ['Capturing image', 'Exporting', 'Completed']);
  assert.equal(u.context.appState.workflowBusy, false);
  assert.equal(u.get('workflowStatus').textContent, 'Ready for next cycle - press Space');
  assert.equal(u.get('exportResult').children[0].href, '/data/layout.svg');
});

test('failed export restores UI without success', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg';
  u.context.api = async (url, options) => options ? { id: 'generated-id', busy: true, stage: 'Exporting' } : { id: 'generated-id', busy: false, stage: 'Failed', error: 'Exporting: disk full' };
  await u.context.runWorkflow();
  assert.equal(u.context.appState.workflowBusy, false);
  assert.match(u.get('workflowStatus').textContent, /disk full/);
  assert.equal(u.context.logs.some(x => x.text.startsWith('Completed:')), false);
});

test('lost start response and transient status failure keep workflow locked', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg'; let reads = 0;
  u.context.api = async (url, options) => {
    assert.equal(u.context.appState.workflowBusy, true);
    if (options || ++reads === 1) throw new Error('offline');
    return reads === 2 ? { id: 'generated-id', busy: true, stage: 'Exporting' } : { id: 'generated-id', busy: false, stage: 'Failed', error: 'failed export' };
  };
  await u.context.runWorkflow();
  assert.equal(reads, 3); assert.equal(u.context.appState.workflowBusy, false);
});

test('disable conflicting controls and restore their original states', () => {
  const u = ui(), enabled = u.get('a'), disabled = u.get('b'); disabled.disabled = true;
  u.document.querySelectorAll = selector => selector === "[data-laser-action]" ? [] : [enabled, disabled];
  u.context.setWorkflowBusy(true); assert.equal(enabled.disabled, true);
  u.context.setWorkflowBusy(true); u.context.setWorkflowBusy(false);
  assert.equal(enabled.disabled, false); assert.equal(disabled.disabled, true);
});

test('library renders all legacy IDs and selects by stable ID', () => {
  const u = ui();
  u.context.appState.templates = Array.from({ length: 17 }, (_, i) => ({ id: String(90 - i), name: 'Name ' + i, width_mm: 20, height_mm: 20, shape: 'square', quantity: 1, enabled: true, priority: 17 - i }));
  u.context.renderTemplates();
  assert.equal(u.get('templates').children.length, 17);
  assert.equal(u.get('preferredTemplate').children.length, 18);
  u.get('librarySelection').value = '90'; u.context.loadSelectedTemplate();
  assert.equal(u.context.appState.selectedTemplateId, '90');
  assert.deepEqual(Array.from(u.context.workflowTemplates(), x => x.id), ['90']);
  u.context.appState.workflowBusy = true; u.get('librarySelection').value = '89'; u.context.loadSelectedTemplate();
  assert.equal(u.context.appState.selectedTemplateId, '90');
});

test('deletion requires valid selection and confirmation, cancellation keeps data', async () => {
  const u = ui(); const template = { id: 'legacy', name: 'Legacy', width_mm: 20, height_mm: 20, quantity: 1, enabled: true };
  u.context.appState.templates = [template]; let calls = 0;
  u.context.api = async () => { calls++; return { templates: [] }; };
  await u.context.deleteSelectedTemplate(); assert.equal(calls, 0);
  u.get('librarySelection').value = 'legacy'; u.context.window.confirm = () => false;
  await u.context.deleteSelectedTemplate(); assert.equal(calls, 0); assert.equal(u.context.appState.templates.length, 1);
  u.context.window.confirm = () => true; u.context.appState.selectedTemplateId = 'legacy';
  await u.context.deleteSelectedTemplate(); assert.equal(calls, 1); assert.equal(u.context.appState.templates.length, 0);
  assert.equal(u.context.appState.selectedTemplateId, null); assert.equal(u.get('templates').children.length, 0);
});

test('deletion storage error keeps the selection and data', async () => {
  const u = ui(); u.context.appState.templates = [{ id: 'old', name: 'Old' }]; u.context.appState.selectedTemplateId = 'old';
  u.get('librarySelection').value = 'old'; u.context.api = async () => { throw new Error('Permission denied'); };
  await u.context.deleteSelectedTemplate();
  assert.equal(u.context.appState.templates.length, 1); assert.equal(u.context.appState.selectedTemplateId, 'old');
  assert.match(u.context.logs[0].text, /Permission denied/);
});

test('existing add workflow saves a unique ID and immediately refreshes library', async () => {
  const u = ui(); u.context.bindEvents();
  u.get('preferredShape').value = 'square'; u.get('preferredUnits').value = 'mm'; u.get('preferredWidth').value = '30';
  u.get('preferredQuantity').value = '2'; u.get('preferredName').value = 'New template';
  let saved;
  u.context.api = async (url, options) => { saved = JSON.parse(options.body); return { templates: saved }; };
  await u.get('templatePreferenceForm').onsubmit({ preventDefault() {} });
  assert.equal(saved[0].id, 'custom-generated-id'); assert.equal(saved[0].width_mm, 30);
  assert.equal(u.get('templates').children.length, 1); assert.equal(u.get('librarySelection').value, saved[0].id);
  assert.equal(u.get('savePreference').disabled, false);
});

test('invalid add input does not save and restores controls', async () => {
  const u = ui(); u.context.bindEvents(); u.get('preferredShape').value = 'square'; u.get('preferredWidth').value = '-5';
  let calls = 0; u.context.api = async () => calls++;
  await u.get('templatePreferenceForm').onsubmit({ preventDefault() {} });
  assert.equal(calls, 0); assert.equal(u.get('savePreference').disabled, false);
});


test('rejected request cannot report an earlier successful export', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg';
  u.context.api = async (url, options) => {
    if (options) { const error = new Error('Invalid template'); error.status = 422; throw error; }
    return { id: 'old-job', busy: false, stage: 'Completed', result: {} };
  };
  await u.context.runWorkflow();
  assert.equal(u.context.logs.some(x => x.text.startsWith('Completed:')), false);
  assert.match(u.get('workflowStatus').textContent, /Invalid template/);
});

test('lost start response cannot reuse a previous job result', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg';
  u.context.api = async (url, options) => {
    if (options) throw new Error('offline');
    return { id: 'old-job', busy: false, stage: 'Completed', result: {} };
  };
  await u.context.runWorkflow();
  assert.equal(u.context.logs.some(x => x.text.startsWith('Completed:')), false);
  assert.match(u.get('workflowStatus').textContent, /not started/);
});


test('page startup opens USB camera automatically when disconnected', async () => {
  const u = ui(), calls = [];
  u.context.loadCameras = async () => calls.push('list');
  u.context.syncCameraStatus = async () => { calls.push('status'); return false; };
  u.context.openMachineCamera = async () => calls.push('openUSB');
  await u.context.initializeDefaultCamera();
  assert.deepEqual(calls, ['list', 'status', 'openUSB']);
});

test('page startup preserves an already open camera', async () => {
  const u = ui(); let opens = 0;
  u.context.loadCameras = async () => {};
  u.context.syncCameraStatus = async () => true;
  u.context.openMachineCamera = async () => opens++;
  await u.context.initializeDefaultCamera(); assert.equal(opens, 0);
});

test('USB startup error is surfaced without a builtin fallback', async () => {
  const u = ui();
  u.context.loadCameras = async () => {};
  u.context.syncCameraStatus = async () => false;
  u.context.openMachineCamera = async () => { throw new Error('USB camera not found'); };
  await assert.rejects(u.context.initializeDefaultCamera(), /USB camera not found/);
});


test('pending selection is distinct from the template the workflow will use', () => {
  const u = ui();
  u.context.appState.templates = ['first', 'second'].map(id => ({ id, name: id, shape: 'square', width_mm: 20, height_mm: 20, quantity: 2, enabled: true }));
  u.context.renderTemplates();
  u.get('librarySelection').value = 'first'; u.context.loadSelectedTemplate();
  u.get('librarySelection').value = 'second'; u.context.updateActiveTemplate();
  assert.equal(u.get('activeTemplate').textContent, 'first');
  assert.equal(u.context.appState.selectedTemplateId, 'first');
  assert.match(u.get('templateSelectionHint').textContent, /second is selected/);
  assert.equal(u.get('loadTemplate').disabled, false);
  u.context.loadSelectedTemplate();
  assert.equal(u.get('activeTemplate').textContent, 'second');
  assert.equal(u.get('loadTemplate').disabled, true);
  assert.match(u.get('templateSelectionHint').textContent, /Spacebar will use/);
});

test('card Use action loads its stable ID directly without changing quantity', () => {
  const u = ui();
  u.context.appState.templates = [{ id: 'legacy-73', name: 'Plate', shape: 'rectangle', width_mm: 20, height_mm: 30, quantity: 4, enabled: true }];
  u.context.renderTemplates();
  const card = u.get('templates').children[0];
  const actions = card.children[2]; actions.children[0].onclick();
  assert.equal(u.context.appState.selectedTemplateId, 'legacy-73');
  assert.equal(u.context.workflowTemplates()[0].quantity, 4);
  assert.match(u.get('activeTemplateDetail').textContent, /Quantity 4/);
});


test('export completion returns Ready and a second cycle requires a fresh user trigger', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg'; let starts = 0;
  u.context.api = async (url, options) => {
    if (options) starts++;
    return { id: 'generated-id', cycle_number: starts, busy: false, stage: 'Completed',
      result: { export: { path: '/tmp/cycle-' + starts + '.svg', url: '/data/cycle-' + starts + '.svg' }, nesting: { placements: [] } } };
  };
  u.context.loadCurrentState = async () => {};
  await u.context.runWorkflow();
  assert.equal(starts, 1);
  assert.equal(u.context.appState.workflowBusy, false);
  assert.equal(u.get('cycleState').textContent, 'Ready for cycle 2');
  assert.equal(u.get('cycleExportLink').href, '/data/cycle-1.svg');
  await new Promise(setImmediate); assert.equal(starts, 1);
  await u.context.runWorkflow();
  assert.equal(starts, 2);
  assert.equal(u.get('cycleState').textContent, 'Ready for cycle 3');
  assert.equal(u.get('cycleExportLink').href, '/data/cycle-2.svg');
});

test('stage history shows fast stages that polling missed and preserves export failure', () => {
  const u = ui();
  u.context.displayWorkflow({ id: 'job', cycle_number: 1, busy: false, stage: 'Failed', error: 'Exporting: disk full',
    events: [
      { stage: 'Capturing image', status: 'completed', elapsed_seconds: .1 },
      { stage: 'Correcting distortion', status: 'completed', elapsed_seconds: .1 },
      { stage: 'Detecting material and converting coordinates', status: 'completed', elapsed_seconds: .2 },
      { stage: 'Processing and applying nesting priority', status: 'completed', elapsed_seconds: .3 },
      { stage: 'Exporting', status: 'failed', elapsed_seconds: .1 },
    ] });
  assert.equal(u.get('cycleCapture').dataset.state, 'completed');
  assert.equal(u.get('cycleNesting').dataset.state, 'completed');
  assert.equal(u.get('cycleExport').dataset.state, 'failed');
  assert.equal(u.get('cycleResult').hidden, true);
  assert.equal(u.get('cycleState').textContent, 'Failed');
});

test('intermediate image views are enabled only after real artifacts are available', () => {
  const u = ui();
  u.context.displayWorkflow({ id: 'job', busy: true, stage: 'Correcting distortion', artifacts: { captured_image: '/data/original.jpg' } });
  assert.equal(u.get('cyclePreviewCapture').disabled, false);
  assert.equal(u.get('cyclePreviewMask').disabled, true);
  assert.equal(u.get('cyclePreviewLayout').disabled, true);
  u.context.displayWorkflow({ id: 'job', busy: true, stage: 'Processing and applying nesting priority', artifacts: {
    captured_image: '/data/original.jpg', corrected_image: '/data/corrected.jpg',
    detection: { overlay: '/data/contour.jpg', mask: '/data/mask.png' } } });
  assert.equal(u.get('cyclePreviewBoundary').disabled, false);
  assert.equal(u.get('cyclePreviewMask').disabled, false);
  u.context.showWorkflowPreview('mask');
  assert.equal(u.get('cameraStream').src, '/data/mask.png');
  assert.equal(u.context.appState.workflowPreview, 'mask');
  assert.equal(u.context.appState.workflowPreviewManual, true);
});


test('completed export resumes MJPEG and archived images remain selectable', () => {
  const u = ui();
  const status = { id: 'job', cycle_number: 1, busy: false, stage: 'Completed',
    artifacts: { captured_image: '/data/original.jpg', corrected_image: '/data/corrected.jpg', nesting: { placements: [] } },
    result: { nesting: { placements: [] }, export: { url: '/data/export.svg' } } };
  u.context.displayWorkflow(status);
  assert.equal(u.context.appState.workflowPreview, 'live');
  assert.match(u.get('cameraStream').src, /api\/camera\/stream/);
  u.context.showWorkflowPreview('corrected');
  assert.equal(u.get('cameraStream').src, '/data/corrected.jpg');
  assert.equal(u.context.appState.workflowPreview, 'corrected');
});

test('dimension panel shows aligned values and labels live values as last captured', () => {
  const u = ui();
  u.context.appState.workflowPreview = 'live';
  u.context.renderMeasurements({ width_mm: 285.5, height_mm: 273.7, aligned_width_mm: 272.1, aligned_height_mm: 264.6, area_m2: .0683 });
  assert.equal(u.get('dimensionWidth').textContent, '272.10 mm');
  assert.equal(u.get('dimensionHeight').textContent, '264.60 mm');
  assert.equal(u.get('dimensionContext').textContent, 'Last captured piece');
  assert.match(u.get('dimensionDetail').textContent, /285.50 \/ 273.70/);
});

test('legacy measurement data is labelled as X/Y spans instead of aligned dimensions', () => {
  const u = ui();
  u.context.renderMeasurements({ width_mm: 285.5, height_mm: 273.7, area_m2: .0683 });
  assert.equal(u.get('dimensionWidthLabel').textContent, 'Width (X span)');
  assert.equal(u.get('dimensionHeightLabel').textContent, 'Height (Y span)');
});

test('camera refresh reconnects once, preserves active settings and blocks new workflow until done', async () => {
  const u = ui(); let release; const gate = new Promise(resolve => release = resolve); let opens = 0;
  u.context.loadCameras = async () => gate;
  u.get('cameraSelect').options = [{ value: '2' }];
  u.context.api = async () => ({ connected: true, index: 2, width: 1280, height: 720, fps: 30 });
  u.context.openCamera = async () => { opens++; u.context.startLivePreview(); };
  const refresh = u.context.refreshCamera();
  assert.equal(u.context.appState.cameraRefreshing, true);
  await u.context.refreshCamera(); await u.context.runWorkflow();
  assert.equal(opens, 0);
  release(); await refresh;
  assert.equal(opens, 1);
  assert.equal(u.get('cameraSelect').value, '2');
  assert.equal(u.get('widthInput').value, '1280');
  assert.equal(u.get('heightInput').value, '720');
  assert.match(u.get('cameraStream').src, /api\/camera\/stream/);
  assert.equal(u.context.appState.cameraRefreshing, false);
});

test('camera refresh failure releases its flag and leaves reconnect available', async () => {
  const u = ui(); u.context.loadCameras = async () => { throw new Error('USB unavailable'); };
  await assert.rejects(u.context.refreshCamera(), /USB unavailable/);
  assert.equal(u.context.appState.cameraRefreshing, false);
  assert.equal(u.get('cycleRefreshCamera').disabled, false);
  assert.match(u.get('cyclePreviewLabel').textContent, /Camera refresh failed/);
});


test('Auto Nest opens the preview of the current captured piece immediately', async () => {
  const u = ui();
  const result = { placements: [{ polygon: [[0, 0], [20, 0], [20, 20], [0, 20]], template_id: 'one', valid: true }],
    sheath_polygon: [[0, 0], [60, 0], [60, 50], [0, 50]], utilization: .5, waste: .5, requested_pieces: 1 };
  const calls = [];
  u.context.api = async (url) => { calls.push(url); return url === '/api/nest' ? result : { image: '/data/current/corrected.jpg' }; };
  await u.context.autoNest();
  assert.deepEqual(calls, ['/api/nest', '/api/state']);
  assert.equal(u.context.appState.workflowPreview, 'layout');
  assert.equal(u.get('cameraStream').src, '/data/current/corrected.jpg');
  assert.equal(u.get('nestingPreviewCard').hidden, false);
  assert.match(u.get('nestingPreviewSummary').textContent, /1 placed pieces/);
  assert.equal(u.context.appState.placements[0].template_id, 'one');
});

test('automatic nesting preview remains visible after export resumes the live camera', () => {
  const u = ui();
  const nesting = { placements: [{ polygon: [[0, 0], [20, 0], [20, 20], [0, 20]], valid: true }], utilization: .5, waste: .5 };
  const artifacts = { corrected_image: '/data/corrected.jpg', nesting };
  u.context.displayWorkflow({ id: 'job', stage: 'Exporting', busy: true, artifacts });
  assert.equal(u.context.appState.workflowPreview, 'layout');
  assert.equal(u.get('nestingPreviewCard').hidden, false);
  u.context.displayWorkflow({ id: 'job', stage: 'Completed', busy: false, artifacts,
    result: { nesting, export: { url: '/data/layout.svg' } } });
  assert.equal(u.context.appState.workflowPreview, 'live');
  assert.equal(u.get('nestingPreviewCard').hidden, false);
  assert.match(u.get('nestingPreviewSummary').textContent, /1 placed pieces/);
});

test('nesting preview works without a photo as a vector layout', () => {
  const u = ui();
  u.context.appState.workflowArtifacts = { nesting: { placements: [{ polygon: [[0, 0], [20, 0], [20, 20], [0, 20]] }] } };
  u.context.showWorkflowPreview('layout');
  assert.equal(u.context.appState.workflowPreview, 'layout');
  assert.equal(u.get('cameraStream').src, '');
  assert.equal(u.context.canProjectLayoutOnPhoto(), false);
  assert.equal(u.get('cyclePreviewLabel').textContent, 'Nesting layout in millimetres');
});


test('two Spacebar cycles export once each and restore camera focus after cycle actions', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg'; let starts = 0;
  u.context.loadCurrentState = async () => {};
  u.context.api = async (url, options) => {
    if (options) {
      starts++;
      // A preview action leaves its button focused during the first cycle.
      u.document.activeElement = { closest: (query) => query.includes('[data-cycle-preview]') ? {} : null };
      return { id: 'generated-id', busy: true, stage: 'Exporting' };
    }
    return { id: 'generated-id', cycle_number: starts, busy: false, stage: 'Completed',
      result: { export: { path: '/tmp/cycle-' + starts + '.svg', url: '/data/cycle-' + starts + '.svg' }, nesting: { placements: [] } } };
  };
  for (let cycle = 1; cycle <= 2; cycle++) {
    const key = space({ target: u.get('overlay') });
    u.context.workflowKeydown(key);
    assert.equal(key.prevented, true);
    u.context.workflowKeydown(space({ repeat: true }));
    u.context.workflowKeydown(space());
    for (let i = 0; i < 10 && u.context.appState.workflowBusy; i++) await new Promise(setImmediate);
    assert.equal(u.context.appState.workflowBusy, false);
    assert.equal(starts, cycle);
    assert.equal(u.document.activeElement, u.get('overlay'));
    assert.equal(u.get('cycleState').textContent, 'Ready for cycle ' + (cycle + 1));
    assert.equal(u.get('cycleExportLink').href, '/data/cycle-' + cycle + '.svg');
  }
});

test('clicking the camera workspace clears control focus so Space can start again', () => {
  const u = ui(); let starts = 0;
  u.context.runWorkflow = () => starts++;
  u.context.bindEvents();
  u.document.activeElement = { closest: () => ({}) };
  u.context.workflowKeydown(space());
  assert.equal(starts, 0);
  u.get('overlay').pointerdown();
  assert.equal(u.document.activeElement, u.get('overlay'));
  u.context.workflowKeydown(space({ target: u.get('overlay') }));
  assert.equal(starts, 1);
});

test('cycle focus restoration preserves editors, inactive windows and calibration editing', () => {
  const u = ui();
  for (const selector of ['input', 'select', '[contenteditable]', '[role="combobox"]']) {
    const editor = { closest: (query) => query.includes(selector) ? {} : null };
    u.document.activeElement = editor;
    u.context.restoreWorkflowFocus();
    assert.equal(u.document.activeElement, editor);
  }
  u.document.activeElement = u.get('processSheath');
  u.document.hasFocus = () => false; u.context.restoreWorkflowFocus();
  assert.equal(u.document.activeElement, u.get('processSheath'));
  u.document.hasFocus = () => true; u.document.hidden = true; u.context.restoreWorkflowFocus();
  assert.equal(u.document.activeElement, u.get('processSheath'));
  u.document.hidden = false; u.context.appState.editBoundary = true; u.context.restoreWorkflowFocus();
  assert.equal(u.document.activeElement, u.get('processSheath'));
  u.context.appState.editBoundary = false; u.context.appState.activeView = 'calibration'; u.context.restoreWorkflowFocus();
  assert.equal(u.document.activeElement, u.get('processSheath'));
});

test('failed cycle releases its lock and restores Start button focus for a Spacebar retry', async () => {
  const u = ui(); u.get('workflowFormat').value = 'svg';
  u.document.activeElement = u.get('processSheath');
  u.context.api = async () => { throw Object.assign(new Error('Capture failed'), { status: 400 }); };
  await u.context.runWorkflow();
  assert.equal(u.context.appState.workflowBusy, false);
  assert.equal(u.document.activeElement, u.get('overlay'));
  let retries = 0; u.context.runWorkflow = () => retries++;
  u.context.workflowKeydown(space({ target: u.get('overlay') }));
  assert.equal(retries, 1);
  assert.equal(u.context.logs.some(x => x.text.startsWith('Completed:')), false);
});


test('preview legend maps original piece numbers to saved inch sizes without changing placements', () => {
  const u = ui(), numbers = [];
  u.get('nestingPreviewCanvas').getContext = () => ({ beginPath() {}, clearRect() {}, fillText(text) { numbers.push(text); } });
  u.context.drawPolygon = () => {};
  const result = { placements: ['7', '3', '4', '7'].map(size => ({
    template_id: size + 'in-square', template_name: size + '" square',
    polygon: [[0, 0], [20, 0], [20, 20], [0, 20]], valid: true,
  })) };
  const before = JSON.stringify(result);
  u.context.renderNestingPreview(result);
  assert.deepEqual(numbers, ['1', '2', '3', '4']);
  assert.deepEqual(u.get('nestingPreviewLegend').children.map(item => item.textContent),
    ['1 → 7 inch square', '2 → 3 inch square', '3 → 4 inch square', '4 → 7 inch square']);
  assert.equal(JSON.stringify(result), before);
  u.context.appState.templates = [{ id: '7in-square', name: 'Changed library name' }];
  u.context.renderNestingPreview(result);
  assert.equal(u.get('nestingPreviewLegend').children[0].textContent, '1 → 7 inch square');
});

test('preview legend supports legacy metadata and refreshes without stale entries', () => {
  const u = ui();
  u.context.appState.templates = [{ id: 'legacy', name: '2.5" circle' }];
  u.context.renderNestingPreview({ placements: [{ template_id: 'legacy' }, { template_id: 'missing' }, { template_name: '<custom plate>' }] });
  assert.deepEqual(u.get('nestingPreviewLegend').children.map(item => item.textContent),
    ['1 → 2.5 inch circle', '2 → Size unavailable', '3 → <custom plate>']);
  u.context.renderNestingPreview({ placements: [{ template_name: '4" square' }] });
  assert.deepEqual(u.get('nestingPreviewLegend').children.map(item => item.textContent), ['1 → 4 inch square']);
});


test('mouse clicks on Refresh, USB Open and other action buttons restore Spacebar capture focus', () => {
  const u = ui(); let starts = 0;
  u.context.runWorkflow = () => starts++;
  for (const id of ['refreshCameras', 'cycleRefreshCamera', 'openCamera', 'openMachineCamera', 'captureImage', 'autoNest', 'loadTemplate']) {
    const button = u.get(id);
    u.document.activeElement = button;
    u.context.workflowActionClick({ detail: 1, target: { closest: () => button } });
    assert.equal(u.document.activeElement, u.get('overlay'), id);
    u.context.workflowKeydown(space({ target: u.get('overlay') }));
  }
  assert.equal(starts, 7);
});

test('mouse action focus preserves keyboard activation, dialogs, editors and calibration', () => {
  const u = ui(), button = u.get('openMachineCamera');
  const click = { detail: 1, target: { closest: () => button } };
  u.document.activeElement = button;
  u.context.workflowActionClick({ ...click, detail: 0 });
  assert.equal(u.document.activeElement, button);
  u.context.workflowActionClick({ ...click, defaultPrevented: true });
  assert.equal(u.document.activeElement, button);
  button.closest = () => ({}); u.context.workflowActionClick(click);
  assert.equal(u.document.activeElement, button);
  button.closest = () => null;
  const editor = { closest: () => null }; u.document.activeElement = editor;
  u.context.workflowActionClick(click); assert.equal(u.document.activeElement, editor);
  u.document.activeElement = button; u.context.appState.activeView = 'calibration';
  u.context.workflowActionClick(click); assert.equal(u.document.activeElement, button);
});

test('mouse focus handler is registered once and removed with keyboard handler', () => {
  const u = ui();
  u.context.registerWorkflowKeys(); u.context.registerWorkflowKeys();
  assert.equal(u.listeners.get('click').size, 1);
  assert.equal(u.listeners.get('keydown').size, 1);
  u.context.unregisterWorkflowKeys();
  assert.equal(u.listeners.get('click').size, 0);
  assert.equal(u.listeners.get('keydown').size, 0);
});

test('returning mouse action focus does not bypass camera-refresh and processing locks', () => {
  const u = ui(); let starts = 0; u.context.runWorkflow = () => starts++;
  for (const flag of ['cameraRefreshing', 'workflowBusy', 'pendingMutations']) {
    const button = u.get('cycleRefreshCamera'); u.document.activeElement = button;
    u.context.appState[flag] = true;
    u.context.workflowActionClick({ detail: 1, target: { closest: () => button } });
    u.context.workflowKeydown(space({ target: u.get('overlay') }));
    assert.equal(starts, 0);
    u.context.appState[flag] = false;
  }
  u.context.workflowKeydown(space({ target: u.get('overlay') }));
  assert.equal(starts, 1);
});


test('rectangle direction saves existing rotation fields with dimensions and priority unchanged', () => {
  const u = ui();
  const previous = { priority: 3, min_spacing_mm: 2, enabled: true };
  const values = { id: 'rect', name: 'Plate', shape: 'rectangle', unit: 'in', width: '7', height: '3', quantity: '2', rotate: true };
  for (const [orientation, expected] of [['horizontal', [0]], ['vertical', [90]], ['both', [0, 90]]]) {
    const template = u.context.preferenceTemplate({ ...values, orientation }, previous);
    assert.deepEqual(Array.from(template.allowed_rotations), expected);
    assert.ok(Math.abs(template.width_mm - 177.8) < 1e-6);
    assert.ok(Math.abs(template.height_mm - 76.2) < 1e-6);
    assert.equal(template.priority, 3); assert.equal(template.quantity, 2);
  }
  assert.throws(() => u.context.preferenceTemplate({ ...values, orientation: 'invalid' }), /direction/);
});

test('editing old rectangle orientations restores selection and retains custom angles', () => {
  const u = ui();
  for (const [rotations, value] of [[[0], 'horizontal'], [[90], 'vertical'], [[90, 0], 'both'], [[17], 'custom']]) {
    u.context.appState.templates = [{ id: 'rect', name: 'Rectangle', shape: 'rectangle', width_mm: 70, height_mm: 30, quantity: 1, allowed_rotations: rotations }];
    u.context.editPreferredTemplate('rect', false);
    assert.equal(u.get('preferredOrientation').value, value);
    assert.equal(u.get('preferredOrientationField').hidden, false);
    assert.equal(u.get('preferredRotateField').hidden, true);
  }
  const result = u.context.preferenceTemplate({ id: 'rect', name: 'Rectangle', shape: 'rectangle', unit: 'mm', width: 70, height: 30, quantity: 1, orientation: 'custom' }, { allowed_rotations: [17] });
  assert.deepEqual(Array.from(result.allowed_rotations), [17]);
  u.get('preferredShape').value = 'square'; u.context.updatePreferenceFields();
  assert.equal(u.get('preferredOrientationField').hidden, true);
  assert.equal(u.get('preferredRotateField').hidden, false);
});

test('Add rectangle submits vertical-only rotation through the existing template save API', async () => {
  const u = ui(); u.context.bindEvents();
  for (const [id, value] of Object.entries({ preferredShape: 'rectangle', preferredUnits: 'mm', preferredWidth: '70', preferredHeight: '30', preferredQuantity: '1', preferredName: 'Vertical plate', preferredOrientation: 'vertical' })) u.get(id).value = value;
  let saved;
  u.context.api = async (url, options) => { assert.equal(url, '/api/templates'); saved = JSON.parse(options.body); return {}; };
  await u.get('templatePreferenceForm').onsubmit({ preventDefault() {} });
  assert.deepEqual(saved[0].allowed_rotations, [90]);
  assert.equal(u.context.appState.templates[0].width_mm, 70);
  assert.equal(u.get('preferredOrientation').value, 'vertical');
});


function materialPieces() {
  return [
    { id: 'sheath-1', mm_polygon: [[0,0],[100,0],[100,80],[0,80]], pixel_polygon: [[0,0],[100,0],[100,80],[0,80]], measurements: { width_mm: 100, height_mm: 80, aligned_width_mm: 100, aligned_height_mm: 80, area_m2: .008 } },
    { id: 'sheath-2', mm_polygon: [[150,0],[210,0],[210,50],[150,50]], pixel_polygon: [[150,0],[210,0],[210,50],[150,50]], measurements: { width_mm: 60, height_mm: 50, aligned_width_mm: 60, aligned_height_mm: 50, area_m2: .003 } },
  ];
}

test('multiple sheath dimensions are listed separately and selecting one preserves template selection', () => {
  const u = ui(); u.context.appState.selectedTemplateId = 'selected-template';
  const pieces = materialPieces(); u.context.renderSheaths(pieces);
  assert.equal(u.get('sheathMeasurements').hidden, false);
  assert.match(u.get('sheathMeasurementList').children[0].textContent, /100.00 × 80.00 mm/);
  assert.match(u.get('sheathMeasurementList').children[1].textContent, /60.00 × 50.00 mm/);
  u.context.selectSheath('sheath-2');
  assert.equal(u.get('dimensionWidth').textContent, '60.00 mm');
  assert.equal(u.get('dimensionHeight').textContent, '50.00 mm');
  assert.match(u.get('dimensionContext').textContent, /Sheath 2/);
  assert.equal(u.context.appState.selectedTemplateId, 'selected-template');
  assert.equal('sheath_polygon' in u.context.nestingPayload(), false);
  u.context.updateCycleVisuals({ artifacts: { detection: { sheaths: pieces, mm_polygon: pieces[0].mm_polygon, pixel_polygon: pieces[0].pixel_polygon }, nesting: { sheaths: pieces, sheath_polygon: pieces[0].mm_polygon, placements: [] } } });
  assert.deepEqual(u.context.appState.sheathMm, pieces[1].mm_polygon);
});

test('preview draws distinct material outlines and legend links cuts to their sheath', () => {
  const u = ui(), outlines = [], numbers = [], pieces = materialPieces();
  u.context.renderSheaths(pieces);
  u.get('nestingPreviewCanvas').getContext = () => ({ beginPath() {}, clearRect() {}, fillText(text) { numbers.push(text); } });
  u.context.drawPolygon = (polygon) => outlines.push(polygon);
  const placements = pieces.map(piece => ({ sheath_id: piece.id, template_name: '3" square', polygon: piece.mm_polygon, valid: true }));
  u.context.renderNestingPreview({ sheaths: pieces, sheath_polygons: pieces.map(piece => piece.mm_polygon), placements });
  assert.deepEqual(outlines.slice(0,2), pieces.map(piece => piece.mm_polygon));
  assert.deepEqual(numbers, ['1','2']);
  assert.deepEqual(u.get('nestingPreviewLegend').children.map(row => row.textContent), ['1 → 3 inch square / Sheath 1', '2 → 3 inch square / Sheath 2']);
  assert.equal(u.context.worldBounds().maxX, 210);
});

test('new capture clears multi-sheath dimensions and one-sheath view retains the original layout', () => {
  const u = ui();
  u.context.renderSheaths(materialPieces()); u.context.resetAnalysisState();
  assert.equal(u.context.appState.sheaths.length, 0);
  assert.equal(u.get('sheathMeasurements').hidden, true);
  u.context.renderSheaths([materialPieces()[0]]);
  assert.equal(u.get('sheathMeasurements').hidden, true);
  assert.equal(u.get('dimensionWidth').textContent, '100.00 mm');
});


test('workflow status reports the active sheath without implying export completion', () => {
  const u = ui();
  u.context.displayWorkflow({ stage: 'Processing and applying nesting priority', busy: true, sheath_index: 2, sheath_count: 3 });
  assert.match(u.get('cycleNextHelp').textContent, /Processing sheath 2 of 3/);
  assert.equal(u.get('cycleResult').hidden, true);
});


test('completed exports expose CSV download links and a failed cycle hides the previous CSV', () => {
  const u = ui();
  const output = { path: '/tmp/cuts.dxf', url: '/data/cuts.dxf', csv: { path: '/tmp/cuts.csv', url: '/data/cuts.csv' } };
  u.context.renderExportLinks(output);
  assert.equal(u.get('exportResult').children[0].href, '/data/cuts.dxf');
  assert.equal(u.get('exportResult').children[2].href, '/data/cuts.csv');
  u.context.displayWorkflow({ stage: 'Completed', busy: false, result: { export: output, nesting: { placements: [] } } });
  assert.equal(u.get('cycleCsvLink').hidden, false);
  assert.equal(u.get('cycleCsvLink').href, '/data/cuts.csv');
  u.context.displayWorkflow({ stage: 'Failed', busy: false, error: 'CSV save failed' });
  assert.equal(u.get('cycleCsvLink').hidden, true);
});

test('dimensions display two decimal places without rounding the underlying measurement', () => {
  const u = ui();
  const measurement = { width_mm: 263.123456789, height_mm: 262.987654321, aligned_width_mm: 263.123456789, aligned_height_mm: 262.987654321, area_m2: .0695 };
  u.context.renderMeasurements(measurement);
  assert.equal(u.get('dimensionWidth').textContent, '263.12 mm');
  assert.equal(u.get('dimensionHeight').textContent, '262.99 mm');
  assert.equal(u.context.appState.measurements.aligned_width_mm, 263.123456789);
});


test('mouse CSV download returns focus to the workspace for the next Spacebar cycle', () => {
  const u = ui(), link = u.get('cycleCsvLink');
  u.document.activeElement = link;
  u.context.workflowActionClick({ detail: 1, target: { closest: () => link } });
  assert.equal(u.document.activeElement, u.get('overlay'));
  let starts = 0; u.context.runWorkflow = () => starts++;
  u.context.workflowKeydown(space({ target: u.get('overlay') }));
  assert.equal(starts, 1);
});


test('header keeps return and dropdown controls and the original Space workflow', () => {
  const root = path.join(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
  const old = fs.readFileSync(path.join(root, 'verification/ui-redesign-backup/index.html'), 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
  assert.equal(new Set(ids).size, ids.length, 'no duplicate controls');
  for (const match of old.matchAll(/\bid="([^"]+)"/g)) if (match[1] !== "runWorkflow") assert.ok(ids.includes(match[1]), match[1]);
  assert.deepEqual([...html.matchAll(/data-view="([^"]+)"/g)].map(m => m[1]), ['camera','calibration','sheath','templates','nesting','cutting-time','export','lasercad','laser-control','settings','data']);
  assert.match(html, /<header[\s\S]*id="processSheath"[\s\S]*id="processSections"[\s\S]*<\/header>/);
  assert.ok(!html.includes('id="runWorkflow"'));
  assert.match(html, /SHEATH TEMPLATE CUTTING/);
  const js = fs.readFileSync(path.join(root,'frontend/app.js'), 'utf8');
  assert.ok(js.includes("void runWorkflow();"));
  assert.ok(!js.includes('$("runWorkflow").onclick'));
  const previous = fs.readFileSync(path.join(root,'verification/ui-redesign-backup/app.js'), 'utf8');
  const workflow = source => source.slice(source.indexOf('async function runWorkflow()'), source.indexOf('function restoreWorkflowFocus')).replace(/\r\n/g, '\n').replace("  if (!hasPermission('operate')) return;\n", '');
  assert.equal(workflow(js), workflow(previous), 'automatic workflow function unchanged after the access check');
});

test('all eight sections and Process Sheath navigate without workflow or camera calls', async () => {
  const u=ui(); u.document.body={dataset:{}};
  const views=['camera','calibration','sheath','templates','nesting','cutting-time','export','settings','data'];
  const tabs=views.map(view=>{const n=u.get('tab-'+view);n.dataset.view=view;return n;});
  const panels=views.map(view=>{const n=u.get('panel-'+view);n.dataset.panel=view;n.classList.toggle=(key,active)=>n.active=active;return n;});
  u.document.querySelectorAll=selector=>selector==='nav button'?tabs:selector==='.panel-view'?panels:[];
  let starts=0, cameraCalls=0, reads=0;
  u.context.runWorkflow=()=>starts++;
  u.context.startLivePreview=()=>cameraCalls++;
  u.context.api=async(path,options)=>{assert.equal(path,'/api/projects');assert.equal(options,undefined);reads++;return {projects:[]};};
  Object.assign(u.context.appState,{selectedTemplateId:'keep-me',templates:[{id:'keep-me'}],placements:[{polygon:[[1,2]]}],scale:{homography:[[1]]},workflowPreview:'corrected',scalePoints:[[5,6]],editBoundary:true});
  u.get('preferredName').value='unsaved name';
  const before=JSON.stringify(u.context.appState);
  for(const view of views){
    u.context.navigateSection(view);
    assert.equal(u.document.body.dataset.screen,view);
    assert.equal(panels.filter(p=>p.active).length,1);
    assert.equal(panels.find(p=>p.active).dataset.panel,view);
    u.context.navigateSection('process');
    assert.equal(u.document.body.dataset.screen,'process');
    assert.equal(panels.filter(p=>p.active).length,0);
  }
  await Promise.resolve();
  const after={...u.context.appState,activeView:'camera'};
  assert.equal(JSON.stringify(after),before);
  assert.equal(u.get('preferredName').value,'unsaved name');
  assert.equal(starts,0);assert.equal(cameraCalls,0);assert.equal(reads,1);
});

test('Data uses the existing read-only project endpoint and handles storage errors', async()=>{
  const u=ui();
  u.context.api=async(url,options)=>{assert.equal(url,'/api/projects');assert.equal(options,undefined);return {projects:['saved-a','saved-b']};};
  await u.context.refreshDataProjects();
  assert.deepEqual(u.get('dataProjects').children.map(n=>n.textContent),['saved-a','saved-b']);
  u.context.api=async()=>{throw new Error('storage unavailable');};
  await u.context.refreshDataProjects();
  assert.match(u.get('dataProjects').textContent,/storage unavailable/);
  assert.equal(u.context.logs.at(-1).error,true);
});

test('Capture, camera refresh and export-format controls keep the existing busy lock',()=>{
  const u=ui(); const controls=['cycleRefreshCamera','captureImage','workflowFormat'].map(id=>u.get(id));
  u.document.querySelectorAll=selector=>selector.includes('#captureImage')?controls:[];
  u.context.setWorkflowBusy(true);
  controls.forEach(c=>assert.equal(c.disabled,true));
  u.context.setWorkflowBusy(false);
  controls.forEach(c=>assert.equal(c.disabled,false));
});

test('nesting empty state becomes a real numbered preview only when placements exist',()=>{
  const u=ui();u.get('nestingPreviewEmpty').hidden=false;u.get('nestingPreviewCanvas').hidden=true;u.get('openNestingPreview').disabled=true;
  u.context.renderNestingPreview({placements:[]});
  assert.equal(u.get('nestingPreviewEmpty').hidden,false);
  u.context.renderNestingPreview({placements:[{template_name:'7 inch',polygon:[[0,0],[1,0],[1,1]]}],utilization:.5});
  assert.equal(u.get('nestingPreviewEmpty').hidden,true);
  assert.equal(u.get('nestingPreviewCanvas').hidden,false);
  assert.equal(u.get('openNestingPreview').disabled,false);
  assert.match(u.get('nestingPreviewLegend').children[0].textContent,/1 → 7 inch/);
});


test('Data preserves the real export download links and displays measured values only',()=>{
  const u=ui();
  u.context.renderDataSummary(); assert.equal(u.get('dataMeasurements').textContent,'No piece measured yet.');
  u.context.appState.measurements={aligned_width_mm:263,aligned_height_mm:262};
  u.get('dimensionContext').textContent='Captured piece';u.get('dimensionWidth').textContent='263.00 mm';u.get('dimensionHeight').textContent='262.00 mm';u.get('dimensionArea').textContent='0.06 m²';
  const link={href:'/data/actual.csv',download:true};link.cloneNode=()=>({...link});u.get('exportResult').children=[link];
  u.context.renderDataSummary();
  assert.match(u.get('dataMeasurements').textContent,/263.00 mm × 262.00 mm/);
  assert.equal(u.get('dataExport').children[0].href,'/data/actual.csv');assert.equal(u.get('dataExport').children[0].download,true);
  assert.equal(u.get('exportResult').children[0],link);
});

test('Data Save Project reuses the original storage endpoint and surfaces errors',async()=>{
  const u=ui();u.context.bindEvents();u.context.appState.activeView='data';
  const requests=[];u.context.api=async(url,options)=>{requests.push({url,options});return url==='/api/project/save'?{project_dir:'saved-current'}:{projects:['saved-current']};};
  await u.get('saveProject').onclick();
  assert.equal(requests[0].url,'/api/project/save');assert.equal(requests[0].options.method,'POST');assert.equal(requests[0].options.body,'{}');
  assert.equal(requests[1].url,'/api/projects');assert.equal(u.get('dataProjects').children[0].textContent,'saved-current');
  u.context.api=async()=>{throw new Error('save denied');};await u.get('saveProject').onclick();
  assert.equal(u.context.logs.at(-1).text,'save denied');assert.equal(u.context.logs.at(-1).error,true);
});


test('Process dropdown contains all sections and closes on navigation without running processing',()=>{
  const u=ui();let runs=0;u.context.runWorkflow=()=>runs++;
  u.get('processSections').open=true;u.context.navigateSection('templates');
  assert.equal(u.get('processSections').open,false);assert.equal(u.get('currentSectionLabel').textContent,'Templates');assert.equal(runs,0);
  u.get('processSections').open=true;u.context.navigateSection('process');
  assert.equal(u.get('processSections').open,false);assert.equal(u.get('currentSectionLabel').textContent,'Processing workspace');assert.equal(runs,0);
  const html=fs.readFileSync(path.join(__dirname,'../frontend/index.html'),'utf8');
  const menu=html.slice(html.indexOf('<details id="processSections"'),html.indexOf('</details>',html.indexOf('<details id="processSections"')));
  assert.deepEqual([...menu.matchAll(/data-view="([^"]+)"/g)].map(m=>m[1]),['camera','calibration','sheath','templates','nesting','cutting-time','export','lasercad','laser-control','settings','data']);
  assert.ok(!menu.includes('id="runWorkflow"'),'Process action is independent of menu');
});


test('single visible status box advances through actual stages and handles completion, failure and retry',()=>{
 const u=ui();const stages=['Capturing image','Correcting distortion','Detecting material and converting coordinates','Processing and applying nesting priority','Exporting'];
 stages.forEach((stage,index)=>{u.context.displayWorkflow({busy:true,stage});assert.equal(u.get('processStageTitle').textContent,stage);assert.equal(u.get('processStageStep').textContent,`Step ${index+1} of 5`);assert.equal(u.get('processStageBox').dataset.state,'running');});
 u.context.displayWorkflow({stage:'Failed',busy:false,error:'Exporting: disk full'});assert.equal(u.get('processStageBox').dataset.state,'failed');assert.match(u.get('processStageDetail').textContent,/disk full/);
 u.context.displayWorkflow({stage:'Capturing image',busy:true});assert.equal(u.get('processStageStep').textContent,'Step 1 of 5');
 u.context.displayWorkflow({stage:'Completed',busy:false,result:{export:{url:'/data/real.svg'},nesting:{placements:[]}}});assert.equal(u.get('processStageTitle').textContent,'Export completed');assert.equal(u.get('processStageBox').dataset.state,'completed');
 u.context.displayWorkflow({stage:'Ready',busy:false});assert.equal(u.get('processStageTitle').textContent,'Ready');
 const html=fs.readFileSync(path.join(__dirname,'../frontend/index.html'),'utf8');assert.match(html,/<ol class="cycle-steps" hidden aria-hidden="true">/);assert.equal((html.match(/id="processStageBox"/g)||[]).length,1);
});


test('full-page Camera navigation preserves the existing stream, zoom and unsaved controls',()=>{
 const u=ui();u.document.body={dataset:{}};let cameraCalls=0;
 u.context.startLivePreview=()=>cameraCalls++;u.context.api=async()=>{throw new Error('Navigation must not call camera API');};
 u.get('cameraStream').src='/api/camera/stream?existing-session';u.get('widthInput').value='1280';u.get('preferredName').value='unsaved template';
 u.context.appState.viewZoom=2;u.context.appState.workflowPreview='corrected';
 u.context.navigateSection('camera');assert.equal(u.document.body.dataset.screen,'camera');assert.equal(u.get('workspacePreviewTitle').textContent,'Camera Preview');
 u.context.navigateSection('calibration');assert.equal(u.get('workspacePreviewTitle').textContent,'Calibration Image');
 u.context.navigateSection('process');assert.equal(u.get('workspacePreviewTitle').textContent,'Live Preview');
 assert.equal(u.get('cameraStream').src,'/api/camera/stream?existing-session');assert.equal(u.context.appState.viewZoom,2);assert.equal(u.context.appState.workflowPreview,'corrected');assert.equal(u.get('preferredName').value,'unsaved template');assert.equal(cameraCalls,0);
});


function liveProjectionUI() {
 const u=ui();Object.assign(u.context.appState,{workflowPreview:'live',activeView:'process',imageNatural:{width:1280,height:720},scale:{image_width:1280,image_height:720,homography:[[2,0,10],[0,2,20],[0,0,1]]}});
 u.get('showLiveNesting').checked=true;u.get('cameraStream').naturalWidth=1280;u.get('cameraStream').naturalHeight=720;u.get('overlay').width=1280;u.get('overlay').height=720;return u;
}

test('live nesting projection reuses calibrated coordinates, crop and zoom without changing placements',()=>{
 const u=liveProjectionUI();assert.deepEqual(Array.from(u.context.liveNestingMapper()([30,60])),[10,20]);
 u.context.appState.previewCrop={green_found:true,x:100,y:50,width:640,height:360};
 assert.deepEqual(Array.from(u.context.liveNestingMapper()([230,160])),[20,40]);
 u.context.appState.viewZoom=2;assert.deepEqual(Array.from(u.context.liveNestingMapper()([230,160])),[-600,-280]);
 const before=JSON.stringify(u.context.appState.scale);u.context.liveNestingMapper()([0,0]);assert.equal(JSON.stringify(u.context.appState.scale),before);
 u.context.appState.previewCrop=null;u.context.appState.viewZoom=1;u.context.appState.scale={mm_per_pixel:2};assert.deepEqual(Array.from(u.context.liveNestingMapper()([100,60])),[50,30]);
});

test('live overlay refuses mismatched resolution, invalid scale and calibration editing and respects its toggle',()=>{
 const u=liveProjectionUI();u.get('cameraStream').naturalWidth=640;assert.equal(u.context.liveNestingMapper(),null);u.get('cameraStream').naturalWidth=1280;
 const scale=u.context.appState.scale;u.context.appState.scale={homography:[[0,0,0],[0,0,0],[0,0,0]]};assert.equal(u.context.liveNestingMapper(),null);
 u.context.appState.scale=null;assert.equal(u.context.liveNestingMapper(),null);u.context.appState.scale=scale;
 u.get('showLiveNesting').checked=false;assert.equal(u.context.liveNestingMapper(),null);u.get('showLiveNesting').checked=true;
 u.context.appState.activeView='calibration';assert.equal(u.context.liveNestingMapper(),null);u.context.appState.activeView='process';
 u.context.appState.editBoundary=true;assert.equal(u.context.liveNestingMapper(),null);u.context.appState.editBoundary=false;
 u.context.appState.workflowPreview='capture';assert.equal(u.context.liveNestingMapper(),null);
});

test('live nesting draws both sheaths with original numbering and cannot mutate geometry or exports',()=>{
 const u=liveProjectionUI();u.context.appState.placements=[{template_id:'a',sheath_id:'sheath-1',polygon:[[30,60],[50,60],[50,80]],x_mm:40,y_mm:70},{template_id:'b',sheath_id:'sheath-2',polygon:[[90,60],[110,60],[110,80]],x_mm:100,y_mm:70}];
 const before=JSON.stringify(u.context.appState.placements),polygons=[],labels=[];let requests=0;
 u.context.api=async()=>{requests++;};u.context.drawPolygon=(points)=>polygons.push(points);u.context.drawPlacementLabel=(label)=>labels.push(label);
 u.context.drawLiveNestingOverlay();assert.equal(polygons.length,2);assert.deepEqual(labels,['1','2']);assert.deepEqual(Array.from(polygons[0][0]),[10,20]);assert.equal(JSON.stringify(u.context.appState.placements),before);assert.equal(requests,0);
 u.context.updateLiveNestingNotice();assert.equal(u.get('liveNestingInfo').hidden,true);assert.equal(u.get('liveNestingInfo').textContent,'');
 u.get('cameraStream').naturalWidth=640;u.context.updateLiveNestingNotice();assert.equal(u.get('liveNestingInfo').hidden,false);assert.match(u.get('liveNestingInfo').textContent,/unavailable/);
 u.context.appState.placements=[];u.context.updateLiveNestingNotice();assert.equal(u.get('liveNestingInfo').hidden,true);
});

test('workflow duration formatting handles zero, minute/hour rollover and missing times',()=>{
 const u=ui();for(const [input,expected] of [[0,'00:00.0'],[65.24,'01:05.2'],[59.96,'01:00.0'],[3599.96,'1:00:00.0'],[-1,'—'],[undefined,'—'],[NaN,'—']])assert.equal(u.context.formatWorkflowDuration(input),expected);
});

test('each stage timer and cycle total come only from measured worker events',()=>{
 const u=ui();const events=[{stage:'Capturing image',status:'completed',elapsed_seconds:1.25},{stage:'Correcting distortion',status:'completed',elapsed_seconds:0},{stage:'Detecting material and converting coordinates',status:'running',elapsed_seconds:12.34}];
 u.context.renderWorkflowTimings({stage:events[2].stage,busy:true,events});
 assert.equal(u.get('workflowCurrentTimer').textContent,'Stage: 00:12.3');assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: 00:13.6');
 assert.equal(u.get('workflowStageTimers').children.length,5);assert.equal(u.get('workflowStageTimers').children[1].children[1].textContent,'00:00.0');assert.equal(u.get('workflowStageTimers').children[3].children[1].textContent,'—');
 events[2].elapsed_seconds=12.98;u.context.renderWorkflowTimings({stage:events[2].stage,busy:true,events});assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: 00:14.2');
 events[2].status='failed';u.context.renderWorkflowTimings({stage:'Failed',busy:false,events});assert.equal(u.get('workflowCurrentTimer').textContent,'Last stage: 00:13.0');assert.match(u.get('workflowStageTimers').children[2].children[1].textContent,/failed/);
 u.context.renderWorkflowTimings({stage:'Capturing image',busy:true,events:[{stage:'Capturing image',status:'running',elapsed_seconds:0}]});assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: 00:00.0');assert.equal(u.get('workflowStageTimers').children[2].children[1].textContent,'—');
 u.context.renderWorkflowTimings({stage:'Ready',busy:false});assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: —');
});

test('completed timer values stop at worker totals and do not imply completion during export',()=>{
 const u=ui();const names=['Capturing image','Correcting distortion','Detecting material and converting coordinates','Processing and applying nesting priority','Exporting'];
 const events=names.map((stage,i)=>({stage,status:i===4?'running':'completed',elapsed_seconds:i+1}));
 u.context.displayWorkflow({stage:'Exporting',busy:true,events});assert.equal(u.get('processStageBox').dataset.state,'running');assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: 00:15.0');
 events[4].status='completed';const complete={stage:'Completed',busy:false,events,result:{export:{url:'/data/real.svg'},nesting:{placements:[]}}};
 u.context.displayWorkflow(complete);assert.equal(u.get('workflowCurrentTimer').textContent,'Last stage: 00:05.0');assert.equal(u.get('processStageBox').dataset.state,'completed');
 u.context.displayWorkflow(complete);assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: 00:15.0');
});


test('live overlay checkbox redraws the existing preview without camera calls',()=>{
 const u=liveProjectionUI();let draws=0;u.context.draw=()=>draws++;u.context.bindEvents();
 u.get('showLiveNesting').checked=false;u.get('showLiveNesting').onchange();
 u.get('showLiveNesting').checked=true;u.get('showLiveNesting').onchange();assert.equal(draws,2);
});

test('rejected workflow start cannot display previous successful cycle times',()=>{
 const u=ui();u.context.renderWorkflowTimings({stage:'Failed',busy:false,error:'No template selected',events:[{stage:'Exporting',status:'completed',elapsed_seconds:9}]});
 assert.equal(u.get('workflowTotalTimer').textContent,'Cycle: —');assert.equal(u.get('workflowStageTimers').children[4].children[1].textContent,'—');
});


test('Estimate keeps its heading and displays actual current nesting waste',()=>{
 const u=ui();const html=fs.readFileSync(path.join(__dirname,'../frontend/index.html'),'utf8');
 assert.ok(!html.includes('Not available'));assert.match(html,/<span>Estimate <strong id="waste">--<\/strong>/);
 u.context.appState.workflowPreviewManual=true;u.context.updateCycleVisuals({stage:'Exporting',busy:true,artifacts:{nesting:{placements:[],utilization:.75,waste:.25}}});
 assert.equal(u.get('waste').textContent,'25.0%');
 u.context.updateCycleVisuals({stage:'Exporting',busy:true,artifacts:{nesting:{placements:[],utilization:1,waste:0}}});assert.equal(u.get('waste').textContent,'0.0%');
 u.context.resetAnalysisState();assert.equal(u.get('waste').textContent,'--');
});


test('nesting preview summary includes actual waste beside pieces and utilization',()=>{
 const u=ui();const placement={template_name:'7 inch square',polygon:[[0,0],[10,0],[10,10]],x_mm:5,y_mm:5};const result={placements:[placement],utilization:.75,waste:.25};const before=JSON.stringify(result);
 u.context.renderNestingPreview(result);assert.equal(u.get('nestingPreviewSummary').textContent,'1 placed pieces / 75.0% utilization / 25.0% waste');assert.equal(JSON.stringify(result),before);
 u.context.renderNestingPreview({...result,utilization:1,waste:0});assert.match(u.get('nestingPreviewSummary').textContent,/100.0% utilization \/ 0.0% waste/);
 u.context.renderNestingPreview({placements:[placement],utilization:.75});assert.match(u.get('nestingPreviewSummary').textContent,/— waste/);
});


test('Data lists daily production CSV download links from the existing projects read',async()=>{
 const u=ui();u.context.api=async(url,options)=>{assert.equal(url,'/api/projects');assert.equal(options,undefined);return {projects:[],production_reports:[{name:'production_2026-10-15.csv',url:'/data/production/production_2026-10-15.csv'}]};};
 await u.context.refreshDataProjects();const link=u.get('dailyProductionReports').children[0].children[0];assert.equal(link.textContent,'production_2026-10-15.csv');assert.equal(link.href,'/data/production/production_2026-10-15.csv');assert.equal(link.attributes.download,'');
 u.context.api=async()=>{throw new Error('storage unavailable');};await u.context.refreshDataProjects();assert.match(u.get('dailyProductionReports').textContent,/storage unavailable/);
});

test('Retry daily record saves once and never invokes capture-to-export workflow',async()=>{
 const u=ui();let retries=0,starts=0;u.context.runWorkflow=()=>starts++;
 const exported={path:'/data/layout.svg',url:'/data/layout.svg',csv:{path:'/data/layout.csv',url:'/data/layout.csv'}};
 const failed={stage:'Failed',busy:false,error:'daily production CSV could not be saved',artifacts:{export:exported,nesting:{placements:[]},detection:{}}};
 u.context.displayWorkflow(failed);assert.equal(u.get('retryDailyProduction').hidden,false);
 u.context.api=async(url,options)=>{if(url==='/api/production/retry'){retries++;assert.equal(options.method,'POST');return {workflow:{stage:'Completed',busy:false,result:{export:{...exported,daily_production:{url:'/data/production/production_2026-10-15.csv'}},nesting:{placements:[]}}}};}assert.equal(url,'/api/projects');return {projects:[],production_reports:[]};};
 const pending=u.context.retryDailyProduction();await u.context.retryDailyProduction();await pending;assert.equal(retries,1);assert.equal(starts,0);assert.equal(u.get('retryDailyProduction').hidden,true);assert.equal(u.get('retryDailyProduction').disabled,false);
});

test('daily recovery replays saved records through its own endpoint and recovers button after error',async()=>{
 const u=ui();let starts=0;u.context.runWorkflow=()=>starts++;
 u.context.api=async(url,options)=>{if(url==='/api/production/recover'){assert.equal(options.method,'POST');return {checked:3};}assert.equal(url,'/api/projects');return {projects:[]};};
 await u.context.recoverDailyProduction();assert.equal(starts,0);assert.match(u.context.logs.at(-1).text,/3 saved export records/);
 u.context.api=async()=>{throw new Error('CSV locked');};await u.context.recoverDailyProduction();assert.equal(u.context.logs.at(-1).error,true);assert.equal(u.get('recoverDailyProduction').disabled,false);
});

function productionRow(overrides={}) {
 return {workflow_id:'cycle-1',status:'exported',exported_at_local:'2026-10-15T10:15:00+05:30',cycle_number:'1',exported_piece_count:'2',sheath_count:'1',material_area_mm2:'100',used_area_mm2:'50',utilization_percent:'50',waste_percent:'50',cycle_seconds:'3',export_format:'svg',template_counts_json:JSON.stringify([{template_id:'t1',name:'7 inch',count:2}]),...overrides};
}
function productionCsv(rows) {
 const fields=Object.keys(productionRow());const cell=value=>'"'+String(value).replaceAll('"','""')+'"';
 return '\uFEFF'+[fields,...rows.map(row=>fields.map(key=>row[key]))].map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';
}
test('production CSV parser handles BOM, Unicode, quoted JSON, commas and multiline template names',()=>{
 const u=ui(),record=productionRow({template_counts_json:JSON.stringify([{template_id:'t1',name:'7 inch, "special"\nதமிழ்',count:2}])});
 assert.equal(u.context.parseProductionCsv(productionCsv([])).length,0);
 const parsed=u.context.parseProductionCsv(productionCsv([record]));assert.equal(parsed.length,1);assert.equal(parsed[0].template_counts_json,record.template_counts_json);
 assert.throws(()=>u.context.parseProductionCsv('workflow_id,status\na,exported'),/columns/);
 assert.throws(()=>u.context.parseProductionCsv('"unfinished'),/Incomplete/);
 assert.throws(()=>u.context.parseProductionCsv(productionCsv([record]).replace('"cycle-1"','"cycle-1"oops')),/quoting/);
 assert.throws(()=>u.context.parseProductionCsv(productionCsv([record])+'bad,row'),/fields/);
});
test('daily summary uses area-weighted utilization, actual counts and measured average timing',()=>{
 const u=ui();const rows=[productionRow(),productionRow({workflow_id:'cycle-2',material_area_mm2:'300',used_area_mm2:'240',cycle_seconds:'7'})];
 const before=JSON.stringify(rows),summary=u.context.summarizeProduction(rows);
 assert.equal(summary.rows.length,2);assert.equal(summary.pieces,4);assert.equal(summary.sheaths,2);assert.equal(summary.utilization,72.5);assert.equal(summary.seconds,5);assert.equal(summary.templates[0].count,4);assert.equal(JSON.stringify(rows),before);
 u.context.renderProduction(rows);assert.equal(u.get('productionMetrics').children[3].children[1].textContent,'72.5%');assert.equal(u.get('productionMetrics').children[4].children[1].textContent,'27.5%');assert.equal(u.get('productionCycles').children.length,2);
});
test('daily summary rejects duplicated IDs, invalid counts and inconsistent template totals',()=>{
 const u=ui();assert.throws(()=>u.context.summarizeProduction([productionRow(),productionRow()]),/duplicate/);
 assert.throws(()=>u.context.summarizeProduction([productionRow({exported_piece_count:'bad'})]),/counts/);
 assert.throws(()=>u.context.summarizeProduction([productionRow({template_counts_json:'[]'})]),/do not match/);
 const incomplete=u.context.summarizeProduction([productionRow({material_area_mm2:'',cycle_seconds:''})]);assert.equal(incomplete.utilization,null);assert.equal(incomplete.seconds,null);
 u.context.renderProduction([]);assert.equal(u.get('productionMetrics').children[0].children[1].textContent,0);assert.equal(u.get('productionMetrics').children[3].children[1].textContent,'--');
 assert.equal(u.context.summarizeProduction([productionRow({status:'failed'})]).pieces,0);
});
test('production date switching preserves selection and uses read-only CSV requests without processing',async()=>{
 const u=ui();let starts=0;u.context.runWorkflow=()=>starts++;const before=JSON.stringify(u.context.appState),urls=[];
 const reports=[{name:'production_2026-10-15.csv',url:'/data/new.csv'},{name:'production_2026-10-14.csv',url:'/data/old.csv'}];
 u.context.fetch=async(url,options)=>{urls.push(url);assert.equal(options.cache,'no-store');assert.equal(options.method,undefined);return {ok:true,text:async()=>productionCsv([productionRow()])};};
 await u.context.setProductionReports(reports);assert.equal(u.get('productionDate').value,reports[0].name);assert.equal(u.get('productionCycles').children.length,1);
 u.get('productionDate').value=reports[1].name;await u.context.setProductionReports(reports);assert.equal(u.get('productionDate').value,reports[1].name);assert.equal(urls.at(-1),'/data/old.csv');assert.equal(u.get('productionDownload').href,'/data/old.csv');assert.equal(starts,0);assert.equal(JSON.stringify(u.context.appState),before);
 await u.context.setProductionReports([]);assert.equal(u.get('productionDate').disabled,true);assert.equal(u.get('productionDownload').hidden,true);
});
test('slow CSV response cannot replace a newly selected day and failures are visible',async()=>{
 const u=ui();let resolve;u.context.fetch=()=>new Promise(done=>resolve=done);
 const old=u.context.setProductionReports([{name:'production_2026-10-14.csv',url:'/old'}]);
 u.context.fetch=async()=>({ok:true,text:async()=>productionCsv([productionRow({workflow_id:'new'})])});
 await u.context.setProductionReports([{name:'production_2026-10-15.csv',url:'/new'}]);resolve({ok:true,text:async()=>productionCsv([])});await old;
 assert.match(u.get('productionStatus').textContent,/2026-10-15/);assert.equal(u.get('productionCycles').children.length,1);
 u.context.fetch=async()=>({ok:false,status:404});await u.context.loadProductionReport();assert.match(u.get('productionStatus').textContent,/404/);assert.equal(u.get('productionMetrics').children[0].children[1].textContent,'--');
 u.context.fetch=async()=>({ok:true,text:async()=>'not a CSV'});await u.context.loadProductionReport();assert.match(u.get('productionStatus').textContent,/Cannot visualize/);
});


test('true shared cut forces zero spacing in the payload and restores ordinary spacing on mode change',()=>{
 const u=ui();u.get('spacing').value='2';u.get('strategy').value='common_line';u.context.updateSharedCutControls();
 assert.equal(u.get('spacing').value,'0');assert.equal(u.get('spacing').disabled,true);assert.equal(u.get('sharedCutHint').hidden,false);
 assert.equal(u.context.nestingPayload().spacing_mm,0);u.get('spacing').value='7';assert.equal(u.context.nestingPayload().spacing_mm,0);
 u.get('strategy').value='shared_side';u.context.updateSharedCutControls();assert.equal(u.get('spacing').value,'2');assert.equal(u.get('spacing').disabled,false);assert.equal(u.get('sharedCutHint').hidden,true);
});
test('saved common-cut mode loads without changing stored spacing for other modes',async()=>{
 const u=ui();u.context.api=async()=>({nesting:{strategy:'common_line',spacing_mm:3,edge_clearance_mm:4}});await u.context.loadConfig();
 assert.equal(u.get('strategy').value,'common_line');assert.equal(u.get('spacing').value,'0');assert.equal(u.get('edgeClearance').value,4);
 u.get('strategy').value='accurate';u.context.updateSharedCutControls();assert.equal(u.get('spacing').value,3);
});

test('Excel download follows the selected production date without starting processing',async()=>{
 const u=ui();let starts=0;u.context.runWorkflow=()=>{starts++;};
 u.context.fetch=async()=>({ok:true,text:async()=>productionCsv([])});
 const reports=[{name:'production_2026-10-15.csv',url:'/data/new.csv',excel_url:'/api/production/2026-10-15/excel'},{name:'production_2026-10-14.csv',url:'/data/old.csv',excel_url:'/api/production/2026-10-14/excel'}];
 await u.context.setProductionReports(reports);
 assert.equal(u.get('productionExcelDownload').hidden,false);assert.equal(u.get('productionExcelDownload').href,reports[0].excel_url);
 u.get('productionDate').value=reports[1].name;await u.context.setProductionReports(reports);assert.equal(u.get('productionExcelDownload').href,reports[1].excel_url);
 await u.context.setProductionReports([]);assert.equal(u.get('productionExcelDownload').hidden,true);assert.equal(starts,0);
});

test('Read-only and signed-out users cannot launch Spacebar workflows',()=>{
 const u=ui();let starts=0;u.context.runWorkflow=()=>{starts++;};
 u.context.appState.user={role:'viewer',permissions:['read','download']};
 const readOnly=space();u.context.workflowKeydown(readOnly);assert.equal(starts,0);assert.equal(readOnly.prevented,undefined);
 u.context.appState.user=null;u.context.workflowKeydown(space());assert.equal(starts,0);
 u.context.appState.user={role:'editor',permissions:['read','download','operate','templates']};
 u.context.workflowKeydown(space());assert.equal(starts,1);
});

test('client blocks protected writes for Read-only and config changes for Editor',async()=>{
 const u=ui();let requests=0;u.context.fetch=async()=>{requests++;return {ok:true,json:async()=>({})};};
 u.context.appState.user={role:'viewer',permissions:['read','download']};
 await assert.rejects(u.context.api('/api/workflow',{method:'POST'}),/permission/);assert.equal(requests,0);
 u.context.appState.user={role:'editor',permissions:['read','download','operate','templates']};
 await assert.rejects(u.context.api('/api/config',{method:'POST'}),/permission/);assert.equal(requests,0);
 u.context.appState.csrfToken='test-csrf';let sent;
 u.context.fetch=async(url,options)=>{requests++;sent=options;return {ok:true,json:async()=>({})};};
 await u.context.api('/api/templates',{method:'POST',body:'[]'});
 assert.equal(sent.headers['X-CSRF-Token'],'test-csrf');assert.equal(requests,1);
});

test('sign-out and account switching clear every password field',()=>{
 const u=ui();const secret={value:'previous administrator password'};
 u.document.body={dataset:{}};u.document.querySelectorAll=selector=>selector==='input[type="password"]'?[secret]:[];
 u.context.fitCanvas=()=>{};
 u.context.lockAccess(false);assert.equal(secret.value,'');assert.equal(u.context.appState.user,null);
 secret.value='unfinished password reset';
 u.context.acceptAccount({user:{id:'reader',username:'reader',role:'viewer',permissions:['read','download']},csrf_token:'new-session'});
 assert.equal(secret.value,'');assert.equal(u.context.appState.user.role,'viewer');
});

test('an in-flight session check cannot restore an account after sign-out',async()=>{
 const u=ui();let tick,resolve;
 u.document.body={dataset:{auth:'signed-in'}};u.context.fitCanvas=()=>{};
 u.context.setInterval=callback=>{tick=callback;};
 u.context.bindAccountUI();u.context.appState.csrfToken='old-session';
 const previous=u.context.appState.user;
 u.context.api=()=>new Promise(done=>{resolve=done;});
 const pending=tick();u.context.lockAccess(false);
 resolve({user:previous,csrf_token:'old-session'});await pending;
 assert.equal(u.context.appState.user,null);assert.equal(u.document.body.dataset.auth,'locked');
});

test('desktop file links stay in the signed-in renderer and signed-out clicks are blocked',()=>{
 const u=ui();u.context.window.pywebview={};u.document.body={dataset:{}};
 const attributes={};let prevented=false;
 const link={href:'http://127.0.0.1:8001/data/layout.dxf',target:'_blank',setAttribute(key,value){attributes[key]=value;},blur(){}};
 const event={target:{closest:()=>link},detail:1,preventDefault(){prevented=true;}};
 u.context.desktopProtectedDownload(event);
 assert.equal(link.target,'_self');assert.equal(attributes.download,'');assert.equal(prevented,false);
 u.context.appState.user=null;u.context.desktopProtectedDownload(event);assert.equal(prevented,true);
});


async function accountDeleteUI() {
 const u=ui();let deleted=false;const calls=[];
 const owner={...u.context.appState.user,enabled:true},other={id:'reader-id',username:'reader',role:'viewer',enabled:true};
 u.context.api=async(url,options)=>{
   if(options?.method==='DELETE'){calls.push(url);deleted=true;return {ok:true};}
   if(url==='/api/auth/users')return {users:deleted?[owner]:[owner,other]};
   if(url==='/api/auth/audit')return {events:[]};
   throw Error('Unexpected request '+url);
 };
 await u.context.refreshAccountPanel();
 return {...u,calls,remove:u.get('accountList').children[1].children[5]};
}
test('account Delete confirms, cancels safely, refreshes and preserves the signed-in account',async()=>{
 const u=await accountDeleteUI();
 assert.equal(u.get('accountList').children[0].children[5].disabled,true);
 let prompt;u.context.window.confirm=text=>{prompt=text;return false;};
 await u.remove.onclick();assert.equal(u.calls.length,0);assert.match(prompt,/reader/);assert.match(prompt,/Production records/);
 u.context.window.confirm=()=>true;await u.remove.onclick();
 assert.deepEqual(u.calls,['/api/auth/users/reader-id']);assert.equal(u.get('accountList').children.length,1);
 assert.match(u.get('accountStatus').textContent,/Deleted reader/);
});
test('account Delete blocks duplicate requests and restores controls after storage errors',async()=>{
 const u=await accountDeleteUI();u.context.window.confirm=()=>true;let reject,requests=0;
 u.context.api=()=>{requests++;return new Promise((_,fail)=>{reject=fail;});};
 const pending=u.remove.onclick();assert.equal(u.remove.disabled,true);
 await u.remove.onclick();assert.equal(requests,1);
 reject(Error('Account storage is unavailable.'));await pending;
 assert.equal(u.remove.disabled,false);assert.equal(u.get('accountList').children[1].children[4].disabled,false);
 assert.match(u.get('accountStatus').textContent,/storage is unavailable/);
});

test('LaserCAD handoff reports warnings separately without changing completed export or launching from UI',()=>{
 const u=ui();let starts=0;u.context.fetch=()=>{starts++;throw Error('No UI launch requests expected');};
 const output={path:'/tmp/cuts.dxf',url:'/data/cuts.dxf',lasercad:{status:'not_installed',message:'DXF saved. Install LaserCAD.'}};
 u.context.displayWorkflow({stage:'Completed',busy:false,result:{export:output,nesting:{placements:[]}}});
 assert.equal(u.get('processStageTitle').textContent,'Export completed');
 assert.equal(u.get('laserCadHandoffStatus').textContent,output.lasercad.message);
 assert.equal(u.get('laserCadHandoffStatus').hidden,false);
 u.context.renderExportLinks(output);assert.equal(u.get('laserCadExportStatus').hidden,false);
 u.context.displayWorkflow({stage:'Capturing image',busy:true});assert.equal(u.get('laserCadHandoffStatus').hidden,true);
 u.context.renderExportLinks({path:'/tmp/cuts.svg',url:'/data/cuts.svg'});assert.equal(u.get('laserCadExportStatus').hidden,true);
 assert.equal(starts,0);
});

test('clipped sheath dimensions are labelled usable only while preserving values and selection',()=>{
 const u=ui(),m={width_mm:100,height_mm:200,area_m2:.02,aligned_width_mm:100,aligned_height_mm:200};
 const first={id:'s1',mm_polygon:[[0,0],[100,0],[100,200]],measurements:m,clipped_to_workspace:true};
 const second={id:'s2',mm_polygon:[[0,0],[50,0],[50,60]],measurements:{...m,width_mm:50,height_mm:60,aligned_width_mm:50,aligned_height_mm:60},clipped_to_workspace:false};
 u.context.renderSheaths([first,second]);
 assert.equal(u.get('dimensionWidth').textContent,'100.00 mm');assert.equal(u.get('dimensionHeight').textContent,'200.00 mm');
 assert.match(u.get('dimensionContext').textContent,/usable portion only/);
 assert.equal(u.get('materialScopeWarning').hidden,false);assert.equal(u.get('measurementScopeWarning').hidden,false);
 assert.match(u.get('sheathMeasurementList').children[0].textContent,/usable portion only/);
 u.context.selectSheath('s2');assert.doesNotMatch(u.get('dimensionContext').textContent,/usable portion only/);
 assert.equal(u.get('dimensionWidth').textContent,'50.00 mm');
 u.context.renderSheaths([second]);assert.equal(u.get('materialScopeWarning').hidden,true);
});

test('cutting estimates use closed outline length and separate sheath totals', () => {
  const u = ui();
  const square = [[0,0],[100,0],[100,100],[0,100]];
  const result = u.context.estimateCuttingTime([
    { sheath_id:'s1', polygon:square },
    { sheath_id:'s1', polygon:[...square, square[0]] },
    { sheath_id:'s2', polygon:[[0,0],[50,0],[50,50],[0,50]] },
  ], [{id:'s1'}, {id:'s2'}, {id:'s3'}], 5);
  assert.equal(result.speed,80);
  assert.equal(result.length,1000);
  assert.equal(result.seconds,12.5);
  assert.equal(result.rows[0].seconds,10);
  assert.equal(result.rows[1].seconds,2.5);
  assert.equal(result.rows[2].seconds,0);
  assert.equal(u.context.estimateCuttingTime([{polygon:square}], [], 10).seconds,10);
  assert.throws(() => u.context.estimateCuttingTime([], [], 0), /greater than zero/);
  assert.throws(() => u.context.estimateCuttingTime([{polygon:[[0,0],[NaN,1],[1,1]]}], [], 5), /invalid geometry/);
});

test('cutting tab renders current placements, editable benchmark and empty or invalid states', () => {
  const u=ui();
  u.get('cuttingBenchmarkSeconds').value='5';
  u.context.appState.placements=[{polygon:[[0,0],[100,0],[100,100],[0,100]]}];
  u.context.navigateSection('cutting-time');
  assert.equal(u.context.appState.activeView,'cutting-time');
  assert.equal(u.get('currentSectionLabel').textContent,'Cutting Time');
  assert.equal(u.get('cuttingTimeRows').children[0].children[3].textContent,'5.0 sec');
  assert.equal(u.get('cuttingTimeTotal').children[0].children[2].textContent,'400.0 mm');
  u.get('cuttingBenchmarkSeconds').value='10';
  u.context.renderCuttingTime();
  assert.equal(u.get('cuttingTimeRows').children[0].children[3].textContent,'10.0 sec');
  u.get('cuttingBenchmarkSeconds').value='';
  u.context.renderCuttingTime();
  assert.equal(u.get('cuttingTimeRows').children.length,0);
  assert.match(u.get('cuttingTimeStatus').textContent,/greater than zero/);
  u.get('cuttingBenchmarkSeconds').value='5';
  u.context.appState.placements=[];
  u.context.renderCuttingTime();
  assert.equal(u.get('cuttingTimeTotal').children.length,0);
  assert.match(u.get('cuttingTimeStatus').textContent,/run nesting/);
});
test('LaserCAD Stop stays usable while other controls are busy, and viewers cannot operate', () => {
  const u=ui(), start=u.get('laser-start'), stop=u.get('laser-stop');
  start.dataset.laserAction='start';stop.dataset.laserAction='stop';
  u.document.querySelectorAll=selector=>selector==='[data-laser-action]'?[start,stop]:[];
  u.run('laserControlSnapshot={actions:{start:true,stop:true}}; laserControlPending=true;');
  u.context.appState.workflowBusy=true;u.context.updateLaserControlButtons();
  assert.equal(start.disabled,true);assert.equal(stop.disabled,false);
  u.context.appState.user={permissions:['read']};u.context.updateLaserControlButtons();
  assert.equal(stop.disabled,true);assert.equal(u.get('openLaserCadControl').disabled,true);
});

test('cancelled LaserCAD Start never sends a machine request', async () => {
  const u=ui();let calls=0;u.context.api=async()=>{calls++;return {};};
  u.run('laserControlSnapshot={actions:{start:true},job:"job.dxf"};');
  u.context.window.confirm=()=>false;
  await u.context.sendLaserControl('start');assert.equal(calls,0);
});

test('automatic Run Box reports request delivery separately from completed motion', () => {
  const u=ui();
  u.context.renderLaserCadHandoff({lasercad:{status:'launch_requested',message:'DXF opened.',boundary_frame:{status:'requested',message:'Run Box click delivered. Boundary completion unverified.'}}});
  assert.match(u.get('laserCadHandoffStatus').textContent,/DXF opened.*Run Box.*unverified/);
  assert.equal(u.get('laserCadHandoffStatus').dataset.state,'launch_requested');
  assert.equal(u.get('laserCadExportStatus').hidden,false);
});

test('skipped automatic Run Box is visible as a warning without hiding a saved DXF', () => {
  const u=ui();
  u.context.renderLaserCadHandoff({lasercad:{status:'launch_requested',message:'DXF saved.',boundary_frame:{status:'skipped',message:'No Run Box sent; drawing not ready.'}}});
  assert.match(u.get('laserCadExportStatus').textContent,/DXF saved.*No Run Box sent/);
  assert.equal(u.get('laserCadExportStatus').dataset.state,'warning');
  u.context.renderLaserCadHandoff({});assert.equal(u.get('laserCadExportStatus').hidden,true);
});


function savedSummaryText(node) {
 return [node.textContent || '', ...(node.children || []).map(savedSummaryText)].join(' ');
}

function savedWorkspaceProfile(width=950,height=650,origin=22) {
 return {source:'aruco_workspace',working_width_mm:width,working_height_mm:height,dictionary:'DICT_5X5_50',
  bed_markers:[{marker_id:origin,x_mm:0,y_mm:0},{marker_id:23,x_mm:width,y_mm:0},{marker_id:24,x_mm:width,y_mm:height},{marker_id:25,x_mm:0,y_mm:height}]};
}

test('saved working-area summary replaces old dimensions and origin without changing fallback settings',()=>{
 const u=ui(),fallback={workspace:{width_mm:600,height_mm:450}};
 u.context.appState.config=fallback;
 u.context.renderCalibrationStatus({scale:savedWorkspaceProfile(1250,560,11),aruco:{}});
 assert.match(savedSummaryText(u.get('settingsActiveCalibration')),/1250 × 560 mm/);
 u.context.renderCalibrationStatus({scale:savedWorkspaceProfile(),aruco:{}});
 for(const id of ['settingsActiveCalibration','calibrationSavedSummary']){
  const text=savedSummaryText(u.get(id));
  assert.match(text,/950 × 650 mm/);assert.match(text,/Working origin: ID 22/);
  assert.doesNotMatch(text,/1250 × 560|Working origin: ID 11/);
 }
 assert.equal(u.context.appState.config,fallback);
});

test('calibration reload takes marker coordinates from the active saved profile instead of the old layout',async()=>{
 const u=ui(),calls=[];
 u.context.api=async(url,options)=>{calls.push([url,options]);if(url!='/api/calibration/status')throw Error('Stale layout must not be requested');return {scale:savedWorkspaceProfile(),aruco:{}};};
 await u.context.loadCalibrationStatus();
 assert.equal(calls.length,1);assert.equal(calls[0][1].cache,'no-store');
 assert.equal(u.get('workspaceWidth').value,'950');assert.equal(u.get('workspaceHeight').value,'650');
 assert.match(savedSummaryText(u.get('settingsActiveCalibration')),/Working origin: ID 22/);
});

test('saved DXF reference updates independently and does not invent a marker match after recalibration',async()=>{
 const u=ui();u.context.appState.savedCalibration={scale:savedWorkspaceProfile()};
 const alignment={enabled:true,reference:'custom',design_x_mm:950,design_y_mm:0,laser_x_mm:0,laser_y_mm:0,rotation_deg:0,flip_x:false,flip_y:true};
 u.context.api=async()=>alignment;
 await u.context.loadAlignment();
 assert.match(u.get('savedAlignmentStatus').textContent,/ID 23 centre/);
 u.context.appState.savedAlignment={...alignment,design_x_mm:1250};u.context.renderSavedWorkspace();
 assert.ok(u.get('savedAlignmentStatus').textContent.includes('Design point (1250, 0)'));
 assert.doesNotMatch(u.get('savedAlignmentStatus').textContent,/ID 23/);
});

test('removing calibration clears the previous saved summary and restores fallback controls',()=>{
 const u=ui(),card=u.get('fallback-workspace-card');
 u.document.querySelectorAll=selector=>selector==='[data-workspace-defaults="true"]'?[card]:[];
 u.context.renderCalibrationStatus({scale:savedWorkspaceProfile(),aruco:{}});
 assert.equal(card.hidden,true);
 u.context.renderCalibrationStatus({scale:null,aruco:{}});
 assert.equal(card.hidden,false);
 assert.match(savedSummaryText(u.get('settingsActiveCalibration')),/No working-area calibration is saved/);
 assert.doesNotMatch(savedSummaryText(u.get('settingsActiveCalibration')),/Calibrated marker span: 950 × 650|ID 22/);
});


test('working-area resize follows the same DXF marker and preserves target, rotation and reversals',()=>{
 const u=ui(),before=savedWorkspaceProfile(1250,560),after=savedWorkspaceProfile(750,180);
 const alignment={enabled:true,reference:'custom',design_x_mm:1250,design_y_mm:0,laser_x_mm:10,laser_y_mm:20,rotation_deg:15,flip_x:false,flip_y:true};
 const updated=u.context.alignmentForWorkspaceSave(before,after,alignment);
 assert.equal(updated.design_x_mm,750);assert.equal(updated.design_y_mm,0);
 for(const key of ['enabled','reference','laser_x_mm','laser_y_mm','rotation_deg','flip_x','flip_y'])assert.equal(updated[key],alignment[key]);
 assert.equal(alignment.design_x_mm,1250);
});

test('changing working origin keeps the DXF reference on its original marker ID',()=>{
 const u=ui(),before=savedWorkspaceProfile(1250,560),after=savedWorkspaceProfile(750,180);
 after.bed_markers.forEach(m=>{m.x_mm-=750;});
 const updated=u.context.alignmentForWorkspaceSave(before,after,{enabled:true,reference:'custom',design_x_mm:1250,design_y_mm:0});
 assert.equal(updated.design_x_mm,0);assert.equal(updated.design_y_mm,0);
});

test('working-area save never guesses an arbitrary DXF point, removed marker or changed dictionary',()=>{
 const u=ui(),before=savedWorkspaceProfile(1250,560),after=savedWorkspaceProfile(750,180);
 const alignment={enabled:true,reference:'custom',design_x_mm:1250,design_y_mm:0};
 assert.equal(u.context.alignmentForWorkspaceSave(before,after,{...alignment,design_x_mm:600}),null);
 assert.equal(u.context.alignmentForWorkspaceSave(before,{...after,dictionary:'DICT_4X4_50'},alignment),null);
 assert.equal(u.context.alignmentForWorkspaceSave(before,{...after,bed_markers:after.bed_markers.filter(m=>m.marker_id!==23)},alignment),null);
 assert.equal(u.context.alignmentForWorkspaceSave(before,after,{...alignment,enabled:false}),null);
 assert.equal(u.context.alignmentForWorkspaceSave(before,after,{...alignment,reference:'design_origin'}),null);
});


test('supervised prepare action starts without a popup and sends loaded DXF flags',async()=>{
 const u=ui();u.get('supervisedCutting').checked=true;u.get('workflowFormat').value='dxf';
 let writes=0,payload;
 u.context.window.confirm=()=>{throw Error('No popup approval')};
 u.context.api=async(url,options)=>{if(options){writes++;payload=JSON.parse(options.body)}return {id:'generated-id',busy:false,stage:'Failed',error:'test preparation ended'}};
 await u.context.runWorkflow();
 assert.equal(writes,1);assert.equal(payload.supervised,true);assert.equal(payload.operator_loaded,true);assert.equal(payload.export.format,'dxf');
});

test('supervised export completion does not invite loading the next piece',()=>{
 const u=ui();u.context.displayWorkflow({id:'one',supervised:true,busy:false,stage:'Completed',result:{export:{path:'test.dxf',url:'/data/test.dxf'},nesting:{placements:[]}}});
 assert.match(u.get('processStageDetail').textContent,/confirm cutting completion/);
 assert.match(u.get('cycleNextHelp').textContent,/remains active/);
 assert.match(u.get('workflowStatus').textContent,/Laser Control/);
});

test('shared ID 12 frame fills marker rows with negative X and Y',()=>{
 const u=ui();u.context.appState.config={workspace:{coordinate_frame:'id12_negative_xy'}};
 const rows=[11,12,13,10].map(id=>{
  const fields={id:{value:String(id)},x:{value:''},y:{value:''}};
  return {fields,querySelector:selector=>fields[selector.match(/"(id|x|y)"/)[1]]};
 });
 u.document.querySelectorAll=selector=>selector==='#workspaceMarkers .bed-marker'?rows:[];
 u.get('workspaceWidth').value='1050';u.get('workspaceHeight').value='510';
 u.context.fillWorkspaceRectangle();
 assert.deepEqual(rows.map(r=>[r.fields.x.value,r.fields.y.value]),[['-1050','0'],['0','0'],['0','-510'],['-1050','-510']]);
});

test('shared ID 12 frame preserves screen orientation and canvas round trips',()=>{
 const u=ui();u.context.appState.config={workspace:{coordinate_frame:'id12_negative_xy'}};
 u.context.appState.scale={coordinate_frame:'id12_negative_xy'};
 u.context.appState.sheathMm=[[-1050,0],[0,0],[0,-510],[-1050,-510]];
 u.get('overlay').width=1090;u.get('overlay').height=550;
 assert.deepEqual(JSON.parse(JSON.stringify(u.context.toCanvas([0,0]))),[1070,20]);
 assert.deepEqual(JSON.parse(JSON.stringify(u.context.toCanvas([0,-510]))),[1070,530]);
 assert.deepEqual(JSON.parse(JSON.stringify(u.context.fromCanvas(1070,530))),[0,-510]);
 u.context.appState.scale={source:'legacy'};
 assert.equal(u.context.worldYUp(),false,'Historical profiles retain their original preview orientation');
});

test('shared ID 12 frame hides secondary transforms and uses template-box DXF origin',()=>{
 const u=ui();u.context.appState.config={workspace:{coordinate_frame:'id12_negative_xy'}};
 u.get('alignmentFlipY').checked=true;u.get('alignmentLaserX').value='1050';
 u.context.applySharedOriginUI();
 assert.equal(u.get('alignmentTransformFields').disabled,true);
 assert.equal(u.get('sharedOriginExplanation').hidden,false);
 const alignment=JSON.parse(JSON.stringify(u.context.readAlignment()));
 assert.equal(alignment.flip_y,false);assert.equal(alignment.laser_x_mm,0);assert.equal(alignment.reference,'templates_top_right');
 assert.match(u.context.savedAlignmentText(),/ID 12 centre \(0, 0\).*DXF origin: top-right corner of the bounding box/);
});



test('template box uses all nested templates and follows Y-up coordinates',()=>{
 const u=ui();
 const pieces=[{polygon:[[-90,-20],[-50,-20],[-50,-10],[-90,-10]]},{polygon:[[-40,-70],[-10,-70],[-10,-40],[-40,-40]]}];
 const before=JSON.stringify(pieces);
 const bounds=JSON.parse(JSON.stringify(u.context.templateBounds(pieces,true)));
 assert.deepEqual(bounds.origin,[-10,-10]);
 assert.deepEqual([bounds.minX,bounds.minY,bounds.maxX,bounds.maxY],[-90,-70,-10,-10]);
 assert.deepEqual(JSON.parse(JSON.stringify(u.context.templateBounds(pieces,false).origin)),[-10,-70]);
 assert.equal(u.context.templateBounds([],true),null);assert.equal(JSON.stringify(pieces),before);
});

function latestMarkerUI() {
 const u=ui();u.context.appState.config={workspace:{coordinate_frame:'id12_negative_xy'}};
 const rows=[11,12,13,10].map(id=>{
  const fields={id:{value:String(id)},x:{value:'0'},y:{value:'0'}};
  return {fields,querySelector:selector=>fields[selector.match(/"(id|x|y)"/)[1]]};
 });
 u.document.querySelectorAll=selector=>selector==='#workspaceMarkers .bed-marker'?rows:[];
 const layout={dictionary:'DICT_4X4_50',markers:[{marker_id:11,x_mm:-800,y_mm:0},{marker_id:12,x_mm:0,y_mm:0},{marker_id:13,x_mm:0,y_mm:-400},{marker_id:10,x_mm:-790,y_mm:-390}]};
 return {...u,rows,layout,pending:{workspace_layout:layout,workspace_calibration_pending:true,scale:null,scale_calibrated:false,aruco:{}}};
}

test('latest marker positions are authoritative in summaries before a photo is available',()=>{
 const u=latestMarkerUI();u.context.renderCalibrationStatus(u.pending);
 assert.equal(u.rows[0].fields.x.value,'-800');assert.equal(u.rows[3].fields.x.value,'-790');
 assert.match(savedSummaryText(u.get('settingsActiveCalibration')),/Saved marker span: 800 × 400 mm/);
 assert.match(savedSummaryText(u.get('calibrationSavedSummary')),/photo is required/i);
 assert.match(u.get('workspaceStatus').textContent,/Older calibration will not be used/);
});

test('latest marker edits survive background calibration refreshes',()=>{
 const u=latestMarkerUI();u.context.applyWorkspaceLayout(u.layout);
 u.rows[0].fields.x.value='-820';u.context.markWorkspaceEdits();
 u.context.renderCalibrationStatus({scale:savedWorkspaceProfile(1050,510,12),aruco:{}});
 assert.equal(u.rows[0].fields.x.value,'-820');assert.equal(u.context.appState.workspaceLayoutDirty,true);
 assert.match(u.get('workspaceEditStatus').textContent,/Unsaved/);
});

test('latest marker width and height edits populate rectangle rows when saving',()=>{
 const u=latestMarkerUI();u.get('workspaceWidth').value='820';u.get('workspaceHeight').value='410';
 u.context.markWorkspaceEdits(true);
 const layout=JSON.parse(JSON.stringify(u.context.readWorkspaceMarkers()));
 assert.deepEqual(layout.markers.map(m=>[m.x_mm,m.y_mm]),[[-820,0],[0,0],[0,-410],[-820,-410]]);
 assert.equal(u.context.appState.workspaceDimensionsDirty,false);
});

test('latest marker save uses camera-free endpoint and retains newer edits during save',async()=>{
 const u=latestMarkerUI();u.context.applyWorkspaceLayout(u.layout);u.context.markWorkspaceEdits();
 let resolve,request,cleared=0;u.context.clearWorkspaceGeometry=()=>{cleared++;};
 u.context.api=(url,options)=>{assert.equal(url,'/api/calibration/workspace-layout');request=JSON.parse(options.body);return new Promise(done=>resolve=done);};
 const saving=u.context.saveWorkspacePositions();
 u.rows[0].fields.x.value='-825';u.context.markWorkspaceEdits();
 resolve({saved:true,changed:true,layout:request,calibration:u.pending});await saving;
 assert.equal(request.markers[3].x_mm,-790);assert.equal(cleared,1);
 assert.equal(u.rows[0].fields.x.value,'-825');assert.equal(u.context.appState.workspaceLayoutDirty,true);
 assert.match(u.get('workspaceEditStatus').textContent,/Newer edits are still unsaved/);
});

test('latest marker load ignores an older response arriving after a newer one',async()=>{
 const u=latestMarkerUI(),responses=[];
 u.context.api=()=>new Promise(done=>responses.push(done));
 const old=u.context.loadCalibrationStatus(),latest=u.context.loadCalibrationStatus();
 responses[1](u.pending);await latest;
 responses[0]({scale:savedWorkspaceProfile(1050,510,12),aruco:{}});await old;
 assert.equal(u.rows[0].fields.x.value,'-800');
 assert.match(savedSummaryText(u.get('settingsActiveCalibration')),/800 × 400/);
});

test('latest marker failed save keeps edited coordinates and dirty state',async()=>{
 const u=latestMarkerUI();u.context.applyWorkspaceLayout(u.layout);u.context.markWorkspaceEdits();
 u.context.api=async()=>{throw Error('Storage unavailable');};
 await u.context.saveWorkspacePositions();
 assert.equal(u.context.appState.workspaceLayoutDirty,true);
 assert.equal(u.rows[3].fields.x.value,'-790');assert.match(u.context.logs.at(-1).text,/Storage unavailable/);
});


test('latest marker positions reject old workflow preview geometry',()=>{
 const u=latestMarkerUI();u.context.appState.savedCalibration=u.pending;
 const current={dictionary:u.layout.dictionary,bed_markers:u.layout.markers};
 assert.equal(u.context.isCurrentMarkerGeometry(current),true);
 assert.equal(u.context.isCurrentMarkerGeometry(savedWorkspaceProfile(1050,510,12)),false);
 assert.equal(u.context.isCurrentMarkerGeometry(null),false);
 let cleared=0;u.context.clearWorkspaceGeometry=()=>{cleared++;};
 u.context.updateCycleVisuals({artifacts:{nesting:{placements:[{polygon:[[0,0],[1,0],[1,1]]}],scale:savedWorkspaceProfile(1050,510,12)}}});
 assert.equal(cleared,1);assert.match(u.get('cyclePreviewLabel').textContent,/older marker positions/);
});


test('version labels use actual backend identity and follow future upgrades', async()=>{
 const u=ui();const labels=[{textContent:'stale header'},{textContent:'stale details'}];
 u.document.querySelectorAll=selector=>selector==='[data-app-version]'?labels:[];
 let version='2026.11.14.9';const calls=[];
 u.context.api=async(url,options)=>{calls.push([url,options]);return {version,update:'Keyboard workflow'};};
 await u.context.refreshApplicationVersion();
 assert.deepEqual(labels.map(x=>x.textContent),['v2026.11.14.9','v2026.11.14.9']);
 assert.match(u.get('applicationBuild').textContent,/2026.11.14.9/);
 version='2026.11.15.1';await u.context.refreshApplicationVersion();
 assert.deepEqual(labels.map(x=>x.textContent),['v2026.11.15.1','v2026.11.15.1']);
 assert.equal(calls.length,2);assert.equal(calls[0][0],'/api/application-info');assert.equal(calls[0][1].cache,'no-store');
});
test('version labels never retain stale versions after failed or incomplete identity lookup',async()=>{
 const u=ui();const labels=[{textContent:'v2026.11.14.7'},{textContent:'v2026.11.14.7'}];
 u.document.querySelectorAll=selector=>selector==='[data-app-version]'?labels:[];
 u.context.api=async()=>{throw new Error('offline');};await u.context.refreshApplicationVersion();
 assert.deepEqual(labels.map(x=>x.textContent),['Version unavailable','Version unavailable']);
 u.context.api=async()=>({});await u.context.refreshApplicationVersion();
 assert.deepEqual(labels.map(x=>x.textContent),['Version unavailable','Version unavailable']);
});


test('role-specific API errors preserve Admin details and simplify Read-only details',async()=>{
 const u=ui();u.document.body={dataset:{}};
 u.run(fs.readFileSync(path.join(__dirname,'../frontend/role-presentation.js'),'utf8'));
 const raw='SQLite failed at C:\\private\\production.csv';
 u.context.fetch=async()=>({ok:false,status:500,json:async()=>({detail:raw})});
 let caught;
 try{await u.context.api('/api/production');}catch(e){caught=e;}
 assert.equal(u.context.displayError(caught),raw);
 u.context.appState.user={role:'viewer',permissions:['read','download']};
 try{await u.context.api('/api/production');}catch(e){caught=e;}
 assert.equal(caught.message,raw);assert.match(u.context.displayError(caught),/report could not/);
 assert.doesNotMatch(u.context.displayError(caught),/SQLite|private/);
});
test('Read-only role still blocks forbidden mutations before any network request',async()=>{
 const u=ui();u.document.body={dataset:{}};
 u.run(fs.readFileSync(path.join(__dirname,'../frontend/role-presentation.js'),'utf8'));
 u.context.appState.user={role:'viewer',permissions:['read','download']};let requests=0;
 u.context.fetch=async()=>{requests++;throw new Error('must not send');};
 await assert.rejects(u.context.api('/api/config',{method:'POST',body:'{}'}),/read-only/);
 assert.equal(requests,0);
});


test('Manager and Read-only retain numeric measurement spans without explanatory prose',()=>{
 const u=ui();const m={width_mm:285.5,height_mm:273.7,area_m2:.0683};
 for(const role of ['editor','viewer']){
  u.context.appState.user.role=role;u.context.renderMeasurements(m);
  assert.match(u.get('dimensionDetail').textContent,/X span: 285.50 mm; Y span: 273.70 mm/);
  assert.doesNotMatch(u.get('dimensionDetail').textContent,/camera estimates|physical accuracy|enclosing/i);
 }
 u.context.appState.user.role='admin';u.context.renderMeasurements(m);
 assert.match(u.get('dimensionDetail').textContent,/Horizontal and vertical spans/);
});
