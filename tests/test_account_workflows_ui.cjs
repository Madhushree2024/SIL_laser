const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const app=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
const first=app.indexOf('async function refreshAccountPanel()'),last=app.indexOf('function desktopProtectedDownload(',first);
function harness(admin=true){
 const nodes=new Map(),calls=[];
 function node(tag){return {tag,children:[],style:{},setAttribute(k,v){this[k]=v},append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
 const get=id=>{if(!nodes.has(id))nodes.set(id,node(id));return nodes.get(id)};
 const user={id:'reader-id',username:'reader',role:'viewer',enabled:true,workflow_mode:'manual'};
 const ctx={document:{createElement:node},$:get,state:{user:{id:'owner'}},hasPermission:()=>admin,roleLabels:{admin:'Admin',editor:'Editor / Manager',viewer:'Read-only'},Option:function(label,value){this.textContent=label;this.value=value},displayError:e=>e.message,accountResult(){},acceptAccount(){},lockAccess(){},api:async(url,options)=>{if(options){calls.push([url,JSON.parse(options.body)]);return {user}};if(url==='/api/auth/users')return {users:[user]};if(url==='/api/auth/audit')return {events:[]};return {user:{id:'owner'}}},window:{confirm:()=>false}};
 const refresh=vm.runInNewContext('('+app.slice(first,last)+')',ctx);return {refresh,get,calls};
}

test('Admin can save per-user Manual/Automated selection only from the Account UI',async()=>{
 const h=harness();await h.refresh();assert.equal(h.get('manageAccounts').hidden,false);
 const row=h.get('accountList').children[0],selects=row.children.filter(n=>n.tag==='select');assert.equal(selects.length,2);
 const mode=selects[1];assert.equal(mode['aria-label'],'Workflow for reader');assert.equal(mode.value,'manual');mode.value='automated';
 await row.children.find(n=>n.textContent==='Save').onclick();assert.equal(h.calls[0][0],'/api/auth/users/reader-id');assert.equal(h.calls[0][1].workflow_mode,'automated');
 const html=fs.readFileSync(path.join(__dirname,'../frontend/index.html'),'utf8');const account=html.slice(html.indexOf('<dialog id="accountDialog"'),html.indexOf('</dialog>',html.indexOf('<dialog id="accountDialog"')));
 assert.ok(account.includes('id="newUserWorkflow"'));assert.equal((html.match(/id="newUserWorkflow"/g)||[]).length,1);
 assert.ok(!html.includes('id="supervisedCutting"'));assert.ok(!html.includes('Supervised cutting cycle (disabled)'));
});

test('Manager and Read-only cannot view account-assignment controls or fetch user management',async()=>{
 const h=harness(false);await h.refresh();assert.equal(h.get('manageAccounts').hidden,true);assert.equal(h.calls.length,0);assert.equal(h.get('accountList').children.length,0);
});
