"""Typed sidecar configuration parsed from environment variables.

All keys are optional with safe defaults, so the plugin can pass a subset (or
none) and the sidecar still behaves sensibly (backward compatible). Stdlib
only. Pure: :meth:`SidecarConfig.from_env` is a pure function of its ``env``
argument (defaulting to :data:`os.environ`), which keeps tests deterministic.

Defaults (mirrors the Rust ``PluginConfig``):
  - ``http_timeout``   30        (env ``HTTP_TIMEOUT``)
  - ``http_retries``   3         (env ``HTTP_RETRIES``)
  - ``max_duration``   120       (env ``MAX_DURATION``)
  - ``audio_backend``  "auto"    (env ``AUDIO_BACKEND``)
  - ``confirm_inject`` True      (env ``CONFIRM_INJECT``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional

DEFAULT_HTTP_TIMEOUT = 30
DEFAULT_HTTP_RETRIES = 3
DEFAULT_MAX_DURATION = 120
DEFAULT_AUDIO_BACKEND = "auto"
DEFAULT_CONFIRM_INJECT = True


@dataclass(frozen=True)
class SidecarConfig:
    """Resolved, typed sidecar configuration.

    ``confirm_inject`` is owned by the WASM plugin's confirmation gate but is
    mirrored here so both sides share a single canonical config surface.
    """

    http_timeout: int
    http_retries: int
    max_duration: int
    audio_backend: str
    confirm_inject: bool

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SidecarConfig":
        """Build a :class:`SidecarConfig` from an environment mapping.

        ``env`` defaults to :data:`os.environ`. Missing keys and malformed
        numeric/boolean values fall back to their safe defaults instead of
        raising, so the sidecar never crashes on bad config.
        """
        source: Mapping[str, str] = (
            MappingProxyType(os.environ) if env is None else env
        )
        return cls(
            http_timeout=_parse_int(source, "HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
            http_retries=_parse_int(source, "HTTP_RETRIES", DEFAULT_HTTP_RETRIES),
            max_duration=_parse_int(source, "MAX_DURATION", DEFAULT_MAX_DURATION),
            audio_backend=source.get("AUDIO_BACKEND", DEFAULT_AUDIO_BACKEND),
            confirm_inject=_parse_bool(source, "CONFIRM_INJECT", DEFAULT_CONFIRM_INJECT),
        )


def _parse_int(env: Mapping[str, str], key: str, default: int) -> int:
    """Parse an integer env value, returning ``default`` on absence/failure."""
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return default


def _parse_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    """Parse a boolean env value (``"true"``/``"false"``, case-insensitive).

    Any other value (including absence) returns ``default``.
    """
    raw = env.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return default
