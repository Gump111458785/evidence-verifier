const $p=s=>document.querySelector(s);
const escapePanel=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const extensionMode=location.protocol==='chrome-extension:';
const localService=extensionMode?'http://127.0.0.1:18790':location.origin;
let pluginData=null,pluginCase=null,pluginAfter=false,pluginStarted=false,pluginRelation=0,pluginTransport='local_program';
const relationNames={designed_by:'设计者',educated_at:'就读学校'};
const panelLabels={entailed:'支持',missing:'缺失',ambiguous:'歧义',contradicted:'矛盾'};
function notifyPreview(){if(window.parent!==window)window.parent.postMessage({type:'ev-plugin-preview',case_id:pluginCase.id,question:pluginCase.question,candidate:pluginCase.candidate,name:pluginCase.name,explanation:pluginCase.explanation,after:pluginAfter,started:pluginStarted},location.origin)}
function choosePluginCase(id){pluginCase=pluginData.cases.find(c=>c.id===id)||pluginData.cases[0];pluginAfter=false;pluginStarted=false;pluginRelation=1;$p('#panel-case').value=pluginCase.id;$p('#panel-answer').textContent=pluginCase.candidate;$p('#panel-result').hidden=true;$p('#panel-run').textContent='审查示例回答';$p('#panel-run').disabled=false;notifyPreview()}
function renderPlugin(){
 const result=pluginAfter?pluginCase.final:pluginCase.initial;
 $p('#panel-result').hidden=false;
 $p('#panel-run').textContent='重新审查示例';
 const positive=result.verdict==='entailed';
 $p('#panel-verdict').className='verdict-box'+(positive?' supported':'');
 $p('#panel-verdict').innerHTML=`<strong>${positive?'声明关系链已支持':result.binding_gap?'关系都像成立，实体却没接上':'引用相关，教育关系仍缺失'}</strong><p>${positive?'新证据绑定到同一位设计师，原有材料继续保留。':escapePanel(pluginCase.explanation)}</p>`;
 $p('#relation-count').textContent=pluginCase.plan.length+' 条关系';
 $p('#panel-relations').innerHTML=pluginCase.plan.map((relation,index)=>{
  const local=result.relation_results[index],proof=positive?result.proof[index]:null,verdict=proof?'entailed':local.verdict;
  const localFact=local.matched_facts?.[0];
  const subject=positive?(result.bindings[relation.subject]||relation.subject):(localFact?.subject||relation.subject);
  const object=positive?(result.bindings[relation.object]||relation.object):(localFact?.object||relation.object);
  return `<button class="relation-row ${pluginRelation===index?'selected':''}" data-relation="${index}"><span><b>${escapePanel(relationNames[relation.predicate])}</b><em class="${verdict}">${escapePanel(panelLabels[verdict])}</em></span><small>${escapePanel(subject)} → ${escapePanel(object)}</small></button>`;
 }).join('');
 $p('#panel-relations').querySelectorAll('[data-relation]').forEach(button=>button.onclick=()=>{pluginRelation=Number(button.dataset.relation);renderPlugin()});
 const local=result.relation_results[pluginRelation];
 const proof=positive?result.proof[pluginRelation]:local.matched_facts?.[0];
 const quote=proof?.quotes?.[0];
 if(quote){
  const full=pluginCase.evidence.find(e=>e.id===quote.evidence_id)?.text||quote.text;
  const marked=full.slice(0,quote.start)+full.slice(quote.start,quote.end)+full.slice(quote.end);
  const html=escapePanel(marked.slice(0,quote.start))+'<mark>'+escapePanel(marked.slice(quote.start,quote.end))+'</mark>'+escapePanel(marked.slice(quote.end));
  $p('#panel-citation').innerHTML=`<article class="citation"><h2>${escapePanel(quote.evidence_id)} · 原文定位 [${quote.start}, ${quote.end})</h2><blockquote>${html}</blockquote><p>${result.binding_gap?'这段引用支持局部关系；它对应的人不一定是前一跳的设计师。':positive&&local.verdict==='ambiguous'?'存在干扰实体，已通过联合绑定消歧。':'位置命中证明出处，语义支持来自本案例的预设核验结果。'}</p></article>`;
 }else{$p('#panel-citation').innerHTML='<article class="citation"><h2>尚无对应教育证据</h2><blockquote>任职地点不能代替就读学校；相关实体出现，不等于这条关系成立。</blockquote></article>'}
 $p('#panel-repair').textContent=pluginAfter?'回到初始证据':'补入关键教育证据';
 $p('#panel-explanation').textContent=pluginCase.explanation;
 $p('#panel-queries').innerHTML=pluginCase.queries.map(q=>q.query?`<p class="query">缺口查询：${escapePanel(q.query)}</p>`:'').join('');
 notifyPreview();
}
async function loadPlugin(){
 $p('#panel-error').hidden=true;
 try{
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),2500);
  try{const r=await fetch(localService+'/api/plugin-demo',{signal:controller.signal});if(!r.ok)throw Error('本地案例服务暂不可用');pluginData=await r.json();pluginTransport='local_program'}finally{clearTimeout(timer)}
 }catch(error){
  if(extensionMode){try{const r=await fetch('plugin-demo.json');pluginData=await r.json();pluginTransport='bundled_snapshot'}catch{$p('#panel-error').textContent='无法载入演示素材，请重新打开侧栏。';$p('#panel-error').hidden=false;return}}
  else{$p('#panel-error').textContent='案例暂未载入，点击下方按钮重试。';$p('#panel-error').hidden=false;$p('#panel-run').textContent='重新载入';$p('#panel-run').disabled=false;return}
 }
 if(pluginData?.source!=='ideal_plugin_demo'||pluginData.measured!==false||pluginData.model_inference_performed!==false||!Array.isArray(pluginData.cases)||!pluginData.cases.length){$p('#panel-error').textContent='演示数据格式不正确。';$p('#panel-error').hidden=false;return}
 $p('#panel-source').textContent=pluginTransport==='bundled_snapshot'?'离线预设回放 · 未连接模型或本地服务。':'理想场景 · 模型结果预设，引用与绑定由本地程序回放。';
 $p('#panel-case').innerHTML=pluginData.cases.map(c=>`<option value="${escapePanel(c.id)}">${escapePanel(c.name)}</option>`).join('');$p('#panel-case').disabled=false;choosePluginCase(pluginData.cases[0].id);
}
$p('#panel-case').onchange=e=>choosePluginCase(e.target.value);
$p('#panel-run').onclick=()=>{if(!pluginData)return loadPlugin();pluginStarted=true;pluginAfter=false;renderPlugin()};
$p('#panel-repair').onclick=()=>{pluginAfter=!pluginAfter;renderPlugin()};
$p('#panel-workbench').onclick=()=>window.open(localService+'/#plugin','_blank','noopener');
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==window.parent||!pluginData)return;if(event.data?.type==='ev-plugin-case')choosePluginCase(event.data.case_id);if(event.data?.type==='ev-plugin-sync')notifyPreview();if(event.data?.type==='ev-plugin-run'){pluginStarted=true;pluginAfter=false;renderPlugin()}});
if(extensionMode){
 const readSelection=value=>{if(!value)return;$p('#selection').hidden=false;$p('#selected-answer').value=value.text||'';$p('#selection-note').textContent=value.error||'选中内容只保留在当前插件会话；下方检测仍使用预设教学案例，不对任意文本生成分数。'};
 chrome.storage.session.get('selection').then(data=>readSelection(data.selection));
 chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.selection)readSelection(changes.selection.newValue)});
}
loadPlugin();
