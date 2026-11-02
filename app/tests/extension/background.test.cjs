const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../extension/background.js'),'utf8');
function fixture(envelope){
 const handlers={},writes=[],opened=[],settings=[];
 const event=name=>({addListener:fn=>{handlers[name]=fn}});
 const context={console,chrome:{action:{onClicked:event('action')},sidePanel:{setPanelBehavior:async v=>settings.push(v),open:v=>{opened.push(v);return Promise.resolve()}},runtime:{id:'fixture',onInstalled:event('install'),onStartup:event('startup'),onMessage:event('message')},contextMenus:{create:(v,fn)=>fn(),onClicked:event('context')},tabs:{query:async()=>[{id:7,windowId:4,url:'https://chatgpt.com/test'}]},storage:{session:{get:async()=>({}),set:async v=>writes.push(v)}},scripting:{executeScript:async s=>s.files?[]:[{result:envelope}]}}};
 vm.createContext(context);vm.runInContext(code,context);return {context,handlers,writes,opened,settings};
}
test('explicit toolbar gesture opens panel synchronously and does not use automatic panel behavior',()=>{const f=fixture();f.handlers.install();f.handlers.action({windowId:4});assert.equal(f.settings[0].openPanelOnActionClick,false);assert.equal(f.opened[0].windowId,4);});
test('adapter exception envelope is not stored as a successful snapshot',async()=>{const f=fixture({ok:false,error:'No visible answer'});await assert.rejects(f.context.pageCommand('capture'),/No visible answer/);assert.equal(f.writes.length,0);});
test('missing injection result is a controlled failure rather than a null snapshot',async()=>{const f=fixture(null);await assert.rejects(f.context.pageCommand('capture'),/未返回有效结果/);assert.equal(f.writes.length,0);});
