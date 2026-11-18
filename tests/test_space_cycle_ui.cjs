const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/space-cycle.js'),'utf8');
function harness(options={}){
 const returnAfterStart=options.returnAfterStart;
 const nodes=new Map(),keys={};
 function node(){const attrs={},classes=new Set();return {hidden:false,dataset:{},children:[],listeners:{},classList:{contains:k=>classes.has(k),toggle(k,on){if(on)classes.add(k);else classes.delete(k);}},setAttribute(k,v){attrs[k]=String(v);},getAttribute:k=>attrs[k]??null,append(...items){this.children.push(...items);for(const i of items)i.parentNode=this;},prepend(item){item.parentNode=this;this.children.unshift(item);},addEventListener(k,f){this.listeners[k]=f;},click(){if(!this.disabled){this.onclick?.();this.listeners.click?.();}},focus(){context.document.activeElement=this;}};}
 const get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
 const process=node(),state={user:{},activeView:'process',pendingMutations:0},calls=[],events=[];
 let cameraCount=0;const confirmations=[];
 get('processSheath').textContent='Process Sheath';
 let stage=options.stage || 'ready_process',revision=0,cycleId='cycle',resetReceipt=null,startedReceipt=null;
 const status=()=>({stage,cycle_id:cycleId,revision,job:stage==='ready_process'?null:{path:'job.dxf'},reset_from:resetReceipt,started_from:startedReceipt,next_action:stage.startsWith('ready_')?'advance':null,message:stage});
 const context={console,state,displayError:error=>error?.message || String(error),$:get,document:{body:{dataset:{screen:'process'}},hidden:false,hasFocus:()=>true,createElement:node,querySelector:()=>process,addEventListener(k,f){keys[k]=f;}},window:{addEventListener(){},LaserCADTab:{hide:async()=>{events.push('hide');if(options.hide)await options.hide();}}},hasPermission:()=>options.permission!==false,crypto:{randomUUID:()=>String(Math.random())},confirm:text=>{confirmations.push(text);return options.confirm!==false},setInterval(fn){context.pollTick=fn;},setTimeout,Date,message(text){events.push('message:'+text);},navigateSection:view=>{state.activeView=view;context.document.body.dataset.screen=view;get('processSheath').setAttribute('aria-pressed',String(view==='process'));get('laserCadTab').classList.toggle('active',view==='lasercad');events.push('view:'+view);},
 refreshCamera:async settings=>{assert.equal(settings.required,true);cameraCount++;events.push('refresh_camera');if(options.camera)await options.camera();return options.refreshResult===false ? false : true;},
 api:async(url,options)=>{if(!options?.method)return status();const data=JSON.parse(options.body);calls.push([url,data]);assert.equal(data.revision,revision);revision++;if(url.endsWith('/reset')){resetReceipt={cycle_id:data.cycle_id,revision:data.revision,completed:data.completed,operator_confirmed_idle:data.operator_confirmed_idle};stage='ready_process';cycleId='cycle-'+revision;}else if(returnAfterStart){startedReceipt={cycle_id:data.cycle_id,revision:data.revision};stage='ready_process';cycleId='started-'+revision;}else stage={ready_start:'awaiting_completion'}[stage];return status();},
 runWorkflow:async s=>{assert.equal(s.stage,'ready_process');calls.push(['process',s]);events.push('process');if(options.process)await options.process();stage=options.mode==='manual'?'awaiting_manual_completion':'ready_start';revision++;},};
 get('processSheath').onclick=()=>{events.push('click:processSheath');context.navigateSection('process');};
 vm.runInNewContext(source,context);
 return {context,calls,events,get,keys,process,confirmations,cameraCount:()=>cameraCount,serverStage:value=>{stage=value;revision++;},action:()=>process.children.find(x=>x.id==='spaceCycleBar')?.children.find(x=>x.id==='automaticWorkflowAction') || get('laserCadTab').children.find(x=>x.id==='spaceCycleBar')?.children.find(x=>x.id==='automaticWorkflowAction'),flush:()=>new Promise(resolve=>setImmediate(resolve)),status};
}
function event(changes={}){return {code:'Space',target:{closest:()=>null},preventDefault(){this.prevented=true;},...changes};}
test('Automated uses two fresh Space presses for processing and Start then returns ready',async()=>{
 const h=harness({returnAfterStart:true,confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
 for(const stage of ['ready_start','ready_process']){h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.status().stage,stage)}
 assert.equal(h.calls.length,2);assert.equal(h.confirmations.length,0);assert.equal(h.cameraCount(),1);
 assert.equal(h.context.state.activeView,'process');assert.ok(!h.calls.some(([url])=>url.endsWith('/reset')));
});

test('Enter and numeric Enter cannot run processing, frame or Start',async()=>{
 const h=harness();await h.flush();const c=h.context.window.SpaceCycle;
 for(const stage of ['ready_process','ready_start']){
   assert.equal(h.status().stage,stage);const before=h.calls.length;
   for(const code of ['Enter','NumpadEnter']){c.keydown(event({code}));c.nativePress(code);await h.flush();assert.equal(h.calls.length,before)}
   if(stage!=='awaiting_completion'){h.keys.keyup({code:'Space'});c.keydown(event());await h.flush()}
 }
 assert.ok(h.calls.every(([url,data])=>url==='process'||['ready_start'].includes(data.expected_stage)));
});

test('text fields, dialogs, modifiers, held keys and other pages do not dispatch',async()=>{
 const h=harness();await h.flush();const c=h.context.window.SpaceCycle;
 for(const key of ['repeat','defaultPrevented','isComposing','ctrlKey','altKey','shiftKey','metaKey'])c.keydown(event({[key]:true}));
 c.keydown(event({target:{closest:()=>({})}}));h.context.state.activeView='settings';c.keydown(event());
 await h.flush();assert.equal(h.calls.length,0);
});

test('focused next-action button cannot be activated by Enter but accepts one Space',async()=>{
 const h=harness();await h.flush();const button=h.action();
 for(const code of ['Enter','NumpadEnter']){const e=event({code});button.listeners.keydown(e);assert.equal(e.prevented,true)}
 await h.flush();assert.equal(h.calls.length,0);
 button.listeners.keydown(event());await h.flush();assert.equal(h.calls[0][0],'process');
});

test('native Space requests Start and returns Process Sheath; Tab is ignored',async()=>{
 const h=harness({stage:'ready_start',returnAfterStart:true});await h.flush();h.context.state.activeView='lasercad';
 const c=h.context.window.SpaceCycle;c.nativePress('Tab');await h.flush();assert.equal(h.calls.length,0);
 c.nativePress('Space');c.nativePress('Space');await h.flush();assert.equal(h.status().stage,'ready_process');assert.equal(h.calls.length,1);assert.equal(h.context.state.activeView,'process');
});

test('processing remains single-flight; additional presses cannot Start before processing finishes',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);const h=harness({process:()=>gate});await h.flush();
 const c=h.context.window.SpaceCycle;c.keydown(event());await h.flush();
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.calls.length,1);
 release();await h.flush();assert.equal(h.status().stage,'ready_start');
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.status().stage,'awaiting_completion');
});

