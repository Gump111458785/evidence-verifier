const $=id=>document.getElementById(id),isExtension=location.protocol==='chrome-extension:';
let snapshot=null,provenance=[],audit=null,job=null,epoch=0,busy=false,latest=null,stale=false;
const status=t=>$('status').textContent=t;
function error(e){$('error').hidden=false;const message=e.message||String(e);$('error').textContent=({'Planner model is not configured':'请先连接关系规划服务，再生成待核验关系。','Verifier model is not configured':'请先连接核验服务，再提交审查。'})[message]||message}
function invalidate(){epoch++;audit=null;latest=null;$('stages').replaceChildren();$('trace').textContent='';$('gap-search').replaceChildren();$('verdict').hidden=true;$('concise-summary').hidden=true;$('concise-summary').replaceChildren();$('supplement').value='';for(const id of ['prompt','repair','copy','insert'])$(id).disabled=true}
function lock(value){busy=value;for(const id of ['plan','verify','capture','watch','source','service','manual-source-add'])$(id).disabled=value;for(const id of ['question','candidate','evidence','relations'])$(id).disabled=value;$('cancel').disabled=!value||!job;if(!isExtension)for(const id of ['capture','watch','stop','source','insert'])$(id).disabled=true;}
async function save(){renderSummaries();if(isExtension)await chrome.storage.session.set({sidecarAudit:{job,snapshot,provenance,question:$('question').value,candidate:$('candidate').value,evidence:$('evidence').value,relations:$('relations').value,service:$('service').value,audit,latest}})}
async function api(path,body){const r=await fetch($('service').value+path,{method:body?'POST':'GET',headers:{...globalThis.evCloudAccess?.headers($('service').value+path),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(path.endsWith("/advance")?60000:15000)});const d=await r.json();if(!r.ok){const err=Error(d.error||`HTTP ${r.status}`);err.status=r.status;err.code=d.code;throw err}return d}
async function page(mode,prompt){if(!isExtension)throw Error('网页预览不读取其他标签页，请加载Chrome插件或手动粘贴。');const r=await chrome.runtime.sendMessage({type:'ev-page-command',mode,prompt});if(!r.ok)throw Error(r.error);return r.value}
function showSnapshot(value){stale=false;snapshot=value;invalidate();$('question').value=value.question||'';$('candidate').value=value.candidate||'';$('evidence').value='[]';$('relations').value='[]';provenance=[];$('capture-trace').textContent=JSON.stringify(value,null,2);const autoTag=value.auto_ready?'（回答流式输出已完成，自动捕获就绪）':'';status(`${value.platform} · 已读取公开回答${autoTag}；仅收集可见引用，未获得完整检索历史。`);save()}
function addStage(text){const n=document.createElement('li');n.textContent=text;$('stages').append(n)}
function signature(){return JSON.stringify(['question','candidate','evidence','relations','service'].map(id=>$(id).value))}
function renderConciseSummary(verdict){
 const box=$('concise-summary');if(!box)return;
 box.replaceChildren();box.hidden=false;
 const title=document.createElement('strong'),desc=document.createElement('p');
 if(verdict==='entailed'){
  box.className='concise-action-card supported';
  title.textContent='✓ 关键关系已获原文支持 · 无需打扰原回答';
  desc.textContent='所有已声明关系均匹配准确原文引文且实体绑定一致。若需深入探索，可点击下方建议追问填入对话草稿（演示）：';
  box.append(title);box.append(desc);
  for(const q of ['这条结论中的关键实体在不同时间段是否有其他合作或后续版本？','除当前已核实的原文外，是否还有独立第三方文献支持这一关系？']){
   const b=document.createElement('button');b.className='followup-chip';b.textContent='追问建议：'+q;
   b.onclick=()=>{
    $('supplement').value=q;$('copy').disabled=false;$('insert').disabled=!isExtension;
    status('已将推荐追问填入修订建议框，可复制或确认填入原对话草稿。');
   };
   box.append(b);
  }
 }else{
  box.className='concise-action-card missing';
  title.textContent='! 发现了什么 → 建议怎么改';
  desc.textContent=verdict==='missing'?'发现证据缺口：当前引用原文尚未支持完整的多跳关系链。建议点击「05 定向补搜并复核」或点击下方「06 生成对话修订建议」让原 AI 补足缺失依据。':'发现证据冲突或歧义：原文与候选结论存在不一致。建议立即生成修订提示词并确认填入原对话草稿。';
  box.append(title);box.append(desc);
 }
}
async function poll(){if(job.signature!==signature())throw Error('输入已改变；请恢复原输入或导出任务记录。');if(!job.id){if(job.kind!=='verification')throw Error('提交结果未知；为避免重复模型调用，不自动重提交。请查看工作台任务记录，并导出本次请求。');const recovered=await api(job.path,job.submission);job.id=recovered.id;await save()}if(job.signature!==signature())throw Error('输入已改变；上次任务ID保留，请恢复原输入或显式停止。');const own=epoch;lock(true);try{while(own===epoch){let d=await api((job.kind==='plan'?'/api/plan-proposals':job.path)+'/'+job.id);if(d.advance_path)d=await api(d.advance_path,{});if(own!==epoch)return;$('trace').textContent=JSON.stringify(d,null,2);if(['completed','failed','canceled'].includes(d.state)){latest=d;await save();if(d.state!=='completed'){job=null;await save();throw Error(d.error||d.state)}if(job.kind==='plan'){if(d.result?.status!=='ok'){job=null;await save();throw Error('关系草稿无效')}$('relations').value=JSON.stringify(d.result.proposal.relations,null,2);addStage('Role 01 · 关系草稿已生成；请审阅角色、候选绑定及覆盖范围后提交核验。')}else{if(job.kind==='repair'){if(d.result?.final?.status!=='ok'){job=null;await save();throw Error('补搜未得到有效判定')}addStage('Role 05 · 定向补搜结束：'+d.result.stop_reason)}else{if(d.result?.status!=='ok'){job=null;await save();throw Error('核验输出无效，不能算证据不足')}audit={id:d.id,question:$('question').value,candidate:$('candidate').value};addStage('Role 02–04 · 盲抽提取、支持核验与原文偏移绑定已完成。')}
 const verdict=job.kind==='repair'?d.result.final.verdict:d.result.verdict;$('verdict').hidden=false;$('verdict').textContent=verdictText(verdict);$('verdict').className='verdict-box'+(verdict==='entailed'?' supported':'');renderConciseSummary(verdict);$('repair').disabled=verdict!=='missing'||job.kind==='repair';$('prompt').disabled=stale}
 job=null;$('resume').hidden=true;await save();return}await new Promise(r=>setTimeout(r,1000))}}finally{lock(false)}}
async function begin(kind,path,body){if(job)throw Error('仍有已提交任务，请先恢复观察或停止它。');$('error').hidden=true;job={kind,path,id:null,submission:body,signature:signature()};await save();let d;try{d=await api(path,body)}catch(e){if([400,401,403,404,409,413,422].includes(e.status)){job=null;$('resume').hidden=true;await save()}throw e}job.id=d.id;await save();await poll()}
async function act(fn){if(busy)return;try{$('error').hidden=true;lock(true);await fn()}catch(e){error(e);if(job){$('resume').hidden=false;status('任务ID已保留；观察失败不代表任务结束，请恢复观察或显式停止。')}}finally{lock(false)}}
$('capture').onclick=()=>act(async()=>showSnapshot(await page('capture')));
$('watch').onclick=()=>act(async()=>showSnapshot(await page('watch')));
$('stop').onclick=()=>act(async()=>{await page('stop');status('已停止当前会话跟随。')});
$('source').onclick=()=>act(async()=>{const s=await page('source'),ev=JSON.parse($('evidence').value);if(ev.length>=40||ev.reduce((n,e)=>n+e.text.length,0)+s.text.length>22000)throw Error('证据超过数量或字符限制，未截断');if(ev.some(e=>e.text===s.text))throw Error('原文已收集');invalidate();let i=1;while(ev.some(e=>e.id==='E'+i))i++;ev.push({id:'E'+i,text:s.text});provenance.push({id:'E'+i,url:s.url,title:s.title,source:'user_opened_visible_source'});$('evidence').value=JSON.stringify(ev,null,2);await save();status('已收集来源原文；请重新核验。')});
$('plan').onclick=()=>act(async()=>{if(!$('question').value.trim()||$('question').value.length>2000||!$('candidate').value.trim()||$('candidate').value.length>1000)throw Error('请填写最多2,000字符的问题和最多1,000字符的回答片段，长内容不会自动截断');invalidate();await begin('plan','/api/sidecar/plans',{question:$('question').value,candidate:$('candidate').value})});
$('verify').onclick=()=>act(async()=>{invalidate();await begin('verification','/api/verifications',{evidence:JSON.parse($('evidence').value),relations:JSON.parse($('relations').value),request_key:crypto.randomUUID(),client_tag:'conversation-sidecar-0.3.3'})});
$('repair').onclick=()=>act(async()=>{if(!audit)throw Error('先完成有效核验');$('supplement').value='';await begin('repair','/api/repair-jobs',{parent_check_id:audit.id,budget:6,rounds:1})});
$('resume').onclick=()=>act(poll);
$('cancel').onclick=async()=>{if(!job)return;try{if(!job.id){epoch++;status('已放弃观察未知提交；未确认服务端取消，请保留导出记录。');await save();job=null;await save();return}if(job.kind==='plan'){epoch++;job=null;status('已停止观察规划任务；服务器规划请求可能继续完成。')}else{latest=await api(job.path+'/'+job.id+'/cancel',{});epoch++;job=null;status('已请求停止；当前模型HTTP调用可能需要先返回。')}await save()}catch(e){error(e)}};
$('prompt').onclick=()=>act(async()=>{if(stale)throw Error('页面已变化，请重新读取和核验。');if(!audit)throw Error('先完成有效核验');const repair=latest?.parent_check_id===audit.id?latest.id:undefined;const report=await api('/api/sidecar/report',{verification_id:audit.id,repair_id:repair,question:audit.question,candidate:audit.candidate});$('supplement').value=report.supplemental_prompt+'\n\n用户收集的出处元数据（非语义金标）：\n'+JSON.stringify(provenance);$('trace').textContent=JSON.stringify(report.trace,null,2);$('gap-search').replaceChildren();for(const gap of report.trace.gaps){if(!gap.query)continue;const a=document.createElement('a');a.textContent='用网页搜索补足：'+gap.query;a.href='https://www.google.com/search?q='+encodeURIComponent(gap.query);a.target='_blank';a.rel='noopener noreferrer';const p=document.createElement('p');p.append(a);$('gap-search').append(p)}$('copy').disabled=false;$('insert').disabled=!isExtension;addStage('Role 06 · 补充prompt已生成；只提供核验记录、证据和补搜要求，不要求隐藏思维链。')});
$('copy').onclick=()=>act(async()=>{await navigator.clipboard.writeText($('supplement').value);status('已复制，尚未向原AI发送。')});
$('insert').onclick=()=>act(async()=>{if(stale||!$('supplement').value)throw Error('prompt已过期，请重新核验');await page('insert',$('supplement').value);status('已填入原对话草稿，未点击发送；请检查后自行发送。')});
$('export').onclick=()=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({snapshot,provenance,audit,job,latest,evidence:$('evidence').value,relations:$('relations').value,prompt:$('supplement').value},null,2)],{type:'application/json'}));a.href=url;a.download='evidence-audit.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
$('preset-gap').onclick=()=>{
 invalidate();
 $('question').value='Which university did the designer of the Meridian Engine attend?';
 $('candidate').value='The designer of the Meridian Engine attended Lake University.';
 $('evidence').value=JSON.stringify([{id:'E1',text:'The Meridian Engine was designed by Nora Hale.'},{id:'E2',text:'Nora Hale worked at Pine Institute as an engineer.'}],null,2);
 $('relations').value=JSON.stringify([{subject:'Meridian Engine',predicate:'designed_by',object:'?designer'},{subject:'?designer',predicate:'educated_at',object:'Lake University'}],null,2);
 provenance=[{id:'E1',title:'作品与设计者',source:'preset_example'},{id:'E2',title:'任职记录（缺失受教育关系）',source:'preset_example'}];
 save();status('已载入「缺证案例（任职≠就读）」，可直接点击「02–04 开始独立核验」。');
};
$('preset-supported').onclick=()=>{
 invalidate();
 $('question').value='Who designed the Analytical Engine?';
 $('candidate').value='The Analytical Engine was designed by Charles Babbage.';
 $('evidence').value=JSON.stringify([{id:'E1',text:'The Analytical Engine was a proposed mechanical general-purpose computer designed by English mathematician Charles Babbage.'}],null,2);
 $('relations').value=JSON.stringify([{subject:'Analytical Engine',predicate:'designed_by',object:'Charles Babbage'}],null,2);
 provenance=[{id:'E1',title:'Analytical Engine 档案',source:'preset_example'}];
 save();status('已载入「通过案例（证据充分）」，可直接点击「02–04 开始独立核验」。');
};
for(const id of ['question','candidate','evidence','relations','service'])$(id).addEventListener('input',()=>{invalidate();save()});
$('setup').textContent=isExtension?`当前扩展ID：${chrome.runtime.id}。在API配置中填写相同extension_id，启动本机服务后即可提交审查；默认端口8790。`:'当前为手动输入模式。Chrome插件提供网页读取与草稿填入；真实核验需先配置规划与核验服务。';
if(isExtension){chrome.storage.onChanged.addListener((c,area)=>{if(area==='session'&&c.liveSnapshot){if(busy||job){stale=true;$('prompt').disabled=true;$('insert').disabled=true;status('页面已更新；当前任务保留原快照，请完成后重新读取。');return}showSnapshot(c.liveSnapshot.newValue)}});chrome.storage.session.get(['sidecarAudit','liveSnapshot']).then(s=>{const old=s.sidecarAudit;if(old){snapshot=old.snapshot;stale=!!(s.liveSnapshot&&JSON.stringify([snapshot?.question,snapshot?.candidate])!==JSON.stringify([s.liveSnapshot.question,s.liveSnapshot.candidate]));provenance=old.provenance||[];for(const id of ['question','candidate','evidence','relations','service'])if(old[id])$(id).value=old[id];job=old.job;audit=old.audit;latest=old.latest;$('resume').hidden=!job;$('prompt').disabled=!audit||stale;$('capture-trace').textContent=JSON.stringify(snapshot,null,2);if(job)status('上次任务身份已保留；点击恢复观察，不会重新提交。')}else if(s.liveSnapshot)showSnapshot(s.liveSnapshot);renderSummaries();restoreVerdict()})}

