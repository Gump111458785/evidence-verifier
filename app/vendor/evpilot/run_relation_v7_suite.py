"""Sequential, offline-only experiment launcher; never installs or cleans up."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MODELS = [('qwen14b', '/path/to/models/Qwen2.5-14B-Instruct', 2),
          ('qwen7b', '/path/to/models/Qwen2.5-7B-Instruct', 1),
          ('llama8b', '/path/to/models/Meta-Llama-3-8B-Instruct', 1)]


def main():
    for tag, model, tp in MODELS:
        out = Path('runs/relation-kernel-v7-' + tag)
        if out.exists():
            raise RuntimeError('Refuse duplicate experiment: ' + str(out))
        log = Path(str(out) + '.log')
        if log.exists():
            raise RuntimeError('Refuse log overwrite: ' + str(log))
        usage = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], text=True)
        occupied = [int(x.strip()) for x in usage.splitlines()][:tp]
        if any(x > 100 for x in occupied):
            out.mkdir()
            (out / 'failure.json').write_text(json.dumps({'category': 'resource_unavailable', 'gpu_memory_used_MiB': occupied}))
            continue
        env = dict(os.environ, CUDA_VISIBLE_DEVICES='0,1' if tp == 2 else '0',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        cmd = [sys.executable, '-m', 'evpilot.relation_kernel_v7', '--data', 'data/relation-kernel-v7',
               '--out', str(out), '--model', model, '--tp', str(tp)]
        started = time.time()
        print(json.dumps({'start': tag, 'utc_timestamp': started}), flush=True)
        with log.open('x') as handle:
            result = subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT, env=env)
        out.mkdir(exist_ok=True)
        (out / 'process.json').write_text(json.dumps({'returncode': result.returncode, 'seconds': time.time()-started}))
        if result.returncode:
            tail = log.read_text(errors='replace')[-5000:]
            category = 'resource_failure' if any(x in tail.lower() for x in ['out of memory', 'no space left', 'cannot allocate']) else 'runtime_or_compatibility_failure'
            (out / 'failure.json').write_text(json.dumps({'category': category, 'returncode': result.returncode, 'log_tail': tail}))
        print(json.dumps({'finish': tag, 'returncode': result.returncode}), flush=True)
        time.sleep(3)  # Allow worker process shutdown before the next resource check.
    subprocess.run([sys.executable, '-m', 'evpilot.report_relation_v7'], check=True)


if __name__ == '__main__':
    main()
