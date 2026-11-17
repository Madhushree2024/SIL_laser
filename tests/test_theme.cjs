const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/theme.js'),'utf8');
function app(saved,blocked=false){
 const handlers={},store={theme:saved},events=[];
 const buttons=Array.from({length:2},()=>({attributes:{},setAttribute(k,v){this.attributes[k]=v},addEventListener(k,fn){this[k]=fn}}));
 const document={documentElement:{dataset:{},style:{}},querySelectorAll:()=>buttons,addEventListener:(name,fn)=>handlers[name]=fn};
 const localStorage={getItem(){if(blocked)throw Error('blocked');return store.theme},setItem(k,v){if(blocked)throw Error('blocked');store.theme=v}};
 vm.runInNewContext(source,{document,localStorage,window:{dispatchEvent:e=>events.push(e.type)},Event:class{constructor(type){this.type=type}}});
 handlers.DOMContentLoaded();return {document,buttons,store,events};
}
test('existing dark appearance is the default; invalid preferences are ignored',()=>{
 for(const saved of [undefined,'invalid','dark']){const a=app(saved);assert.equal(a.document.documentElement.dataset.theme,'dark');assert.equal(a.buttons[0].attributes['aria-label'],'Switch to light mode');assert.match(a.buttons[0].innerHTML,/<svg/)}
});
test('saved light preference loads before display and synchronizes both buttons',()=>{
 const a=app('light');assert.equal(a.document.documentElement.dataset.theme,'light');assert.equal(a.document.documentElement.style.colorScheme,'light');assert.ok(a.buttons.every(b=>b.attributes['aria-label']==='Switch to dark mode'));assert.match(a.buttons[0].innerHTML,/<path/);
});
test('either toolbar button toggles and persists the same preference without backend calls',()=>{
 const a=app('dark');a.buttons[0].click();assert.equal(a.store.theme,'light');assert.ok(a.buttons.every(b=>b.attributes['aria-pressed']==='true'));
 a.buttons[1].click();assert.equal(a.store.theme,'dark');assert.deepEqual(a.events,['themechange','themechange']);
});
test('theme remains usable when storage is denied',()=>{
 const a=app(null,true);a.buttons[1].click();assert.equal(a.document.documentElement.dataset.theme,'light');
});