test('camera refresh, existing workflow and pending mutations block a new capture',async()=>{
 for(const property of ['cameraRefreshing','workflowBusy','pendingMutations']){
  const h=harness();await h.flush();h.context.state[property]=true;h.context.window.SpaceCycle.keydown(event());await h.flush();assert.equal(h.calls.length,0);
 }
});

test('Process Sheath and workflow reference remain navigation controls without Datum or camera reconnect',async()=>{
 const h=harness();await h.flush();h.get('automaticWorkflow').listeners.click();
 assert.equal(h.get('processSheath').textContent,'Process Sheath');assert.equal(h.calls.length,0);assert.equal(h.cameraCount(),0);
});

test('interrupted cycle requires explicit idle recovery before first Space',async()=>{
 const h=harness({stage:'interrupted'});await h.flush();h.context.window.SpaceCycle.keydown(event());await h.flush();assert.equal(h.calls.length,0);
 const bar=h.process.children.find(x=>x.id==='spaceCycleBar');await bar.children.find(x=>x.textContent==='Machine idle — reset cycle').listeners.click();await h.flush();
 assert.equal(h.status().stage,'ready_process');assert.equal(h.calls[0][1].operator_confirmed_idle,true);
});

test('declining completion leaves Start stage complete and does not reset',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();
 const bar=h.process.children.find(x=>x.id==='spaceCycleBar');await bar.children.find(x=>x.textContent==='Confirm cutting completed (Space)').listeners.click();await h.flush();
 assert.equal(h.status().stage,'awaiting_completion');assert.equal(h.calls.length,0);
});


