"""Tests for whisper_sidecar.lifecycle — deterministic recorder teardown.

Run with: python3 -m unittest tests.test_lifecycle
"""
import os
import sys

# Make scripts/ importable so `import whisper_sidecar` resolves.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import unittest

from whisper_sidecar.lifecycle import Lifecycle


class _FakeChild:
    def __init__(self):
        self.killed = False


class _Recorder:
    """Captures side effects: kills, file removals, atexit/signal wiring."""

    def __init__(self):
        self.killed = []
        self.removed = []
        self.atexit_registered = []
        self.signals_set = {}  # signum -> handler

    def kill_child(self, child):
        self.killed.append(child)
        child.killed = True

    def remove_file(self, path):
        self.removed.append(path)

    def atexit_register(self, fn):
        self.atexit_registered.append(fn)

    def signal_setter(self):
        def _set(signum, handler):
            self.signals_set[signum] = handler
        return _set


class LifecycleInstallTest(unittest.TestCase):
    def test_install_registers_atexit_and_sigterm_sigint(self):
        import signal

        rec = _Recorder()
        lc = Lifecycle(
            atexit_register=rec.atexit_register,
            signal_setter=rec.signal_setter(),
            kill_child=rec.kill_child,
            remove_file=rec.remove_file,
        )
        lc.install()
        self.assertEqual(len(rec.atexit_registered), 1)
        self.assertIn(signal.SIGTERM, rec.signals_set)
        self.assertIn(signal.SIGINT, rec.signals_set)

    def test_install_is_idempotent(self):
        rec = _Recorder()
        lc = Lifecycle(
            atexit_register=rec.atexit_register,
            signal_setter=rec.signal_setter(),
        )
        lc.install()
        lc.install()
        lc.install()
        self.assertEqual(len(rec.atexit_registered), 1)


class LifecycleCleanupTest(unittest.TestCase):
    def test_signal_handler_kills_child_and_removes_tracked_files(self):
        import signal

        rec = _Recorder()
        child = _FakeChild()
        lc = Lifecycle(
            atexit_register=rec.atexit_register,
            signal_setter=rec.signal_setter(),
            kill_child=rec.kill_child,
            remove_file=rec.remove_file,
        )
        lc.register_recorder(child)
        lc.track("/tmp/zellij-voice.lock")
        lc.track("/tmp/zellij-voice.txt")
        lc.install()
        # Simulate SIGTERM delivery.
        handler = rec.signals_set[signal.SIGTERM]
        handler(signal.SIGTERM, None)
        self.assertTrue(child.killed)
        self.assertEqual(rec.removed, ["/tmp/zellij-voice.lock", "/tmp/zellij-voice.txt"])

    def test_atexit_handler_runs_cleanup(self):
        rec = _Recorder()
        child = _FakeChild()
        lc = Lifecycle(
            atexit_register=rec.atexit_register,
            signal_setter=rec.signal_setter(),
            kill_child=rec.kill_child,
            remove_file=rec.remove_file,
        )
        lc.register_recorder(child)
        lc.track("/tmp/zellij-voice.wav")
        lc.install()
        # Simulate interpreter shutdown invoking the atexit callback.
        rec.atexit_registered[0]()
        self.assertTrue(child.killed)
        self.assertEqual(rec.removed, ["/tmp/zellij-voice.wav"])

    def test_cleanup_is_idempotent_child_killed_once(self):
        rec = _Recorder()
        child = _FakeChild()
        lc = Lifecycle(
            kill_child=rec.kill_child, remove_file=rec.remove_file,
            atexit_register=rec.atexit_register, signal_setter=rec.signal_setter(),
        )
        lc.register_recorder(child)
        lc.track("/tmp/zellij-voice.lock")
        lc.cleanup()
        lc.cleanup()  # second call must not double-kill or re-remove
        self.assertEqual(rec.killed, [child])
        self.assertEqual(rec.removed, ["/tmp/zellij-voice.lock"])

    def test_cleanup_noop_when_no_child_and_no_files(self):
        rec = _Recorder()
        lc = Lifecycle(
            kill_child=rec.kill_child, remove_file=rec.remove_file,
            atexit_register=rec.atexit_register, signal_setter=rec.signal_setter(),
        )
        lc.cleanup()
        self.assertEqual(rec.killed, [])
        self.assertEqual(rec.removed, [])

    def test_track_ignores_none_and_empty(self):
        rec = _Recorder()
        lc = Lifecycle(
            kill_child=rec.kill_child, remove_file=rec.remove_file,
            atexit_register=rec.atexit_register, signal_setter=rec.signal_setter(),
        )
        lc.track(None)
        lc.track("")
        lc.track("/tmp/zellij-voice.lock")
        lc.cleanup()
        self.assertEqual(rec.removed, ["/tmp/zellij-voice.lock"])

    def test_register_recorder_replaces_child(self):
        rec = _Recorder()
        old = _FakeChild()
        new = _FakeChild()
        lc = Lifecycle(
            kill_child=rec.kill_child, remove_file=rec.remove_file,
            atexit_register=rec.atexit_register, signal_setter=rec.signal_setter(),
        )
        lc.register_recorder(old)
        lc.register_recorder(new)
        lc.cleanup()
        # Only the currently-registered child is killed.
        self.assertEqual(rec.killed, [new])
        self.assertFalse(old.killed)
        self.assertTrue(new.killed)

    def test_sigint_handler_cleans_up_then_reraises(self):
        import signal

        rec = _Recorder()
        child = _FakeChild()
        lc = Lifecycle(
            kill_child=rec.kill_child, remove_file=rec.remove_file,
            atexit_register=rec.atexit_register, signal_setter=rec.signal_setter(),
        )
        lc.register_recorder(child)
        lc.install()
        handler = rec.signals_set[signal.SIGINT]
        with self.assertRaises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
        self.assertTrue(child.killed)
        # SIG_DFL restored so the process can exit with conventional status.
        self.assertEqual(rec.signals_set[signal.SIGINT], signal.SIG_DFL)

    def test_cleanup_tolerates_kill_and_remove_failures(self):
        def raising_kill(_child):
            raise OSError("boom")

        def raising_remove(_path):
            raise OSError("boom")

        child = _FakeChild()
        lc = Lifecycle(
            kill_child=raising_kill, remove_file=raising_remove,
            atexit_register=lambda _fn: None, signal_setter=lambda *_a: None,
        )
        lc.register_recorder(child)
        lc.track("/tmp/zellij-voice.lock")
        # Must not raise even when kill/remove error out.
        lc.cleanup()


if __name__ == "__main__":
    unittest.main()
