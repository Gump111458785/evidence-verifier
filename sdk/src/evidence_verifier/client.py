"""Standard-library client; observation timeouts never restart remote jobs."""
from __future__ import annotations
import json
import math
import re
import socket
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
from ._version import VERSION


class VerifierError(RuntimeError):
    pass


class VerifierHTTPError(VerifierError):
    def __init__(self, message: str, status: int, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code


class ModelUnavailable(VerifierHTTPError):
    pass


class VerifierTransportError(VerifierError):
    request_key: str | None = None


class JobFailed(VerifierError):
    def __init__(self, job: dict[str, Any]):
        super().__init__(job.get('error') or 'Verification job failed')
        self.job, self.job_id = job, job['id']


class JobCanceled(JobFailed):
    pass


class InvalidOutput(JobFailed):
    pass


class WaitTimeout(VerifierError):
    def __init__(self, job_id: str):
        super().__init__('Wait expired; remote job was not restarted or canceled: ' + job_id)
        self.job_id = job_id


class VerifierClient:
    def __init__(self, base_url: str = 'http://127.0.0.1:8790', *, request_timeout: float = 30,
                 headers: Mapping[str, str] | None = None, advance_cloud_jobs: bool = False):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('Use an HTTP(S) workbench base URL without query or fragment')
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError('Request timeout must be finite and positive')
        self.base_url, self.request_timeout = base_url.rstrip('/'), request_timeout
        self.headers = dict(headers or {})
        self.advance_cloud_jobs = advance_cloud_jobs

    def _request(self, path: str, body: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        timeout = self.request_timeout if timeout is None else timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('HTTP timeout must be finite and positive')
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        if payload is not None and len(payload) > 200000:
            raise ValueError('Request exceeds the workbench 200000-byte limit')
        headers = dict(self.headers)
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base_url + path, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                value = json.loads(exc.read(10000))
            except (ValueError, OSError):
                value = {'error': 'HTTP request failed'}
            if not isinstance(value, dict):
                value = {'error': 'HTTP request failed'}
            cls = ModelUnavailable if value.get('code') == 'model_not_configured' else VerifierHTTPError
            raise cls(str(value.get('error', 'HTTP request failed')), exc.code, value.get('code')) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise VerifierTransportError(str(exc)) from exc
        except ValueError as exc:
            raise VerifierError('Workbench returned a non-JSON response') from exc
        if not isinstance(data, dict):
            raise VerifierError('Expected a JSON object from the workbench')
        return data

    @staticmethod
    def _id(job_id: str) -> str:
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', job_id):
            raise ValueError('Invalid job ID')
        return job_id

    def health(self) -> dict[str, Any]:
        """Read service metadata. Does not load or invoke a model."""
        return self._request('/api/health')

    def submit(self, evidence: Sequence[Mapping[str, str]], relations: Sequence[Mapping[str, Any]], *,
               request_key: str | None = None) -> dict[str, Any]:
        key = request_key or uuid.uuid4().hex
        body = {'evidence': list(evidence), 'relations': list(relations),
                'request_key': key, 'client_tag': 'evidence-verifier-client/' + VERSION}
        try:
            return self._request('/api/verifications', body)
        except VerifierTransportError as exc:
            exc.request_key = key  # Caller can reconcile the same submission explicitly.
            raise

    def get(self, job_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request('/api/verifications/' + self._id(job_id), timeout=timeout)

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel only this job. A current request may still need to return."""
        return self._request('/api/verifications/' + self._id(job_id) + '/cancel', {})

    def advance(self, job_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Explicit paid-capable cloud step. Never retry an uncertain response."""
        return self._request('/api/verifications/' + self._id(job_id) + '/advance', {}, timeout=timeout)

    def wait(self, job_id: str, *, timeout: float = 300, poll_interval: float = .5) -> dict[str, Any]:
        if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError('Wait timeout and poll interval must be finite and positive')
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise WaitTimeout(job_id)
            try:
                job = self.get(job_id, timeout=min(self.request_timeout, remaining))
                if job.get('advance_path'):
                    expected = '/api/verifications/' + self._id(job_id) + '/advance'
                    if job['advance_path'] != expected:
                        raise VerifierError('Unexpected cloud advance path')
                    if not self.advance_cloud_jobs:
                        raise VerifierError('Cloud task awaits an explicit advance(); opt in with advance_cloud_jobs=True to let wait() progress paid steps')
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise WaitTimeout(job_id)
                    job = self.advance(job_id, timeout=min(self.request_timeout, remaining))
            except VerifierTransportError as exc:
                if time.monotonic() >= deadline:
                    raise WaitTimeout(job_id) from exc
                raise
            state = job.get('state')
            if state == 'canceled':
                raise JobCanceled(job)
            if state == 'failed':
                cls = InvalidOutput if any(e.get('state') == 'invalid_output' for e in job.get('events', [])) else JobFailed
                raise cls(job)
            if state == 'completed':
                result = job.get('result')
                if not isinstance(result, dict) or result.get('status') != 'ok':
                    raise InvalidOutput(dict(job, error='Verification output is not valid'))
                if result.get('verdict') not in ('entailed', 'missing', 'contradicted', 'ambiguous'):
                    raise InvalidOutput(dict(job, error='Unknown verification verdict'))
                if result.get('scope') != 'declared_relations_only':
                    raise InvalidOutput(dict(job, error='Verification scope is not the declared relation plan'))
                return job
            if state not in ('queued', 'running', 'cancel_requested'):
                raise VerifierError('Unknown job state: ' + str(state))
            time.sleep(min(poll_interval, max(0, deadline-time.monotonic())))

    def verify(self, evidence: Sequence[Mapping[str, str]], relations: Sequence[Mapping[str, Any]], *,
               request_key: str | None = None, timeout: float = 300) -> dict[str, Any]:
        """Verify declared relations only; no full-question or truthfulness guarantee."""
        submitted = self.submit(evidence, relations, request_key=request_key)
        return self.wait(submitted['id'], timeout=timeout)['result']
