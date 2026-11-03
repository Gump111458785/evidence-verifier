"""White-list EdgeOne build. No keys, local DBs, weights, or user materials."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
APP_MODULES=('engine.py','grounding.py','inference.py','relations.py','planning.py',
             'repair.py','retrieval.py','plugin_scenarios.py','scenarios.py','sidecar.py',
             'agent.py','curation.py')
KERNEL_MODULES=('__init__.py','relation_kernel_v7.py','relation_kernel_v6.py','common.py',
                'judge_v3_1.py','judge_v3.py','judge.py','relation_kernel_v5.py')


def build(destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Choose a new build directory; previous builds are retained')
    destination.mkdir(parents=True)
    files=[]
    for source in (ROOT/'app/static').rglob('*'):
        if source.is_symlink():raise ValueError('Symlink in frontend source')
        if source.is_file() and source.suffix in ('.html','.css','.js','.mjs','.json','.svg','.png','.jpg','.ttf','.woff','.woff2','.zip','.txt'):
            target=destination/source.relative_to(ROOT/'app/static');target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    # Convert extension and references before fingerprinting. Source is unchanged.
    for p in list(destination.rglob('*.mjs')):
        p.rename(p.with_suffix('.js'))
    for p in destination.rglob('*'):
        if p.suffix in ('.js','.html','.css','.json'):
            p.write_text(p.read_text().replace('.mjs','.js'))
    # Hash the exact served JS/CSS bytes; HTML entry always revalidates.
    mapping={}
    for p in sorted(destination.glob('*')):
        if p.suffix in ('.js','.css'):
            name=p.stem+'.'+hashlib.sha256(p.read_bytes()).hexdigest()[:12]+p.suffix
            mapping[p.name]=name;p.rename(destination/name)
    for p in destination.glob('*.html'):
        content=p.read_text()
        for old,new in mapping.items():content=content.replace('/'+old,'/'+new).replace('"'+old+'"','"'+new+'"')
        p.write_text(content)
    fn=destination/'cloud-functions/api';fn.mkdir(parents=True)
    shutil.copy2(ROOT/'deploy/edgeone/cloud-functions/api/[[route]].py',fn/'[[route]].py')
    shutil.copy2(ROOT/'deploy/edgeone/requirements.txt',destination/'cloud-functions/requirements.txt')
    lib=fn/'evlib';lib.mkdir()
    for name in APP_MODULES:shutil.copy2(ROOT/'app'/name,lib/name)
    vend=lib/'vendor/evpilot';vend.mkdir(parents=True)
    for name in KERNEL_MODULES:shutil.copy2(ROOT/'app/vendor/evpilot'/name,vend/name)
    (lib/'data').mkdir();shutil.copy2(ROOT/'app/data/archive.json',lib/'data/archive.json')
    runtime=lib/'cloud_runtime';runtime.mkdir()
    for p in (ROOT/'cloud_runtime').glob('*.py'):shutil.copy2(p,runtime/p.name)
    # Generate only public, source-controlled examples. No server or database imported.
    sys.path.insert(0,str(ROOT/'app'))
    from engine import CASES,ARCHIVE
    from plugin_scenarios import catalog as plugins
    from scenarios import catalog as scenarios
    public={'/api/cases':list(CASES.values()),'/api/plugin-demo':plugins(),'/api/scenarios':scenarios(),
            '/api/evaluations':{k:v['evaluation'] for k,v in ARCHIVE['models'].items()},
            '/api/data-template':[json.loads(s) for s in (ROOT/'app/configs/curation-fixture.jsonl').read_text().splitlines() if s.strip()]}
    (runtime/'public-data.json').write_text(json.dumps(public,ensure_ascii=False))
    shutil.copy2(ROOT/'deploy/edgeone/edgeone.json',destination/'edgeone.json')
    assert (destination/'index.html').is_file()
    forbidden={'.env','.sqlite','.db','.safetensors','.pt','.pth','.pem','.key'}
    for p in destination.rglob('*'):
        if not p.is_file():continue
        if p.name.startswith('.env') or p.suffix in forbidden or p.name=='config.local.json':
            raise ValueError('Forbidden deployment file')
        files.append({'path':str(p.relative_to(destination)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
    archive=destination.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for f in files:z.write(destination/f['path'],f['path'])
    with zipfile.ZipFile(archive) as z:assert 'index.html' in z.namelist()
    manifest={'files':files,'root_index':True,'output_directory':str(destination),'asset_fingerprints':mapping,
              'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'paid_model_calls':0}
    destination.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2))
    return {'archive':str(archive),'files':len(files),'bytes':archive.stat().st_size,'root_index':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args()
    print(json.dumps(build(args.out)))
