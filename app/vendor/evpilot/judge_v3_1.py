"""v3.1: choose exact source spans; derive verdict from per-relation judgments."""
import argparse,json,copy,os,re
from pathlib import Path
from .common import read_jsonl,write_jsonl,file_hash
from .judge_v3 import SUPPORT_SYSTEM,SUFF_SYSTEM,validate
SCHEMA={'type':'object','properties':{'claims':{'type':'array','minItems':1,'items':{'type':'object','properties':{'claim':{'type':'string'},'label':{'type':'string','enum':['supported','unsupported','indeterminate']},'quote_ids':{'type':'array','items':{'type':'string'}},'rationale':{'type':'string'}},'required':['claim','label','quote_ids','rationale'],'additionalProperties':False}},'rationale':{'type':'string'}},'required':['claims','rationale'],'additionalProperties':False}

def segments(evidence):
    catalogue={};items=[]
    for e in evidence:
        spans=[]
        # Every span remains a literal substring, including unbroken final clauses.
        for m in re.finditer(r'[^\n]+',e['text']):
            for s in re.finditer(r'.+?(?:[.!?](?=\s|$)|$)',m.group()):
                text=s.group().strip()
                if not text:continue
                start=m.start()+s.start()+len(s.group())-len(s.group().lstrip())
                q='Q'+str(len(catalogue)+1)
                assert e['text'][start:start+len(text)]==text
                catalogue[q]={'evidence_id':e['id'],'text':text,'start':start,'end':start+len(text)}
                spans.append({'quote_id':q,'text':text})
        items.append({'id':e['id'],'segments':spans})
    return items,catalogue

def materialize(raw,task,evidence,catalogue):
    claims=[]
    for c in raw['claims']:
        if any(q not in catalogue for q in c['quote_ids']):raise ValueError('unknown source span')
        quotes=[{'evidence_id':catalogue[q]['evidence_id'],'text':catalogue[q]['text']} for q in dict.fromkeys(c['quote_ids'])]
        claims.append({'claim':c['claim'],'label':c['label'],'support':list(dict.fromkeys(q['evidence_id'] for q in quotes)),'quotes':quotes,'rationale':c['rationale']})
    labels=[c['label'] for c in claims]
    verdict='unsupported' if 'unsupported' in labels else ('indeterminate' if 'indeterminate' in labels else 'supported')
    field='answer_label' if task=='support' else 'sufficiency'
    if task=='sufficiency':verdict={'supported':'sufficient','unsupported':'insufficient','indeterminate':'indeterminate'}[verdict]
    value={field:verdict,'claims':claims,'rationale':raw['rationale']}
    return validate(value,task,evidence)

def judge(traces_path,out,model):
    from vllm import LLM,SamplingParams
    from vllm.sampling_params import StructuredOutputsParams
    out=Path(out)
    if out.exists():raise RuntimeError('Refuse overwrite')
    out.mkdir(parents=True);rows=list(read_jsonl(traces_path))
    instruction=' Output the supplied schema only: each claim has claim,label,quote_ids,rationale. Select quote_ids from the supplied exact source segments; do NOT copy quote text or invent quote IDs. The program materializes quotes and computes the verdict: any unsupported necessary relation makes the verdict unsupported/insufficient. Missing relations require an explicit unsupported claim with no quotes. Supported claims need relevant quote_ids. Quotes must entail the requested relation, not a neighboring relation. Check every necessary relation, including the question chain and exact candidate answer type. Each rationale explains only the corresponding claim.'
    # Replace copy-output instructions; preserve semantic v3 rules.
    support=SUPPORT_SYSTEM+instruction;suff=SUFF_SYSTEM+instruction
    config={'source_sha256':file_hash(__file__),'semantic_rules_sha256':file_hash('evpilot/judge_v3.py'),'traces_sha256':file_hash(traces_path),'model':str(model),'model_config_sha256':file_hash(Path(model)/'config.json'),'max_model_len':8192,'max_prompt_tokens':6400,'max_new_tokens':1700,'seed':42,'temperature':0,'support_system':support,'sufficiency_system':suff,'schema':SCHEMA,'gpu':os.environ.get('CUDA_VISIBLE_DEVICES'),'aggregation':'deterministic over model relation labels; not a repair of raw v3 outputs','quote_policy':'model selects spans; program copies literal source text; entailment remains model judgment'}
    (out/'config.json').write_text(json.dumps(config,indent=2))
    llm=LLM(model=model,dtype='bfloat16',max_model_len=8192,max_num_seqs=2,max_num_batched_tokens=4096,gpu_memory_utilization=.78,enforce_eager=True,seed=42,enable_prefix_caching=False);tokenizer=llm.get_tokenizer();outputs={r['id']:{'id':r['id']} for r in rows}
    for task,system in [('support',support),('sufficiency',suff)]:
        active=[];prompts=[];params=[]
        for r in rows:
            if task=='support' and r['status']!='answered':outputs[r['id']][task]={'status':'not_applicable'};continue
            evidence,catalogue=segments(r['evidence']);payload={'question':r['question'],'evidence':evidence}
            if task=='support':payload['answer']=r['answer']
            prompt=tokenizer.apply_chat_template([{'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],tokenize=False,add_generation_prompt=True)
            if len(tokenizer.encode(prompt,add_special_tokens=False))>6400:outputs[r['id']][task]={'status':'context_limit'};continue
            schema=copy.deepcopy(SCHEMA);citations=schema['properties']['claims']['items']['properties']['quote_ids']
            if catalogue:citations['items']['enum']=list(catalogue)
            else:citations['maxItems']=0
            params.append(SamplingParams(temperature=0,seed=42,max_tokens=1700,structured_outputs=StructuredOutputsParams(json=schema)));prompts.append(prompt);active.append((r,catalogue))
        results=llm.generate(prompts,params,use_tqdm=False) if prompts else []
        for (r,catalogue),result in zip(active,results):
            raw=result.outputs[0].text
            try:
                value=materialize(json.loads(raw),task,r['evidence'],catalogue)
                outputs[r['id']][task]={'status':'ok','prediction':value,'raw':raw,'prompt_tokens':len(result.prompt_token_ids),'completion_tokens':len(result.outputs[0].token_ids)}
            except (ValueError,KeyError,TypeError) as exc:outputs[r['id']][task]={'status':'invalid_output','raw':raw,'error':str(exc)}
        print(json.dumps({'task':task,'processed':len(active)}),flush=True)
    write_jsonl(out/'predictions.jsonl',outputs.values())
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--traces',required=True);p.add_argument('--out',required=True);p.add_argument('--model',default='/path/to/models/Qwen2.5-7B-Instruct');a=p.parse_args();judge(a.traces,a.out,a.model)