if(!isExtension){const option=document.createElement('option');option.textContent=location.origin;$('service').append(option);$('service').value=location.origin}

function verdictText(value){return ({entailed:'所列关系已获得证据支持',missing:'证据尚未支持完整关系链',contradicted:'原文与所列关系存在矛盾',ambiguous:'现有证据存在歧义，需要进一步确认'})[value]||'尚未产生有效判定'}
function restoreVerdict(){const r=latest?.result;if(!audit||stale||latest?.state!=='completed')return;const value=latest.parent_check_id===audit.id&&r?.final?.status==='ok'?r.final.verdict:latest.id===audit.id&&r?.status==='ok'?r.verdict:null;if(value){$('verdict').hidden=false;$('verdict').textContent=verdictText(value);$('verdict').className='verdict-box'+(value==='entailed'?' supported':'');renderConciseSummary(value)}}
function renderSummaries(){
 const counter=$('input-count');if(counter)counter.textContent='问题 '+$('question').value.length+' / 2,000 字符 · 回答 '+$('candidate').value.length+' / 1,000 字符';
 const descriptions={employed_at:'任职于',educated_at:'受教育于',in_region:'所在区域',in_province:'所在省份',has_author:'作者',designed_by:'设计者',houses:'收藏或容纳',has_child:'子女',opened_in_year:'开放年份',lasted_between:'持续区间',has_no_teams:'无队伍',founded_in_year:'成立年份'};
 for(const [input,host,empty] of [['evidence','evidence-summary','尚未添加原文。可以采集来源页，或手动添加相关段落。'],['relations','relation-summary','尚未确认待核验关系。']]){
  const box=$(host);if(!box)return;box.replaceChildren();let rows;try{rows=JSON.parse($(input).value||'[]');if(!Array.isArray(rows))throw Error()}catch{box.textContent='数据格式待检查，请展开编辑区修正。';continue}
  if(!rows.length){const p=document.createElement('p');p.className='meta';p.textContent=empty;box.append(p);continue}
  rows.forEach((r,index)=>{const article=document.createElement('article');article.className='summary-row';const title=document.createElement('strong'),detail=document.createElement('p');if(input==='evidence'){const origin=provenance.find(x=>x.id===r.id);title.textContent=r.id+(origin?.title?' · '+origin.title:'');detail.textContent=typeof r.text==='string'?(r.text.length>220?r.text.slice(0,220)+'…':r.text):'原文格式待检查';article.append(title);article.append(detail);if(origin?.url){const source=document.createElement('small');source.textContent=origin.url;article.append(source)}}else{title.textContent='关系 '+(index+1);detail.textContent=[r.subject,descriptions[r.predicate]||r.predicate,r.object].map(x=>String(x??'')).join(' → ');article.append(title);article.append(detail)}box.append(article)});
 }
}
$('manual-source-add').onclick=()=>act(async()=>{const text=$('manual-source-text').value.trim(),urlText=$('manual-source-url').value.trim();if(!text)throw Error('请填写来源原文。');let url='';if(urlText){const parsed=new URL(urlText);if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password)throw Error('来源网址必须是没有账号密码的HTTP或HTTPS地址。');url=parsed.href}const ev=JSON.parse($('evidence').value);if(!Array.isArray(ev))throw Error('请先修正证据数据格式。');if(ev.length>=40||ev.reduce((n,e)=>n+String(e.text||'').length,0)+text.length>22000)throw Error('证据超过40段或22,000字符，未添加或截断。');if(ev.some(e=>e.text===text))throw Error('这段原文已经添加。');let i=1;while(ev.some(e=>e.id==='E'+i))i++;const id='E'+i;invalidate();ev.push({id,text});provenance.push({id,url,source:'user_pasted_source'});$('evidence').value=JSON.stringify(ev,null,2);$('manual-source-text').value='';$('manual-source-url').value='';await save();status('原文已添加，确认关系后可开始核验。')});
$('mode-label').textContent=isExtension?'网页会话':'手动输入';
if(!isExtension){for(const id of ['capture','watch','stop','source','insert'])$(id).disabled=true;status('手动输入模式：粘贴问题和回答，或点击上方「载入缺证案例 / 载入通过案例」体验核验。')}
renderSummaries();
if(typeof URLSearchParams!=='undefined'&&new URLSearchParams(location.search||'').get('embedded')==='1'){
 document.body.classList.add('embedded');
 if(typeof ResizeObserver!=='undefined'){const main=document.querySelector('main');new ResizeObserver(()=>{if(window.parent!==window)window.parent.postMessage({type:'ev-audit-size',height:Math.ceil(main.getBoundingClientRect().bottom)},location.origin)}).observe(main)}
}

if(!isExtension&&globalThis.evCloudAccess){api("/api/health").then(h=>{if(h.cloud_mode)globalThis.evCloudAccess?.show()}).catch(()=>{})}
