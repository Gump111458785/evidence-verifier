/* Content script (isolated world) on supported chat sites. Reads visible DOM only:
   no network, no session internals, no hidden reasoning. Pushes a snapshot to the
   extension whenever the latest answer settles, so the panel never waits on a read. */
(() => {
 if(globalThis.evPage)return;
 const providers={
  'chat.deepseek.com':{name:'DeepSeek',assistant:'div.ds-markdown:not(.ds-think-content *), [data-message-author-role="assistant"]',user:'.ds-message:not(:has(.ds-markdown)):not(:has(.ds-think-content)):not(:has(.ds-markdown--block)), .fbb737a4, [data-message-author-role="user"]',input:'textarea, [contenteditable="true"][role="textbox"]'},
  'chatgpt.com':{name:'ChatGPT',assistant:'[data-message-author-role="assistant"], h4[data-conversation-role="assistant"]',user:'[data-message-author-role="user"], h4[data-conversation-role="user"]',input:'#prompt-textarea, main textarea, main [contenteditable="true"]'},
  'claude.ai':{name:'Claude',assistant:'[data-is-streaming], .font-claude-message',user:'[data-testid="user-message"]',input:'[contenteditable="true"][role="textbox"]'},
  'gemini.google.com':{name:'Gemini',assistant:'model-response',user:'user-query',input:'.ql-editor[contenteditable="true"]'}};
 const CONTEXT_TURNS=6, CONTEXT_CHARS=2000;
 const visible=n=>{if(!(n instanceof Element)||n.getAttribute('aria-hidden')==='true')return false;const style=getComputedStyle(n);if(style.visibility==='hidden'||style.display==='none')return false;return !!n.getClientRects().length||(style.display==='contents'&&[...n.children].some(visible))};
 const ignored='script,style,nav,textarea,h4[data-conversation-role],[hidden],[aria-hidden="true"],[data-testid*="thinking"],[data-testid*="reasoning"],.thinking-block,.ds-think-content,[class*="ds-think"],.md-code-block-banner';
 function text(n){if(!n||!visible(n))return '';const walk=node=>{if(node.nodeType===3)return node.textContent;if(node.nodeType!==1||!visible(node)||node.matches(ignored))return '';if(node.tagName==='BUTTON'&&!node.closest('p,li,td,th,blockquote,figcaption'))return '';return Array.from(node.childNodes).map(walk).join(' ')};return walk(n).replace(/\s+/g,' ').trim()}
 function messageNodes(selector){const raw=[...new Set([...document.querySelectorAll(selector)].map(n=>n.matches('h4[data-conversation-role]')?n.parentElement:n))].filter(visible);return raw.filter(n=>!raw.some(o=>o!==n&&o.contains?.(n)))}
 function provider(){const p=providers[location.hostname];if(!p)throw Error('仅适配 DeepSeek、ChatGPT、Claude 与 Gemini 网页。');return p}
 const before=(a,b)=>Boolean(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING);

 /* Earlier visible turns, newest first, until the turn or character budget is used.
    A turn that does not fit is counted in context_dropped, never cut mid-turn. */
 function context(earlier){
  const ordered=earlier.slice().sort((x,y)=>before(x.n,y.n)?-1:1);
  const picked=[];let used=0,dropped=0;
  for(const t of ordered.reverse()){
   const value=text(t.n);
   if(!value)continue;
   if(picked.length>=CONTEXT_TURNS||used+value.length>CONTEXT_CHARS){dropped++;continue}
   picked.unshift({role:t.role,text:value});used+=value.length;
  }
  return {context:picked,context_dropped:dropped};
 }

 function capture(){
  const p=provider(),answers=messageNodes(p.assistant);const answer=answers.at(-1);
  if(!answer)throw Error('没有找到可见回答。');
  const users=messageNodes(p.user);const current=users.filter(n=>before(n,answer)).at(-1);
  const q=text(current),a=text(answer);
  if(!q)throw Error('没有找到对应的用户问题。');
  if(!a)throw Error('回答尚未显示完整文本。');
  if(a.length>5000||q.length>1000)throw Error('当前问答超过审阅上限（问题 1,000 字、回答 5,000 字），未截断。');
  const citations=[...answer.querySelectorAll('a[href]')].filter(visible).map(n=>({title:text(n)||n.hostname,url:n.href})).filter(n=>{try{const u=new URL(n.url);return u.protocol==='https:'&&u.hostname!==location.hostname&&!u.username&&!u.password}catch{return false}});
  const unique=[...new Map(citations.map(n=>[n.url,n])).values()];
  const streaming=Boolean(answer.getAttribute?.('data-is-streaming')==='true'||document.querySelector?.('[data-testid*="stop-button"], button[aria-label*="Stop" i], button[aria-label*="停止"], [role="button"][aria-label*="Stop" i], [role="button"][aria-label*="停止"], .ds-markdown--streaming'));
  const earlier=[...users.map(n=>({role:'user',n})),...answers.map(n=>({role:'assistant',n}))].filter(t=>t.n!==current&&t.n!==answer&&before(t.n,current));
  return {platform:p.name,page_url:location.origin+location.pathname,question:q,candidate:a,citations:unique,...context(earlier),
   streaming_complete:!streaming,retrieval_scope:'visible_dom_only',captured_at:new Date().toISOString()};
 }

 function insert(prompt){
  const p=provider(),inputs=[...document.querySelectorAll(p.input)].filter(visible),input=inputs.length===1?inputs[0]:null;
  if(!input)throw Error('找不到输入框');
  if((input.value||input.innerText||'').trim())throw Error('输入框已有草稿，未覆盖');
  input.focus();
  if(input instanceof HTMLTextAreaElement){Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,prompt)}else{input.textContent=prompt}
  input.dispatchEvent(new Event('input',{bubbles:true}));
  return {inserted:true,sent:false};
 }

 let observer=null,timer=null,last='';
 const keyOf=s=>JSON.stringify([s.question,s.candidate,s.context.length]);
 function push(snapshot){try{chrome.runtime.sendMessage({type:'ev-visible-update',snapshot}).catch(()=>{})}catch{/* extension was reloaded */}}
 function settle(){
  let s;
  try{s=capture()}catch(error){const k='!'+error.message;if(k!==last){last=k;push({platform:providers[location.hostname]?.name,error:error.message})}return}
  if(s.streaming_complete===false){if(last!=='~'){last='~';push({platform:s.platform,streaming:true})}return}
  const key=keyOf(s);if(key===last)return;last=key;push({...s,auto_ready:true});
 }
 function watch(){
  if(observer)return;
  observer=new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(settle,700)});
  observer.observe(document.body||document.documentElement,{childList:true,subtree:true,characterData:true});
  setTimeout(settle,300);
 }

 globalThis.evPage={run(mode,prompt){
  if(mode==='stop'){observer?.disconnect();observer=null;clearTimeout(timer);return {stopped:true}}
  if(mode==='insert')return insert(prompt);
  if(mode==='watch')watch();
  return capture();
 }};

 if(typeof chrome!=='undefined'&&chrome.runtime?.onMessage){
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
   if(message?.type!=='ev-tab-command'||sender?.id!==chrome.runtime.id)return;
   try{respond({ok:true,value:globalThis.evPage.run(message.mode,message.prompt)})}catch(error){respond({ok:false,error:error.message})}
  });
  if(providers[location.hostname])watch();
 }
})();
