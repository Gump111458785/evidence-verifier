const $p=s=>document.querySelector(s);
const escapePanel=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const extensionMode=location.protocol==='chrome-extension:';
const localService=extensionMode?'http://127.0.0.1:18790':location.origin;
let pluginData=null,pluginCase=null,pluginAfter=false,pluginStarted=false,pluginRelation=0,pluginTransport='local_program';
const relationNames={designed_by:'设计者',educated_at:'就读学校'};
const panelLabels={entailed:'支持',missing:'缺失',ambiguous:'歧义',contradicted:'矛盾'};

function notifyPreview(){
 if(window.parent!==window&&pluginCase){
  window.parent.postMessage({
   type:'ev-plugin-preview',
   case_id:pluginCase.id,
   question:pluginCase.question,
   candidate:pluginCase.candidate,
   name:pluginCase.name,
   explanation:pluginCase.explanation,
   after:pluginAfter,
   started:pluginStarted
  },location.origin);
 }
}

function choosePluginCase(id){
 pluginCase=pluginData.cases.find(c=>c.id===id)||pluginData.cases[0];
 pluginAfter=false;
 pluginStarted=true;
 pluginRelation=pluginCase.id==='verified-clean'?0:1;
 $p('#panel-case').value=pluginCase.id;
 $p('#panel-answer').textContent=pluginCase.candidate;
 if($p('#panel-draft-box'))$p('#panel-draft-box').hidden=true;
 $p('#panel-run').disabled=false;
 renderPlugin();
}

