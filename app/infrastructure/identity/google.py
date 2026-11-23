"""Google identity token verification adapter."""

import json
import re
from threading import Lock

import requests
from cachecontrol import CacheControl
from cachecontrol.cache import DictCache
from cachecontrol.heuristics import BaseHeuristic
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from app.core.execution import ExecutionDeadlineExceeded, remaining_time
from app.core.telemetry import timed
from app.services.auth_ports import (
    GoogleIdentityTokenError,
    GoogleVerificationUnavailable,
)

_CERTIFICATES_URL = "https://www.googleapis.com/oauth2/v1/certs"


class CertificateFreshness(BaseHeuristic):
    """Conservatively account for upstream Age, which CacheControl ignores."""

    def warning(self, response):
        return None

    def update_headers(self, response):
        age = response.headers.get("Age", "0")
        if not age.isdigit():
            return {"cache-control": "no-store"}
        if int(age) == 0:
            return {}
        control = response.headers.get("Cache-Control", "")
        match = re.search(r'(?i)(?:^|,)\s*max-age\s*=\s*"?(\d+)"?\s*(?:,|$)', control)
        if match is None:
            return {"cache-control": "no-store"}
        remaining = max(0, int(match[1]) - int(age))
        start, end = match.span(1)
        return {"cache-control": control[:start] + str(remaining) + control[end:]}


class GoogleCertificateRequest:
    """Cache public certificates only; never cache identities or share sessions.

    CacheControl implements HTTP freshness/revalidation. A single-flight lock
    prevents concurrent cache misses from fetching the same certificates. Each
    session is used by one thread, then closed before releasing the lock.
    """

    def __init__(self) -> None:
        self._cache = DictCache()
        self._lock = Lock()

    def __call__(self, url, method="GET", body=None, headers=None, timeout=10, **kwargs):
        if url != _CERTIFICATES_URL or method != "GET" or body is not None or headers:
            raise TransportError("Only public Google certificate requests are allowed")
        if not self._lock.acquire(timeout=remaining_time(10)):
            raise ExecutionDeadlineExceeded("Certificate fetch admission expired")
        try:
            with CacheControl(
                requests.Session(), cache=self._cache, heuristic=CertificateFreshness()
            ) as session:
                response = Request(session=session)(
                    url, method="GET", timeout=remaining_time(min(timeout, 10)),
                    allow_redirects=False,
                )
                if response.status != 200:
                    self._cache.delete(url)
                    raise TransportError("Google certificate endpoint is unavailable")
                try:
                    certificates = json.loads(response.data)
                except ValueError as error:
                    self._cache.delete(url)
                    raise TransportError("Invalid certificate response") from error
                if not isinstance(certificates, dict) or not certificates:
                    self._cache.delete(url)
                    raise TransportError("Invalid certificate response")
                return response
        finally:
            self._lock.release()


_certificate_request = GoogleCertificateRequest()


@timed("auth.google_verification")
def verify_google_id_token(token: str, audience: str) -> dict[str, object]:
    try:
        return id_token.verify_oauth2_token(token, _certificate_request, audience)
    except TransportError as error:
        raise GoogleVerificationUnavailable from error
    except (ValueError, GoogleAuthError) as error:
        raise GoogleIdentityTokenError from error
