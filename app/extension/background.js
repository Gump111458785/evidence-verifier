/* Service worker. Content scripts push a snapshot per tab as soon as an answer settles;
   this worker keeps the active tab's snapshot in `liveSnapshot`, so the panel shows
   a ready conversation the moment it opens and a review starts without a page read. */
const SITES=['https://chat.deepseek.com/*','https://chatgpt.com/*','https://claude.ai/*','https://gemini.google.com/*'];
const snapKey=id=>'snap:'+id;

chrome.action.onClicked.addListener(tab=>{
 if(!Number.isInteger(tab?.windowId))return;
 // Must remain synchronous with the action click, before any awaited operation.
 chrome.sidePanel.open({windowId:tab.windowId}).catch(console.error);
});

async function injectOpenTabs(){
 const tabs=await chrome.tabs.query({url:SITES});
 await Promise.all(tabs.map(t=>chrome.scripting.executeScript({target:{tabId:t.id},files:['page-adapter.js']}).catch(()=>{})));
}
function configure(){
 chrome.sidePanel.setPanelBehavior({openPanelOnActionClick:false}).catch(console.error);
 injectOpenTabs().catch(console.error);
}
chrome.runtime.onInstalled.addListener(configure);
chrome.runtime.onStartup.addListener(configure);

async function publish(tabId){
 const key=snapKey(tabId),stored=await chrome.storage.session.get(key);
 await chrome.storage.session.set({liveSnapshot:stored[key]?{...stored[key],tab_id:tabId}:{tab_id:tabId,idle:true}});
}
chrome.tabs.onActivated.addListener(({tabId})=>{publish(tabId).catch(console.error)});
chrome.tabs.onRemoved.addListener(tabId=>{chrome.storage.session.remove(snapKey(tabId)).catch(()=>{})});

async function activeTab(){
 const [tab]=await chrome.tabs.query({active:true,lastFocusedWindow:true});
 if(!Number.isInteger(tab?.id))throw Error('没有活动标签页');
 return tab;
}
async function tabCommand(tabId,mode,prompt){
 let reply;
 try{reply=await chrome.tabs.sendMessage(tabId,{type:'ev-tab-command',mode,prompt})}
 catch(_){
  // Tab opened before install or after an extension reload: inject once, then retry.
  try{await chrome.scripting.executeScript({target:{tabId},files:['page-adapter.js']})}
  catch(error){throw Error('当前页面不是支持的 AI 对话页')}
  reply=await chrome.tabs.sendMessage(tabId,{type:'ev-tab-command',mode,prompt});
 }
 if(!reply||reply.ok!==true)throw Error(reply?.error||'页面没有返回有效结果');
 if(!reply.value||typeof reply.value!=='object')throw Error('页面返回了无效快照');
 return reply.value;
}

async function pageCommand(mode,prompt){
 if(mode==='capture'){
  const tab=await activeTab();
  const value=await tabCommand(tab.id,'capture');
  await chrome.storage.session.set({[snapKey(tab.id)]:value,liveSnapshot:{...value,tab_id:tab.id}});
  return value;
 }
 if(mode==='insert'){
  const {liveSnapshot:live}=await chrome.storage.session.get('liveSnapshot');
  if(!Number.isInteger(live?.tab_id))throw Error('没有可填入的对话');
  const fresh=await tabCommand(live.tab_id,'capture');
  if(fresh.question!==live.question||fresh.candidate!==live.candidate)throw Error('对话已变化，未填入');
  return tabCommand(live.tab_id,'insert',prompt);
 }
 throw Error('不支持的页面命令');
}

chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id)return;
 if(message.type==='ev-visible-update'&&sender.tab){
  const id=sender.tab.id,snap=message.snapshot||{};
  const writes={[snapKey(id)]:snap};
  if(sender.tab.active)writes.liveSnapshot={...snap,tab_id:id};
  chrome.storage.session.set(writes).catch(console.error);
  return;
 }
 if(message.type!=='ev-page-command'||sender.tab)return;
 if(!['capture','insert'].includes(message.mode))return;
 pageCommand(message.mode,message.prompt).then(value=>respond({ok:true,value}),error=>respond({ok:false,error:error.message}));
 return true;
});
