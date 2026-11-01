"""Checkpoint-friendly nodes. LangGraph is imported only when building a graph."""
from __future__ import annotations
import copy
import hashlib
import json
from typing import Any, Callable, Mapping, Sequence, TypedDict
import uuid
from .client import VerifierClient, VerifierError, WaitTimeout


class InputChanged(VerifierError):
    """A retained job belongs to a different input, server or logical request."""


class VerificationState(TypedDict, total=False):
    evidence: list[dict[str, str]]
    relations: list[dict[str, Any]]
    verification_key: str
    verification_job_id: str | None
    verification_input_sha256: str | None
    verification_server_sha256: str | None
    verification_status: str
    verification: dict[str, Any] | None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def new_verification_input(evidence: Sequence[Mapping[str, str]], relations: Sequence[Mapping[str, Any]], *,
                           key: str | None = None) -> VerificationState:
    """Explicitly start a new logical check; persist the returned key with state."""
    return {'evidence': copy.deepcopy(list(evidence)), 'relations': copy.deepcopy(list(relations)),
            'verification_key': key if key is not None else uuid.uuid4().hex,
            'verification_job_id': None, 'verification_input_sha256': None,
            'verification_server_sha256': None, 'verification': None, 'verification_status': 'new'}


def _identity(state: VerificationState, client: VerifierClient) -> tuple[str, str, str]:
    key = state.get('verification_key')
    if not isinstance(key, str) or not key.strip() or len(key) > 1000:
        raise ValueError('Persist a nonempty verification_key of at most 1000 characters')
    if not isinstance(state.get('evidence'), list) or not isinstance(state.get('relations'), list):
        raise ValueError('Graph state needs evidence and relations arrays')
    return ('lg-' + _hash(key), _hash({'evidence': state['evidence'], 'relations': state['relations'], 'key': key}),
            _hash(client.base_url))


def _bound_job(state: VerificationState, client: VerifierClient) -> dict[str, Any]:
    request_key, input_sha, server_sha = _identity(state, client)
    if (state.get('verification_input_sha256') != input_sha
            or state.get('verification_server_sha256') != server_sha):
        raise InputChanged('Evidence, relations or server changed; explicitly create a new verification input')
    job_id = state.get('verification_job_id')
    if not job_id:
        raise ValueError('Submission node must produce a verification_job_id first')
    job = client.get(job_id)
    if job.get('request_key') != request_key:
        raise InputChanged('Retained job does not match this logical verification key; server API must expose request_key')
    return job


def make_submission_node(client: VerifierClient) -> Callable[[VerificationState], dict[str, Any]]:
    def submit(state: VerificationState) -> dict[str, Any]:
        request_key, input_sha, server_sha = _identity(state, client)
        if state.get('verification_job_id'):
            _bound_job(state, client)
            return {}  # Retain the exact job; do not re-submit a model request.
        if state.get('verification_input_sha256') is not None and state['verification_input_sha256'] != input_sha:
            raise InputChanged('Partially retained submission changed its input')
        created = client.submit(state['evidence'], state['relations'], request_key=request_key)
        return {'verification_job_id': created['id'], 'verification_input_sha256': input_sha,
                'verification_server_sha256': server_sha, 'verification_status': created['state'], 'verification': None}
    return submit


def make_wait_node(client: VerifierClient, *, timeout: float = 30) -> Callable[[VerificationState], dict[str, Any]]:
    def observe(state: VerificationState) -> dict[str, Any]:
        job = _bound_job(state, client)
        try:
            completed = client.wait(job['id'], timeout=timeout)
        except WaitTimeout:
            # Graph state remains resumable; pending is not evidence insufficiency.
            return {'verification_status': 'pending', 'verification': None}
        return {'verification_status': 'completed', 'verification': completed['result']}
    return observe


def build_verification_graph(client: VerifierClient, *, checkpointer: Any = None, timeout: float = 30) -> Any:
    """Build a two-node graph in a caller environment that already has LangGraph."""
    try:
        from langgraph.graph import StateGraph, START, END
    except ImportError as exc:
        raise ImportError('Provide LangGraph in the calling environment; this client does not install it') from exc
    graph = StateGraph(VerificationState)
    graph.add_node('submit_verification', make_submission_node(client))
    graph.add_node('observe_verification', make_wait_node(client, timeout=timeout))
    graph.add_edge(START, 'submit_verification')
    graph.add_edge('submit_verification', 'observe_verification')
    graph.add_edge('observe_verification', END)
    return graph.compile(checkpointer=checkpointer)