test('completion Space resets directly; held/repeated presses do not capture the next sheath',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());c.keydown(event({repeat:true}));await h.flush();assert.equal(h.status().stage,'ready_process');assert.equal(h.calls.length,1);
 c.keydown(event({repeat:true}));c.keydown(event());await h.flush();assert.equal(h.calls.length,1);assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.status().stage,'ready_start');
});

test('browser and native completion Space send only reset and never ask for another dialog',async()=>{
 for(const input of ['browser','native']){
  const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
  if(input==='native'){h.context.state.activeView='lasercad';c.nativePress('Space')}else c.keydown(event());
  await h.flush();assert.equal(h.calls.length,1);assert.ok(h.calls[0][0].endsWith('/reset'));assert.equal(h.calls[0][1].operator_confirmed_idle,true);assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
 }
});

test('completion Space respects editable controls, modifiers, busy state and permissions',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event({target:{closest:()=>({})}}));c.keydown(event({ctrlKey:true}));h.context.state.workflowBusy=true;c.nativePress('Space');c.keydown(event());await h.flush();assert.equal(h.calls.length,0);
 const viewer=harness({stage:'awaiting_completion',permission:false});await viewer.flush();viewer.context.state.activeView='lasercad';viewer.context.window.SpaceCycle.nativePress('Space');await viewer.flush();assert.equal(viewer.calls.length,0);
});

test('completion Space cannot silently recover an interrupted cycle',async()=>{
 const h=harness({stage:'interrupted'});await h.flush();h.context.window.SpaceCycle.keydown(event());h.context.state.activeView='lasercad';h.context.window.SpaceCycle.nativePress('Space');await h.flush();assert.equal(h.calls.length,0);assert.equal(h.confirmations.length,0);
});

test('every cycle first Space refreshes camera and post-Start return never captures',async()=>{
 const h=harness({returnAfterStart:true});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);
 assert.ok(h.events.indexOf('hide')<h.events.indexOf('refresh_camera'));assert.ok(h.events.indexOf('refresh_camera')<h.events.indexOf('process'));
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.status().stage,'ready_process');assert.equal(h.cameraCount(),1);
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),2);assert.equal(h.status().stage,'ready_start');
});

test('camera refresh failure blocks capture and download; next first Space retries refresh',async()=>{
 let fail=true;const h=harness({camera:async()=>{if(fail)throw Error('Camera disconnected')}});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());await h.flush();assert.equal(h.calls.length,0);assert.equal(h.status().stage,'ready_process');assert.equal(h.cameraCount(),1);
 fail=false;h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),2);assert.equal(h.calls.length,1);assert.equal(h.calls[0][0],'process');
});

test('a skipped camera refresh is never accepted as a successful capture prerequisite',async()=>{
 const h=harness({refreshResult:false});await h.flush();h.context.window.SpaceCycle.keydown(event());await h.flush();
 assert.equal(h.calls.length,0);assert.equal(h.status().stage,'ready_process');
});

test('waiting camera refresh blocks held/repeated presses from starting processing early',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);const h=harness({camera:()=>gate});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.calls.length,0);assert.equal(h.action().disabled,true);
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.calls.length,0);
 release();await h.flush();assert.equal(h.calls.length,1);assert.equal(h.status().stage,'ready_start');
});

test('first Space waits for native display mutations before mandatory refresh',async()=>{
 const h=harness({hide:async()=>{h.context.state.pendingMutations=1;setTimeout(()=>{h.context.state.pendingMutations=0},25)}});await h.flush();
 h.context.window.SpaceCycle.keydown(event());await h.flush();assert.equal(h.cameraCount(),0);assert.equal(h.calls.length,0);
 await new Promise(resolve=>setTimeout(resolve,85));await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.calls.length,1);
});

function actualCameraRefresh(state={}){
 const app=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
 const first=app.indexOf('async function refreshCamera('),last=app.indexOf('async function loadTemplates()',first);
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,{options:[]});return nodes.get(id)};const calls=[];
 const context={state,$:get,cameraImage:{src:'old'},loadCameras:async()=>calls.push('load'),api:async()=>({connected:false}),openCamera:async()=>calls.push('open'),showWorkflowPreview(){},message(){},displayError:e=>e.message};
 return {fn:vm.runInNewContext('('+app.slice(first,last)+')',context),calls,state};
}

