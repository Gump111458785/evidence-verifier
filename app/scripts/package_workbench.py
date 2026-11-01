"""Create a source delivery without runtime databases, credentials or model files."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def included(path, root):
    relative = path.relative_to(root)
    if any(part in {'__pycache__', '.git', '.venv', 'node_modules', '.pytest_cache', 'outputs'} for part in relative.parts):
        return False
    if relative.parts[0] == 'data' and relative.name not in {'archive.json', 'corpus-manifest.json', 'development-gate-v7-qwen14b.json', 'musique-corpus.sqlite'}:
        return False
    if path.name == '.env' or path.name.startswith('.env.') or path.suffix in {'.pyc', '.pem', '.key', '.safetensors', '.pt', '.bin'}:
        return False
    if path.name.startswith('workbench.sqlite') or path.suffix in {'.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm'}:
        return relative == Path('data/musique-corpus.sqlite')
    if '-wal' in path.name or '-shm' in path.name:
        return False
    return relative != Path('docs/source-manifest.json')


def package(root, output):
    if output.exists():
        raise ValueError('Output already exists; choose a new delivery filename')
    files = sorted(path for path in root.rglob('*') if path.is_file() and included(path, root))
    manifest = root / 'docs/source-manifest.json'
    manifest.write_text(json.dumps({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in files}, ensure_ascii=False, indent=2)+'\n')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in files + [manifest]:
            archive.write(path, 'evidence-verifier-workbench/' + str(path.relative_to(root)))
    return {'package': str(output.resolve()), 'files': len(files)+1, 'bytes': output.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.out)))
