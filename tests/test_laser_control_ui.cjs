const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function dashboard(role='admin',jobControls=null,statusExtras={}) {
  const nodes = new Map(), calls = [], commands = [], fallbackButtons = [];
  const context2d = {scale(){},clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},stroke(){}};
  function node(tag='div') {
    return {tag,children:[],dataset:{},style:{},value:'',textContent:'',innerHTML:'',type:'',checked:false,disabled:false,
      append(...values){this.children.push(...values)},prepend(value){this.children.unshift(value)},
      replaceChildren(...values){this.children=values},setAttribute(){},getContext(){return context2d},clientWidth:600,
      querySelectorAll(selector){const out=[];function walk(n){for(const child of n.children || []){if(selector==='input'&&child.tag==='input')out.push(child);walk(child)}}walk(this);return out}};
  }
  function get(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)}
  for(const action of ['connect','disconnect','transfer','start','pause','resume','stop']){const button=node('button');button.dataset.lcCommand=action;commands.push(button)}
  for(const action of ['download','frame','stop','start','pause','resume']){const button=node('button');button.dataset.lcFallbackAction=action;fallbackButtons.push(button)}
  const permissions=['lcPrepare','lcFallback','lcLoadProfile','lcSaveProfile'];
  for(const id of permissions)get(id).dataset.lcPermission=id==='lcSaveProfile'?'configure':'operate';
  const job={sha256:'digest',id:'job1',filename:'<unsafe>.dxf',status:'validated',revision:1,source:'upload',parameters:{material:'Areca',thickness_mm:2.5,speed_mm_s:20,max_power_percent:40,min_power_percent:null,passes:1,layers:{}},
    validation:{valid:true,errors:[],warnings:[],entity_count:1,layers:[{name:'CUT',default_enabled:true}],dimensions_mm:{width:40,height:30},bounds_mm:{min_x:10,min_y:10,max_x:50,max_y:40},preview:[{layer:'CUT',closed:true,points:[[10,10],[50,10],[50,40],[10,40]]}]}};
  const state={user:{role},activeView:'laser-control'};
  const document={hidden:false,activeElement:null,createElement:node,querySelectorAll(selector){
    if(selector==='[data-lc-permission]')return permissions.map(get);
    if(selector==='[data-lc-command]')return commands;
    if(selector==='[data-lc-fallback-action]')return fallbackButtons;
    if(selector==='#lcLayers tr')return get('lcLayers').children;
    return [];
  }};
  let poll;
  const window={devicePixelRatio:1,addEventListener(){},confirm(){return true},beginSupervisedCycle:async()=>{window.preparations=(window.preparations||0)+1}};
  const api=async(url,options={})=>{
    calls.push([url,options]);
    if(url.endsWith('/status'))return {desktop_layout_supported:true,controller:{ip:'192.168.8.8',blocker:'Protocol unverified'},job,job_controls:jobControls,...statusExtras};
    if(url.endsWith('/job-controls'))return jobControls || {actions:{}};
    if(url.endsWith('/events'))return {events:[]};
    if(url.endsWith('/jobs'))return {jobs:[]};
    if(url.endsWith('/profiles'))return {profiles:[]};
    if(url.endsWith('/parameters')){job.revision++;job.parameters=JSON.parse(options.body).parameters;return job}
    if(url.endsWith('/prepare'))return job;
    if(url.endsWith('/cycle/review')){statusExtras.supervised.active.state='reviewed';statusExtras.supervised.active.review={revision:job.revision};return statusExtras.supervised.active}
    if(url.endsWith('/cycle/confirm')){statusExtras.supervised.active=null;return {state:'completed'}};
    if(url.endsWith('/layout'))return {message:'Windows arranged'};
    if(url.endsWith('/fallback-command')||url.endsWith('/fallback-stop')){if(statusExtras.panelError)throw Error('Panel outcome unknown');return {requested:true,message:'Panel click requested; completion unverified.'}};
    throw new Error('Unexpected request '+url);
  };
  const context=vm.createContext({window,document,state,$:get,api,hasPermission:p=>p==='read'||(role==='admin')||(role==='editor'&&p==='operate'),
    Option:function(text,value){this.text=text;this.value=value},setInterval:fn=>{poll=fn;return 1},clearInterval(){},FormData:class{append(){}},crypto:{randomUUID:()=> 'request-id'},navigateSection(){},console});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/laser-control.js'),'utf8'),context);
  return {window,document,state,get,calls,commands,job,fallbackButtons,poll:()=>poll()};
}

test('dashboard loads preview without connecting or sending commands',async()=>{
  const d=dashboard();assert.equal(d.calls.length,0);
  await d.window.LaserControl.refresh();
  assert.equal(d.get('lcAddress').textContent,'192.168.8.8');
  assert.equal(d.get('lcFilename').textContent,'<unsafe>.dxf');
  assert.equal(d.get('lcFilename').innerHTML,'');
  assert.equal(d.get('lcPreview').width,600);
  assert.ok(d.commands.every(button=>button.disabled));
  assert.ok(d.calls.every(([url])=>!url.endsWith('/command')));
});