test('real refreshCamera reports success only after reconnect and releases its busy flag',async()=>{
 const a=actualCameraRefresh();assert.equal(await a.fn({required:true}),true);assert.deepEqual(a.calls,['load','open']);assert.equal(a.state.cameraRefreshing,false);
});

test('real refreshCamera required mode rejects busy/skipped refresh while the manual button stays compatible',async()=>{
 const a=actualCameraRefresh({pendingMutations:1});await assert.rejects(a.fn({required:true}),/still busy/);assert.equal(await a.fn(),false);assert.equal(a.calls.length,0);
});

function actualApiWithPending(state){
 const app=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
 const first=app.indexOf('async function api('),last=app.indexOf('function message(',first);
 let release;const gate=new Promise(resolve=>release=resolve);
 const context={state,FormData:class {},hasPermission:()=>true,actionPermission:()=> 'operate',displayError:e=>String(e),fetch:()=>gate};
 const fn=vm.runInNewContext('('+app.slice(first,last)+')',context);
 return {fn,release:()=>release({ok:true,json:async()=>({status:'hosted'})})};
}

test('second Space reaches Start while the embedded-display heartbeat is in flight',async()=>{
 const h=harness({stage:'ready_start'});await h.flush();const c=h.context.window.SpaceCycle;h.context.state.activeView='lasercad';
 const display=actualApiWithPending(h.context.state);const showing=display.fn('/api/laser-control/lasercad-host',{method:'POST',body:JSON.stringify({action:'show'})});
 c.nativePress('Space');await h.flush();assert.equal(h.status().stage,'awaiting_completion');assert.equal(h.calls.length,1);
 c.nativePress('Tab');await h.flush();assert.equal(h.calls.length,1);
 display.release();await showing;assert.equal(h.context.state.pendingMutations || 0,0);
});

test('real pending mutations still block Start and a fresh Space is required after they finish',async()=>{
 const h=harness({stage:'ready_start'});await h.flush();h.context.state.activeView='lasercad';h.context.state.pendingMutations=0;
 const mutation=actualApiWithPending(h.context.state);const saving=mutation.fn('/api/config',{method:'POST',body:'{}'});
 h.context.window.SpaceCycle.nativePress('Space');await h.flush();assert.equal(h.calls.length,0);
 mutation.release();await saving;await h.flush();assert.equal(h.calls.length,0);
 h.context.window.SpaceCycle.nativePress('Space');await h.flush();assert.equal(h.status().stage,'awaiting_completion');assert.equal(h.calls.length,1);
});


test('Tab and Shift+Tab keep normal navigation at every stage in both modes',async()=>{
 for(const stage of ['ready_process','ready_start','processing','interrupted','awaiting_completion','awaiting_manual_completion'])for(const shiftKey of [false,true]){
  const h=harness({stage});await h.flush();const e=event({code:'Tab',shiftKey});h.context.window.SpaceCycle.keydown(e);h.context.state.activeView='lasercad';h.context.window.SpaceCycle.nativePress('Tab');await h.flush();
  assert.equal(e.prevented,undefined);assert.equal(h.calls.length,0);assert.equal(h.confirmations.length,0);
 }
});

test('Enter does not confirm completion, including on the completion button',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();const c=h.context.window.SpaceCycle;
 const bar=h.process.children.find(x=>x.id==='spaceCycleBar'),done=bar.children.find(x=>x.textContent==='Confirm cutting completed (Space)');
 for(const code of ['Enter','NumpadEnter']){c.keydown(event({code}));done.listeners.keydown(event({code}));h.context.state.activeView='lasercad';c.nativePress(code);}
 await h.flush();assert.equal(h.calls.length,0);assert.equal(h.confirmations.length,0);assert.equal(h.status().stage,'awaiting_completion');
});


