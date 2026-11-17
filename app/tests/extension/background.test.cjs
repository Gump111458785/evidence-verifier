const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../extension/background.js'),'utf8');

function fixture(opts={}){
 const handlers={},opened=[],settings=[],sent=[],injected=[];
 const session={...(opts.session||{})};
 const event=name=>({addListener:fn=>{handlers[name]=fn}});
 const replies=opts.replies||{};
 const context={console,chrome:{
  action:{onClicked:event('action')},
  sidePanel:{setPanelBehavior:async v=>settings.push(v),open:v=>{opened.push(v);return Promise.resolve()}},
  runtime:{id:'ext',onInstalled:event('install'),onStartup:event('startup'),onMessage:event('message')},
  tabs:{query:async()=>[{id:7,windowId:4,url:'https://chatgpt.com/c/1'}],onActivated:event('activated'),onRemoved:event('removed'),
   sendMessage:async(id,msg)=>{sent.push([id,msg]);const r=replies[msg.mode];if(r instanceof Error)throw r;return typeof r==='function'?r():r}},
  scripting:{executeScript:async s=>{injected.push(s);return []}},
  storage:{session:{get:async k=>{if(typeof k==='string')return k in session?{[k]:session[k]}:{};return {...session}},set:async v=>Object.assign(session,v),remove:async k=>{delete session[k]}}}}};
 vm.createContext(context);vm.runInContext(code,context);
 return {context,handlers,opened,settings,sent,injected,session};
}
const tick=()=>new Promise(r=>setTimeout(r,5));

test('toolbar click opens the panel synchronously; install injects into already-open chat tabs',async()=>{
 const f=fixture();f.handlers.install();f.handlers.action({windowId:4});await tick();
 assert.equal(f.settings[0].openPanelOnActionClick,false);assert.equal(f.opened[0].windowId,4);
 assert.equal(f.injected[0].files[0],'page-adapter.js');
});

test('content-script snapshots are kept per tab and published only for the active tab',async()=>{
 const f=fixture();
 f.handlers.message({type:'ev-visible-update',snapshot:{platform:'ChatGPT',question:'q1',candidate:'a1'}},{id:'ext',tab:{id:3,active:false}});
 await tick();
 assert.equal(f.session['snap:3'].question,'q1');assert.equal(f.session.liveSnapshot,undefined);
 f.handlers.message({type:'ev-visible-update',snapshot:{platform:'ChatGPT',question:'q2',candidate:'a2'}},{id:'ext',tab:{id:7,active:true}});
 await tick();
 assert.equal(f.session.liveSnapshot.question,'q2');assert.equal(f.session.liveSnapshot.tab_id,7);
 f.handlers.activated({tabId:3});await tick();
 assert.equal(f.session.liveSnapshot.question,'q1');
 f.handlers.activated({tabId:99});await tick();
 assert.equal(f.session.liveSnapshot.idle,true);
});

test('messages from other extensions are ignored',async()=>{
 const f=fixture();
 f.handlers.message({type:'ev-visible-update',snapshot:{question:'x'}},{id:'other',tab:{id:7,active:true}});
 await tick();assert.equal(f.session.liveSnapshot,undefined);
});

test('a failed capture is reported and never stored as a snapshot',async()=>{
 const f=fixture({replies:{capture:{ok:false,error:'没有找到可见回答。'}}});
 await assert.rejects(f.context.pageCommand('capture'),/没有找到可见回答/);
 assert.equal(f.session.liveSnapshot,undefined);
});

test('insert refuses when the conversation changed since the snapshot',async()=>{
 const f=fixture({session:{liveSnapshot:{tab_id:7,question:'q',candidate:'old'}},replies:{capture:{ok:true,value:{question:'q',candidate:'new'}},insert:{ok:true,value:{inserted:true}}}});
 await assert.rejects(f.context.pageCommand('insert','prompt'),/对话已变化/);
 assert.ok(!f.sent.some(([,m])=>m.mode==='insert'));
});

test('insert goes to the snapshot tab when unchanged and never sends the message',async()=>{
 const f=fixture({session:{liveSnapshot:{tab_id:5,question:'q',candidate:'a'}},replies:{capture:{ok:true,value:{question:'q',candidate:'a'}},insert:{ok:true,value:{inserted:true,sent:false}}}});
 const r=await f.context.pageCommand('insert','prompt');
 assert.equal(r.sent,false);assert.deepEqual(f.sent.map(([id,m])=>[id,m.mode]),[[5,'capture'],[5,'insert']]);
});

test('a tab without the content script gets it injected once, then retried',async()=>{
 let calls=0;
 const f=fixture({replies:{capture:()=>{calls++;if(calls===1)throw Error('no receiver');return {ok:true,value:{question:'q',candidate:'a'}}}}});
 const v=await f.context.pageCommand('capture');
 assert.equal(v.question,'q');assert.equal(f.injected.length,1);assert.equal(f.session['snap:7'].candidate,'a');
});
