"""Audit original v7 outputs and package a diagnostic delivery without rescoring."""
import collections
import datetime
import json
import zipfile
from pathlib import Path
from .common import file_hash, read_jsonl


def main():
    data = Path('data/relation-kernel-v7')
    frozen = json.loads((data/'freeze.json').read_text())
    assert all(file_hash(p)==h for p,h in frozen['files'].items())
    # Verify preservation of the entire prior frozen experiment as well.
    old_freeze = json.loads(Path('data/relation-kernel-v6/freeze.json').read_text())
    assert all(file_hash(p)==h for p,h in old_freeze['files'].items())
    inputs = {r['id']:r for r in read_jsonl(data/'inputs.jsonl')}
    expected = {r['id']:r for r in read_jsonl(data/'expected.jsonl')}
    old_input = {r['id']:r for r in read_jsonl('data/relation-kernel-v6/inputs.jsonl')}
    old_expected = {r['id']:r for r in read_jsonl('data/relation-kernel-v6/expected.jsonl')}
    assert all(inputs[k]==v for k,v in old_input.items())
    assert all(all(expected[k][field]==value for field,value in v.items()) for k,v in old_expected.items())
    score = json.loads(Path('artifacts/relation-kernel-v7-evaluation.json').read_text())
    previous = json.loads(Path('artifacts/relation-kernel-v6-evaluation.json').read_text())
    assert len(score['results'])==3
    table, probe_table, audit, reviews = [], [], {}, []
    for name, result in sorted(score['results'].items()):
        d = Path('runs')/name
        if 'failure' in result:
            audit[name] = result
            continue
        assert json.loads((d/'process.json').read_text())['returncode']==0
        calls = list(read_jsonl(d/'calls.jsonl'))
        assert len({c['call_id'] for c in calls})==len(calls)
        for c in calls:
            if c['stage']=='extract':assert set(c['payload'])=={'predicate_definitions','evidence'}
            if c['stage']=='verify':assert set(c['payload'])=={'predicate_definition','fact','computed_object','evidence'}
        replayed=0
        errors={}
        tag=name.removeprefix('relation-kernel-v7-')
        for mode in ['split','direct']:
            rows=list(read_jsonl(d/(mode+'.predictions.jsonl')))
            assert len(rows)==92 and {r['id'] for r in rows}==set(inputs)
            for r in rows:
                ev={e['id']:e['text'] for e in inputs[r['id']]['evidence']}
                fs=[x['fact'] for x in r.get('facts',[])] if mode=='split' else r.get('prediction',{}).get('matched_facts',[])
                for f in fs:
                    for span in f['subject_spans']+f['object_spans']+f['quotes']:
                        assert ev[span['evidence_id']][span['start']:span['end']]==span['text']
                        replayed+=1
            errors[mode]=dict(collections.Counter(e.get('error','')[:160] for r in rows for e in (r.get('errors',[]) if mode=='split' else [r] if r['status']!='ok' else [])))
            base=result[mode]['regression80']['counts'];probe=result[mode]['probes12']['counts']
            old=previous['results'][name.replace('v7','v6')][mode]['counts']
            table.append(f"| {tag} | {mode} | {old['joint']} → {base['joint']}/80 | {base['positive']}/40 | {base['negative']}/40 | {base['variables']}/16 | {old['invalid']} → {base['invalid']} | {base['critical_false_accept']} | {probe['joint']}/12 |")
            for detail in result[mode]['regression80']['details']+result[mode]['probes12']['details']:
                if not detail['joint_correct']:
                    row=next(r for r in rows if r['id']==detail['id'])
                    reviews.append({'model':tag,'mode':mode,'score':detail,'input':inputs[row['id']],'output':row})
            if mode=='split':
                for detail in result[mode]['probes12']['details']:
                    probe_table.append(f"| {tag} | {detail['id']} | {detail['gold']} | {detail['prediction']} | {'是' if detail['joint_correct'] else '否'} |")
        audit[name]={'calls':dict(collections.Counter(c['stage'] for c in calls)), 'span_replays':replayed,
                     'tokens':{stage:sum(c['completion_tokens'] for c in calls if c['stage']==stage) for stage in ['extract','verify','direct']},
                     'errors_row_weighted_overlapping':errors,'timing':json.loads((d/'completed.json').read_text())}
    Path('artifacts/relation-kernel-v7-audit.json').write_text(json.dumps({'v6_frozen_unchanged':True,'v7_frozen_unchanged':True,
        'original80_inputs_and_gold_unchanged':True,'runs':audit},indent=2))
    Path('artifacts/relation-kernel-v7-errors.json').write_text(json.dumps(reviews,indent=2))
    eligible=score['eligible_split_runs']
    conclusion='所有拆分候选均未通过全部门槛；本轮不进入70题回归、新标注或训练。' if not eligible else '存在通过诊断的拆分候选；尚需完成70题开发回归，不能据此进入训练。'
    report='''# v7：简化接口与实体匹配后的诊断结果

'''+conclusion+'''

75项程序测试通过；原v6冻结文件以及原80题的输入和期望标签未改动。新增12题为构造开发诊断，并非独立人工验证。

## 六组结果

联合正确要求四分类、绑定、必要引用和事实角色同时通过。箭头前为v6，后为v7；无效输出不计为正确拒答。

| 模型 | 流程 | 联合正确 v6→v7 | 正例 | 负例 | 变量题 | 无效输出 v6→v7 | 关键误放行 | 新增诊断 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(table)+'''

