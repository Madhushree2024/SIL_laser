const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..');
function policy(){const logs=[],document={body:{dataset:{}}};const context={window:{},document,console:{error:(...args)=>logs.push(args)}};vm.runInNewContext(fs.readFileSync(path.join(root,'frontend/role-presentation.js'),'utf8'),context);return {p:context.window.RolePresentation,document,logs};}
test('Admin and Manager retain the exact original error text',()=>{
 const {p,logs}=policy();for(const role of ['admin','editor',undefined])for(const raw of ['SQLite failed at C:\\private\\report.csv','Camera frame capture failed','No controller SDK installed'])assert.equal(p.error(new Error(raw),{role,status:500}),raw);
 assert.equal(logs.length,0);
});
test('Read-only permission and session failures are clear and actionable',()=>{
 const {p}=policy();assert.match(p.error(new Error('Administrator permission required'),{role:'viewer',status:403}),/read-only.*Admin or Manager/);
 assert.match(p.error(new Error('token rejected'),{role:'viewer',status:401}),/sign in again/);
});
test('Read-only technical details are logged and never placed in the visible message',()=>{
 const {p,logs}=policy();const raw='SQLite SELECT failed in C:\\private\\db.sqlite3 at line 41';
 const shown=p.error(new Error(raw),{role:'viewer',status:500});assert.match(shown,/Try again or ask an Admin/);assert.doesNotMatch(shown,/SQLite|SELECT|private|line 41/);
 assert.equal(logs[0][1].detail,raw);assert.equal(p.error(shown,{role:'viewer'}),shown);
});
test('Read-only file, camera, report and network errors use specific plain messages',()=>{
 const {p}=policy();for(const [text,options,expected] of [['native API failed',{path:'/api/production'},/report could not/],['frame capture failed',{},/camera is unavailable/],['failed to fetch',{},/Reopen/],['file gone',{status:404},/not found/],['DXF parse failed',{},/file could not/]])assert.match(p.error(text,{role:'viewer',...options}),expected);
});
test('Read-only password errors retain usable guidance',()=>{
 const {p}=policy();assert.match(p.error('new passwords do not match',{role:'viewer'}),/do not match/);assert.match(p.error('password too short',{role:'viewer'}),/at least 12/);
});
test('role changes and logout restore the Admin help visibility policy without editing text or controls',()=>{
 const {p,document}=policy();for(const role of ['editor','viewer','admin',null]){p.apply(role);assert.equal(document.body.dataset.userRole,role || '');}
 const css=fs.readFileSync(path.join(root,'frontend/styles.css'),'utf8');assert.match(css,/body\[data-user-role="editor"\] :is\(\[data-role-help\],\.role-help\)/);assert.match(css,/body\[data-user-role="viewer"\] :is\(\[data-role-help\],\.role-help\)/);assert.doesNotMatch(css,/body\[data-user-role="admin"\]/);
});
test('live statuses, saved origins and measurements are not tagged as hidden help',()=>{
 const html=fs.readFileSync(path.join(root,'frontend/index.html'),'utf8');
 for(const id of ['workflowStatus','laserCadHandoffStatus','workspaceEditStatus','machinePlacementStatus','materialScopeWarning','productionStatus','settingsStatus','nestingOrigin','nestingExportOrigin']){
  const tag=html.match(new RegExp('<[^>]+id="'+id+'"[^>]*>'));assert.ok(tag,id);assert.doesNotMatch(tag[0],/data-role-help/,id);
 }
 assert.ok((html.match(/data-role-help/g)||[]).length>50);
});