function fillDraft(text){
 const box=$p('#panel-draft-box'),input=$p('#panel-draft-input');
 if(!box||!input)return;
 box.hidden=false;
 input.value=text;
 box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function renderPlugin(){
 const result=pluginAfter?pluginCase.final:pluginCase.initial;
 const isCleanCase=pluginCase.id==='verified-clean';
 $p('#panel-result').hidden=false;
 $p('#panel-run').textContent='重新执行自动核验';
 const positive=result.verdict==='entailed';

 // Role handoff strip
 const roleStrip=$p('#panel-role-strip');
 if(roleStrip){
  roleStrip.innerHTML=`
   <span class="role-chip done">01 规划与消歧 ✓</span>
   <span class="role-chip done">02 盲抽提取 ✓</span>
   <span class="role-chip ${positive?'done':'warn'}">03 支持核验 ${positive?'✓':'!'}</span>
   <span class="role-chip ${positive?'done':'warn'}">04 原文绑定 ${positive?'✓':'!'}</span>
   <span class="role-chip ${pluginAfter?'done':isCleanCase?'idle':'warn'}">05 缺口补搜 ${pluginAfter?'已补证':isCleanCase?'无需触发':'待补证'}</span>
  `;
 }

 $p('#panel-verdict').className='verdict-box'+(positive?' supported':'');
 $p('#panel-verdict').innerHTML=`<strong>${
  isCleanCase
   ? '✓ 回答关键关系已获原文支持 · 无需打扰'
   : positive
    ? '✓ 补入 E3 后：声明关系链已完整支持'
    : result.binding_gap
     ? '! 发现实体张冠李戴：局部关系有证据，但中间实体未连通'
     : '! 发现证据缺口：任职记录不能代替就读学校'
 }</strong><p>${
  isCleanCase
   ? '两跳关系（Meridian Engine → Nora Hale → Lake University）均已在公开引用中精确定位并完成绑定。'
   : positive
    ? '新补入的校友档案 E3 绑定到同一位设计师 Nora Hale，原有材料继续保留。'
    : escapePanel(pluginCase.explanation)
 }</p>`;

 const actionCard=$p('#panel-action-card');
 const repairRow=$p('#panel-repair-row');
 if(actionCard){
  if(positive){
   const followups=pluginCase.followup_questions||[
    'Nora Hale 在 Lake University 就读期间主修什么方向？',
    'Meridian Engine 的后续改进是否沿用了她在该校时期的成果？'
   ];
   actionCard.className='concise-action-card supported';
   const promptText=pluginAfter
    ? `请结合已核实的引用 E1（Meridian Engine 由 Nora Hale 设计）与补充证据 E3（Nora Hale 曾就读于 Lake University），重新整理回答并显式标注两跳出处。`
    : followups[0];
   actionCard.innerHTML=`
    <strong>${isCleanCase?'推荐延伸追问（点击即可填入对话草稿）':'建议怎么改 → 确认补入原对话'}</strong>
    <p>${isCleanCase?'当前回答无需纠偏。若希望继续深入提问，可点击下方高价值追问建议：':'证据链已闭合！可点击下方按钮将带完整引用的修订提示词填入原对话草稿框：'}</p>
    <div class="followup-list">
     ${isCleanCase
       ? followups.map(q=>`<button type="button" class="followup-chip" data-fill="${escapePanel(q)}">追问：${escapePanel(q)}</button>`).join('')
       : `<button type="button" class="primary full-btn" data-fill="${escapePanel(promptText)}">确认并填入原对话输入框（不自动发送）</button>`
     }
    </div>
   `;
  }else{
   const promptFix=result.binding_gap
    ? `核验发现实体绑定断裂：E1 证明 Meridian Engine 的设计者是 Nora Hale，但 E2 中的 Lake University 就读记录属于 Leo Marsh。请仅基于与 Nora Hale 同一实体直接相关的证据重新回答，或说明目前缺少其本人就读院校的直接证据。`
    : `核验发现证据缺口：E1 证明 Meridian Engine 由 Nora Hale 设计，但 E2 仅说明她在 Pine Institute 任职，并未提供她就读于 Lake University 的直接证据。请补充 Nora Hale 本人的受教育经历出处，或在未获直接证据前修正结论。`;
   actionCard.className='concise-action-card missing';
   actionCard.innerHTML=`
    <strong>发现了什么 → 建议怎么改</strong>
    <p>${escapePanel(promptFix)}</p>
    <div class="action-btn-group">
     <button type="button" class="primary full-btn" data-fill="${escapePanel(promptFix)}">确认并将修订提示词填入原对话</button>
    </div>
   `;
  }
  actionCard.querySelectorAll('[data-fill]').forEach(btn=>{
   btn.onclick=()=>fillDraft(btn.dataset.fill);
  });
 }

 if(repairRow){
  repairRow.hidden=isCleanCase;
 }

 $p('#relation-count').textContent=pluginCase.plan.length+' 条关系';
 $p('#panel-relations').innerHTML=pluginCase.plan.map((relation,index)=>{
  const local=result.relation_results[index],proof=positive?result.proof[index]:null,verdict=proof?'entailed':local.verdict;
  const localFact=local.matched_facts?.[0];
  const subject=positive?(result.bindings[relation.subject]||relation.subject):(localFact?.subject||relation.subject);
  const object=positive?(result.bindings[relation.object]||relation.object):(localFact?.object||relation.object);
  return `<button class="relation-row ${pluginRelation===index?'selected':''}" data-relation="${index}"><span><b>${escapePanel(relationNames[relation.predicate]||relation.predicate)}</b><em class="${verdict}">${escapePanel(panelLabels[verdict])}</em></span><small>${escapePanel(subject)} → ${escapePanel(object)}</small></button>`;
 }).join('');
 $p('#panel-relations').querySelectorAll('[data-relation]').forEach(button=>button.onclick=()=>{pluginRelation=Number(button.dataset.relation);renderPlugin()});
 const local=result.relation_results[pluginRelation];
 const proof=positive?result.proof[pluginRelation]:local.matched_facts?.[0];
 const quote=proof?.quotes?.[0];
 if(quote){
  const full=pluginCase.evidence.find(e=>e.id===quote.evidence_id)?.text||quote.text;
  const marked=full.slice(0,quote.start)+full.slice(quote.start,quote.end)+full.slice(quote.end);
  const html=escapePanel(marked.slice(0,quote.start))+'<mark>'+escapePanel(marked.slice(quote.start,quote.end))+'</mark>'+escapePanel(marked.slice(quote.end));
  $p('#panel-citation').innerHTML=`<article class="citation"><h2>${escapePanel(quote.evidence_id)} · 原文字符定位 [${quote.start}, ${quote.end})</h2><blockquote>${html}</blockquote><p>${result.binding_gap?'这段引用支持局部关系；但它对应的人（Leo Marsh）不是前一跳的设计师（Nora Hale）。':positive&&local.verdict==='ambiguous'?'存在干扰实体，已通过跨段落联合绑定消歧。':'字符偏移命中证明出处，语义支持来自多角色核验。'}</p></article>`;
 }else{
  $p('#panel-citation').innerHTML='<article class="citation"><h2>尚无对应教育证据</h2><blockquote>任职地点不能代替就读学校；相关实体出现，不等于这条关系成立。</blockquote></article>';
 }
 $p('#panel-repair').textContent=pluginAfter?'还原至初始缺证状态':'05 自动补入就读证据 E3 并复核';
 $p('#panel-explanation').textContent=pluginCase.explanation;
 $p('#panel-queries').innerHTML=(pluginCase.queries||[]).map(q=>q.query?`<p class="query">Role 05 缺口检索词：<code>${escapePanel(q.query)}</code></p>`:'').join('');
 notifyPreview();
}

async function loadPlugin(){
 $p('#panel-error').hidden=true;
 try{
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),2500);
  try{
   const r=await fetch(localService+'/api/plugin-demo',{signal:controller.signal});
   if(!r.ok)throw Error('本地案例服务暂不可用');
   pluginData=await r.json();
   pluginTransport='local_program';
  }finally{clearTimeout(timer)}
 }catch(error){
  if(extensionMode){
   try{const r=await fetch('plugin-demo.json');pluginData=await r.json();pluginTransport='bundled_snapshot'}
   catch{$p('#panel-error').textContent='无法载入演示素材，请重新打开侧栏。';$p('#panel-error').hidden=false;return}
  }else{
   $p('#panel-error').textContent='案例暂未载入，点击下方按钮重试。';
   $p('#panel-error').hidden=false;
   $p('#panel-run').textContent='重新载入';
   $p('#panel-run').disabled=false;
   return;
  }
 }
 if(pluginData?.source!=='ideal_plugin_demo'||pluginData.measured!==false||pluginData.model_inference_performed!==false||!Array.isArray(pluginData.cases)||!pluginData.cases.length){
  $p('#panel-error').textContent='演示数据格式不正确。';
  $p('#panel-error').hidden=false;
  return;
 }
 $p('#panel-source').textContent=pluginTransport==='bundled_snapshot'?'离线预设回放 · 未连接模型或本地服务。':'自动辅助演示 · 回答稳定后自动拆解并核验（预设判断 + 真实程序绑定）。';
 $p('#panel-case').innerHTML=pluginData.cases.map(c=>`<option value="${escapePanel(c.id)}">${escapePanel(c.name)}</option>`).join('');
 $p('#panel-case').disabled=false;
 choosePluginCase(pluginData.cases[0].id);
}

