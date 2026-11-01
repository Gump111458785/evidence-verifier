"""Bundle the shared panel UI and authored program-generated demo snapshots."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugin_scenarios import catalog


def build(output, web_copy=False):
    if output.exists():
        raise ValueError('Choose a new versioned output filename; old packages are retained')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [(p, p.name) for p in (ROOT/'extension').iterdir() if p.is_file()]
    files += [(ROOT/'static'/name, name) for name in ('plugin.html', 'plugin.css', 'plugin.js', 'sidecar.html', 'sidecar.js')]
    files += [(p, 'fonts/' + p.name) for p in (ROOT/'static/fonts').iterdir() if p.is_file()]
    snapshot = json.dumps(catalog(), ensure_ascii=False, indent=2)+'\n'
    hashes = {name: hashlib.sha256(p.read_bytes()).hexdigest() for p, name in files}
    hashes['plugin-demo.json'] = hashlib.sha256(snapshot.encode()).hexdigest()
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path, name in files:
            archive.write(path, name)
        archive.writestr('plugin-demo.json', snapshot)
        archive.writestr('bundle-manifest.json', json.dumps({'source':'ideal_plugin_demo', 'model_inference_performed':False,
                          'browser_extension_installed':False, 'files':hashes}, ensure_ascii=False, indent=2)+'\n')
    if web_copy:
        destination=ROOT/'static/downloads/evidence-verifier-extension.zip'
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output,destination)
    return {'package':str(output.resolve()), 'bytes':output.stat().st_size,
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(), 'model_inference_performed':False}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--web-copy',action='store_true')
    args=parser.parse_args()
    print(json.dumps(build(args.out,args.web_copy)))