test('completion button still confirms; Tab does not bypass interrupted recovery',async()=>{
 const h=harness({stage:'interrupted',confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event({code:'Tab'}));await h.flush();assert.equal(h.calls.length,0);assert.equal(h.confirmations.length,0);
 const bar=h.process.children.find(x=>x.id==='spaceCycleBar');await bar.children.find(x=>x.textContent==='Machine idle — reset cycle').listeners.click();await h.flush();
 assert.equal(h.confirmations.length,1);assert.equal(h.calls.length,0);assert.equal(h.status().stage,'interrupted');
});

test('two cycles refresh and Start once each without Run Box or completion reset',async()=>{
 const h=harness({returnAfterStart:true});await h.flush();const c=h.context.window.SpaceCycle;
 for(let i=0;i<2;i++)for(const stage of ['ready_start','ready_process']){h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.status().stage,stage)}
 assert.equal(h.cameraCount(),2);assert.equal(h.calls.filter(([url])=>url.endsWith('/action')).length,2);assert.equal(h.calls.filter(([url])=>url.endsWith('/reset')).length,0);assert.equal(h.confirmations.length,0);
});

test('Manual keeps first Space through Download Document; next fresh Space resets without Start',async()=>{
 const h=harness({mode:'manual',confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());await h.flush();assert.equal(h.status().stage,'awaiting_manual_completion');assert.equal(h.cameraCount(),1);
 c.keydown(event({repeat:true}));await h.flush();assert.equal(h.calls.length,1);
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.status().stage,'ready_process');assert.equal(h.calls.length,2);assert.ok(h.calls[1][0].endsWith('/reset'));assert.equal(h.confirmations.length,0);
});

test('Manual completion Space stays blocked in editable fields and with modifiers',async()=>{
 const h=harness({stage:'awaiting_manual_completion'});await h.flush();h.context.window.SpaceCycle.keydown(event({shiftKey:true}));h.context.window.SpaceCycle.keydown(event({target:{closest:()=>({})}}));await h.flush();assert.equal(h.calls.length,0);
});

test('Read-only and Manager retain assigned Manual flow and Automated post-Start home return',async()=>{
 for(const role of ['viewer','editor'])for(const mode of ['manual','automated']){
  const h=harness({mode,returnAfterStart:true,confirm:false});await h.flush();h.context.state.user={role,workflow_mode:mode};const c=h.context.window.SpaceCycle;
  c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);
  assert.equal(h.status().stage,mode==='automated'?'ready_start':'awaiting_manual_completion');
  h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.status().stage,'ready_process');assert.equal(h.cameraCount(),1);assert.equal(h.confirmations.length,0);
  assert.ok(h.calls.at(-1)[0].endsWith(mode==='automated'?'/action':'/reset'));
 }
});

test('Space on the focused completion button resets directly and prevents a second browser activation',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
 const bar=h.process.children.find(x=>x.id==='spaceCycleBar'),done=bar.children.find(x=>x.textContent==='Confirm cutting completed (Space)');
 const e=event();done.listeners.keydown(e);assert.equal(e.prevented,true);e.defaultPrevented=true;c.keydown(e);await h.flush();
 assert.equal(h.calls.length,1);assert.ok(h.calls[0][0].endsWith('/reset'));assert.equal(h.confirmations.length,0);assert.equal(h.cameraCount(),0);
});


test('next Space after post-Start return refreshes for new sheath without another Start',async()=>{
 const h=harness({returnAfterStart:true});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());await h.flush();h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();
 const before=h.events.length;h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();const events=h.events.slice(before);
 assert.ok(events.indexOf('refresh_camera')<events.indexOf('process'));assert.equal(h.cameraCount(),2);
 assert.equal(h.calls.filter(([url])=>url.endsWith('/action')).length,1);assert.equal(h.calls.filter(([url])=>url.endsWith('/reset')).length,0);
});

test('completion Space clicks Process Sheath once after reset and focuses the next-cycle workspace',async()=>{
 for(const stage of ['awaiting_completion','awaiting_manual_completion'])for(const input of ['native','process-button']){
  const h=harness({stage,confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
  h.context.state.activeView='lasercad';h.context.document.activeElement=h.get('processSheath');
  if(input==='native')c.nativePress('Space');
  else {const e=event({target:h.get('processSheath')});h.get('processSheath').listeners.keydown(e);assert.equal(e.prevented,true);}
  await h.flush();assert.equal(h.status().stage,'ready_process');assert.equal(h.context.state.activeView,'process');
  assert.equal(h.events.filter(x=>x==='click:processSheath').length,1);assert.equal(h.context.document.activeElement,h.get('overlay'));
  assert.equal(h.calls.length,1);assert.ok(h.calls[0][0].endsWith('/reset'));assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
  c.keydown(event({repeat:true}));await h.flush();assert.equal(h.calls.length,1);
  h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.calls.at(-1)[0],'process');
 }
});

test('clicking Process Sheath with the mouse only navigates and never confirms cutting',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();h.context.state.activeView='lasercad';
 h.get('processSheath').click();await h.flush();assert.equal(h.context.state.activeView,'process');
 assert.equal(h.status().stage,'awaiting_completion');assert.equal(h.calls.length,0);
});


