"""Bounded replica routing and in-flight coalescing. Never starts GPU processes."""
import copy
from concurrent.futures import Future
from collections import deque
import hashlib
import json
import os
import threading
import time
from urllib.parse import urlsplit


class InferenceBusy(RuntimeError):
    pass


def configuration():
    urls = [s.strip().rstrip('/') for s in os.environ.get('EV_VERIFIER_URLS', '').split(',') if s.strip()]
    if not urls and os.environ.get('EV_MODEL_URL'):
        urls = [os.environ['EV_MODEL_URL'].rstrip('/')]
    urls = list(dict.fromkeys(urls))
    model, revision = os.environ.get('EV_MODEL_NAME', ''), os.environ.get('EV_MODEL_REVISION', '')
    error = None
    if any(urlsplit(u).scheme not in ('http', 'https') or not urlsplit(u).netloc for u in urls):
        error = 'Verifier URLs must be HTTP(S) endpoints'
    if len(urls) > 1 and not revision:
        error = 'Multiple replicas require EV_MODEL_REVISION identifying the same weights'
    policy = os.environ.get('EV_ROUTING_POLICY', 'least_work')
    if policy not in ('least_work', 'round_robin'):
        error = 'Routing policy must be least_work or round_robin'
    try:
        parallel = int(os.environ.get('EV_REPLICA_CONCURRENCY', '1'))
        pending = int(os.environ.get('EV_INFERENCE_PENDING', '32'))
        wait = float(os.environ.get('EV_INFERENCE_QUEUE_SECONDS', '30'))
        if not (1 <= parallel <= 8 and 1 <= pending <= 128 and 0 < wait <= 300):
            raise ValueError()
    except ValueError:
        parallel, pending, wait = 1, 32, 30
        error = 'Invalid concurrency, queue size or queue timeout'
    return {'configured': bool(urls and model and not error), 'urls': urls, 'model': model,
            'revision': revision, 'policy': policy, 'parallel': parallel, 'pending': pending,
            'wait_seconds': wait, 'error': error}


def estimate_work(body):
    # An explicitly named heuristic, not a tokenizer measurement or actual GPU time.
    chars = sum(len(str(m.get('content', ''))) for m in body.get('messages', []))
    return max(1, (chars+3)//4 + int(body.get('max_tokens', 0)))


class ReplicaRouter:
    def __init__(self, config):
        if not config['configured']:
            raise ValueError(config['error'] or 'Verifier model is not configured')
        self.config = config
        self.condition = threading.Condition()
        self.flights = {}
        self.queues = [deque() for _ in config['urls']]
        self.active = [0] * len(self.queues)
        self.work = [0] * len(self.queues)
        self.next_replica = 0
        self.counts = {'submitted': 0, 'completed': 0, 'failed': 0, 'coalesced': 0, 'rejected': 0}

    def run(self, key, work, operation):
        started = time.monotonic()
        with self.condition:
            future = self.flights.get(key)
            owner = future is None
            if not owner:
                self.counts['coalesced'] += 1
            else:
                if len(self.flights) >= self.config['pending']:
                    self.counts['rejected'] += 1
                    raise InferenceBusy('Inference queue is full; retry later')
                if self.config['policy'] == 'round_robin':
                    replica = self.next_replica % len(self.queues)
                    self.next_replica += 1
                else:
                    replica = min(range(len(self.queues)), key=lambda i: (self.work[i], self.active[i], i))
                future = Future()
                self.flights[key] = future
                self.queues[replica].append(key)
                self.work[replica] += work
                self.counts['submitted'] += 1
        if not owner:
            record, routing = future.result(timeout=self.config['wait_seconds']+185)
            return copy.deepcopy(record), dict(routing, coalesced=True,
                follower_wait_seconds=round(time.monotonic()-started, 6))
        active = False
        try:
            deadline = started + self.config['wait_seconds']
            with self.condition:
                while self.active[replica] >= self.config['parallel'] or self.queues[replica][0] != key:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise InferenceBusy('Inference queue wait timed out')
                    self.condition.wait(remaining)
                self.queues[replica].popleft()
                self.active[replica] += 1
                active = True
            routing = {'replica': replica, 'policy': self.config['policy'],
                       'estimated_work': work, 'estimate_kind': 'characters_div_4_plus_max_output_tokens',
                       'queue_seconds': round(time.monotonic()-started, 6), 'coalesced': False}
            record = operation(self.config['urls'][replica])
            future.set_result((record, routing))
            with self.condition:
                self.counts['completed'] += 1
            return copy.deepcopy(record), routing
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            with self.condition:
                self.counts['failed'] += 1
            raise
        finally:
            with self.condition:
                if active:
                    self.active[replica] -= 1
                else:
                    self.queues[replica].remove(key)
                self.work[replica] -= work
                self.flights.pop(key, None)
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {'configured': True, 'policy': self.config['policy'], 'counters': dict(self.counts),
                    'replicas': [{'id': i, 'active': self.active[i], 'queued': len(self.queues[i]),
                                  'estimated_work': self.work[i]} for i in range(len(self.queues))],
                    'queue_limit': self.config['pending'], 'concurrency_per_replica': self.config['parallel'],
                    'model': self.config['model'], 'revision': self.config['revision'],
                    'performance_claim': 'not_measured'}


_routers = {}
_lock = threading.Lock()


def router():
    config = configuration()
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    with _lock:
        if key not in _routers:
            _routers[key] = ReplicaRouter(config)
        return _routers[key]


def snapshot():
    config = configuration()
    if not config['configured']:
        return {'configured': False, 'error': config['error'], 'replicas': [], 'performance_claim': 'not_measured'}
    return router().snapshot()
