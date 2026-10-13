"""The sidecar must die with its app.

These spawn the real service as a subprocess, because the property under test is
what happens to a process when its parent goes away — which no in-process test
can produce.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def shortdir() -> Path:
    """A temp dir whose paths fit in an AF_UNIX socket name.

    macOS caps that at ~104 bytes and pytest's ``tmp_path`` is far longer, so a
    socket under it fails with a bare ``OSError: AF_UNIX path too long``.
    """
    return Path(tempfile.mkdtemp(prefix="oa"))


def _spawn(stdin_fd: int, socket_path: Path) -> subprocess.Popen:
    """Start ``cli_coder.py serve`` with a specific fd 0.

    ``stdin_fd`` is duped onto the child's stdin, so the child's stdin is
    exactly the pipe (or /dev/null) the test controls.
    """
    home = socket_path.parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # The service saves a session after every run; the suite must not write into
    # the developer's ~/.openagent.
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(socket_path.parent / "config")
    return subprocess.Popen(
        [sys.executable, "cli_coder.py", "serve", "--socket", str(socket_path)],
        cwd=str(REPO),
        env=env,
        stdin=stdin_fd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _wait_for_socket(path: Path, proc: subprocess.Popen, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.1)
    return False


def test_the_service_exits_when_its_stdin_closes(shortdir):
    """A killed app must not leave its service bound to a socket.

    The app holds a pipe open on the service's stdin. Closing the write end —
    which the kernel does for any death, including SIGKILL — is the one signal
    that cannot be missed: ``willTerminateNotification`` does not fire on
    ``kill``, and nothing fires on SIGKILL.
    """
    socket_path = shortdir / "a.sock"
    read_fd, write_fd = os.pipe()
    proc = _spawn(read_fd, socket_path)
    os.close(read_fd)  # the child holds its own dup; our copy is in the way
    try:
        assert _wait_for_socket(socket_path, proc), (
            "the service never bound its socket: "
            + (proc.stdout.read().decode(errors="replace") if proc.stdout else "")
        )
        os.close(write_fd)  # the parent is gone
        deadline = time.time() + 10
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        assert proc.poll() is not None, "the service outlived the closing of its stdin"
        assert not socket_path.exists(), "the socket was left behind"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def test_a_stdin_that_reports_eof_at_once_does_not_watch_a_parent(shortdir):
    """``/dev/null`` reads as EOF immediately.

    A service started with ``< /dev/null`` — a launcher, a test harness, a
    detached spawn — must not read that as a dead parent and quit, or it would
    never start. Only a pipe (or socket) counts as a parent.
    """
    socket_path = shortdir / "b.sock"
    devnull = os.open(os.devnull, os.O_RDONLY)
    proc = _spawn(devnull, socket_path)
    os.close(devnull)
    try:
        assert _wait_for_socket(socket_path, proc), (
            "the service never bound its socket: "
            + (proc.stdout.read().decode(errors="replace") if proc.stdout else "")
        )
        time.sleep(2.0)
        assert proc.poll() is None, "the service read EOF on /dev/null and quit"
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