test('cycle-pinned native completion resets and opens Process Sheath without generic advance',async()=>{
 for(const stage of ['awaiting_completion','awaiting_manual_completion']){
  const h=harness({stage,confirm:false});await h.flush();h.context.state.activeView='lasercad';const c=h.context.window.SpaceCycle;
  const edge={...h.status()};const result=await c.nativeComplete(edge);await h.flush();
  assert.equal(result.status,'reset');assert.equal(h.calls.length,1);assert.ok(h.calls[0][0].endsWith('/reset'));
  assert.equal(h.events.filter(x=>x==='click:processSheath').length,1);assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
  h.context.state.activeView='lasercad';await c.nativeComplete(edge);await h.flush();assert.equal(h.calls.length,1);
  h.context.state.activeView='process';h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);
 }
});

test('native completion cannot become Start when the browser still shows ready_start',async()=>{
 const h=harness({stage:'ready_start',confirm:false});await h.flush();h.context.state.activeView='lasercad';
 h.serverStage('awaiting_completion');await h.context.window.SpaceCycle.nativeComplete({...h.status()});await h.flush();
 assert.equal(h.calls.length,1);assert.ok(h.calls[0][0].endsWith('/reset'));assert.equal(h.status().stage,'ready_process');
});

test('malformed or stale native completion never authorizes Start or captures a new sheath',async()=>{
 const h=harness({stage:'ready_start'});await h.flush();h.context.state.activeView='lasercad';const c=h.context.window.SpaceCycle;
 await c.nativeComplete({...h.status()});await c.nativeComplete({stage:'awaiting_completion',cycle_id:'old-cycle',revision:0});await h.flush();
 assert.equal(h.calls.length,0);assert.equal(h.cameraCount(),0);assert.equal(h.status().stage,'ready_start');
});


test('reset succeeds and broken Process Sheath click recovers through the existing navigator only once',async()=>{
 for(const failure of ['throw','noop']){
  const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();h.context.state.activeView='lasercad';let clicks=0;
  h.get('processSheath').onclick=()=>{clicks++;if(failure==='throw')throw Error('handler failed');};
  const result=await h.context.window.SpaceCycle.nativeComplete({...h.status()});await h.flush();
  assert.equal(result.status,'reset');assert.equal(clicks,1);assert.equal(h.calls.length,1);assert.ok(h.calls[0][0].endsWith('/reset'));
  assert.equal(h.context.state.activeView,'process');assert.equal(h.context.document.body.dataset.screen,'process');assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
 }
});

test('persistent navigation failure keeps recovery and a fresh Space retries navigation, never reset or Start',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();h.context.state.activeView='lasercad';const navigator=h.context.navigateSection;let clicks=0;
 h.get('processSheath').onclick=()=>{clicks++;};h.context.navigateSection=()=>{throw Error('DOM unavailable')};
 const c=h.context.window.SpaceCycle;const old={...h.status()};assert.equal((await c.nativeComplete(old)).status,'recovery_pending');
 assert.equal(h.status().stage,'ready_process');assert.equal(h.calls.length,1);assert.equal(clicks,1);
 h.context.navigateSection=navigator;c.nativePress('Space');await h.flush();
 assert.equal(h.context.state.activeView,'process');assert.equal(h.calls.length,1);assert.equal(clicks,1);assert.equal(h.cameraCount(),0);
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.calls.at(-1)[0],'process');
});

test('lost reset response is reconciled with durable receipt without another reset POST',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();h.context.state.activeView='lasercad';const original=h.context.api;
 h.context.api=async(url,opts)=>{const result=await original(url,opts);if(url.endsWith('/reset'))throw Error('response lost');return result;};
 const result=await h.context.window.SpaceCycle.nativeComplete({...h.status()});await h.flush();
 assert.equal(result.status,'reset');assert.equal(h.context.state.activeView,'process');assert.equal(h.calls.length,1);assert.equal(h.events.filter(e=>e==='click:processSheath').length,1);assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
});

