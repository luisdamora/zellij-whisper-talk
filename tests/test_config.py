"""Tests for whisper_sidecar.config — env -> typed config with safe defaults.

Run with: python3 -m unittest tests.test_config
"""
import os
import sys

# Make scripts/ importable so `import whisper_sidecar` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import unittest

from whisper_sidecar.config import SidecarConfig


class SidecarConfigDefaultsTest(unittest.TestCase):
    def test_defaults_when_env_empty(self):
        cfg = SidecarConfig.from_env({})
        self.assertEqual(cfg.http_timeout, 30)
        self.assertEqual(cfg.http_retries, 3)
        self.assertEqual(cfg.max_duration, 120)
        self.assertEqual(cfg.audio_backend, "auto")
        self.assertTrue(cfg.confirm_inject)

    def test_overrides_from_env(self):
        cfg = SidecarConfig.from_env(
            {
                "HTTP_TIMEOUT": "45",
                "HTTP_RETRIES": "7",
                "MAX_DURATION": "90",
                "AUDIO_BACKEND": "arecord",
                "CONFIRM_INJECT": "false",
            }
        )
        self.assertEqual(cfg.http_timeout, 45)
        self.assertEqual(cfg.http_retries, 7)
        self.assertEqual(cfg.max_duration, 90)
        self.assertEqual(cfg.audio_backend, "arecord")
        self.assertFalse(cfg.confirm_inject)


class SidecarConfigParsingTest(unittest.TestCase):
    def test_invalid_ints_fall_back_to_defaults(self):
        cfg = SidecarConfig.from_env(
            {"HTTP_TIMEOUT": "oops", "HTTP_RETRIES": "", "MAX_DURATION": "not-a-num"}
        )
        self.assertEqual(cfg.http_timeout, 30)
        self.assertEqual(cfg.http_retries, 3)
        self.assertEqual(cfg.max_duration, 120)

    def test_confirm_inject_bool_parsing(self):
        self.assertTrue(SidecarConfig.from_env({"CONFIRM_INJECT": "true"}).confirm_inject)
        self.assertFalse(SidecarConfig.from_env({"CONFIRM_INJECT": "false"}).confirm_inject)
        # Invalid -> safe default (True).
        self.assertTrue(SidecarConfig.from_env({"CONFIRM_INJECT": "maybe"}).confirm_inject)
        # Case-insensitive.
        self.assertFalse(SidecarConfig.from_env({"CONFIRM_INJECT": "FALSE"}).confirm_inject)

    def test_audio_backend_absent_is_auto(self):
        self.assertEqual(SidecarConfig.from_env({}).audio_backend, "auto")
        self.assertEqual(
            SidecarConfig.from_env({"AUDIO_BACKEND": "pw-record"}).audio_backend,
            "pw-record",
        )

    def test_from_env_defaults_to_os_environ(self):
        # No-arg call must not crash and must use the real os.environ.
        cfg = SidecarConfig.from_env()
        self.assertIsInstance(cfg, SidecarConfig)
        self.assertIsInstance(cfg.http_timeout, int)
        self.assertIsInstance(cfg.confirm_inject, bool)


if __name__ == "__main__":
    unittest.main()
