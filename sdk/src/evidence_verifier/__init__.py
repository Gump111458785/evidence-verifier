"""Evidence Verifier HTTP client. No model or training dependencies."""
from ._version import VERSION as __version__
from .client import (
    VerifierClient, VerifierError, VerifierHTTPError, VerifierTransportError,
    ModelUnavailable, JobFailed, JobCanceled, InvalidOutput, WaitTimeout,
)

__all__ = ['VerifierClient', 'VerifierError', 'VerifierHTTPError', 'VerifierTransportError',
           'ModelUnavailable', 'JobFailed', 'JobCanceled', 'InvalidOutput', 'WaitTimeout']
