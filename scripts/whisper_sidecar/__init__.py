"""whisper_sidecar — production-hardened internals for transcribe.py.

Stdlib-only. Modules:
  - config:     env -> typed config with safe defaults
  - security:   umask, key-file, temp paths, sanitize()
  - http:       post_with_retry()
  - recorders:  detect_backend() + per-backend WAV Popen
  - lifecycle:  signal/atexit cleanup
"""

__all__: list[str] = []