test('lost reset response and failed status read block later actions until receipt is checked',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();h.context.state.activeView='lasercad';const original=h.context.api;let unavailable=true;
 h.context.api=async(url,opts)=>{if(!opts?.method&&unavailable)throw Error('network unavailable');const result=await original(url,opts);if(url.endsWith('/reset'))throw Error('response lost');return result;};
 const c=h.context.window.SpaceCycle;const old={...h.status()};assert.equal((await c.nativeComplete(old)).status,'recovery_pending');assert.equal(h.calls.length,1);
 unavailable=false;c.nativePress('Space');await h.flush();assert.equal(h.context.state.activeView,'process');assert.equal(h.calls.length,1);assert.equal(h.cameraCount(),0);
});

test('a failed uncommitted reset is checked before a later operator Space retries it',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();h.context.state.activeView='lasercad';const original=h.context.api;let fail=true,attempts=0;const order=[];
 h.context.api=async(url,opts)=>{order.push(opts?.method?'POST':'GET');if(url.endsWith('/reset')){attempts++;if(fail)throw Error('storage error');}return original(url,opts);};
 const c=h.context.window.SpaceCycle;assert.equal((await c.nativeComplete({...h.status()})).status,'recovery_pending');assert.equal(attempts,1);assert.equal(h.status().stage,'awaiting_completion');
 fail=false;order.length=0;c.nativePress('Space');await h.flush();assert.deepEqual(order.slice(0,2),['GET','POST']);assert.equal(attempts,2);assert.equal(h.status().stage,'ready_process');assert.equal(h.context.state.activeView,'process');
});

test('changed backend cycle without matching receipt never authorizes a recovery reset',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();h.context.state.activeView='lasercad';const original=h.context.api;let attempts=0;
 h.context.api=async(url,opts)=>{if(url.endsWith('/reset')){attempts++;h.serverStage('interrupted');throw Error('uncertain response');}return original(url,opts);};
 const c=h.context.window.SpaceCycle;await c.nativeComplete({...h.status()});c.nativePress('Space');await h.flush();assert.equal(attempts,1);assert.equal(h.cameraCount(),0);assert.equal(h.events.filter(e=>e==='click:processSheath').length,0);
});

test('concurrent native completion callbacks use one reset and one Process Sheath activation',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();h.context.state.activeView='lasercad';const c=h.context.window.SpaceCycle,snapshot={...h.status()};
 await Promise.all([c.nativeComplete(snapshot),c.nativeComplete(snapshot),c.nativeComplete(snapshot)]);await h.flush();assert.equal(h.calls.length,1);assert.equal(h.events.filter(e=>e==='click:processSheath').length,1);assert.equal(h.cameraCount(),0);
});

test('an older in-flight status poll cannot restore a completed cycle after reset',async()=>{
 const h=harness({stage:'awaiting_completion'});await h.flush();h.context.state.activeView='lasercad';const c=h.context.window.SpaceCycle,old={...h.status()},original=h.context.api;let release,gate=true;
 h.context.api=(url,opts)=>{if(!opts?.method&&gate){gate=false;return new Promise(resolve=>release=()=>resolve(old));}return original(url,opts);};
 const polling=h.context.pollTick();await h.flush();await c.nativeComplete(old);release();await polling;await h.flush();
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),1);assert.equal(h.calls.filter(c=>c[0].endsWith('/reset')).length,1);
});


test('completion runs the actual existing Process Sheath handler and navigation function',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();h.context.state.activeView='lasercad';h.get('laserCadTab').classList.toggle('active',true);h.get('laserCadTab').dataset.panel='lasercad';
 const app=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');const first=app.indexOf('function navigateSection(view) {'),last=app.indexOf('// Each placed polygon',first);
 h.context.document.querySelectorAll=selector=>selector==='.panel-view'?[h.get('laserCadTab')]:[];
 h.context.updateViewerMode=()=>{};h.context.draw=()=>{};h.context.fitCanvas=()=>{};h.context.window.requestAnimationFrame=()=>{};
 vm.runInNewContext(app.slice(first,last)+"\n$('processSheath').onclick = () => navigateSection('process');",h.context);
 const result=await h.context.window.SpaceCycle.nativeComplete({...h.status()});await h.flush();
 assert.equal(result.status,'reset');assert.equal(h.context.state.activeView,'process');assert.equal(h.context.document.body.dataset.screen,'process');assert.equal(h.get('processSheath').getAttribute('aria-pressed'),'true');assert.equal(h.get('laserCadTab').classList.contains('active'),false);assert.equal(h.calls.length,1);assert.equal(h.cameraCount(),0);
});

