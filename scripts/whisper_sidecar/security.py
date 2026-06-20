"""Security helpers: output sanitization, key-file handling, temp paths, umask.

Stdlib only. The :func:`sanitize` function is pure and deterministic; the
key-file/temp helpers carry side effects (FS + umask) and are kept narrow.

Strip policy (mirrors the Rust ``sanitize_terminal_text``):
  - CSI sequences (``ESC [ ... final``)
  - OSC sequences (``ESC ] ... BEL`` or ``ESC ] ... ST``)
  - C0 control bytes (``0x00``-``0x1f``) except newline (``0x0a``) and tab
    (``0x09``); DEL (``0x7f``) is also removed.
UTF-8 multi-byte content (bytes ``>= 0x80``) is never touched, so accents and
emoji survive intact.
"""

from __future__ import annotations

import os
import re
import secrets
from typing import Mapping, Optional

# Control byte constants built from chr() so the source has no \xHH escapes
# that could be mangled by tooling.
_ESC = chr(0x1B)  # ESC (CSI/OSC introducer)
_BEL = chr(0x07)  # BEL (one OSC terminator)
_ST = _ESC + "\\"  # String Terminator (ESC backslash)

# CSI: ESC [  <param 0x30-0x3F>  <interim 0x20-0x2F>  <final 0x40-0x7E>
_CSI_RE = re.compile(_ESC + r"\[[0-?]*[ -/]*[@-~]")
# OSC: ESC ]  <string>  (BEL  OR  ST)
_OSC_RE = re.compile(_ESC + r"\].*?(?:" + _BEL + "|" + re.escape(_ST) + ")", re.DOTALL)
# C0 control bytes 0x00-0x1f except newline (0x0a) and tab (0x09), plus DEL.
_CTRL_CLASS = (
    "[" + chr(0) + "-" + chr(8) + chr(11) + "-" + chr(31) + chr(127) + "]"
)
_CONTROL_RE = re.compile(_CTRL_CLASS)


def sanitize(text: str) -> str:
    """Return ``text`` with terminal escape sequences and C0 control bytes removed.

    Newlines and tabs are preserved. UTF-8 multi-byte sequences are left intact.
    """
    text = _CSI_RE.sub("", text)
    text = _OSC_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return text


# --- Secret protection: umask, temp paths, key file ----------------------

KEY_FILE_MODE = 0o600
OWNER_ONLY_UMASK = 0o077
_KEY_FILE_PREFIX = "zellij-voice-key-"


def apply_secure_umask() -> int:
    """Set ``umask(0o077)`` so all subsequently created files are owner-only.

    Returns the previous umask (callers in tests may restore it). Per the
    secret-protection spec this MUST be called before any temp file is created.
    """
    return os.umask(OWNER_ONLY_UMASK)


def resolve_temp_dir(
    env: Optional[Mapping[str, str]] = None,
    *,
    dir_exists=os.path.isdir,
) -> str:
    """Return the directory for owner-only temp files.

    Prefers ``$XDG_RUNTIME_DIR`` when set and present (tmpfs, 0700,
    user-session-scoped); falls back to ``/tmp``. Pure function of ``env`` and
    the ``dir_exists`` predicate, so resolution is unit-testable without FS.
    """
    source = os.environ if env is None else env
    xdg = source.get("XDG_RUNTIME_DIR")
    if xdg and dir_exists(xdg):
        return xdg
    return "/tmp"


def write_file_mode(path: str, data: str, mode: int = KEY_FILE_MODE) -> None:
    """Write ``data`` to ``path`` with an explicit creation ``mode``.

    Uses :func:`os.open` so the mode is applied at creation (no chmod race).
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)


def write_key_file(
    key: str,
    *,
    directory: Optional[str] = None,
) -> str:
    """Write ``key`` to a fresh ``0600`` file and return its path.

    ``directory`` defaults to :func:`resolve_temp_dir`. The filename is unique
    per call (pid + random token) so concurrent runs never collide.
    """
    target = directory if directory is not None else resolve_temp_dir()
    path = os.path.join(target, f"{_KEY_FILE_PREFIX}{os.getpid()}-{secrets.token_hex(8)}")
    write_file_mode(path, key, KEY_FILE_MODE)
    return path


def read_key_file(path: str) -> str:
    """Read and return the stripped API key from ``path``.

    Raises :class:`FileNotFoundError` if the file is absent and :class:`ValueError`
    if it is empty — either case means the caller MUST exit non-zero without
    making any API call (secret-protection: never proceed without a real key).
    """
    with open(path, "r", encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        raise ValueError(f"Key file {path} is empty")
    return key
