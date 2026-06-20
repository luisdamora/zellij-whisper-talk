"""Bounded, retrying HTTP POST with exponential backoff.

Stdlib only. The retry *policy* (which statuses retry, how long to wait) lives
in pure functions (:func:`is_retryable_status`, :func:`backoff_delay`) so it can
be unit-tested without any mocking. The orchestration (:func:`post_with_retry`)
accepts injected ``sleep`` and ``open_request`` callables for deterministic
testing of the retry sequence and timing.

Contract:
  - Every request passes ``timeout`` (default 30s) so a hung endpoint cannot
    block longer than configured.
  - Transient failures (``URLError`` incl. timeouts, HTTP 429, 5xx) are retried
    up to ``retries`` total attempts with exponential backoff (base 1s, factor2).
  - Non-retryable 4xx fail immediately (no retry, no sleep).
  - On exhaustion the last error is re-raised so the caller can exit non-zero
    and clean up audio/lock files (handled by transcribe.py + lifecycle.py).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
BACKOFF_BASE = 1.0
BACKOFF_FACTOR = 2.0

USER_AGENT_TITLE = "Zellij Voice Input"
REFERER = "https://github.com/page-agent/page-agent"


def is_retryable_status(code: int) -> bool:
    """Return True for transient HTTP status codes: 429 and any 5xx."""
    return code == 429 or 500 <= code < 600


def backoff_delay(
    attempt: int,
    base: float = BACKOFF_BASE,
    factor: float = BACKOFF_FACTOR,
) -> float:
    """Exponential backoff seconds for a 0-indexed failed ``attempt``."""
    return base * (factor ** attempt)


def post_with_retry(
    url: str,
    body: dict,
    key: str,
    *,
    retries: int = DEFAULT_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    open_request: Optional[Callable] = None,
) -> dict:
    """POST ``body`` (JSON) to ``url`` with bearer ``key``, retrying transients.

    ``retries`` is the maximum number of attempts (including the first).
    ``open_request`` defaults to :func:`urllib.request.urlopen` and must accept
    ``(request, timeout)`` and return a context manager whose ``read()`` yields
    the response body bytes. Returns the parsed JSON body as a dict. Raises the
    last error when all attempts are exhausted (caller exits non-zero).
    """
    opener = open_request if open_request is not None else urllib.request.urlopen
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": REFERER,
            "X-Title": USER_AGENT_TITLE,
        },
        method="POST",
    )

    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            resp = opener(request, timeout)
        except urllib.error.HTTPError as exc:
            if not is_retryable_status(exc.code):
                raise  # Non-retryable 4xx: fail fast, no backoff.
            last_exc = exc
        except urllib.error.URLError as exc:
            last_exc = exc  # Network/timeout: transient, retry.
        else:
            with resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8"))

        # Back off before the next attempt (skip after the final attempt).
        if attempt < retries - 1:
            sleep(backoff_delay(attempt))

    assert last_exc is not None  # retries >= 1 guarantees at least one attempt
    raise last_exc
