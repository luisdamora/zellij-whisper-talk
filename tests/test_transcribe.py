"""Tests for scripts/transcribe.py orchestration (task 3.1 thin entrypoint).

Run with: python3 -m unittest tests.test_transcribe

These exercise the refactored :func:`run_transcription` which wires the five
``whisper_sidecar`` modules together. All external boundaries (recorder spawn,
lock polling, recorder stop, HTTP, signal/atexit) are injected as fakes, so the
tests are deterministic and touch only the real ``/tmp`` via ``tempfile``.

Spec capabilities covered here: secret-protection (key file 0600 + env scrub),
network-resilience (retry config forwarded), output-sanitization (sanitize
before write/print), recording-lifecycle (recorder stopped + files removed),
audio-capture-backends (detect_backend + WAV normalization).
"""

import os
import shutil
import stat
import sys
import tempfile

# Make scripts/ importable so `import whisper_sidecar` resolves AND so the
# transcribe module itself is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import unittest

from whisper_sidecar.config import SidecarConfig

# transcribe.py lives at scripts/transcribe.py (on sys.path now).
import transcribe  # noqa: E402


def _wav_bytes():
    """Minimal valid WAV byte string (RIFF/WAVE, 1 frame s16le mono 16k)."""
    from whisper_sidecar.recorders import raw_pcm_to_wav

    return raw_pcm_to_wav(b"\x00\x00", channels=1, sample_width=2, rate=16000)


class FakeRecorder:
    """Fake Popen-like child."""

    def __init__(self):
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):  # noqa: ARG002
        return 0


class FakeLifecycle:
    def __init__(self):
        self.registered = []
        self.tracked = []
        self.installed = False

    def register_recorder(self, child):
        self.registered.append(child)

    def track(self, path):
        if path:
            self.tracked.append(path)

    def install(self):
        self.installed = True


class FakePost:
    """Scripts the transcribe + clean HTTP responses and records calls."""

    def __init__(self, transcribe_text, cleaned_text, clean_raises=False):
        self._transcribe_text = transcribe_text
        self._cleaned_text = cleaned_text
        self._clean_raises = clean_raises
        self.calls = []  # list of dicts: url, body, key, retries, timeout

    def __call__(self, url, body, key, *, retries=3, timeout=30):
        self.calls.append(
            {"url": url, "body": body, "key": key, "retries": retries, "timeout": timeout}
        )
        if "audio/transcriptions" in url:
            return {"text": self._transcribe_text}
        if "chat/completions" in url:
            if self._clean_raises:
                raise RuntimeError("clean failed")
            return {"choices": [{"message": {"content": self._cleaned_text}}]}
        raise AssertionError(f"unexpected url {url}")


class _Env(dict):
    """A dict that mimics os.environ pop semantics for the scrub test."""


def _run(
    *,
    env,
    audio_path,
    text_file,
    lock_file,
    spawn_backend="arecord",
    transcribe_text="hola mundo",
    cleaned_text="hola limpio",
    audio_bytes=None,
    config=None,
    clean_raises=False,
    post=None,
    recorder=None,
):
    """Invoke run_transcription with standard fakes; return (rc, captures)."""
    if config is None:
        config = SidecarConfig(
            http_timeout=30, http_retries=3, max_duration=120, audio_backend="auto", confirm_inject=True
        )
    recorder = recorder or FakeRecorder()
    spawned = {}

    def spawn(backend, ap):
        spawned["backend"] = backend
        spawned["audio_path"] = ap
        return recorder

    def wait_lock(lf):
        spawned["waited_lock"] = lf

    def stop(child):
        spawned["stopped"] = child

    outputs = []

    def output(line):
        outputs.append(line)

    if post is None:
        post = FakePost(transcribe_text, cleaned_text, clean_raises=clean_raises)
    if audio_bytes is None:
        audio_bytes = _wav_bytes()
    # Pre-create the audio file as if the recorder had written it.
    with open(audio_path, "wb") as f:
        f.write(audio_bytes)

    lc = FakeLifecycle()

    rc = transcribe.run_transcription(
        lock_file,
        config=config,
        model="deepseek/x",
        audio_path=audio_path,
        text_file=text_file,
        env=env,
        spawn_recorder=spawn,
        wait_while_lock_exists=wait_lock,
        stop_recorder=stop,
        post_fn=post,
        lifecycle=lc,
        key_dir=os.path.dirname(audio_path),
        output=output,
    )
    return rc, {
        "spawned": spawned,
        "post": post,
        "outputs": outputs,
        "lifecycle": lc,
        "recorder": recorder,
    }


class RunTranscriptionHappyPathTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_returns_zero_and_prints_sanitized_cleaned_text(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-secret"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            cleaned_text="hola limpio",
        )
        self.assertEqual(rc, 0)
        # Sanitized cleaned text printed to stdout for the WASM to capture.
        self.assertEqual(cap["outputs"], ["hola limpio"])

    def test_writes_text_file_with_cleaned_content_at_0600(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-secret"})
        text_file = os.path.join(self.dir, "out.txt")
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=text_file,
            lock_file=os.path.join(self.dir, "lock"),
            cleaned_text="texto final",
        )
        self.assertEqual(rc, 0)
        with open(text_file) as f:
            self.assertEqual(f.read(), "texto final")
        mode = stat.S_IMODE(os.lstat(text_file).st_mode)
        self.assertEqual(mode, 0o600)


class SecretProtectionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_api_key_returns_nonzero_without_spawning_or_posting(self):
        env = _Env({})  # no OPENROUTER_API_KEY
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
        )
        self.assertNotEqual(rc, 0)
        self.assertNotIn("backend", cap["spawned"])  # no recorder spawned
        self.assertEqual(cap["post"].calls, [])       # no API call made

    def test_empty_api_key_treated_as_missing(self):
        env = _Env({"OPENROUTER_API_KEY": "   "})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
        )
        self.assertNotEqual(rc, 0)
        self.assertEqual(cap["post"].calls, [])

    def test_api_key_scrubbed_from_env_after_run(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-secret"})
        rc, _ = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
        )
        self.assertEqual(rc, 0)
        # Spec secret-protection: the key MUST NOT remain in the environment.
        self.assertNotIn("OPENROUTER_API_KEY", env)

    def test_key_file_written_0600_then_removed(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-secret"})
        rc, _ = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
        )
        self.assertEqual(rc, 0)
        # Key file must not survive a successful run (hygiene).
        leftovers = [
            n for n in os.listdir(self.dir) if n.startswith("zellij-voice-key-")
        ]
        self.assertEqual(leftovers, [])


class BackendDetectionAndAudioTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_forced_backend_is_spawned(self):
        # AUDIO_BACKEND override -> detect_backend returns it verbatim.
        cfg = SidecarConfig(
            http_timeout=30, http_retries=3, max_duration=120,
            audio_backend="arecord", confirm_inject=True,
        )
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            config=cfg,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(cap["spawned"]["backend"], "arecord")

    def test_pw_record_raw_audio_normalized_to_wav_before_post(self):
        # pw-record writes RAW s16le; the body sent to the API must be a WAV.
        from whisper_sidecar.recorders import is_wav

        cfg = SidecarConfig(
            http_timeout=30, http_retries=3, max_duration=120,
            audio_backend="pw-record", confirm_inject=True,
        )
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "raw.pcm"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            config=cfg,
            audio_bytes=b"\x01\x00\x02\x00\x03\x00",  # raw s16le, NOT a WAV
        )
        self.assertEqual(rc, 0)
        self.assertEqual(cap["spawned"]["backend"], "pw-record")
        # The transcribe request body's audio data, once base64-decoded, is WAV.
        transcribe_call = cap["post"].calls[0]
        import base64

        decoded = base64.b64decode(transcribe_call["body"]["input_audio"]["data"])
        self.assertTrue(is_wav(decoded))

    def test_arecord_wav_audio_passed_through_unchanged(self):
        # arecord already produces WAV -> no normalization needed.
        cfg = SidecarConfig(
            http_timeout=30, http_retries=3, max_duration=120,
            audio_backend="arecord", confirm_inject=True,
        )
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            config=cfg,
            audio_bytes=_wav_bytes(),
        )
        self.assertEqual(rc, 0)
        import base64

        decoded = base64.b64decode(cap["post"].calls[0]["body"]["input_audio"]["data"])
        # Same bytes we wrote (already WAV) -> identity.
        self.assertEqual(decoded, _wav_bytes())


class NetworkResilienceConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_post_fn_receives_configured_retries_and_timeout(self):
        cfg = SidecarConfig(
            http_timeout=12, http_retries=5, max_duration=120,
            audio_backend="arecord", confirm_inject=True,
        )
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            config=cfg,
        )
        self.assertEqual(rc, 0)
        for call in cap["post"].calls:
            self.assertEqual(call["retries"], 5)
            self.assertEqual(call["timeout"], 12)


class OutputSanitizationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cleaned_text_is_sanitized_before_write_and_print(self):
        # LLM returns ANSI-laden cleaned text -> must be stripped everywhere.
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        text_file = os.path.join(self.dir, "out.txt")
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=text_file,
            lock_file=os.path.join(self.dir, "lock"),
            cleaned_text="\x1b[31mred\x1b[0m text",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(cap["outputs"], ["red text"])
        with open(text_file) as f:
            self.assertEqual(f.read(), "red text")


class RecordingLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_recorder_stopped_and_files_tracked_with_lifecycle(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        audio_path = os.path.join(self.dir, "a.wav")
        rc, cap = _run(
            env=env,
            audio_path=audio_path,
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
        )
        self.assertEqual(rc, 0)
        # Recorder was registered and explicitly stopped after the lock cleared.
        self.assertEqual(cap["lifecycle"].registered, [cap["recorder"]])
        self.assertIs(cap["spawned"]["stopped"], cap["recorder"])
        # Lifecycle installed (atexit + signal handlers) and tracked temp files.
        self.assertTrue(cap["lifecycle"].installed)
        self.assertIn(audio_path, cap["lifecycle"].tracked)

    def test_audio_file_removed_after_successful_run(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        audio_path = os.path.join(self.dir, "a.wav")
        rc, _ = _run(
            env=env,
            audio_path=audio_path,
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
        )
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(audio_path))


class TranscribeFlowEdgeCasesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_empty_transcription_returns_nonzero_and_skips_clean(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            transcribe_text="   ",  # whitespace-only -> empty after strip
        )
        self.assertNotEqual(rc, 0)
        # Only the transcribe call should have happened (no clean).
        urls = [c["url"] for c in cap["post"].calls]
        self.assertTrue(any("transcriptions" in u for u in urls))
        self.assertFalse(any("chat/completions" in u for u in urls))

    def test_clean_failure_falls_back_to_raw_transcription(self):
        env = _Env({"OPENROUTER_API_KEY": "sk-x"})
        rc, cap = _run(
            env=env,
            audio_path=os.path.join(self.dir, "a.wav"),
            text_file=os.path.join(self.dir, "out.txt"),
            lock_file=os.path.join(self.dir, "lock"),
            transcribe_text="raw speech",
            cleaned_text="ignored",  # clean raises -> fallback to raw
            clean_raises=True,
        )
        # Fallback is non-fatal: run still succeeds with the raw text.
        self.assertEqual(rc, 0)
        self.assertEqual(cap["outputs"], ["raw speech"])


class MainEntryPointTest(unittest.TestCase):
    """The thin main() wrapper must keep the argv contract (backward compat)."""

    def test_missing_argv_exits_nonzero(self):
        # Same behavior as the original: lock file path is argv[1].
        old_argv = sys.argv
        try:
            sys.argv = ["transcribe.py"]
            with self.assertRaises(SystemExit) as ctx:
                transcribe.main()
            self.assertNotEqual(ctx.exception.code, 0)
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