门槛未降低：80题联合≥72、正例≥36、负例≥36、变量16/16、无效0、关键误放行0；额外要求新增12题全部通过。

## 本轮修改

- 客体采用互斥分支：原文端点、年份加减、起止区间或明确不存在。普通事实不再携带无用计算字段；计算分支没有模型生成的结果字段。
- 每条事实使用一个引用列表。程序定位主体、客体及操作数的原文位置，计算结果来自白名单运算。
- 只对限定的组织/地理/事件通用名称移除开头冠词；保留原始名称和位置。The Hague、The Gift等名称不受此规则影响。
- 明确作品→作者、地点→组织、父母→直接子女的方向；明确职业性 worked at 算任职。抽取仍看不到目标，核验仍独立逐条进行。
- 新增当前任职、教育正例、非唯一子女、否定、来源冲突、年份减法以及名称保护诊断。

## 已核对的14B剩余失败

- region-positive.paraphrased：正确抽出 Tavi → in_region → Eastern Region，但核验器错误地把“地区包含城镇”和“城镇位于地区”判为相反关系。
- houses-positive.clarified：抽取结果为空，后面的独立核验无法补回漏抽事实。四个 houses 正例均漏判。
- author-positive.paraphrased：正确作者事实已通过核验，但同时生成了错误的 in_region/absence 事实。当前保守策略是一条抽取记录无效即使整题无效，因而损失了这个可回答正例；本轮未事后放宽。
- date-positive.clarified：年份运算和来源均已正确提交，核验器却把开业原因的1991年重新混入推理，拒绝1994年的结论。
- probe-explicit-negative 与 probe-conflicting-education：把“不曾就读”的负极性误编码为 has_no_teams 专用的 absence 客体，而非保留学校名并标注负极性，触发接口错误。
- probe-conflicting-dates：没有保留两条独立来源的年份事实，反而生成原文不存在的“一年偏移”计算，引用检查予以拒绝。

上述问题分别属于抽取完整性、核验语义、无效事实传播和否定/冲突表示，不能通过继续放宽字符串匹配解决。后续应单独设计这些机制的诊断；本轮不继续改写已冻结实验。

## 新增12题：拆分流程逐题结果

| 模型 | 编号 | 期望 | 输出 | 联合正确 |
|---|---|---|---|---|
'''+ '\n'.join(probe_table)+'''

## 审计与限制

原始请求、响应和逐题评分完整保存；所有已物化原文位置均已回放，v6与v7冻结哈希均通过。
自动错误分类可能重叠，事实清单也并非穷尽全部合理解释，不能把自动计数直接当成人工确认的语义错误总数。完整失败项在 relation-kernel-v7-errors.json。

本轮同时改变接口和提示词，不能将改变量严格归因于某一个修改。80题仅有20个已知开发题族，12道新题也由开发者构造；不存在独立验证或训练效果结论。直接与拆分流程调用预算不同，运行时间亦不能当作等成本模型排名。

资源策略保持离线、顺序加载；14B双卡、7B/8B单卡，均单并发。不下载权重、不安装依赖、不修改内存/swap、不删除文件。
'''
    Path('artifacts/relation-kernel-v7-report.md').write_text(report)
    before=Path('artifacts/STATUS.before-relation-kernel-v7.json')
    if not before.exists():before.write_bytes(Path('STATUS.json').read_bytes())
    status=json.loads(Path('STATUS.json').read_text())
    status.update(updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  relation_kernel_v7=conclusion,relation_kernel_v7_report='artifacts/relation-kernel-v7-report.md',
                  full_regression_v7='pending: split candidate passed' if eligible else 'not started: split gates failed',
                  next_required_input='No human labels requested; see v7 diagnostic results.')
    Path('STATUS.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n')
    files={Path(p) for p in frozen['files']}
    files.update(data.glob('*'))
    files.update(Path('artifacts').glob('*relation-kernel-v7*'))
    files.update([Path('evpilot/audit_relation_v7.py'),Path('evpilot/judge_v3.py'),Path('STATUS.json'),Path('artifacts/relation-kernel-v6-evaluation.json')])
    for p in Path('runs').glob('relation-kernel-v7-*'):
        files.update(p.rglob('*') if p.is_dir() else [p])
    manifest=Path('artifacts/relation-kernel-v7-delivery-manifest.json')
    files=sorted(p for p in files if p.is_file() and p.suffix!='.zip' and p!=manifest)
    manifest.write_text(json.dumps({str(p):file_hash(p) for p in files},indent=2))
    archive=Path('artifacts/relation-kernel-v7-delivery.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in files+[manifest]:z.write(p,str(p))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    print(json.dumps({'eligible':eligible,'archive_bytes':archive.stat().st_size,'sha256':file_hash(archive),'span_replays':sum(x.get('span_replays',0) for x in audit.values())}))


if __name__=='__main__':main()
