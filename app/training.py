"""Offline LoRA training preparation. Preflight never imports torch or loads weights."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from engine import digest


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gate(report):
    """Recompute the original development gate from individual scored rows, not a pass flag."""
    rows = report['split']['regression80']['details']
    if len(rows) != 80 or len({r['id'] for r in rows}) != 80:
        raise ValueError('Gate requires 80 unique scored development rows')
    positive = [r for r in rows if r['positive']]
    negative = [r for r in rows if not r['positive']]
    variables = [r for r in rows if r['variable']]
    if len(positive) != 40 or len(negative) != 40 or len(variables) != 16:
        raise ValueError('Gate distribution must be 40 positive, 40 negative and 16 variable cases')
    def correct(r):
        return all(r[x] is True for x in ('label_correct', 'bindings_correct', 'evidence_correct', 'fact_roles_correct')) and not r['invalid']
    counts = {'joint': sum(correct(r) for r in rows), 'positive': sum(correct(r) for r in positive),
              'negative': sum(correct(r) for r in negative), 'variables': sum(correct(r) for r in variables),
              'invalid': sum(bool(r['invalid']) for r in rows),
              'critical_false_accept': sum(bool(r['critical_false_accept']) for r in rows)}
    passed = counts['joint'] >= 72 and counts['positive'] >= 36 and counts['negative'] >= 36 and counts['variables'] == 16 and counts['invalid'] == 0 and counts['critical_false_accept'] == 0
    return {'passed': passed, 'counts': counts, 'scope': 'development_gate_only'}


def data_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError('Training data cannot be empty')
    seen = set()
    for row in rows:
        if row.get('id') in seen or not isinstance(row.get('id'), str):
            raise ValueError('Training row IDs must be unique strings')
        seen.add(row['id'])
        if row.get('split') != 'train' or row.get('task') not in ('extract', 'verify'):
            raise ValueError('Only declared train split extract/verify records are accepted')
        if not row.get('source_group') or row.get('label_source') not in ('human_verified', 'teacher_screened', 'synthetic_fixture'):
            raise ValueError('Data needs source-group and label provenance')
        messages = row.get('messages')
        if not isinstance(messages, list) or [m.get('role') for m in messages] != ['system', 'user', 'assistant']:
            raise ValueError('Expected system/user/assistant messages')
        if any(not isinstance(m.get('content'), str) or not m['content'].strip() for m in messages):
            raise ValueError('Messages must contain text')
        value = json.loads(messages[1]['content'])
        if row['task'] == 'extract' and set(value) - {'predicate_definitions', 'evidence', 'focus_quote_ids'}:
            raise ValueError('Extraction data must not include question, candidate or target')
        json.loads(messages[2]['content'])
        if row.get('curation') is not None:
            curated = row['curation']
            if (not isinstance(curated, dict) or curated.get('messages_sha256') != digest(messages)
                    or curated.get('evidence_sha256') != digest(curated.get('evidence'))
                    or curated.get('source_replay') != 'passed' or curated.get('source', {}).get('split') != 'train'):
                raise ValueError('Curation snapshot does not match approved training messages')
            dataset = curated['source'].get('dataset', '').casefold().replace('-', '').replace('_', '').replace(' ', '')
            if dataset in ('2wiki', '2wikimultihopqa') or curated['source'].get('origin') == 'synthetic_fixture':
                raise ValueError('Held-out or fixture curation cannot enter formal training')
    return rows


def preflight(config, data_path, gate_path):
    blockers = []
    result = gate(json.loads(Path(gate_path).read_text()))
    if not result['passed']:
        blockers.append('Verifier development gate has not passed; formal training remains gated')
    rows = data_rows(data_path)
    if any(r['label_source'] == 'synthetic_fixture' for r in rows):
        blockers.append('Synthetic fixtures are protocol tests, not approved training data')
    for key in ('max_length', 'micro_batch', 'gradient_accumulation', 'optimizer_steps', 'chunk_tokens'):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ValueError('Positive integer config required: ' + key)
    if config.get('loss_mode') not in ('standard', 'checkpointed_chunked_ce'):
        raise ValueError('Unimplemented loss mode; Liger is not installed or silently substituted')
    for package in ('torch', 'transformers', 'peft'):
        if importlib.util.find_spec(package) is None:
            blockers.append('Missing existing dependency: ' + package)
    model = Path(config['model'])
    model_files = {}
    if not (model/'config.json').is_file() or not (model/'tokenizer_config.json').is_file():
        blockers.append('Local model configuration/tokenizer is missing')
    else:
        for name in ('config.json', 'tokenizer_config.json', 'model.safetensors.index.json'):
            if (model/name).is_file():
                model_files[name] = sha(model/name)
        index = model/'model.safetensors.index.json'
        shards = set(json.loads(index.read_text())['weight_map'].values()) if index.is_file() else {'model.safetensors'}
        if any(not (model/s).is_file() for s in shards):
            blockers.append('One or more model weight files are missing')
    return {'ready': not blockers, 'blockers': blockers, 'development_gate': result,
            'data_sha256': sha(data_path), 'gate_sha256': sha(gate_path), 'rows': len(rows),
            'groups': len({r['source_group'] for r in rows}), 'model_files': model_files,
            'config': config, 'scope': 'preflight_only_no_model_loaded'}


def encode(row, tokenizer, max_length):
    prefix = tokenizer.apply_chat_template(row['messages'][:2], tokenize=False, add_generation_prompt=True)
    full = tokenizer.apply_chat_template(row['messages'], tokenize=False, add_generation_prompt=False)
    if not full.startswith(prefix):
        raise ValueError('Chat template prefix does not align with the supervised response')
    tokenized = tokenizer(full, add_special_tokens=False, return_offsets_mapping=True)
    ids = tokenized['input_ids']
    if len(ids) > max_length:
        raise ValueError('Sample exceeds max_length; no silent truncation: ' + row['id'])
    labels = [tok if end > start and start >= len(prefix) else -100
              for tok, (start, end) in zip(ids, tokenized['offset_mapping'])]
    if not any(x != -100 for x in labels[1:]):
        raise ValueError('Sample has no supervised causal target tokens')
    return {'input_ids': ids, 'labels': labels, 'id': row['id']}


def train(config, data_path, gate_path, output):
    checked = preflight(config, data_path, gate_path)
    if not checked['ready']:
        raise RuntimeError('; '.join(checked['blockers']))
    # All permission/quality/resource decisions happen before heavy imports and weights.
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import LoraConfig, get_peft_model
    from torch_training import optimize
    if not torch.cuda.is_available():
        raise RuntimeError('Formal training requires an available GPU; CPU fallback is disabled')
    free, total = torch.cuda.mem_get_info()
    if free < int(config.get('minimum_free_gib', 12) * 1024**3):
        raise RuntimeError('Insufficient free GPU memory; existing processes are not modified')
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    (out/'preflight.json').write_text(json.dumps(checked, indent=2))
    try:
        torch.manual_seed(config.get('seed', 42))
        tokenizer = AutoTokenizer.from_pretrained(config['model'], local_files_only=True, trust_remote_code=False, use_fast=True)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        items = [encode(r, tokenizer, config['max_length']) for r in data_rows(data_path)]
        model = AutoModelForCausalLM.from_pretrained(config['model'], local_files_only=True,
                    trust_remote_code=False, torch_dtype=torch.bfloat16, attn_implementation='sdpa').cuda()
        model.config.use_cache = False
        model = get_peft_model(model, LoraConfig(task_type='CAUSAL_LM', r=16, lora_alpha=32,
                                               lora_dropout=0, target_modules=['q_proj', 'v_proj']))
        if config.get('gradient_checkpointing', True):
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
            model.enable_input_require_grads()
        report = optimize(model, items, config, tokenizer.pad_token_id, out/'steps.jsonl')
        model.save_pretrained(out/'adapter', safe_serialization=True)
        tokenizer.save_pretrained(out/'adapter')
        (out/'completed.json').write_text(json.dumps(report, indent=2))
    except Exception as exc:
        (out/'failed.json').write_text(json.dumps({'error': str(exc)}))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True); parser.add_argument('--data', required=True)
    parser.add_argument('--gate', required=True); parser.add_argument('--execute', action='store_true')
    parser.add_argument('--out')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.execute:
        if not args.out:
            parser.error('--execute requires a new --out directory')
        train(config, args.data, args.gate, args.out)
    else:
        print(json.dumps(preflight(config, args.data, args.gate), indent=2))