test('unavailable Process Sheath handler can recover later without resetting twice',async()=>{
 const h=harness({stage:'awaiting_completion',confirm:false});await h.flush();h.context.state.activeView='lasercad';const original=h.get('processSheath').onclick;h.get('processSheath').onclick=null;
 const c=h.context.window.SpaceCycle;assert.equal((await c.nativeComplete({...h.status()})).status,'recovery_pending');assert.equal(h.calls.length,1);
 h.get('processSheath').onclick=original;c.nativePress('Space');await h.flush();assert.equal(h.calls.length,1);assert.equal(h.events.filter(e=>e==='click:processSheath').length,1);assert.equal(h.context.state.activeView,'process');assert.equal(h.confirmations.length,0);
});


test('two Space workflow returns home immediately after Start without reset, popup or recapture',async()=>{
 const h=harness({returnAfterStart:true,confirm:false});await h.flush();const c=h.context.window.SpaceCycle;
 c.keydown(event());await h.flush();assert.equal(h.status().stage,'ready_start');
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();
 assert.equal(h.status().stage,'ready_process');assert.equal(h.context.state.activeView,'process');
 assert.equal(h.calls.length,2);assert.ok(h.calls[1][0].endsWith('/action'));
 assert.equal(h.calls.some(([url])=>url.endsWith('/reset')),false);
 assert.equal(h.confirmations.length,0);assert.equal(h.cameraCount(),1);
 assert.equal(h.events.filter(e=>e==='click:processSheath').length,1);
 c.keydown(event({repeat:true}));await h.flush();assert.equal(h.calls.length,2);
 h.keys.keyup({code:'Space'});c.keydown(event());await h.flush();assert.equal(h.cameraCount(),2);assert.equal(h.status().stage,'ready_start');
});

test('lost Start response reads receipt and returns home without another Start',async()=>{
 const h=harness({stage:'ready_start',returnAfterStart:true});await h.flush();const api=h.context.api;
 h.context.api=async(url,options)=>{const result=await api(url,options);if(url.endsWith('/action'))throw new Error('lost response');return result;};
 h.context.window.SpaceCycle.keydown(event());await h.flush();
 assert.equal(h.context.state.activeView,'process');assert.equal(h.calls.length,1);assert.equal(h.cameraCount(),0);assert.equal(h.confirmations.length,0);
});

test('failed post-Start navigation retries navigation only and never clicks Process Sheath twice',async()=>{
 const h=harness({stage:'ready_start',returnAfterStart:true});await h.flush();const nav=h.context.navigateSection;let failed=true;
 h.get('processSheath').onclick=()=>{h.events.push('click:processSheath');throw new Error('handler failed');};
 h.context.navigateSection=view=>{if(failed&&view==='process')throw new Error('navigation failed');nav(view);};
 h.context.window.SpaceCycle.keydown(event());await h.flush();assert.equal(h.calls.length,1);assert.equal(h.context.state.activeView,'lasercad');
 failed=false;h.keys.keyup({code:'Space'});h.context.window.SpaceCycle.nativePress('Space');await h.flush();
 assert.equal(h.context.state.activeView,'process');assert.equal(h.calls.length,1);assert.equal(h.cameraCount(),0);assert.equal(h.events.filter(e=>e==='click:processSheath').length,1);
});

test('duplicate second Space while Start is pending dispatches once',async()=>{
 let release;const gate=new Promise(resolve=>release=resolve);
 const h=harness({stage:'ready_start',returnAfterStart:true});await h.flush();const api=h.context.api;
 h.context.api=async(url,options)=>{if(url.endsWith('/action'))await gate;return api(url,options);};
 const c=h.context.window.SpaceCycle;c.keydown(event());c.nativePress('Space');c.nativePress('Space');
 release();await h.flush();assert.equal(h.calls.length,1);assert.equal(h.context.state.activeView,'process');assert.equal(h.cameraCount(),0);
});
