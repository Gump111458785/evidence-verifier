"""Re-score frozen historical predictions against the original constructed oracle. No model calls."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/vendor'))
from evpilot.report_relation_v7 import evaluate


def recompute(archive, expected):
    ids = [r['id'] for r in expected]
    if len(ids)!=92 or len(set(ids))!=92 or len(archive['inputs'])!=92:
        raise ValueError('Expected 92 unique frozen diagnostic records')
    if {r['id'] for r in archive['inputs']}!=set(ids):
        raise ValueError('Input/oracle IDs do not match')
    results={}
    for model,conditions in archive['models'].items():
        results[model]={}
        for mode in ('direct','split'):
            raw=conditions[mode]
            if set(raw)!=set(ids) or any(k!=v['id'] for k,v in raw.items()):
                raise ValueError('Prediction IDs must match the frozen inputs')
            results[model][mode]={}
            for suite,size in (('regression80',80),('probes12',12)):
                gold={r['id']:r for r in expected if r['suite']==('new_probes12' if suite=='probes12' else suite)}
                if len(gold)!=size:raise ValueError('Unexpected suite size')
                rows=[r for r in raw.values() if r['id'] in gold]
                scored=evaluate(rows,gold,mode)
                if suite=='probes12':
                    scored['checks']={'all_correct':scored['counts']['joint']==12,'valid':scored['counts']['invalid']==0}
                    scored['passed']=all(scored['checks'].values())
                cached=conditions['evaluation'][mode][suite]
                if scored!=cached:
                    raise ValueError(f'Recomputed rows disagree with retained evaluation: {model}/{mode}/{suite}')
                results[model][mode][suite]=scored
    return results


def reproduce():
    path=ROOT/'app/data/archive.json';oracle=ROOT/'app/data/diagnostics/expected.jsonl'
    manifest=json.loads((oracle.parent/'manifest.json').read_text())
    for file,key in ((path,'archive_sha256'),(oracle,'expected_sha256')):
        if hashlib.sha256(file.read_bytes()).hexdigest()!=manifest[key]:
            raise ValueError('Frozen diagnostic hash mismatch: '+file.name)
    results=recompute(json.loads(path.read_text()),[json.loads(line) for line in oracle.read_text().splitlines() if line.strip()])
    return {'scope':manifest['scope'],'new_inference_performed':False,'inputs':92,'historical_predictions':552,
            'source_archive_sha256':manifest['source_archive_sha256'],'results':results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',action='store_true',help='Print full recomputed per-example results')
    parser.add_argument('--out',type=Path,help='Write JSON to a new file; never overwrite')
    args=parser.parse_args();report=reproduce()
    if args.out:
        with args.out.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    if args.json:print(json.dumps(report,ensure_ascii=False,indent=2));return
    print('Historical development diagnostics: 80 main + 12 probes; no new model inference.\n')
    print('| Model | Flow | Joint /80 | Positive /40 | Negative /40 | Binding /16 | Invalid | Gate |')
    print('|---|---|---:|---:|---:|---:|---:|---|')
    for model,conditions in report['results'].items():
        for mode,suites in conditions.items():
            c=suites['regression80']['counts']
            print(f"| {model} | {mode} | {c['joint']} | {c['positive']} | {c['negative']} | {c['variables']} | {c['invalid']} | {'PASS' if suites['regression80']['passed'] else 'FAIL'} |")
    print('\nInvalid output covers schema/source/materialization failures; it is not synonymous with contradiction.')

if __name__=='__main__':main()
