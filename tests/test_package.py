"""Tests for the whisper_sidecar package marker.

Run with: python3 -m unittest tests.test_package
"""
import os
import sys

# Make scripts/ importable so `import whisper_sidecar` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import unittest


class WhisperSidecarPackageTest(unittest.TestCase):
    def test_package_is_importable(self):
        # Importing succeeds only if scripts/whisper_sidecar/__init__.py exists.
        import whisper_sidecar

        # A real package exposes __path__; a missing marker would raise
        # ModuleNotFoundError before reaching this assertion.
        self.assertTrue(hasattr(whisper_sidecar, "__path__"))


if __name__ == "__main__":
    unittest.main()
