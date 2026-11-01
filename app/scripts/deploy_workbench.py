"""Deploy a private, CPU-only workbench release on the existing Linux server.

No dependency installation, GPU model startup, public listener or deletion.
Run this script on the target machine with its already installed Python.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import time
import urllib.request
import uuid
import zipfile


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024*1024), b''):
            value.update(data)
    return value.hexdigest()


def proc_info(pid):
    root = Path('/proc') / str(pid)
    try:
        fields = (root/'stat').read_text().rsplit(')', 1)[1].split()
        return {'state': fields[0], 'start_ticks': fields[19],
                'command': (root/'cmdline').read_bytes().decode().strip('\0').split('\0'),
                'cwd': str((root/'cwd').resolve())}
    except (OSError, IndexError):
        return None


def owns_listener(pid, port):
    inodes = set()
    try:
        descriptors = list((Path('/proc')/str(pid)/'fd').iterdir())
    except OSError:
        return False
    for fd in descriptors:
        try:
            target = os.readlink(fd)
            if target.startswith('socket:['):
                inodes.add(target[8:-1])
        except OSError:
            continue
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if (fields[1] == '0100007F:' + format(port, '04X') and fields[3] == '0A'
                and fields[9] in inodes):
            return True
    return False


def status(record):
    value = json.loads(Path(record).read_text())
    process = proc_info(value['pid'])
    matches = bool(process and process['state'] != 'Z'
                   and all(process[k] == value['process_identity'][k] for k in ('start_ticks', 'command', 'cwd')))
    return {'deployment_record': str(Path(record).resolve()), 'release': value['release'],
            'archive_sha256': value['archive_sha256'], 'pid': value['pid'],
            'process_state': 'owned_running' if matches else 'stopped_or_identity_changed',
            'listening': owns_listener(value['pid'], value['port']) if matches else False,
            'port': value['port'], 'profile': 'cpu_demo_no_model_services',
            'log': value['log']}


def stop(record):
    current = status(record)
    if current['process_state'] != 'owned_running':
        return current
    # Recheck immediately before signaling; never terminate by port or a broad name.
    value = json.loads(Path(record).read_text())
    process = proc_info(value['pid'])
    if not process or any(process[k] != value['process_identity'][k] for k in ('start_ticks', 'command', 'cwd')):
        raise RuntimeError('Process identity changed; no signal was sent')
    os.kill(value['pid'], signal.SIGTERM)
    for _ in range(25):
        if status(record)['process_state'] != 'owned_running':
            break
        time.sleep(.2)
    return status(record)


def deploy(args):
    if not Path('/proc/net/tcp').is_file():
        raise RuntimeError('This deployment entry is for the existing Linux server')
    archive, python = args.archive.resolve(), args.python.absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError('Provide an existing executable Python; nothing is installed automatically')
    actual = file_sha(archive)
    if actual != args.sha256:
        raise RuntimeError('Uploaded archive hash does not match the selected local delivery')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', args.tag):
        raise ValueError('Release tag must contain 1–40 letters, numbers, dashes or underscores')
    if not 1024 <= args.port <= 65535:
        raise ValueError('Use a nonprivileged workbench port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    args.base.mkdir(parents=True, exist_ok=True)
    release = args.base.resolve() / (args.tag + '-' + actual[:12] + '-' + uuid.uuid4().hex[:6])
    release.mkdir()
    app = release / 'app'
    app.mkdir()
    prefix = 'evidence-verifier-workbench/'
    with zipfile.ZipFile(archive) as bundle:
        members = [m for m in bundle.infolist() if not m.is_dir()]
        names = [m.filename for m in members]
        if len(names) != len(set(names)) or len(names) > 1000 or sum(m.file_size for m in members) > 512*1024**2:
            raise RuntimeError('Duplicate entries or unexpected source bundle size')
        if shutil.disk_usage(release).free < sum(m.file_size for m in members)+20*1024**2:
            raise RuntimeError('Insufficient space; no files will be deleted to make room')
        manifest = json.loads(bundle.read(prefix + 'docs/source-manifest.json'))
        if set(names) != {prefix + n for n in manifest} | {prefix + 'docs/source-manifest.json'}:
            raise RuntimeError('Source bundle does not match its file manifest')
        for member in members:
            if not member.filename.startswith(prefix):
                raise RuntimeError('Unexpected bundle path')
            relative = PurePosixPath(member.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                raise RuntimeError('Unsafe source path')
            if stat.S_ISLNK(member.external_attr >> 16):
                raise RuntimeError('Source symlinks are not permitted')
            destination = app.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            sha = hashlib.sha256()
            with bundle.open(member) as source, destination.open('xb') as output:
                for data in iter(lambda: source.read(1024*1024), b''):
                    output.write(data); sha.update(data)
            if str(relative) in manifest and sha.hexdigest() != manifest[str(relative)]:
                raise RuntimeError('Source file hash mismatch: ' + str(relative))
    runtime = release / 'runtime'
    runtime.mkdir()
    state_source = None
    if args.state_from:
        previous = json.loads(args.state_from.read_text())
        if previous.get('profile') != 'cpu_demo_no_model_services':
            raise RuntimeError('State import is limited to this project CPU workbench deployment records')
        if status(args.state_from)['process_state'] == 'owned_running':
            raise RuntimeError('Stop the recorded CPU workbench before copying its final state')
        source_db = Path(previous['runtime_db'])
        if not source_db.is_file():
            raise RuntimeError('Previous workbench state is missing; no empty substitute was created')
        with sqlite3.connect(source_db.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(runtime/'workbench.sqlite') as destination:
                source.backup(destination)
        state_source = str(source_db.resolve())
    env = {k: v for k, v in os.environ.items() if not k.startswith('EV_')}
    env.update(EV_DB=str(runtime/'workbench.sqlite'), EV_CORPUS=str(app/'data/musique-corpus.sqlite'),
               CUDA_VISIBLE_DEVICES='', PYTHONDONTWRITEBYTECODE='1')
    log = runtime/'workbench.log'
    command = [str(python), str(app/'server.py'), '--port', str(args.port)]
    with log.open('xb') as output:
        process = subprocess.Popen(command, cwd=app, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    identity = proc_info(process.pid)
    if not identity:
        raise RuntimeError('Workbench exited before a process identity could be recorded; inspect ' + str(log))
    record = runtime/'deployment.json'
    value = {'release': str(release), 'archive': str(archive), 'archive_sha256': actual,
             'pid': process.pid, 'port': args.port, 'process_identity': identity,
             'python': str(python), 'log': str(log), 'created': datetime.now(timezone.utc).isoformat(),
             'profile': 'cpu_demo_no_model_services', 'model_services_disabled': True,
             'runtime_db': env['EV_DB'], 'bind': '127.0.0.1',
             'state_copied_from': state_source, 'state_copy_method': 'sqlite_backup' if state_source else None}
    record.write_text(json.dumps(value, indent=2)+'\n')
    ready = False
    for _ in range(20):
        if process.poll() is not None:
            break
        if owns_listener(process.pid, args.port):
            try:
                with urllib.request.urlopen('http://127.0.0.1:' + str(args.port) + '/api/health', timeout=1) as response:
                    health = json.load(response)
                ready = bool(health.get('ok') and not health['live_configured']
                             and not health['agent']['configured'] and not health['planner']['configured'])
                if ready:
                    value['startup_health'] = health
                    break
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(.25)
    value['startup_ready'] = ready
    record.write_text(json.dumps(value, indent=2)+'\n')
    if not ready:
        raise RuntimeError('CPU workbench did not become ready; retained record/log: ' + str(record))
    return dict(status(record), model_services_disabled=True,
                corpus_docs=value['startup_health']['corpus_docs'], startup_only_no_model_test=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('deploy')
    create.add_argument('--archive', type=Path, required=True)
    create.add_argument('--sha256', required=True)
    create.add_argument('--python', type=Path, required=True)
    create.add_argument('--base', type=Path, required=True)
    create.add_argument('--tag', default='workbench')
    create.add_argument('--port', type=int, default=18790)
    create.add_argument('--state-from', type=Path, help='Previous stopped CPU deployment record; preserve its SQLite state')
    for action in ('status', 'stop'):
        sub.add_parser(action).add_argument('--record', type=Path, required=True)
    args = parser.parse_args()
    result = deploy(args) if args.action == 'deploy' else status(args.record) if args.action == 'status' else stop(args.record)
    print(json.dumps(result, ensure_ascii=False))
