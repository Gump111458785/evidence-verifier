"""Build an offline engineering delivery; never install, train or call a model."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/scripts'))
from build_extension import build as build_extension

EXCLUDED = {'__pycache__', '.git', '.venv', '.venv-api', 'node_modules', 'dist', 'build', 'runtime', 'runs', 'checkpoints', 'outputs'}


def include(path):
    parts = path.relative_to(ROOT).parts
    if any(p in EXCLUDED or p.endswith('.egg-info') for p in parts):
        return False
    if path.is_symlink():
        raise ValueError('Source delivery must not contain symlinks')
    if path.name=='config.local.json':
        return False
    if path.name.startswith('.env') and path.name != '.env.example':
        return False
    return not (path.name == '.DS_Store' or path.suffix in {'.pyc', '.sqlite', '.sqlite3', '.db', '.log', '.pem', '.key', '.pt', '.pth', '.bin', '.safetensors'} or '-wal' in path.name or '-shm' in path.name)


def bundle(output, files, prefix):
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            archive.write(file, prefix + '/' + str(file.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.3.0')
    parser.add_argument('--out', type=Path, default=ROOT/'dist')
    parser.add_argument('--wheel', action='store_true', help='Use existing setuptools/wheel only')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        raise ValueError('Version must be major.minor.patch')
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Choose an empty output directory; previous releases are preserved')
    out.mkdir(parents=True, exist_ok=True)
    build_extension(out/f'evidence-verifier-extension-{args.version}.zip', web_copy=True)
    if args.wheel:
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--disable-pip-version-check', '--no-deps', '--no-build-isolation',
                        str(ROOT/'sdk'), '--wheel-dir', str(out)], check=True)
    files = []
    for folder in ('app', 'sdk', 'docs', 'scripts', '.github', 'api_edition'):
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and include(p)]
    files += [ROOT/name for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', '.gitignore', '.env.example')]
    bundle(out/f'evidence-verifier-{args.version}-source.zip', files, 'evidence-verifier')
    bundle(out/'evidence-verifier-sdk-0.2.0-source.zip', [p for p in files if p.is_relative_to(ROOT/'sdk')], 'evidence-verifier')
    api_files=[p for p in files if not str(p.relative_to(ROOT)).startswith(('app/training.py','app/torch_training.py','app/tests/test_training.py','app/vendor/evpilot/run_relation_v7_suite.py','app/configs/sft'))]
    bundle(out/f'evidence-verifier-api-{args.version}-source.zip',api_files,'evidence-verifier')
    manifest = {'version': args.version, 'profile': 'engineering_demo_delivery',
                'model_inference_performed': False, 'training_executed': False,
                'assets': {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in sorted(out.iterdir()) if p.is_file()},
                'source_files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(files)}}
    (out/'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                                       for p in sorted(out.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
    print(json.dumps({'version': args.version, 'assets': len(list(out.iterdir())), 'source_files': len(files),
                      'model_inference_performed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
