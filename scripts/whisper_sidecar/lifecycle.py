"""Deterministic recorder teardown: atexit + signal handlers.

Stdlib only. Owns two cleanup duties required by the ``recording-lifecycle``
spec: (1) terminate any live recorder child so no orphaned ``pw-record``/
``parec``/``arecord`` survives script exit, and (2) remove the lock + text temp
files. Cleanup is wired to :func:`atexit.register` plus ``SIGTERM``/``SIGINT``
handlers so it runs even on uncaught exception or explicit kill.

Side-effectful primitives (``kill_child``, ``remove_file``, ``atexit_register``,
``signal_setter``) are injected so the teardown logic is unit-testable without
real processes or signals.

Note: design decision #8 also proposed ``prctl(PR_SET_PDEATHSIG)`` via ctypes
as defense-in-depth. It is omitted here because it is kernel-state (not unit-
testable without violating strict TDD's no-production-code-without-a-test rule)
and is not required by any spec scenario. It remains a candidate for a future,
separately-tested hardening pass.
"""

from __future__ import annotations

import atexit
import os
import signal
from typing import Any, Callable, List, Optional


def _default_kill_child(child: Any) -> None:
    """Best-effort terminate + wait on a ``subprocess.Popen``-like child."""
    try:
        child.terminate()
    except Exception:
        try:
            child.kill()
        except Exception:
            pass
    try:
        child.wait(timeout=5)
    except Exception:
        try:
            child.kill()
        except Exception:
            pass


def _default_remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


class Lifecycle:
    """Owns cleanup of a recorder child and a set of temp-file paths.

    Call :meth:`register_recorder` with the spawned ``Popen`` and
    :meth:`track` each file (lock, text, audio) that must not survive exit,
    then :meth:`install` once to wire atexit + signal handlers. Cleanup is
    idempotent: a child is killed at most once and each file removed at most once.
    """

    def __init__(
        self,
        *,
        atexit_register: Callable[[Callable[[], None]], Any] = atexit.register,
        signal_setter: Callable[[int, Callable], Any] = signal.signal,
        kill_child: Callable[[Any], None] = _default_kill_child,
        remove_file: Callable[[str], None] = _default_remove_file,
    ) -> None:
        self._atexit_register = atexit_register
        self._signal_setter = signal_setter
        self._kill_child = kill_child
        self._remove_file = remove_file
        self._child: Optional[Any] = None
        self._paths: List[str] = []
        self._installed = False

    def register_recorder(self, child: Any) -> None:
        """Register the live recorder child to be terminated on cleanup."""
        self._child = child

    def track(self, path: Optional[str]) -> None:
        """Add a file path to remove on cleanup. Empty/None is ignored."""
        if path:
            self._paths.append(path)

    def install(self) -> None:
        """Wire atexit + SIGTERM/SIGINT handlers. Idempotent."""
        if self._installed:
            return
        self._installed = True
        self._atexit_register(self.cleanup)
        for sig in (signal.SIGTERM, signal.SIGINT):
            self._signal_setter(sig, self.handle_signal)

    def handle_signal(self, signum: int, frame: Any) -> None:  # noqa: ARG002
        """Signal-handler entry point: clean up, then re-raise default for SIGINT."""
        self.cleanup()
        if signum == signal.SIGINT:
            # Restore default and re-raise so the process exits with the
            # conventional Ctrl-C status rather than swallowing it.
            self._signal_setter(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt

    def cleanup(self) -> None:
        """Terminate the child (once) and remove tracked files (once each).

        Each duty is independent and failure-tolerant: a kill or remove that
        raises is swallowed so the remaining duties still run (an orphaned
        recorder is worse than a best-effort cleanup, and a half-cleaned temp
        dir is worse than fully attempting both).
        """
        child = self._child
        if child is not None:
            try:
                self._kill_child(child)
            except Exception:
                pass
            self._child = None
        paths = self._paths
        self._paths = []
        for path in paths:
            try:
                self._remove_file(path)
            except Exception:
                pass