test('viewers cannot change parameters or open fallback',async()=>{
  const d=dashboard('viewer');await d.window.LaserControl.refresh();
  assert.equal(d.get('lcPrepare').disabled,true);
  assert.equal(d.get('lcFallback').disabled,true);
  assert.equal(d.get('lcSaveProfile').disabled,true);
});

test('inactive, hidden and signed-out dashboards do not poll',async()=>{
  const d=dashboard();d.document.hidden=true;d.poll();
  d.document.hidden=false;d.state.activeView='process';d.poll();
  d.state.activeView='laser-control';d.state.user=null;d.poll();
  await Promise.resolve();assert.equal(d.calls.length,0);
});

test('saving a draft sends parameters and never starts or transfers',async()=>{
  const d=dashboard();await d.window.LaserControl.refresh();
  d.get('lcSpeed').value='25';d.get('lcParametersForm').oninput();
  d.get('lcParametersForm').onsubmit({preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  const writes=d.calls.filter(([,options])=>options.method==='POST');
  assert.equal(writes.length,1);assert.ok(writes[0][0].endsWith('/parameters'));
  assert.equal(JSON.parse(writes[0][1].body).parameters.speed_mm_s,25);
  assert.ok(d.commands.every(button=>button.disabled));
});

test('logout clears selected job and history',async()=>{
  const d=dashboard();await d.window.LaserControl.refresh();d.window.LaserControl.reset();
  assert.equal(d.get('lcFilename').textContent,'Waiting for a DXF');
  assert.equal(d.get('lcHistory').children.length,0);
});

test('matching LaserCAD job enables available job tools without enabling direct commands',async()=>{
  const d=dashboard('admin',{available:true,matching_job:true,actions:{download:true,frame:true,stop:true}});
  await d.window.LaserControl.refresh();
  assert.ok(d.fallbackButtons.slice(0,3).every(button=>!button.disabled));
  assert.ok(d.commands.every(button=>button.disabled));
  assert.equal(d.get('lcValidate').disabled,false);
  assert.match(d.get('lcDownload').href,/jobs\/job1\/file$/);
  d.fallbackButtons[0].onclick();await new Promise(resolve=>setImmediate(resolve));
  const writes=d.calls.filter(([,options])=>options.method==='POST');
  assert.equal(writes.length,1);assert.ok(writes[0][0].endsWith('/fallback-command'));
  assert.equal(JSON.parse(writes[0][1].body).action,'download');
});

test('read-only accounts cannot use LaserCAD job actions',async()=>{
  const d=dashboard('viewer',{available:true,matching_job:true,actions:{download:true,frame:true,stop:true}});
  await d.window.LaserControl.refresh();assert.ok(d.fallbackButtons.every(button=>button.disabled));
});


for (const [action,observed] of [['start',null],['pause','running'],['resume','paused']]) {
 test(action+' sends one explicit button request without any approval popup',async()=>{
  const d=dashboard('admin',{available:true,matching_job:true,window_title:'LaserCAD - job.dxf',communication_selection:['MachName:COM4'],actions:{[action]:true}});
  d.window.confirm=()=>{throw Error('Popup approval must not be used')};
  await d.window.LaserControl.refresh();
  const button=d.fallbackButtons.find(b=>b.dataset.lcFallbackAction===action);
  if(observed){assert.equal(button.disabled,true);d.get('lcObservedState').value=observed;d.get('lcObservedState').onchange()}
  button.onclick();await new Promise(resolve=>setImmediate(resolve));
  const writes=d.calls.filter(([,o])=>o.method==='POST');assert.equal(writes.length,1);
  const payload=JSON.parse(writes[0][1].body);
  assert.equal(payload.action,action);assert.equal(payload.operator_confirmed,true);
  assert.equal(payload.revision,1);assert.equal(payload.dxf_sha256,'digest');
  assert.equal(payload.observed_state,observed);assert.deepEqual(payload.communication_selection,['MachName:COM4']);
  if(observed){assert.equal(d.get('lcObservedState').value,'');assert.equal(button.disabled,true)}
  assert.ok(d.commands.every(b=>b.disabled));
 });
}

test('split and restore use only window-layout requests',async()=>{
 const d=dashboard('admin',{available:true,matching_job:true,actions:{}});
 await d.window.LaserControl.refresh();assert.equal(d.get('lcSplit').disabled,false);
 await d.get('lcSplit').onclick();await d.get('lcRestore').onclick();
 const writes=d.calls.filter(([,o])=>o.method==='POST');assert.equal(writes.length,2);
 assert.ok(writes.every(([url])=>url.endsWith('/layout')));
 assert.deepEqual(writes.map(([,o])=>JSON.parse(o.body).action),['split','restore']);
});


test('automatic split and layout failure reason stay visible',async()=>{
 const d=dashboard('admin',null,{split_after_dxf:true,desktop_layout_status:{status:'unavailable',message:'Open the matching DXF first.'}});
 await d.window.LaserControl.refresh();
 assert.match(d.get('lcLayoutStatus').textContent,/export is on/);
 assert.match(d.get('lcLayoutStatus').textContent,/matching DXF/);
 assert.equal(d.get('lcLayoutStatus').className,'lc-error');
});

test('explicit automatic split opt-out is displayed',async()=>{
 const d=dashboard('admin',null,{split_after_dxf:false});await d.window.LaserControl.refresh();
 assert.match(d.get('lcLayoutStatus').textContent,/Automatic split is off/);
});


test('guided cycle requires a review button action without a popup',async()=>{
 const extra={supervised:{active:{id:'cycle-1',job_id:'job1',state:'awaiting_review'},recent:[]}};
 const d=dashboard('admin',{available:true,matching_job:true,window_title:'LaserCAD - job1.dxf',communication_selection:['Machine A'],actions:{start:true,download:true}},extra);
 await d.window.LaserControl.refresh();
 const start=d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='start');
 assert.equal(start.disabled,true);assert.equal(d.get('lcCycleReady').disabled,true);
 d.window.confirm=()=>{throw Error('No popup approval')};await d.get('lcCycleReview').onclick();
 const approval=d.calls.find(([url])=>url.endsWith('/cycle/review'));
 assert.equal(JSON.parse(approval[1].body).cycle_id,'cycle-1');
 assert.equal(start.disabled,false);
});

test('completion is explicitly confirmed and never captures the next sheath',async()=>{
 const extra={supervised:{active:{id:'cycle-1',job_id:'job1',state:'awaiting_completion',filename:'sheath.dxf'},recent:[]}};
 const d=dashboard('admin',{available:true,matching_job:true,actions:{start:true,download:true,pause:true,resume:true,stop:true}},extra);
 await d.window.LaserControl.refresh();
 assert.equal(d.get('lcCycleComplete').disabled,false);assert.equal(d.get('lcCycleReady').disabled,true);
 assert.equal(d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='start').disabled,true);
 d.window.confirm=()=>{throw Error('No popup approval')};await d.get('lcCycleComplete').onclick();
 const writes=d.calls.filter(([,o])=>o.method==='POST');assert.equal(writes.length,1);
 const payload=JSON.parse(writes[0][1].body);
 assert.equal(payload.outcome,'completed');assert.equal(payload.machine_stopped,true);assert.equal(payload.operator_confirmed,true);
 assert.equal(d.window.preparations,undefined);
 assert.equal(d.get('lcCycleReady').disabled,false);
 d.get('lcCycleReady').onclick();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(d.window.preparations,1);
});

