"""Tests for whisper_sidecar.security — sanitize, key-file, umask, temp paths.

Run with: python3 -m unittest tests.test_security
"""
import os
import shutil
import stat
import sys
import tempfile

# Make scripts/ importable so `import whisper_sidecar` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import unittest

from whisper_sidecar.security import sanitize


class SanitizeTest(unittest.TestCase):
    def test_strips_csi_color_codes(self):
        # Spec: ANSI color codes present -> \x1b[31mred\x1b[0m becomes "red".
        result = sanitize("\x1b[31mred\x1b[0m")
        self.assertEqual(result, "red")
        # Zero ESC bytes remain.
        self.assertNotIn("\x1b", result)

    def test_strips_c0_control_bytes_but_keeps_newline_and_tab(self):
        # Spec: backspace, bell, form-feed removed; \n and \t preserved.
        result = sanitize("a\x08b\x07c\x0cd\ne\tf")
        self.assertEqual(result, "abcd\ne\tf")

    def test_strips_osc_sequences_bell_and_st_terminated(self):
        # OSC title set: ESC ] 0 ; title BEL  and  ESC ] 0 ; title ESC backslash
        result = sanitize("x\x1b]0;my title\x07y\x1b]0;other\x1b\\z")
        self.assertEqual(result, "xyz")
        self.assertNotIn("\x1b", result)

    def test_strips_cursor_and_erase_sequences(self):
        # CSI variants: cursor up, erase line, 256-color, cursor position.
        result = sanitize("\x1b[1A\x1b[2K\x1b[38;5;200mhi\x1b[1;1H")
        self.assertEqual(result, "hi")

    def test_plain_text_unchanged(self):
        result = sanitize("hello world\n\tindented")
        self.assertEqual(result, "hello world\n\tindented")

    def test_empty_string_returns_empty(self):
        self.assertEqual(sanitize(""), "")

    def test_preserves_utf8_multibyte_accents_and_emoji(self):
        # Critical: cleanup prompt is Rioplatense Spanish; accents/emoji are
        # codepoints >= 0x80 and MUST survive (never dropped).
        result = sanitize("\x1b[31m Voces áéí 🎤\x1b[0m")
        self.assertEqual(result, " Voces áéí 🎤")

    def test_no_escape_bytes_remain_in_mixed_input(self):
        result = sanitize("\x1b[31mred\x1b[0m\x00\x07normal")
        # No ESC (0x1b), no NUL, no BEL anywhere in output.
        self.assertNotIn("\x1b", result)
        self.assertNotIn("\x00", result)
        self.assertNotIn("\x07", result)
        self.assertEqual(result, "rednormal")


class KeyFileTest(unittest.TestCase):
    def test_write_key_file_creates_0600_file_with_content(self):
        from whisper_sidecar.security import write_key_file

        d = tempfile.mkdtemp()
        try:
            path = write_key_file("secret-key", directory=d)
            mode = stat.S_IMODE(os.lstat(path).st_mode)
            self.assertEqual(mode, 0o600)
            with open(path) as f:
                self.assertEqual(f.read(), "secret-key")
            self.assertIn(d, path)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_write_key_file_unique_names_across_calls(self):
        from whisper_sidecar.security import write_key_file

        d = tempfile.mkdtemp()
        try:
            p1 = write_key_file("k1", directory=d)
            p2 = write_key_file("k2", directory=d)
            self.assertNotEqual(p1, p2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ResolveTempDirTest(unittest.TestCase):
    def test_xdg_runtime_dir_used_when_set_and_present(self):
        from whisper_sidecar.security import resolve_temp_dir

        # dir_exists=lambda _: True simulates the dir existing on disk.
        result = resolve_temp_dir({"XDG_RUNTIME_DIR": "/run/user/1000"}, dir_exists=lambda _: True)
        self.assertEqual(result, "/run/user/1000")

    def test_falls_back_to_tmp_when_xdg_unset(self):
        from whisper_sidecar.security import resolve_temp_dir

        self.assertEqual(resolve_temp_dir({}, dir_exists=lambda _: True), "/tmp")

    def test_falls_back_to_tmp_when_xdg_dir_missing(self):
        from whisper_sidecar.security import resolve_temp_dir

        # XDG set but the path does not exist on disk -> /tmp fallback.
        result = resolve_temp_dir(
            {"XDG_RUNTIME_DIR": "/run/user/1000"}, dir_exists=lambda _: False
        )
        self.assertEqual(result, "/tmp")

    def test_falls_back_to_tmp_when_xdg_empty(self):
        from whisper_sidecar.security import resolve_temp_dir

        self.assertEqual(resolve_temp_dir({"XDG_RUNTIME_DIR": ""}, dir_exists=lambda _: True), "/tmp")


class ReadKeyFileTest(unittest.TestCase):
    def test_raises_file_not_found_when_missing(self):
        from whisper_sidecar.security import read_key_file

        with self.assertRaises(FileNotFoundError):
            read_key_file("/nonexistent/zellij-voice-key-deadbeef")

    def test_raises_value_error_when_empty(self):
        from whisper_sidecar.security import read_key_file, write_key_file

        d = tempfile.mkdtemp()
        try:
            path = write_key_file("   \n  ", directory=d)
            with self.assertRaises(ValueError):
                read_key_file(path)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_returns_stripped_key_when_present(self):
        from whisper_sidecar.security import read_key_file, write_key_file

        d = tempfile.mkdtemp()
        try:
            path = write_key_file("  sk-real-key\n", directory=d)
            self.assertEqual(read_key_file(path), "sk-real-key")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ApplySecureUmaskTest(unittest.TestCase):
    def test_sets_umask_to_owner_only(self):
        from whisper_sidecar.security import apply_secure_umask

        prev = os.umask(0o022)  # known starting point
        try:
            returned = apply_secure_umask()
            self.assertEqual(returned, 0o022)
            # Read back the CURRENT umask (umask(0) returns current then resets).
            current = os.umask(0)
            os.umask(current)  # restore the 0o077 just set
            self.assertEqual(current, 0o077)
        finally:
            os.umask(prev)  # restore original for other tests


if __name__ == "__main__":
    unittest.main()
