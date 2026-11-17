"""Bundle the Trisight side panel extension (no demo pages)."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build(output, web_copy=False):
    if output.exists():
        raise ValueError('Choose a new versioned output filename; old packages are retained')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [(p, p.name) for p in (ROOT/'extension').iterdir() if p.is_file()]
    files += [(ROOT/'static'/name, name) for name in ('sidecar.html', 'sidecar.css', 'sidecar.js', 'favicon.svg')]
    hashes = {name: hashlib.sha256(p.read_bytes()).hexdigest() for p, name in files}
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path, name in files:
            archive.write(path, name)
        archive.writestr('bundle-manifest.json', json.dumps({'source':'trisight_side_panel', 'model_inference_performed':False,
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