test('viewers cannot approve or end supervised cycles',async()=>{
 const d=dashboard('viewer',null,{supervised:{active:{id:'cycle-1',job_id:'job1',state:'awaiting_completion'},recent:[]}});
 await d.window.LaserControl.refresh();
 for(const id of ['lcCycleReady','lcCycleReview','lcCycleComplete','lcCycleAbort'])assert.equal(d.get(id).disabled,true);
});


test('wrong observed state cannot enable a Pause or Resume toggle',async()=>{
 const d=dashboard('admin',{available:true,matching_job:true,actions:{pause:true,resume:true}});
 await d.window.LaserControl.refresh();
 const pause=d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='pause'),resume=d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='resume');
 assert.equal(pause.disabled,true);assert.equal(resume.disabled,true);
 d.get('lcObservedState').value='paused';d.get('lcObservedState').onchange();
 assert.equal(pause.disabled,true);assert.equal(resume.disabled,false);
 d.get('lcObservedState').value='running';d.get('lcObservedState').onchange();
 assert.equal(pause.disabled,false);assert.equal(resume.disabled,true);
});

test('dirty drafts block replacement inline instead of showing approvals',async()=>{
 const d=dashboard();await d.window.LaserControl.refresh();
 d.window.confirm=()=>{throw Error('No popup approval')};
 d.get('lcParametersForm').oninput();assert.equal(d.get('lcDiscardDraft').disabled,false);
 const before=d.calls.length;d.get('lcRefresh').onclick();
 assert.equal(d.calls.length,before);assert.match(d.get('lcMessage').textContent,/Discard draft/);
});


test('uncertain toggle delivery clears observed state and never retries',async()=>{
 const d=dashboard('admin',{available:true,matching_job:true,actions:{pause:true,resume:true}},{panelError:true});
 await d.window.LaserControl.refresh();d.get('lcObservedState').value='running';d.get('lcObservedState').onchange();
 d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='pause').onclick();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(d.calls.filter(([,o])=>o.method==='POST').length,1);
 assert.equal(d.get('lcObservedState').value,'');
 assert.equal(d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='pause').disabled,true);
 assert.equal(d.fallbackButtons.find(b=>b.dataset.lcFallbackAction==='resume').disabled,true);
 assert.match(d.get('lcMessage').textContent,/unknown/);
});
