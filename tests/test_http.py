"""Tests for whisper_sidecar.http — bounded, retrying POST with backoff.

Run with: python3 -m unittest tests.test_http
"""
import os
import sys

# Make scripts/ importable so `import whisper_sidecar` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import json
import unittest
from io import BytesIO
from urllib.error import HTTPError, URLError

from whisper_sidecar.http import (
    backoff_delay,
    is_retryable_status,
    post_with_retry,
)


class RetryPolicyTest(unittest.TestCase):
    def test_429_and_5xx_are_retryable(self):
        self.assertTrue(is_retryable_status(429))
        self.assertTrue(is_retryable_status(500))
        self.assertTrue(is_retryable_status(503))
        self.assertTrue(is_retryable_status(599))

    def test_non_retryable_4xx_and_success_are_not_retryable(self):
        self.assertFalse(is_retryable_status(400))
        self.assertFalse(is_retryable_status(404))
        self.assertFalse(is_retryable_status(401))
        self.assertFalse(is_retryable_status(200))


class BackoffDelayTest(unittest.TestCase):
    def test_exponential_sequence_is_base_times_factor_pow_attempt(self):
        # Spec: base 1s, factor 2 -> 1, 2, 4.
        self.assertAlmostEqual(backoff_delay(0), 1.0)
        self.assertAlmostEqual(backoff_delay(1), 2.0)
        self.assertAlmostEqual(backoff_delay(2), 4.0)


def _fake_response(body):
    """A minimal context manager mimicking urllib's addinfourl return value."""
    payload = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    return _Resp()


def _http_error(code):
    return HTTPError("https://example.test", code, f"err{code}", {}, BytesIO(b"{}"))


class _FakeOpener:
    """Records calls and serves a scripted sequence of outcomes.

    Each outcome is either a dict (success body) or an Exception to raise.
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []  # list of (url, timeout)

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _fake_response(outcome)


class PostWithRetryTest(unittest.TestCase):
    def test_success_first_attempt_no_sleep(self):
        sleeps = []
        opener = _FakeOpener([{"text": "hello"}])
        result = post_with_retry(
            "https://example.test/api",
            {"input": "x"},
            key="sk-test",
            retries=3,
            timeout=30,
            sleep=sleeps.append,
            open_request=opener,
        )
        self.assertEqual(result, {"text": "hello"})
        self.assertEqual(sleeps, [])
        self.assertEqual(len(opener.calls), 1)

    def test_transient_then_success_retries_with_growing_backoff(self):
        # Spec scenario: 503, 503, 200 -> succeeds on attempt 3.
        sleeps = []
        opener = _FakeOpener(
            [_http_error(503), _http_error(503), {"text": "ok"}]
        )
        result = post_with_retry(
            "https://example.test/api",
            {"input": "x"},
            key="sk-test",
            retries=3,
            timeout=30,
            sleep=sleeps.append,
            open_request=opener,
        )
        self.assertEqual(result, {"text": "ok"})
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(len(opener.calls), 3)

    def test_non_retryable_4xx_raises_immediately_without_sleep(self):
        sleeps = []
        opener = _FakeOpener([_http_error(400)])
        with self.assertRaises(HTTPError) as ctx:
            post_with_retry(
                "https://example.test/api",
                {"input": "x"},
                key="sk-test",
                retries=3,
                timeout=30,
                sleep=sleeps.append,
                open_request=opener,
            )
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(opener.calls), 1)

    def test_429_is_retryable_then_succeeds(self):
        sleeps = []
        opener = _FakeOpener([_http_error(429), {"text": "ok"}])
        result = post_with_retry(
            "https://example.test/api",
            body={"x": 1},
            key="sk-test",
            retries=3,
            timeout=10,
            sleep=sleeps.append,
            open_request=opener,
        )
        self.assertEqual(result, {"text": "ok"})
        self.assertEqual(sleeps, [1.0])

    def test_urlerror_network_failure_is_retried(self):
        sleeps = []
        opener = _FakeOpener([URLError("no route"), {"text": "late"}])
        result = post_with_retry(
            "https://example.test/api",
            body={"x": 1},
            key="sk-test",
            retries=3,
            timeout=5,
            sleep=sleeps.append,
            open_request=opener,
        )
        self.assertEqual(result, {"text": "late"})
        self.assertEqual(sleeps, [1.0])

    def test_retries_exhausted_raises_last_error(self):
        # Spec scenario: all 3 attempts return 503 -> raise after backoff.
        sleeps = []
        opener = _FakeOpener([_http_error(503), _http_error(503), _http_error(503)])
        with self.assertRaises(HTTPError) as ctx:
            post_with_retry(
                "https://example.test/api",
                body={"x": 1},
                key="sk-test",
                retries=3,
                timeout=30,
                sleep=sleeps.append,
                open_request=opener,
            )
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(len(opener.calls), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_timeout_forwarded_to_opener(self):
        opener = _FakeOpener([{"text": "ok"}])
        post_with_retry(
            "https://example.test/api",
            body={"x": 1},
            key="sk-test",
            timeout=12,
            open_request=opener,
        )
        self.assertEqual(opener.calls[0][1], 12)

    def test_authorization_header_carries_bearer_key(self):
        seen = {}

        def capturing_opener(request, timeout):
            seen["auth"] = request.headers.get("Authorization")
            return _fake_response({"text": "ok"})

        post_with_retry(
            "https://example.test/api",
            body={"x": 1},
            key="sk-secret-123",
            open_request=capturing_opener,
        )
        self.assertEqual(seen["auth"], "Bearer sk-secret-123")


if __name__ == "__main__":
    unittest.main()
