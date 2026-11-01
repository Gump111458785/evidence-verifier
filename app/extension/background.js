function configureSidePanel(){
 chrome.sidePanel.setPanelBehavior({openPanelOnActionClick:true}).catch(console.error);
}
chrome.runtime.onInstalled.addListener(()=>{
 chrome.contextMenus.create({id:'evidence-verifier-selection',title:'用证据侧栏查看选中回答',contexts:['selection']},()=>{void chrome.runtime.lastError});
 configureSidePanel();
});
chrome.runtime.onStartup.addListener(configureSidePanel);
chrome.contextMenus.onClicked.addListener((info,tab)=>{
 if(info.menuItemId!=='evidence-verifier-selection'||!Number.isInteger(tab?.windowId))return;
 const text=String(info.selectionText||'');
 const selection=text.length>12000?{text:'',error:'选中内容超过12,000字符，请重新选择较短内容；未进行截断判定。'}:{text,created_at:Date.now()};
 // Open directly in the user-gesture handler, before awaiting storage.
 const opened=chrome.sidePanel.open({windowId:tab.windowId});
 Promise.all([opened,chrome.storage.session.set({selection})]).catch(console.error);
});

// Explicit extension gestures only. activeTab grants temporary access, not all_urls.
async function pageCommand(mode,prompt){
 let [tab]=await chrome.tabs.query({active:true,lastFocusedWindow:true});
 const stored=await chrome.storage.session.get('liveSnapshot');
 if(mode==='stop'&&stored.liveSnapshot?.tab_id)tab={id:stored.liveSnapshot.tab_id};
 if(mode==='insert'&&tab?.id!==stored.liveSnapshot?.tab_id)throw Error('请回到原始对话标签页，再填入补充prompt。');
 if(!Number.isInteger(tab?.id))throw Error('没有活动标签页');
 if(mode==='source'){
  const state=await chrome.storage.session.get('liveSnapshot');
  const clean=v=>{const u=new URL(v);u.hash='';return u.href};
  if(!state.liveSnapshot?.citations?.some(c=>clean(c.url)===clean(tab.url)))throw Error('请先打开当前回答中记录的引用链接，再收集原文；重定向链接请手动粘贴并保留来源。');
 }
 await chrome.scripting.executeScript({target:{tabId:tab.id},files:['page-adapter.js']});
 if(mode==='insert'){
  const fresh=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.evPage.run('capture')});
  if(fresh[0]?.result?.candidate!==stored.liveSnapshot?.candidate||fresh[0]?.result?.question!==stored.liveSnapshot?.question)throw Error('对话已变化，请重新读取和核验，旧prompt未写入。');
 }
 const values=await chrome.scripting.executeScript({target:{tabId:tab.id},func:(m,p)=>globalThis.evPage.run(m,p),args:[mode,prompt||'']});
 const value=values[0]?.result;
 if(mode==='capture'||mode==='watch')await chrome.storage.session.set({liveSnapshot:{...value,tab_id:tab.id}});
 return value;
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id)return;
 if(message.type==='ev-visible-update'&&sender.tab){
  chrome.storage.session.get('liveSnapshot').then(s=>{if(s.liveSnapshot?.tab_id===sender.tab.id)return chrome.storage.session.set({liveSnapshot:{...message.snapshot,tab_id:sender.tab.id}})});return;
 }
 if(message.type!=='ev-page-command'||sender.tab)return;
 if(!['capture','watch','stop','source','insert'].includes(message.mode))return;
 pageCommand(message.mode,message.prompt).then(value=>respond({ok:true,value}),error=>respond({ok:false,error:error.message}));return true;
});
