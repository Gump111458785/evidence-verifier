"""Optional PyTorch-only training helpers; no imports in the workbench server."""
import itertools
import json
import time
import torch
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint


def chunked_ce(hidden, labels, weight, bias=None, chunk_tokens=128):
    """Causal masked CE without materializing sequence×vocabulary logits at once.

    Checkpoint each projection+CE chunk so backward also recomputes instead of
    retaining every chunk's logits. This is not a custom Liger/Triton kernel.
    """
    if type(chunk_tokens) is not int or chunk_tokens < 1:
        raise ValueError('chunk_tokens must be positive')
    x, y = hidden[:, :-1].reshape(-1, hidden.shape[-1]), labels[:, 1:].reshape(-1)
    active = y != -100
    x, y = x[active], y[active]
    if not y.numel():
        raise ValueError('No supervised causal tokens')
    total = x.new_zeros((), dtype=torch.float32)
    def loss(h, w, b, target):
        return F.cross_entropy(F.linear(h, w, b).float(), target, reduction='sum')
    for start in range(0, len(y), chunk_tokens):
        total = total + checkpoint(loss, x[start:start+chunk_tokens], weight, bias,
                                   y[start:start+chunk_tokens], use_reentrant=False)
    return total / len(y)


def collate(items, pad_id, device):
    length = max(len(x['input_ids']) for x in items)
    ids = torch.full((len(items), length), pad_id, dtype=torch.long, device=device)
    labels = torch.full_like(ids, -100); mask = torch.zeros_like(ids)
    for i, item in enumerate(items):
        n = len(item['input_ids'])
        ids[i, :n] = torch.tensor(item['input_ids'], device=device)
        labels[i, :n] = torch.tensor(item['labels'], device=device)
        mask[i, :n] = 1
    return {'input_ids': ids, 'labels': labels, 'attention_mask': mask}


def model_loss(model, batch, config):
    if config['loss_mode'] == 'standard':
        return model(**batch, use_cache=False).loss
    base = model.get_base_model()
    if base.config.model_type not in ('qwen2', 'llama'):
        raise ValueError('Chunked decoder path supports qwen2/llama only')
    output = base.model(input_ids=batch['input_ids'], attention_mask=batch['attention_mask'], use_cache=False)
    return chunked_ce(output.last_hidden_state, batch['labels'], base.lm_head.weight,
                      getattr(base.lm_head, 'bias', None), config['chunk_tokens'])


def optimize(model, items, config, pad_id, log_path):
    if not items:
        raise ValueError('No training items')
    model.train(); device = next(model.parameters()).device
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                                  lr=config.get('learning_rate', 2e-4), weight_decay=0)
    # Reproducible length ordering controls padding; record it, do not claim throughput benefit without measurement.
    ordered = sorted(items, key=lambda x: (len(x['input_ids']), x['id'])) if config.get('length_buckets', True) else items
    batches = [ordered[i:i+config['micro_batch']] for i in range(0, len(ordered), config['micro_batch'])]
    iterator = itertools.cycle(batches)
    started = time.monotonic(); supervised = 0; steps = []
    if device.type == 'cuda':
        torch.cuda.reset_peak_memory_stats(device)
    for step in range(config['optimizer_steps']):
        group = [collate(next(iterator), pad_id, device) for _ in range(config['gradient_accumulation'])]
        counts = [int((b['labels'][:, 1:] != -100).sum()) for b in group]
        if not all(counts):
            raise ValueError('A microbatch has no supervised target tokens')
        optimizer.zero_grad(set_to_none=True); value = 0
        for batch, n in zip(group, counts):
            loss = model_loss(model, batch, config)
            if not torch.isfinite(loss):
                raise ValueError('Non-finite training loss; no successful checkpoint reported')
            scaled = loss * (n / sum(counts))
            scaled.backward(); value += float(scaled.detach())
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.)
        optimizer.step(); supervised += sum(counts)
        if device.type == 'cuda':
            torch.cuda.synchronize(device)
        rec = {'step': step+1, 'loss': value, 'supervised_tokens': sum(counts),
               'seconds_since_start': time.monotonic()-started}
        with open(log_path, 'a') as f:
            f.write(json.dumps(rec)+'\n')
        steps.append(rec)
    return {'optimizer_steps': len(steps), 'seconds': time.monotonic()-started, 'supervised_tokens': supervised,
            'peak_allocated_bytes': torch.cuda.max_memory_allocated(device) if device.type == 'cuda' else None,
            'peak_reserved_bytes': torch.cuda.max_memory_reserved(device) if device.type == 'cuda' else None,
            'device': str(device), 'loss_mode': config['loss_mode']}
