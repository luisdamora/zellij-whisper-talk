"""Audio recorder backend detection.

Stdlib only. :func:`detect_backend` is a pure function of its ``backend_pref``
argument and a ``which`` predicate, so detection order and overrides are unit-
testable without touching the real ``$PATH``.

Probe order (per spec ``audio-capture-backends``): ``pw-record`` (PipeWire) ->
``parec`` (PulseAudio) -> ``arecord`` (ALSA). An explicit preference other than
``"auto"`` forces that backend, skipping detection.

Spawning each backend into a WAV-producing child is handled in transcribe.py
(task 3.1) so this module stays a thin, fully testable selector.
"""

from __future__ import annotations

import io
import shutil
import wave
from typing import Callable, List, Optional

# Probe order is authoritative: spec audio-capture-backends.
# (Design decision #6 listed parec before pw-record; the spec and tasks both
# specify pw-record first — spec wins as the acceptance criteria.)
SUPPORTED_BACKENDS = ("pw-record", "parec", "arecord")
AUTO = "auto"


class NoRecorderError(RuntimeError):
    """Raised when no supported recorder is available on PATH."""


def detect_backend(
    backend_pref: str = AUTO,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> str:
    """Return the recorder backend name to use.

    ``backend_pref`` is the resolved audio-backend preference (typically
    ``SidecarConfig.audio_backend``). Any value other than ``"auto"`` is treated
    as a forced backend and returned verbatim, skipping PATH detection.
    Otherwise probes :data:`SUPPORTED_BACKENDS` in order and returns the first
    found. Raises :class:`NoRecorderError` (listing the required tools) when none
    is available — the caller MUST exit non-zero.
    """
    forced = (backend_pref or "").strip()
    if forced and forced != AUTO:
        return forced

    for name in SUPPORTED_BACKENDS:
        if which(name):
            return name

    raise NoRecorderError(
        "No supported audio recorder found. Install one of: "
        + ", ".join(SUPPORTED_BACKENDS)
    )


# --- Per-backend WAV-producing argv (task 3.1) ---------------------------
#
# Whisper expects a WAV container. ``arecord`` and ``parec`` can emit WAV
# natively; ``pw-record`` writes RAW samples, so we capture s16le and wrap it
# into a WAV in memory via :func:`raw_pcm_to_wav` (stdlib ``wave`` supports only
# integer PCM, hence s16 rather than f32). All backends use 16 kHz mono — the
# format the transcription model expects.
_AUDIO_RATE = "16000"
_AUDIO_CHANNELS = "1"

_ARECORD_CMD = ["arecord", "-f", "S16_LE", "-c", _AUDIO_CHANNELS, "-r", _AUDIO_RATE, "-t", "wav"]
# pw-record takes the output file as a POSITIONAL argument ([options] [<file>|-]);
# it has no -o flag. build_recorder_command appends audio_path positionally.
_PWRECORD_CMD = ["pw-record", "--format", "s16", "--rate", _AUDIO_RATE, "--channels", _AUDIO_CHANNELS]
_PAREC_CMD = ["parec", "--file-format=wav", "--rate=" + _AUDIO_RATE, "--channels=" + _AUDIO_CHANNELS]

_BACKEND_COMMANDS = {
    "arecord": _ARECORD_CMD,
    "pw-record": _PWRECORD_CMD,
    "parec": _PAREC_CMD,
}


def build_recorder_command(backend: str, audio_path: str) -> List[str]:
    """Return the argv that spawns ``backend`` writing audio to ``audio_path``.

    Pure function of its arguments. For ``pw-record`` the output is RAW s16le
    (no WAV header) and the caller MUST normalize it via
    :func:`raw_pcm_to_wav` before sending it to the API. ``arecord`` and
    ``parec`` produce WAV directly. Raises :class:`ValueError` for an unknown
    backend so a misconfigured ``AUDIO_BACKEND`` fails loudly instead of
    silently spawning the wrong tool.
    """
    template = _BACKEND_COMMANDS.get(backend)
    if template is None:
        raise ValueError(
            f"Unknown audio backend {backend!r}. Supported: {', '.join(SUPPORTED_BACKENDS)}"
        )
    return template + [audio_path]


def is_wav(data: bytes) -> bool:
    """Return True iff ``data`` starts with a RIFF/WAVE header.

    Used to decide whether a captured audio blob needs raw->WAV wrapping.
    Pure; tolerates inputs shorter than 12 bytes.
    """
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def raw_pcm_to_wav(
    raw: bytes,
    *,
    channels: int = 1,
    sample_width: int = 2,
    rate: int = 16000,
) -> bytes:
    """Wrap raw PCM ``raw`` into an in-memory WAV byte string.

    Pure (no FS). Uses stdlib :mod:`wave`, which only supports integer PCM, so
    the recorder MUST capture an int format (s16 by default for ``pw-record``).
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(rate)
        writer.writeframes(raw)
    return buf.getvalue()
