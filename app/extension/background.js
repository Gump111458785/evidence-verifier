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