$p('#panel-case').onchange=e=>choosePluginCase(e.target.value);
$p('#panel-run').onclick=()=>{if(!pluginData)return loadPlugin();pluginStarted=true;pluginAfter=false;renderPlugin()};
$p('#panel-repair').onclick=()=>{pluginAfter=!pluginAfter;renderPlugin()};
$p('#panel-workbench').onclick=()=>window.open(localService+'/#plugin','_blank','noopener');
window.addEventListener('message',event=>{
 if(event.origin!==location.origin||event.source!==window.parent||!pluginData)return;
 if(event.data?.type==='ev-plugin-case')choosePluginCase(event.data.case_id);
 if(event.data?.type==='ev-plugin-sync')notifyPreview();
 if(event.data?.type==='ev-plugin-run'){pluginStarted=true;pluginAfter=false;renderPlugin()}
});
if(extensionMode){
 const readSelection=value=>{if(!value)return;$p('#selection').hidden=false;$p('#selected-answer').value=value.text||'';$p('#selection-note').textContent=value.error||'选中内容只保留在当前插件会话；下方检测仍使用预设教学案例，不对任意文本生成分数。'};
 chrome.storage.session.get('selection').then(data=>readSelection(data.selection));
 chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.selection)readSelection(changes.selection.newValue)});
}
loadPlugin();
