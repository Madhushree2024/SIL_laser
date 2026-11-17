const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/manual-datum.js'),'utf8');
function app(options={}){
 const navigation=[],calls=[],button={dataset:{},addEventListener(k,f){this[k]=f}},state={user:{},activeView:'process'};
 const context={state,$:()=>button,hasPermission:()=>options.permission!==false,crypto:{randomUUID:()=> 'once'},confirm:()=>options.confirm!==false,displayError:e=>e.message,message(){},navigateSection:v=>{navigation.push(v);state.activeView=v},window:{LaserCADTab:{hide:async()=>{}}},api:async(url,request)=>{if(!request?.method)return {stage:options.stage || 'ready_process'};calls.push([url,JSON.parse(request.body)]);return {message:'Datum requested'}}};
 vm.runInNewContext(source,context);return {calls,button,state,navigation};
}
test('no Datum or API call occurs on script load; one explicit click requests only manual Datum',async()=>{
 const a=app();assert.equal(a.calls.length,0);await a.button.click();assert.equal(a.calls.length,1);assert.equal(a.calls[0][0],'/api/laser-control/manual-datum');assert.equal(a.calls[0][1].operator_confirmed_idle,true);assert.equal(a.state.activeView,'process');assert.deepEqual(a.navigation,[]);
});
test('Cancel, insufficient permission and active stages never request manual Datum',async()=>{
 for(const option of [{confirm:false},{permission:false},{stage:'awaiting_completion'},{stage:'ready_frame'}]){const a=app(option);await a.button.click();assert.equal(a.calls.length,0)}
});
