"""Tests for whisper_sidecar.recorders — backend detection.

Run with: python3 -m unittest tests.test_recorders
"""
import os
import sys

# Make scripts/ importable so `import whisper_sidecar` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import unittest

from whisper_sidecar.recorders import SUPPORTED_BACKENDS, detect_backend
from whisper_sidecar.recorders import NoRecorderError


def _which_factory(present):
    """Return a fake ``which`` that reports only the binaries in ``present``."""
    present_set = set(present)

    def _which(name):
        return f"/usr/bin/{name}" if name in present_set else None

    return _which


class DetectBackendProbeOrderTest(unittest.TestCase):
    def test_pw_record_preferred_when_present(self):
        # Spec: PipeWire preferred -> pw-record wins when on PATH.
        result = detect_backend(which=_which_factory(["pw-record", "parec", "arecord"]))
        self.assertEqual(result, "pw-record")

    def test_parec_when_pw_record_absent(self):
        result = detect_backend(which=_which_factory(["parec", "arecord"]))
        self.assertEqual(result, "parec")

    def test_arecord_when_only_arecord_present(self):
        result = detect_backend(which=_which_factory(["arecord"]))
        self.assertEqual(result, "arecord")


class DetectBackendOverrideTest(unittest.TestCase):
    def test_forced_arecord_skips_detection_even_when_pw_record_present(self):
        # Spec scenario: AUDIO_BACKEND=arecord, pw-record also present -> arecord.
        result = detect_backend(
            backend_pref="arecord",
            which=_which_factory(["pw-record", "parec", "arecord"]),
        )
        self.assertEqual(result, "arecord")

    def test_forced_pw_record_honored(self):
        result = detect_backend(
            backend_pref="pw-record",
            which=_which_factory(["arecord"]),  # pw-record NOT on PATH
        )
        self.assertEqual(result, "pw-record")

    def test_auto_runs_detection(self):
        result = detect_backend(
            backend_pref="auto",
            which=_which_factory(["parec", "arecord"]),
        )
        self.assertEqual(result, "parec")

    def test_empty_or_whitespace_pref_runs_detection(self):
        for pref in ("", "   "):
            result = detect_backend(
                backend_pref=pref, which=_which_factory(["arecord"])
            )
            self.assertEqual(result, "arecord")


class DetectBackendNoneAvailableTest(unittest.TestCase):
    def test_raises_listing_required_tools_when_none_present(self):
        with self.assertRaises(NoRecorderError) as ctx:
            detect_backend(which=_which_factory([]))
        message = str(ctx.exception)
        # All three tools named so the user knows what to install.
        for name in SUPPORTED_BACKENDS:
            self.assertIn(name, message)

    def test_supported_backends_order_is_pw_record_parec_arecord(self):
        # Lock the probe-order contract (spec authority over design #6).
        self.assertEqual(SUPPORTED_BACKENDS, ("pw-record", "parec", "arecord"))


class BuildRecorderCommandTest(unittest.TestCase):
    """Per-backend WAV-producing argv (task 3.1 spawn-WAV-recorder contract)."""

    def test_arecord_produces_native_wav_16k_mono_s16le(self):
        from whisper_sidecar.recorders import build_recorder_command

        cmd = build_recorder_command("arecord", "/tmp/a.wav")
        self.assertEqual(cmd[0], "arecord")
        # 16-bit LE, mono, 16kHz, WAV container, file last (status quo contract).
        self.assertIn("-f", cmd)
        self.assertEqual(cmd[cmd.index("-f") + 1], "S16_LE")
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "1")
        self.assertIn("-r", cmd)
        self.assertEqual(cmd[cmd.index("-r") + 1], "16000")
        self.assertIn("-t", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "wav")
        self.assertEqual(cmd[-1], "/tmp/a.wav")

    def test_pw_record_uses_s16_16k_mono_and_positional_output(self):
        # pw-record writes RAW samples (no WAV header) -> s16 keeps it
        # wrappable by stdlib `wave` (which only supports int PCM).
        from whisper_sidecar.recorders import build_recorder_command

        cmd = build_recorder_command("pw-record", "/tmp/raw.pcm")
        self.assertEqual(cmd[0], "pw-record")
        self.assertIn("--format", cmd)
        self.assertEqual(cmd[cmd.index("--format") + 1], "s16")
        self.assertIn("--rate", cmd)
        self.assertEqual(cmd[cmd.index("--rate") + 1], "16000")
        self.assertIn("--channels", cmd)
        self.assertEqual(cmd[cmd.index("--channels") + 1], "1")
        # pw-record has no -o flag; the output file is positional (last arg).
        self.assertNotIn("-o", cmd)
        self.assertEqual(cmd[-1], "/tmp/raw.pcm")

    def test_parec_uses_native_wav_file_format(self):
        # parec can emit WAV directly via --file-format=wav.
        from whisper_sidecar.recorders import build_recorder_command

        cmd = build_recorder_command("parec", "/tmp/p.wav")
        self.assertEqual(cmd[0], "parec")
        self.assertIn("--file-format=wav", cmd)
        self.assertEqual(cmd[-1], "/tmp/p.wav")

    def test_unknown_backend_raises_value_error(self):
        from whisper_sidecar.recorders import build_recorder_command

        with self.assertRaises(ValueError):
            build_recorder_command("sox", "/tmp/x.wav")


class IsWavTest(unittest.TestCase):
    """RIFF/WAVE magic detection (decides whether raw->WAV conversion needed)."""

    def test_wav_data_detected(self):
        from whisper_sidecar.recorders import is_wav

        self.assertTrue(is_wav(b"RIFF\x00\x00\x00\x00WAVEfmt "))

    def test_raw_pcm_not_detected(self):
        from whisper_sidecar.recorders import is_wav

        self.assertFalse(is_wav(b"\x00\x01\x02\x03raw-bytes"))

    def test_empty_and_short_data_not_detected(self):
        from whisper_sidecar.recorders import is_wav

        self.assertFalse(is_wav(b""))
        self.assertFalse(is_wav(b"RIFF"))  # too short for WAVE header check


class RawPcmToWavTest(unittest.TestCase):
    """Convert raw PCM (pw-record s16le) to in-memory WAV via stdlib wave."""

    def test_converts_raw_s16le_to_valid_wav_bytes(self):
        import wave
        from io import BytesIO

        from whisper_sidecar.recorders import is_wav, raw_pcm_to_wav

        # 4 bytes = 2 stereo-ish frames of s16le; we claim mono so 2 frames.
        raw = b"\x01\x00\x02\x00"
        wav = raw_pcm_to_wav(raw, channels=1, sample_width=2, rate=16000)
        self.assertTrue(is_wav(wav))
        with wave.open(BytesIO(wav), "rb") as r:
            self.assertEqual(r.getnchannels(), 1)
            self.assertEqual(r.getsampwidth(), 2)
            self.assertEqual(r.getframerate(), 16000)
            self.assertEqual(r.readframes(2), raw)

    def test_roundtrip_empty_raw(self):
        from whisper_sidecar.recorders import is_wav, raw_pcm_to_wav

        wav = raw_pcm_to_wav(b"", channels=1, sample_width=2, rate=16000)
        self.assertTrue(is_wav(wav))


if __name__ == "__main__":
    unittest.main()
