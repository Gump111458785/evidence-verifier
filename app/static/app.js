const $ = s => document.querySelector(s);
const labels = {entailed:'支持',missing:'缺失',contradicted:'矛盾',ambiguous:'歧义',invalid_output:'无效输出'};
const modelLabels={qwen14b:'Qwen2.5 14B',qwen7b:'Qwen2.5 7B',llama8b:'Llama 3 8B'};
let liveReady=false,plannerReady=false,cases=[],current=null,runRecord=null,generation=0,corpusLabel="本地开发诊断证据库";
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pluginFrame=document.getElementById('plugin-frame');
function pluginMessage(value){pluginFrame.contentWindow?.postMessage(value,location.origin)}
window.addEventListener('message',event=>{
 if(event.origin!==location.origin||event.source!==pluginFrame.contentWindow||event.data?.type!=='ev-plugin-preview')return;
 const data=event.data;
 $('#plugin-question').textContent=data.question;$('#plugin-candidate').textContent=data.candidate;
 $('#plugin-finding-title').textContent=data.after?'同一设计师，关系链重新连通':data.name;
 $('#plugin-finding-text').textContent=data.after?'补充材料支持Nora Hale的教育关系；已有原文和判定继续保留。':data.explanation;
 $('#plugin-preview-state').textContent=data.after?'已补入证据':data.started?'审查已展开':'可运行示例';
 $('#plugin-run-embedded').disabled=false;
 document.querySelectorAll('[data-plugin-example]').forEach(b=>b.classList.toggle('selected',b.dataset.pluginExample===data.case_id));
});
pluginFrame.addEventListener('load',()=>pluginMessage({type:'ev-plugin-sync'}));
pluginMessage({type:'ev-plugin-sync'});
document.querySelectorAll('[data-plugin-example]').forEach(b=>b.onclick=()=>pluginMessage({type:'ev-plugin-case',case_id:b.dataset.pluginExample}));
$('#plugin-run-embedded').onclick=()=>pluginMessage({type:'ev-plugin-run'});
async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await r.json();if(!r.ok)throw Error(data.error||'请求失败');return data}
function error(e){$('#error').hidden=false;$('#error').textContent=e.message||String(e)}
function clearError(){$('#error').hidden=true}
function showView(view){if(!document.getElementById(view)?.classList.contains('view'))return;history.replaceState(null,'','#'+view);document.querySelectorAll('.view .title h1,.view .title h2[data-page-title]').forEach(h=>{const tag=h.closest('.view').id===view?'h1':'h2';if(h.tagName.toLowerCase()!==tag){const replacement=document.createElement(tag);replacement.textContent=h.textContent;replacement.dataset.pageTitle='true';h.replaceWith(replacement)}});document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==view);document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('#breadcrumb').textContent={demo:'流程演示',review:'证据核验',search:'证据检索',agent:'取证任务',experiments:'实验对照',history:'运行记录',data:'训练样本',plugin:'LLM 插件'}[view];$('.source-label').textContent={demo:'理想流程演示',review:'v7 · 核验协议',agent:'实时任务 · 来源单独记录',search:'本地证据库',data:'来源与版本留存',experiments:'v7 · 冻结开发诊断',history:'原始记录留存',plugin:'证据侧栏 · 理想演示'}[view];clearError();if(view==='history')loadHistory();if(view==='data')loadDataCandidates();if(view==='agent')loadAgentJobs();if(view==='experiments'){loadExperiments();loadRoutingStatus()};requestAnimationFrame(()=>window.scrollTo(0,0))}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>showView(b.dataset.view));
function caseList(){const q=$('#case-filter').value.toLowerCase();const rows=cases.filter(c=>JSON.stringify(c).toLowerCase().includes(q));$('#case-count').textContent=rows.length+' 题';$('#cases').innerHTML=rows.map(c=>`<button class="case ${current?.id===c.id?'selected':''}" data-id="${esc(c.id)}"><strong>${esc(c.relation.predicate)}</strong><small>${esc(c.relation.subject)} → ${esc(c.relation.object)}</small><small>${esc(c.id.split('.').pop())}</small></button>`).join('')||'<p class="meta">没有匹配样本</p>';$('#cases').querySelectorAll('button').forEach(b=>b.onclick=()=>selectCase(b.dataset.id))}
function selectCase(id){generation++;current=cases.find(c=>c.id===id);runRecord=null;$('#review-note').value='';$('#export').disabled=true;$('#review-form').hidden=true;$('#run').disabled=false;$('#run-state').textContent='尚未执行';$('#case-title').textContent='目标关系 · '+current.relation.predicate;$('#case-id').textContent=current.id;$('#target').innerHTML=`<strong>${esc(current.relation.subject)}</strong><span class="predicate">${esc(current.relation.predicate)} →</span><strong>${esc(current.relation.object)}</strong>`;$('#evidence').innerHTML=current.evidence.map(e=>`<article class="quote"><small>${esc(e.id)} · ${e.text.length} 字符</small><p>${esc(e.text)}</p></article>`).join('');$('#custom-input').value=JSON.stringify(current,null,2);$('#result').className='result-empty';$('#result').textContent='选择模型与流程，查看该样本的实际核验过程。';$('#facts').innerHTML='';$('#review-plan').innerHTML='';delete $('#review-plan').dataset.source;$('#trace').innerHTML='';caseList();clearError()}
$('#case-filter').addEventListener('input',caseList);
$('#run').onclick=async()=>{const token=++generation;$('#run').disabled=true;$('#run-state').textContent='执行中';clearError();try{let spec={case_id:current.id,model:$('#model').value,mode:$('#mode').value,source:$('#source').value};if(spec.source==='live')spec.custom_input=JSON.parse($('#custom-input').value);const created=await api('/api/runs',spec);for(;;){const rec=await api('/api/runs/'+created.id);if(token!==generation)return;if(['completed','failed'].includes(rec.state)){renderRun(rec);break}await new Promise(r=>setTimeout(r,350))}}catch(e){if(token===generation){error(e);$('#run-state').textContent='未能执行'}}finally{if(token===generation)$('#run').disabled=false}};
function renderRun(rec){runRecord=rec;if(rec.result)displayInput(rec.result.input);$('#followup')?.remove();$('#export').disabled=false;$('#run-state').textContent=rec.state==='completed'?'执行完成':'执行失败';$('#trace').innerHTML=`<h2>执行记录</h2>${rec.events.map(e=>`<div class="trace-item"><span>${esc(e.stage)} · ${esc(e.state)}</span><span>${new Date(e.at*1000).toLocaleTimeString()}</span></div>`).join('')}`;if(rec.state==='failed'){$('#review-plan').innerHTML='';delete $('#review-plan').dataset.source;$('#result').textContent=rec.error;$('#review-form').hidden=true;return}const result=rec.result,out=result.output;if(result.mode==='split')mountPlan('review-plan','verifier_run',rec.id,[result.input.relation],true);else{$('#review-plan').innerHTML='';delete $('#review-plan').dataset.source}const pred=out.prediction||out.response?.parsed||{};const verdict=out.status==='ok'?pred.verdict:'invalid_output';$('#result').className='result-body';const valid=result.span_checks.filter(x=>x.valid).length;$('#result').innerHTML=`<p class="verdict ${verdict==='invalid_output'?'bad':''}">${esc(labels[verdict]||verdict||'无判定')}</p><span class="tag">${result.source==='archived_v7'?'历史实验回放':'新模型推理'}</span><p class="meta">${esc(modelLabels[result.model]||result.model)} · ${result.mode==='split'?'抽取 + 核验':'直接核验'}</p>${pred.rationale?`<p>${esc(pred.rationale)}</p>`:''}<p>变量绑定：${Object.keys(pred.bindings||{}).length?esc(JSON.stringify(pred.bindings)):'本例无绑定输出'}</p><p class="meta">引用位置检查 ${valid} / ${result.span_checks.length} 通过<br>位置命中仅证明出处，不代表语义支持。</p>${(out.errors||[]).length?`<details><summary>查看运行错误</summary><pre>${esc(JSON.stringify(out.errors,null,2))}</pre></details>`:''}`;const facts=out.facts||[];$('#facts').innerHTML=`<div class="section-head"><h2>抽取与独立核验</h2><span class="meta">${facts.length} 条事实</span></div>${facts.length?facts.map(f=>{const fact=f.fact||f;const v=f.verification?.parsed||{};return `<article class="fact"><div class="fact-top"><strong>${esc(fact.subject)} → ${esc(fact.predicate)} → ${esc(fact.object)}</strong><span class="tag">${esc(labels[v.verdict]||'未独立核验')}</span></div><p>${esc(v.rationale||'')}</p>${(fact.quotes||[]).map(q=>`<blockquote>${esc(q.text)}<br><small>${esc(q.evidence_id)} · [${q.start}, ${q.end})</small></blockquote>`).join('')}${fact.calculation?.kind&&fact.calculation.kind!=='none'?`<p class="meta">程序计算：${esc(JSON.stringify(fact.calculation))}</p>`:''}</article>`}).join(''):'<p class="meta">本次输出没有可展示的结构化事实；直接核验的原始响应可在导出记录中检查。</p>'}`;if(result.mode==='split')mountCurationControls(rec);if(verdict==='missing'){const b=document.createElement('button');b.id='followup';b.textContent='按缺失关系补搜';b.onclick=()=>followup(rec.id);$('#result').append(b)}$('#review-form').hidden=false;$('#reviews').innerHTML=rec.reviews.map(r=>`<p class="review-item"><span class="tag">${esc(labels[r.verdict])}</span> ${esc(r.note)}</p>`).join('')}
$('#save-review').onclick=async()=>{if(!runRecord)return;try{await api('/api/reviews',{run_id:runRecord.id,verdict:$('#review-verdict').value,note:$('#review-note').value});$('#review-note').value='';const id=runRecord.id;const rec=await api('/api/runs/'+id);if(runRecord?.id===id)renderRun(rec)}catch(e){error(e)}};
$('#export').onclick=()=>{if(!runRecord)return;const url=URL.createObjectURL(new Blob([JSON.stringify(runRecord,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='evidence-run-'+runRecord.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
async function loadExperiments(){try{const all=await api('/api/evaluations');let html='',bars='';for(const model of ['qwen14b','qwen7b','llama8b'])for(const mode of ['split','direct']){const c=all[model][mode].regression80.counts;const name=modelLabels[model]+' / '+(mode==='split'?'拆分':'直接');html+=`<tr><td>${esc(name)}</td><td>${c.joint}/80</td><td>${c.positive}/40</td><td>${c.negative}/40</td><td>${c.variables}/16</td><td>${c.invalid}</td><td>${c.critical_false_accept}</td></tr>`;bars+=`<div class="bar-row"><span>${esc(name)}</span><div class="bar-track"><div class="bar ${mode}" style="width:${c.joint/80*100}%"></div></div><small>${(c.joint/80*100).toFixed(2)}%</small></div>`}$('#experiment-rows').innerHTML=html;$('#experiment-chart').innerHTML=bars}catch(e){error(e)}}
async function loadHistory(){loadRepairHistory();try{const rows=await api('/api/runs');$('#history-list').innerHTML=rows.map(r=>`<article class="history-row"><div><strong>${esc(r.input.custom_input?.id||r.input.case_id)}</strong><p>${esc(modelLabels[r.input.model])} · ${esc(r.input.mode)} · ${r.input.source==='archive'?'历史回放':'新推理'}</p><p>${new Date(r.created*1000).toLocaleString()} · ${esc(r.state)}</p></div><button data-id="${r.id}">查看详情</button></article>`).join('')||'<p>还没有运行记录。先在证据核验中查看一个样本。</p>';$('#history-list').querySelectorAll('button').forEach(b=>b.onclick=async()=>{const token=++generation;try{const rec=await api('/api/runs/'+b.dataset.id);if(token!==generation)return;selectCase(rec.input.case_id);showView('review');renderRun(rec)}catch(e){error(e)}})}catch(e){error(e)}}
$('#refresh-history').onclick=loadHistory;
function displayInput(row){
 $('#case-title').textContent='目标关系 · '+row.relation.predicate;
 $('#case-id').textContent=row.id;
 $('#target').innerHTML=`<strong>${esc(row.relation.subject)}</strong><span class="predicate">${esc(row.relation.predicate)} →</span><strong>${esc(row.relation.object)}</strong>`;
 $('#evidence').innerHTML=row.evidence.map(e=>`<article class="quote"><small>${esc(e.id)} · ${e.text.length} 字符</small><p>${esc(e.text)}</p></article>`).join('');
 $('#custom-input').value=JSON.stringify(row,null,2);
}
let searchSession=null,openedDoc=null,searchEpoch=0;
function renderSession(session){
 searchSession=session;
 $('#session-budget').textContent=`工具调用 ${session.used} / ${session.budget} · ${session.state==='exhausted'?'预算已用完':'可继续取证'}`;
 $('#export-session').disabled=false;
 $('#verify-session').disabled=!liveReady||!session.target||!session.calls.some(c=>['open','find'].includes(c.action)&&c.result.ok&&c.result.evidence.length);
 $('#session-trace').hidden=false;
 $('#tool-events').innerHTML=session.calls.map(c=>`<div class="trace-item"><span>${esc(c.action)} · ${c.result.ok?'完成':esc(c.result.error)}${c.cached?' · 缓存命中':''}</span><span>${c.latency_ms.toFixed(1)} ms</span></div>`).join('');
 $('#retrieval-plan').innerHTML=session.plan.length?`<div class="notice">缺失关系查询：${esc(session.plan[0])}<br>来源：固定关系模板。检索命中不等于证据支持，收集后需重新核验。</div>`:'';
 $('#search-form button').disabled=session.used>=session.budget;
 $('#find-form button').disabled=session.used>=session.budget;
 refreshSessionOptions();
}
async function refreshSessionOptions(){
 try{const rows=await api('/api/search-sessions');$('#session-picker').innerHTML='<option value="">恢复已有取证会话</option>'+rows.map(r=>`<option value="${esc(r.id)}">${esc(r.id.slice(0,8))} · ${r.used}/${r.budget} 次${r.parent_run?' · 关系补搜':''}</option>`).join('');if(searchSession)$('#session-picker').value=searchSession.id}catch(e){error(e)}
}
$('#session-picker').onchange=async()=>{
 const id=$('#session-picker').value;if(!id)return;const token=++searchEpoch;
 try{const session=await api('/api/search-sessions/'+id);if(token!==searchEpoch)return;renderSession(session);const last=session.calls.at(-1);openedDoc=session.documents.find(d=>d.opened)?.doc_id||null;$('#find-form').hidden=!openedDoc;if(last)renderTool({...last.result,used:session.used,budget:session.budget},last.action);else $('#search-results').innerHTML='';$('#query').value=session.plan[0]||''}catch(e){if(token===searchEpoch)error(e)}
};
function renderTool(result,action){
 $('#search-info').textContent=result.ok?`${result.evidence.length} 个证据片段 · ${corpusLabel} · ${action}`:result.message;
 $('#search-results').innerHTML=result.evidence.map(r=>`<article class="search-hit"><div class="section-head"><h2>${esc(r.title)}</h2>${action==='search'?`<button data-doc="${esc(r.doc_id)}" ${result.used>=result.budget?'disabled':''}>打开段落</button>`:''}</div><p>${esc(r.text)}</p><small class="meta">${esc(r.doc_id)} · 原文 [${r.start}, ${r.end}) / ${r.document_chars} 字符${r.score!==undefined?' · BM25 '+r.score.toFixed(5):''}</small></article>`).join('')||(result.ok?'<p>没有匹配证据，可更换查询；本次调用仍计入工具预算。</p>':'');
 $('#search-results').querySelectorAll('[data-doc]').forEach(b=>b.onclick=()=>toolCall('open',{doc_id:b.dataset.doc}));
}
async function ensureSession(){
 if(!searchSession){const token=searchEpoch;const session=await api('/api/search-sessions',{});if(token!==searchEpoch)throw Error('取证会话已切换');renderSession(session)}
 return searchSession;
}
async function toolCall(action,args){
 const token=searchEpoch;
 try{
  const session=await ensureSession();
  const result=await api('/api/search-sessions/'+session.id+'/calls',{action,args});
  const updated=await api('/api/search-sessions/'+session.id);
  if(token!==searchEpoch)return;
  renderSession(updated);renderTool(result,action);
  if(action==='open'&&result.ok){openedDoc=args.doc_id;$('#find-form').hidden=false}
 }catch(e){if(token===searchEpoch)error(e)}
}
$('#search-form').onsubmit=e=>{e.preventDefault();clearError();toolCall('search',{query:$('#query').value})};
$('#find-form').onsubmit=e=>{e.preventDefault();clearError();toolCall('find',{doc_id:openedDoc,keyword:$('#keyword').value})};
$('#new-session').onclick=async()=>{
 ++searchEpoch;searchSession=null;openedDoc=null;$('#find-form').hidden=true;$('#search-results').innerHTML='';clearError();
 try{await ensureSession();$('#search-info').textContent='新会话已创建，工具预算已独立重置。'}catch(e){error(e)}
};
async function followup(runId){
 const token=++searchEpoch;showView('search');clearError();$('#search-info').textContent='正在根据缺失关系检索并打开首个段落';
 try{const session=await api('/api/followups',{run_id:runId,budget:6});if(token!==searchEpoch)return;renderSession(session);const last=session.calls.at(-1);if(last){renderTool({...last.result,used:session.used,budget:session.budget},last.action);if(last.action==='open'&&last.result.ok){openedDoc=last.args.doc_id;$('#find-form').hidden=false}}$('#query').value=session.plan[0]||''}catch(e){if(token===searchEpoch)error(e)}
}
$('#export-session').onclick=()=>{
 if(!searchSession)return;
 const u=URL.createObjectURL(new Blob([JSON.stringify(searchSession,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='retrieval-'+searchSession.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
};
$('#verify-session').onclick=async()=>{
 if(!searchSession||!liveReady)return;
 const sid=searchSession.id,token=++generation,caseId=current.id;
 try{
  const input=await api('/api/search-sessions/'+sid+'/input');if(token!==generation)return;
  const spec={case_id:caseId,model:$('#model').value,mode:'split',source:'live',retrieval_session:sid};
  const job=await api('/api/runs',spec);if(token!==generation)return;
  showView('review');$('#run').disabled=true;$('#run-state').textContent='收集证据重新核验中';
  for(;;){const rec=await api('/api/runs/'+job.id);if(token!==generation)return;if(['completed','failed'].includes(rec.state)){displayInput(input);renderRun(rec);break}await new Promise(r=>setTimeout(r,350))}
 }catch(e){if(token===generation)error(e)}finally{if(token===generation)$('#run').disabled=false}
};
function mountPlan(containerId,kind,parentId,defaults,enabled,context={}){
 const box=$('#'+containerId),identity=kind+':'+parentId;
 if(box.dataset.source===identity)return;
 box.dataset.source=identity;
 box.innerHTML=`<details class="relation-plan"><summary>关系计划与联合绑定</summary><p class="meta">先提出必要关系，再核对同一条链上的实体。自动草稿只看问题和候选答案；事实抽取只看证据。</p><details class="proposal-controls"><summary>从问题生成关系草稿</summary><label>问题<textarea class="proposal-question" rows="2" maxlength="2000" ${kind==='agent_job'?'readonly':''}></textarea></label><label>候选答案<input class="proposal-candidate" maxlength="1000" ${kind==='agent_job'?'readonly':''}></label><button class="proposal-generate" ${plannerReady?'':'disabled'}>生成草稿</button>${plannerReady?'':'<p class="meta">尚未连接计划模型；可继续手写下方关系。</p>'}<div class="proposal-history"><label>已有计划<select class="proposal-picker"><option value="">暂无记录</option></select></label><button class="proposal-restore" disabled>恢复草稿</button></div><div class="proposal-status" aria-live="polite"></div></details><div class="proposal-coverage"></div><label>关系计划（JSON数组）<textarea class="plan-editor" rows="6" spellcheck="false"></textarea></label><label>计划审阅说明<input class="proposal-note" maxlength="2000" placeholder="使用自动草稿时，说明关系与遗漏条件的审阅情况"></label><button class="plan-start" ${enabled?'':'disabled'}>核验所列关系</button>${enabled?'':'<p class="meta">需先连接核验模型，才能检查搜索候选的证据。</p>'}<div class="plan-result" aria-live="polite"></div></details>`;
 const editor=box.querySelector('.plan-editor'),button=box.querySelector('.plan-start'),resultBox=box.querySelector('.plan-result');
 const question=box.querySelector('.proposal-question'),candidate=box.querySelector('.proposal-candidate'),generate=box.querySelector('.proposal-generate'),status=box.querySelector('.proposal-status'),coverage=box.querySelector('.proposal-coverage'),picker=box.querySelector('.proposal-picker'),restore=box.querySelector('.proposal-restore'),note=box.querySelector('.proposal-note');
 const active=()=>box.isConnected&&box.dataset.source===identity&&box.querySelector('.plan-editor')===editor;
 let proposalId=null,proposalEpoch=0,busy=false;
 function lock(value){busy=value;editor.disabled=value;button.disabled=value||!enabled;generate.disabled=value||!plannerReady;restore.disabled=value||!picker.value;question.disabled=value;candidate.disabled=value;note.disabled=value;picker.disabled=value}
 question.value=context.question||'';candidate.value=context.candidate||'';
 editor.value=JSON.stringify(defaults,null,2);
 async function refreshProposals(){
  try{const rows=await api('/api/plan-proposals?parent_kind='+encodeURIComponent(kind)+'&parent_id='+encodeURIComponent(parentId));if(!active())return;const selected=picker.value;picker.innerHTML='<option value="">选择已有草稿</option>'+rows.map(r=>`<option value="${esc(r.id)}">${esc(r.state)} · ${new Date(r.created*1000).toLocaleString()}</option>`).join('');if(rows.some(r=>r.id===selected))picker.value=selected;restore.disabled=busy||!picker.value}catch(e){if(active())status.textContent=e.message}
 }
 function applyProposal(job){
  proposalId=job.id;question.value=job.input.question;candidate.value=job.input.candidate;
  const p=job.result.proposal,last=job.revisions.at(-1);
  editor.value=JSON.stringify(last?last.snapshot.relations:p.relations,null,2);note.value=last?.snapshot.note||'';
  coverage.innerHTML=`<h3>逐项查看问题条件</h3>${p.clauses.map(c=>`<div class="proposal-clause"><strong>${esc(c.text)}</strong><span>${{relation:'已提出关系',unsupported:'当前关系范围无法表达',ambiguous:'条件有歧义'}[c.kind]}</span><p>${esc(c.reason)}</p></div>`).join('')}${p.coverage.unattributed_question_spans.length?`<p>尚未归属的文字：${p.coverage.unattributed_question_spans.map(s=>esc(s.text)).join('；')}</p>`:''}<p class="meta">模型自报覆盖：${esc(p.model_reported_coverage)}。原文位置和结构可校验，语义覆盖仍需审阅。${last?.snapshot.user_edited?'已恢复人工修改版本，原逐项对应可能过期。':''}</p><details><summary>查看计划来源与版本</summary><pre>${esc(JSON.stringify(job,null,2))}</pre></details>`;
  status.textContent='草稿已载入；确认关系、角色与未支持条件后再提交核验。';
 }
 async function watchProposal(id,token){
  for(;;){const job=await api('/api/plan-proposals/'+id);if(!active()||token!==proposalEpoch)return;if(job.state==='canceled')throw Error('草稿任务已停止');if(job.state==='failed'){coverage.innerHTML=`<details><summary>查看失败记录</summary><pre>${esc(JSON.stringify(job,null,2))}</pre></details>`;throw Error(job.error)}if(job.state==='completed'){applyProposal(job);return}status.textContent='正在生成关系草稿；不会向计划模型发送证据。';await new Promise(resolve=>setTimeout(resolve,500))}
 }
 function clearProposal(){proposalEpoch++;proposalId=null;coverage.innerHTML='';status.textContent='问题或答案已修改，原草稿不再关联当前手写计划。'}
 question.oninput=clearProposal;candidate.oninput=clearProposal;
 generate.onclick=async()=>{
  const token=++proposalEpoch;lock(true);proposalId=null;coverage.innerHTML='';clearError();
  try{const created=await api('/api/plan-proposals',{parent_kind:kind,parent_id:parentId,question:question.value,candidate:candidate.value});if(active()&&token===proposalEpoch)await watchProposal(created.id,token)}catch(e){if(active()&&token===proposalEpoch){status.textContent=e.message;error(e)}}finally{if(active()){lock(false);refreshProposals()}}
 };
 picker.onchange=()=>restore.disabled=busy||!picker.value;
 restore.onclick=async()=>{if(!picker.value)return;const token=++proposalEpoch;lock(true);try{await watchProposal(picker.value,token)}catch(e){if(active()){status.textContent=e.message;error(e)}}finally{if(active())lock(false)}};
 refreshProposals();
 button.onclick=async()=>{
  lock(true);clearError();
  try{
   const relations=JSON.parse(editor.value);
   const spec={parent_kind:kind,parent_id:parentId,relations};
   if(proposalId){const revision=await api('/api/plan-proposals/'+proposalId+'/revisions',{relations,note:note.value});if(!active())return;spec.plan_revision_id=revision.id}
   const created=await api('/api/relation-checks',spec);
   for(;;){
    const job=await api('/api/relation-checks/'+created.id);
    if(!active())return;
    if(job.state==='failed')throw Error(job.error);
    if(job.state==='completed'){
     const r=job.result;
     resultBox.innerHTML=`<h3>联合绑定：${esc(labels[r.verdict]||r.status)}</h3><p>${r.binding_gap?'各关系分别有证据，但变量无法绑定成同一条链。':esc(r.error||'')}</p><p>绑定：${esc(JSON.stringify(r.bindings||{}))}</p><p class="meta">范围：声明关系 · 问题覆盖完整性未建立</p>${(r.relation_results||[]).map((item,i)=>`<p class="meta">关系 ${i+1} 独立判断：${esc(labels[item.verdict]||item.verdict)}</p>`).join('')}<details><summary>查看输入、引用与原始结果</summary><pre>${esc(JSON.stringify(job,null,2))}</pre></details>`;
     mountRepairControls(resultBox,job);
     mountCurationControls(job,resultBox,'check');
     break;
    }
    resultBox.textContent='正在抽取、核验或合并变量绑定';await new Promise(resolve=>setTimeout(resolve,350));
   }
  }catch(e){if(active()){resultBox.textContent=e.message;error(e)}}
  finally{if(active())lock(false)}
 };
}
let repairEpoch=0;
const repairStates={preparing:'准备会话',queued:'排队中',running:'补搜与核验中',cancel_requested:'等待当前请求结束',canceled:'已停止',failed:'执行失败',completed:'执行结束'};
const repairReasons={entailed:'声明关系已支持',contradicted:'发现矛盾',ambiguous:'发现歧义',tool_budget_exhausted:'额外工具预算耗尽',round_limit:'达到核验轮次上限',no_searchable_gap:'缺口缺少可检索实体',no_new_evidence:'未取得新证据',invalid_output:'核验输出无效',execution_failure:'运行失败',preparation_failure:'会话准备失败',service_restarted:'服务重启，任务已中断',canceled:'用户停止'};
function downloadRecord(value,name){const u=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=name+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
function downloadText(value,name){const u=URL.createObjectURL(new Blob([value],{type:'application/x-ndjson'}));const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
let dataEpoch=0,dataRecord=null,dataListEpoch=0,dataExportEpoch=0,dataOffset=0;
const dataUsage={train_candidate:'训练来源候选',development_only:'仅开发分析',holdout:'保留作留出',fixture:'教学材料'};
const dataDecisions={pending:'待审',include:'人工纳入',exclude:'人工排除'};
async function loadDataCandidates(){
 const token=++dataListEpoch;
 try{const [page,exports]=await Promise.all([api('/api/data-candidates?offset='+dataOffset),api('/api/data-exports')]);if(token!==dataListEpoch)return;
  const rows=page.rows;
  $('#data-counts').textContent=`实际收集 ${page.total} 条 · 待审 ${page.counts.pending} 条 · 人工纳入 ${page.counts.included} 条 · 训练来源候选 ${page.counts.training_source} 条`;
  $('#data-prev').disabled=page.offset===0;$('#data-next').disabled=page.offset+page.limit>=page.total;$('#data-page').textContent=page.total?`${page.offset+1}–${Math.min(page.offset+page.limit,page.total)}`:'0 条';
  $('#data-list').innerHTML=rows.map(r=>`<button class="data-sample ${dataRecord?.id===r.id?'selected':''}" data-candidate="${esc(r.id)}"><strong>${r.task==='extract'?'事实抽取':'支持性核验'} · ${esc(r.source.dataset)}</strong><small>${esc(dataDecisions[r.decision])} · ${esc(dataUsage[r.usage])}</small></button>`).join('')||'<p class="meta">暂无样本。核验结果下可收集待审事实，已有数据也可从上方导入。</p>';
  $('#data-list').querySelectorAll('[data-candidate]').forEach(b=>b.onclick=()=>openDataCandidate(b.dataset.candidate,++dataEpoch));
  const selected=$('#data-export-picker').value;
  $('#data-export-picker').innerHTML='<option value="">选择已有导出</option>'+exports.map(e=>`<option value="${esc(e.id)}">${e.rows} 条 · ${new Date(e.created*1000).toLocaleString()}</option>`).join('');
  if(exports.some(e=>e.id===selected))$('#data-export-picker').value=selected;
 }catch(e){if(token===dataListEpoch)error(e)}
}
function renderDataCandidate(row){
 dataRecord=row;
 const host=$('#data-detail'),value=row.input,last=row.reviews.at(-1)?.input;
 host.className='data-candidate-detail';
 host.innerHTML=`<h2>${value.task==='extract'?'审阅事实抽取':'审阅支持性判断'}</h2><p class="meta">${esc(value.source.dataset)} · ${esc(value.source.split)} · ${esc(dataUsage[value.usage])}<br>模型建议不是金标；训练来源声明不代表语义正确或已获独立双标。</p><div class="data-evidence">${row.quoted_evidence.map(e=>`<article class="quote"><small>${esc(e.id)}</small>${e.segments.map(q=>`<p><strong>${esc(q.quote_id)}</strong> ${esc(q.text)}</p>`).join('')}</article>`).join('')}</div>${value.raw_fact?`<details open><summary>待核验事实</summary><pre>${esc(JSON.stringify(value.raw_fact,null,2))}</pre></details>`:''}<form class="data-review-form"><label>修订后的输出（JSON）<textarea class="data-answer" rows="9" spellcheck="false" required></textarea></label><div class="review-controls"><label>整理人<input class="data-reviewer" maxlength="100" required placeholder="填写自己的标注ID"></label><label>处理方式<select class="data-decision"><option value="include">纳入候选</option><option value="exclude">排除</option></select></label></div><label>修订理由<input class="data-note" maxlength="2000" required placeholder="说明关系、角色或完整性判断"></label><button class="data-save" type="submit">保存人工修订</button><span class="data-review-status meta" aria-live="polite"></span></form><details><summary>来源与全部修订记录</summary><pre>${esc(JSON.stringify(row,null,2))}</pre></details>`;
 host.querySelector('.data-answer').value=JSON.stringify(last?.answer||value.suggestion,null,2);
 host.querySelector('.data-reviewer').value=last?.reviewer||'';host.querySelector('.data-note').value=last?.note||'';host.querySelector('.data-decision').value=last?.decision||'include';
 host.querySelector('form').onsubmit=async e=>{e.preventDefault();const token=dataEpoch,button=host.querySelector('.data-save'),status=host.querySelector('.data-review-status');button.disabled=true;clearError();try{const body={base_review_id:row.latest_review_id,reviewer:host.querySelector('.data-reviewer').value,note:host.querySelector('.data-note').value,decision:host.querySelector('.data-decision').value,answer:JSON.parse(host.querySelector('.data-answer').value)};const saved=await api('/api/data-candidates/'+row.id+'/reviews',body);if(token===dataEpoch){renderDataCandidate(saved);$('#data-detail .data-review-status').textContent='新版本已保存；原模型输出和旧修订均保留。';loadDataCandidates()}}catch(err){if(token===dataEpoch){status.textContent=err.message;error(err)}}finally{if(button.isConnected)button.disabled=false}};
 $('#data-list').querySelectorAll('[data-candidate]').forEach(b=>b.classList.toggle('selected',b.dataset.candidate===row.id));
}
async function openDataCandidate(id,token){try{const row=await api('/api/data-candidates/'+id);if(token!==dataEpoch)return;renderDataCandidate(row)}catch(e){if(token===dataEpoch)error(e)}}
function mountCurationControls(rec,host=$('#facts'),kind='run'){
 const facts=(kind==='run'?rec.result.output.facts:rec.result.fact_rows||rec.input.archived_output?.facts)||[],box=document.createElement('details');box.className='curation-collect';
 box.innerHTML=`<summary>收集为待审样本</summary><p class="meta">仅收集原始材料与模型建议。整题人工复核不充当逐事实标签；开发题仍只用于开发分析。</p><div class="repair-actions"><label>任务<select class="collect-task"><option value="extract">完整事实抽取</option>${facts.length?'<option value="verify">逐事实支持性</option>':''}</select></label><label class="collect-index-label" hidden>事实<select class="collect-index">${facts.map((f,i)=>`<option value="${i}">${i+1} · ${esc(f.fact?.predicate||'无效事实')}</option>`).join('')}</select></label><button class="collect-save">收集待审样本</button></div><span class="collect-status meta"></span>`;
 host.append(box);
 box.querySelector('.collect-task').onchange=e=>box.querySelector('.collect-index-label').hidden=e.target.value!=='verify';
 box.querySelector('.collect-save').onclick=async e=>{const button=e.currentTarget;button.disabled=true;try{const body={task:box.querySelector('.collect-task').value,fact_index:Number(box.querySelector('.collect-index').value)};body[kind==='run'?'run_id':'check_id']=rec.id;const row=await api('/api/data-candidates/from-'+(kind==='run'?'run':'check'),body);if(!box.isConnected)return;dataOffset=0;showView('data');await openDataCandidate(row.id,++dataEpoch)}catch(err){if(box.isConnected){box.querySelector('.collect-status').textContent=err.message;error(err)}}finally{if(button.isConnected)button.disabled=false}};
}
$('#data-refresh').onclick=loadDataCandidates;
$('#data-prev').onclick=()=>{dataOffset=Math.max(0,dataOffset-50);loadDataCandidates()};
$('#data-next').onclick=()=>{dataOffset+=50;loadDataCandidates()};
$('#data-template').onclick=async()=>{try{const rows=await api('/api/data-template');$('#data-import-text').value=rows.map(r=>JSON.stringify(r)).join('\n');$('#data-import-status').textContent='已填入教学模板，尚未导入；即使导入并审阅，也不会进入训练导出。'}catch(e){error(e)}};
$('#data-file').onchange=async e=>{const file=e.target.files[0];if(!file)return;if(file.size>190000){$('#data-import-status').textContent='单次文件需小于190KB，请分批导入。';return}$('#data-import-text').value=await file.text()};
$('#data-import-button').onclick=async()=>{const button=$('#data-import-button');button.disabled=true;clearError();try{const text=$('#data-import-text').value.trim(),records=text.startsWith('[')?JSON.parse(text):text.split('\n').filter(s=>s.trim()).map(s=>JSON.parse(s));if(new TextEncoder().encode(JSON.stringify({records})).length>200000)throw Error('单次导入内容过大，请分批到200KB以内。');const result=await api('/api/data-candidates',{records});$('#data-import-status').textContent=`已接收 ${result.ids.length} 条记录；完全相同的候选复用已有记录。`;await loadDataCandidates()}catch(e){$('#data-import-status').textContent=e.message;error(e)}finally{button.disabled=false}};
function renderDataExport(value){const m=value.manifest,host=$('#data-export-detail');host.innerHTML=`<h3>冻结 ${m.rows} 条 · ${m.groups} 个来源组</h3><p class="meta">排除或去重 ${m.excluded.length} 条。仅完成数据整理，不代表已训练或独立验证。</p><div class="repair-actions"><button class="download-data" ${m.rows?'':'disabled'}>下载训练JSONL</button><button class="download-manifest">下载来源清单</button></div><details><summary>查看分布、排除原因和摘要</summary><pre>${esc(JSON.stringify(m,null,2))}</pre></details>`;host.querySelector('.download-data').onclick=()=>downloadText(value.jsonl,'sft-curated-'+m.id+'.jsonl');host.querySelector('.download-manifest').onclick=()=>downloadRecord(m,'sft-manifest-'+m.id)}
$('#data-export').onclick=async()=>{const button=$('#data-export'),token=++dataExportEpoch;button.disabled=true;clearError();try{const value=await api('/api/data-exports',{});if(token===dataExportEpoch){renderDataExport(value);await loadDataCandidates()}}catch(e){if(token===dataExportEpoch)error(e)}finally{button.disabled=false}};
$('#data-export-picker').onchange=async e=>{const id=e.target.value,token=++dataExportEpoch;if(!id){$('#data-export-detail').innerHTML='';return}try{const value=await api('/api/data-exports/'+id);if(token===dataExportEpoch)renderDataExport(value)}catch(err){if(token===dataExportEpoch)error(err)}};
function renderRepairJob(job,host,alive){
 const result=job.result||{},used=job.session?.used||0,initial=job.input.initial_tool_calls;
 host.innerHTML=`<div class="section-head"><h3>${esc(repairStates[job.state]||job.state)}</h3><span class="meta">额外调用 ${used} / ${job.input.additional_tool_budget}</span></div><p>初始判定：${esc(labels[result.initial?.verdict]||result.initial?.status)} → 当前判定：${esc(labels[result.final?.verdict]||result.final?.status)}</p><p class="meta">${result.stop_reason?esc(repairReasons[result.stop_reason]||result.stop_reason):'只补搜所声明计划的缺口，原计划和原证据保留。'}<br>原搜索调用：${initial===null?'未记录':initial} · 累计调用：${initial===null?'无法计算':initial+used} · 已完成重核验 ${result.rounds?.length||0} / ${job.input.max_rounds}</p>${job.error?`<p>${esc(job.error)}</p>`:''}<div class="repair-actions"><button class="repair-cancel" ${['preparing','queued','running'].includes(job.state)?'':'disabled'}>停止补搜</button><button class="repair-export">导出完整记录</button></div>${result.usage_complete===false?'<p class="meta">调用中断，部分成本仅保留在阶段记录中，当前汇总不完整。</p>':''}<details><summary>关系、证据和执行轨迹</summary><pre>${esc(JSON.stringify(job,null,2))}</pre></details>`;
 host.querySelector('.repair-export').onclick=()=>downloadRecord(job,'relation-repair-'+job.id);
 host.querySelector('.repair-cancel').onclick=async e=>{e.currentTarget.disabled=true;try{const value=await api('/api/repair-jobs/'+job.id+'/cancel',{});if(alive())renderRepairJob(value,host,alive)}catch(err){if(alive())error(err)}};
}
async function watchRepairJob(id,host,token){
 const alive=()=>host.isConnected&&token===repairEpoch;
 for(;;){const job=await api('/api/repair-jobs/'+id);if(!alive())return;renderRepairJob(job,host,alive);if(['completed','failed','canceled'].includes(job.state))return;await new Promise(resolve=>setTimeout(resolve,700))}
}
function mountRepairControls(host,check){
 if(check.result.status!=='ok'||check.result.verdict!=='missing')return;
 const panel=document.createElement('section');panel.className='repair-controls';
 panel.innerHTML=`<h3>按这份核验记录补搜</h3><p class="meta">固定关系查询，打开候选原文后重新核验整份计划。下方预算是额外调用；不会改写当前编辑器中的草稿。</p><div class="repair-actions"><label>额外工具预算<select class="repair-budget"><option value="2">2次</option><option value="4">4次</option><option value="6" selected>6次</option><option value="8">8次</option><option value="12">12次</option></select></label><label>最多核验轮次<select class="repair-rounds"><option value="1">1轮</option><option value="2" selected>2轮</option></select></label><button class="repair-start" ${liveReady?'':'disabled'}>补搜并重新核验</button></div>${liveReady?'':'<p class="meta">连接核验模型后可执行，当前不会启动真实推理。</p>'}<div class="repair-result" aria-live="polite"></div>`;
 host.append(panel);
 const button=panel.querySelector('.repair-start'),output=panel.querySelector('.repair-result');
 button.onclick=async()=>{button.disabled=true;clearError();const token=++repairEpoch;try{const created=await api('/api/repair-jobs',{parent_check_id:check.id,budget:Number(panel.querySelector('.repair-budget').value),rounds:Number(panel.querySelector('.repair-rounds').value)});if(output.isConnected&&token===repairEpoch)await watchRepairJob(created.id,output,token)}catch(e){if(output.isConnected&&token===repairEpoch){output.textContent=e.message;error(e)}}finally{if(button.isConnected)button.disabled=!liveReady}};
}
async function loadRepairHistory(){
 try{const rows=await api('/api/repair-jobs');$('#repair-history').innerHTML=rows.map(r=>`<article class="history-row"><div><strong>缺失关系补搜</strong><p>${esc(repairStates[r.state]||r.state)} · ${new Date(r.created*1000).toLocaleString()}</p><small>父核验 ${esc(r.parent_check_id)}</small></div><button data-repair-id="${esc(r.id)}">查看补搜</button></article>`).join('')||'<p class="meta">还没有补搜任务。联合核验判定缺失后，可从结果处发起。</p>';$('#repair-history').querySelectorAll('[data-repair-id]').forEach(b=>b.onclick=async()=>{const host=$('#repair-history-detail');host.textContent='正在读取补搜记录';try{await watchRepairJob(b.dataset.repairId,host,++repairEpoch)}catch(e){error(e)}})}catch(e){$('#repair-history').textContent=e.message}
}
let agentReady=false,agentEpoch=0,agentRecord=null,workflowReady=false,workflowEpoch=0,workflowRecord=null,executionKind='workflow',workflowListEpoch=0;
const workflowStates={queued:'排队中',running:'执行中',cancel_requested:'等待当前调用结束',canceled:'已停止',failed:'执行失败',completed:'执行结束'};
const workflowStages={search:'搜索取证',planning:'提出关系计划',verification:'独立核验与联合绑定',repair:'缺口补搜与重核验'};
const workflowOutcomes={pending:'尚无最终结果',search_abstained:'搜索阶段已拒答',no_supported_plan:'没有可表达的关系计划',proposed_chain_supported:'所提关系链成立',partial_chain_supported:'已覆盖的部分关系成立',candidate_binding_unestablished:'候选答案绑定尚未建立',entailed:'声明关系支持',missing:'仍缺少必要证据',contradicted:'发现矛盾',ambiguous:'仍有歧义',invalid_output:'输出格式无效',execution_failure:'运行失败',service_restarted:'服务重启中断',session_sealing_failure:'会话关闭失败',canceled:'用户停止'};
function showExecution(kind){executionKind=kind;$('#agent-pane').hidden=kind!=='agent';$('#workflow-pane').hidden=kind!=='workflow';$('#agent-export').disabled=kind==='agent'?!agentRecord:!workflowRecord}
function chooseBudget(value){const select=$('#agent-budget');if(![...select.options].some(o=>o.value===String(value))){const option=document.createElement('option');option.value=String(value);option.textContent=value+'次';select.append(option)}select.value=String(value)}
function updateTaskForm(busy=false){const full=$('#agent-flow').value==='workflow';[...$('#agent-budget').options].forEach(o=>o.disabled=full&&Number(o.value)<4);if(full&&Number($('#agent-budget').value)<4)$('#agent-budget').value='6';$('#agent-start').textContent=full?'开始完整任务':'开始搜索';$('#agent-start').disabled=busy||!(full?workflowReady:agentReady);$('#agent-question').disabled=busy;$('#agent-budget').disabled=busy;$('#agent-flow').disabled=busy}
function setupAgent(config,workflow){
 agentReady=!!config?.configured;
 workflowReady=!!workflow?.configured;
 $('#agent-status').textContent=workflowReady?`完整流程：${workflow.search_model} 搜索 → ${workflow.planner_model} 计划 → ${workflow.verifier_model} 核验。搜索与补搜共享工具预算；关系计划由模型提出。${workflow.source==='protocol_fixture'?' 当前为受控协议演练。':''}`:agentReady?`搜索模型：${config.model}。完整流程还需计划与核验服务；当前可执行仅搜索。`:'尚未连接模型服务。理想演示、历史回放、样本整理和证据检索可继续使用。';
 if(!workflowReady&&agentReady)$('#agent-flow').value='agent';showExecution($('#agent-flow').value);updateTaskForm();
}
$('#agent-flow').onchange=()=>updateTaskForm();
async function loadWorkflows(){const token=++workflowListEpoch;try{const rows=await api('/api/workflows');if(token!==workflowListEpoch)return;$('#workflow-jobs').innerHTML=rows.map(r=>`<button class="agent-job" data-workflow="${esc(r.id)}"><strong>${esc(r.question)}</strong><small>${esc(workflowStates[r.state]||r.state)} · ${new Date(r.created*1000).toLocaleString()}</small></button>`).join('')||'<p class="meta">还没有完整任务。模型服务连接后可从上方开始。</p>';$('#workflow-jobs').querySelectorAll('[data-workflow]').forEach(b=>b.onclick=()=>watchWorkflow(b.dataset.workflow,++workflowEpoch))}catch(e){if(token===workflowListEpoch)error(e)}}
function renderWorkflow(job){
 workflowRecord=job;showExecution('workflow');$('#agent-flow').value='workflow';$('#agent-question').value=job.input.question;chooseBudget(job.input.total_tool_budget);
 const busy=['queued','running','cancel_requested'].includes(job.state),r=job.result,host=$('#workflow-detail');updateTaskForm(busy);$('#agent-cancel').disabled=!['queued','running'].includes(job.state);$('#workflow-state').textContent=workflowStates[job.state]||job.state;$('#agent-progress').textContent=`总工具调用 ${job.budget.used===null?'未知':job.budget.used} / ${job.budget.limit}`;host.className='workflow-result';
 const proposed=job.children.planning?.result?.proposal,check=job.children.verification;
 host.innerHTML=`<div class="workflow-stages">${Object.entries(workflowStages).map(([stage,title])=>{const child=job.children[stage];return `<div class="workflow-stage"><strong>${esc(title)}</strong><span>${child?esc(workflowStates[child.state]||child.state):'未执行'}</span></div>`}).join('')}</div><h3>${esc(workflowOutcomes[r.outcome]||r.outcome)}</h3>${r.candidate?`<p>候选答案：${esc(r.candidate)}</p>`:''}<p class="meta">自动计划只声明模型提出的关系，整题覆盖与答案正确性尚未建立。${job.input.services?.source==='protocol_fixture'?' 当前为受控协议演练。':''}</p>${job.error?`<p>${esc(job.error)}</p>`:''}${proposed?`<details><summary>模型提出的关系与未支持条件</summary>${proposed.relations.map(rel=>`<p>${esc(rel.subject)} → ${esc(rel.predicate)} → ${esc(rel.object)}</p>`).join('')}${proposed.clauses.filter(c=>c.kind!=='relation').map(c=>`<p>${esc(c.text)}：${esc(c.reason)}</p>`).join('')}<pre>${esc(JSON.stringify(proposed.coverage,null,2))}</pre></details>`:''}<p class="meta">已记录模型请求 ${job.usage.known_totals.model_calls} 次 · 缓存命中 ${job.usage.known_totals.cache_hits} 次 · 并发合并 ${job.usage.known_totals.coalesced_hits} 次${job.usage.complete?'':' · 成本尚未完整'}</p>${r.evidence?.length?`<details><summary>保留的证据（${r.evidence.length} 段）</summary>${r.evidence.map(e=>`<article class="quote"><small>${esc(e.id)}</small><p>${esc(e.text)}</p></article>`).join('')}</details>`:''}<div class="workflow-curation"></div><details><summary>阶段事件与全部原始记录</summary><pre>${esc(JSON.stringify(job,null,2))}</pre></details>`;
 if(!busy&&check?.state==='completed')mountCurationControls(check,host.querySelector('.workflow-curation'),'check');
}
async function watchWorkflow(id,token){
 ++agentEpoch;showExecution('workflow');
 try{for(;;){const job=await api('/api/workflows/'+id);if(token!==workflowEpoch)return;renderWorkflow(job);if(['completed','failed','canceled'].includes(job.state)){loadWorkflows();return}await new Promise(resolve=>setTimeout(resolve,700))}}catch(e){if(token===workflowEpoch)error(e)}
}
async function loadAgentJobs(){
 loadWorkflows();
 try{const rows=await api('/api/agent-jobs');$('#agent-jobs').innerHTML=rows.map(r=>`<button class="agent-job" data-job="${esc(r.id)}"><strong>${esc(r.question)}</strong><small>${esc(r.state)} · ${new Date(r.created*1000).toLocaleString()}</small></button>`).join('')||'<p class="meta">还没有搜索任务。配置模型后从上方问题开始。</p>';$('#agent-jobs').querySelectorAll('[data-job]').forEach(b=>b.onclick=()=>watchAgent(b.dataset.job,++agentEpoch))}catch(e){error(e)}
}
function renderAgent(job){
 agentRecord=job;showExecution('agent');$('#agent-flow').value='agent';chooseBudget(job.config.tool_budget);$('#agent-question').value=job.question;updateTaskForm(['queued','running','cancel_requested'].includes(job.state));$('#agent-export').disabled=false;
 $('#agent-state').textContent={queued:'排队中',running:'执行中',cancel_requested:'等待当前请求结束',canceled:'已停止',failed:'执行失败',completed:'执行完成'}[job.state]||job.state;
 $('#agent-progress').textContent=`工具调用 ${job.session.used} / ${job.session.budget}`;
 $('#agent-cancel').disabled=!['queued','running'].includes(job.state);
 $('#agent-start').disabled=!agentReady||['queued','running','cancel_requested'].includes(job.state);
 const result=job.result;
 if(result&&!result.abstained)mountPlan('agent-plan','agent_job',job.id,[],liveReady,{question:job.question,candidate:result.answer});else{$('#agent-plan').innerHTML='';delete $('#agent-plan').dataset.source}
 if(result){$('#agent-answer').innerHTML=`<h3>${result.abstained?'证据不足，已拒答':esc(result.answer)}</h3><p>${esc(result.reason)}</p><p class="meta">${job.config.source==='protocol_fixture'?'受控接口演练 · ':''}候选答案尚未独立核验 · ${result.usage.model_calls} 次接口调用</p>`;$('#agent-answer').className='result-body'}
 else{$('#agent-answer').className='result-empty';$('#agent-answer').textContent=job.error||(['running','queued','cancel_requested'].includes(job.state)?'正在执行。下方保留实际模型与工具事件。':'任务未产生候选答案。')}
 $('#agent-evidence').innerHTML=(result?.evidence||[]).map(e=>`<article class="quote"><h3>${esc(e.title)}</h3><p>${esc(e.text)}</p><small>${esc(e.id)} · 原文 [${e.start}, ${e.end})</small></article>`).join('');
 $('#agent-timeline').innerHTML='<h2>搜索轨迹</h2>'+job.events.map(e=>{
  let title={model_request:'发送模型请求',model_response:'模型选择动作',tool_observation:'工具返回证据',final_candidate:'提交候选答案',failure:'执行失败'}[e.kind]||e.kind;
  const p=e.payload;
  if(e.kind==='model_response'){try{title+=' · '+JSON.parse(p.raw).action}catch{title+=' · 格式错误'}}
  if(e.kind==='tool_observation')title+=' · '+p.action.action+' · '+(p.result.ok?p.result.evidence.length+' 个片段':p.result.error);
  return `<details class="agent-event"><summary>${esc(title)}<span>${new Date(e.created*1000).toLocaleTimeString()}</span></summary><pre>${esc(JSON.stringify(p,null,2))}</pre></details>`;
 }).join('');
}
async function watchAgent(id,token){
 ++workflowEpoch;showExecution('agent');
 try{for(;;){const job=await api('/api/agent-jobs/'+id);if(token!==agentEpoch)return;renderAgent(job);if(['completed','failed','canceled'].includes(job.state)){loadAgentJobs();return}await new Promise(r=>setTimeout(r,500))}}catch(e){if(token===agentEpoch)error(e)}
}
$('#agent-form').onsubmit=async e=>{
 if($('#agent-flow').value==='workflow'){
  e.preventDefault();++agentEpoch;const token=++workflowEpoch;clearError();updateTaskForm(true);
  try{const created=await api('/api/workflows',{question:$('#agent-question').value,budget:Number($('#agent-budget').value),request_key:crypto.randomUUID()});if(token===workflowEpoch){loadWorkflows();await watchWorkflow(created.id,token)}}catch(err){if(token===workflowEpoch){error(err);updateTaskForm(false)}}return;
 }
 e.preventDefault();++workflowEpoch;const token=++agentEpoch;clearError();updateTaskForm(true);
 try{const job=await api('/api/agent-jobs',{question:$('#agent-question').value,budget:Number($('#agent-budget').value)});if(token===agentEpoch){loadAgentJobs();await watchAgent(job.id,token)}}catch(e){if(token===agentEpoch){error(e);updateTaskForm(false)}}
};
$('#agent-cancel').onclick=async()=>{if(executionKind==='workflow'){if(!workflowRecord)return;const token=workflowEpoch;try{const job=await api('/api/workflows/'+workflowRecord.id+'/cancel',{});if(token===workflowEpoch)renderWorkflow(job)}catch(e){error(e)}return}if(!agentRecord)return;const id=agentRecord.id,token=agentEpoch;try{const job=await api('/api/agent-jobs/'+id+'/cancel',{});if(token===agentEpoch)renderAgent(job)}catch(e){error(e)}};
$('#agent-refresh').onclick=loadAgentJobs;
$('#agent-export').onclick=()=>{if(executionKind==='workflow'){if(workflowRecord)downloadRecord(workflowRecord,'evidence-workflow-'+workflowRecord.id);return}if(agentRecord)downloadRecord(agentRecord,'search-agent-'+agentRecord.id)};
Promise.all([api('/api/cases'),api('/api/health')]).then(([data,health])=>{cases=data;liveReady=health.live_configured;plannerReady=!!health.planner?.configured;setupAgent(health.agent,health.workflow);corpusLabel=health.corpus_scope==='musique_dev_passages'?'MuSiQue开发集段落库':'开发诊断证据库';$('#connection').textContent=health.corpus_docs.toLocaleString()+' 个证据段落';if(!health.live_configured)$('#source option[value="live"]').disabled=true;selectCase(cases[0].id);refreshSessionOptions();showView(location.hash.slice(1)||'plugin')}).catch(error);

$('#demo-run').onclick=async()=>{
 const button=$('#demo-run');button.disabled=true;clearError();
 try{const r=await api('/api/scenarios/designer-education/run',{});
 $('#demo-content').innerHTML=`<div class="demo-outcomes"><div><h2>补搜之前</h2><p class="verdict">${esc(labels[r.initial.verdict])}</p><p>任职证据无法填补受教育关系。</p></div><div><h2>补搜之后</h2><p class="verdict">${esc(labels[r.final.verdict])}</p><p>同一设计师的两段关系完成联合绑定。</p></div></div><div class="evidence-result"><div><h2>证据材料</h2>${r.evidence.map(e=>`<article class="quote"><small>${esc(e.id)} · ${esc(e.title)}</small><p>${esc(e.text)}</p></article>`).join('')}</div><div><h2>执行过程</h2>${r.steps.map(step=>`<article class="fact"><h3>${esc(step.title)}</h3><p>${esc(step.detail)}</p><small>${{program:'程序计算',teaching_fixture:'教学材料',assumed_model_result:'理想模型假设'}[step.source]}</small></article>`).join('')}</div></div><p class="meta">联合绑定：${esc(JSON.stringify(r.final.bindings))} · 演示不包含模型准确率、训练收益或GPU性能数字。</p><details><summary>查看演示来源与原始记录</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details>`;
 }catch(e){error(e)}finally{button.disabled=false}
};

async function loadRoutingStatus(){
 try{const r=await api('/api/inference-status');
 if(!r.configured){$('#routing-status').innerHTML='<p class="meta">尚未连接核验副本。当前跳过真实模型验证与GPU性能评估。</p>';return}
 $('#routing-status').innerHTML=`<p>${r.policy==='least_work'?'按估算工作量分配':'轮询分配'} · 每副本并发上限 ${r.concurrency_per_replica} · 队列上限 ${r.queue_limit}</p><p class="meta">已提交 ${r.counters.submitted} · 已完成 ${r.counters.completed} · 合并重复请求 ${r.counters.coalesced} · 失败 ${r.counters.failed}</p><div class="table-wrap"><table><thead><tr><th>核验副本</th><th>执行中</th><th>排队中</th><th>估算工作量</th></tr></thead><tbody>${r.replicas.map(x=>`<tr><td>副本 ${x.id+1}</td><td>${x.active}</td><td>${x.queued}</td><td>${x.estimated_work}</td></tr>`).join('')}</tbody></table></div><p class="meta">工作量按输入字符与最大输出长度估算；不等于实际token数或GPU秒。</p>`;
 }catch(e){$('#routing-status').textContent=e.message}
}
$('#routing-refresh').onclick=loadRoutingStatus;
