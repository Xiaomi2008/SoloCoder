"""``cli_coder.py`` as a person runs it, piped: a temp HOME, a scripted model
server, and the real output. With ``OPENAGENT_RS_BIN`` set, the Rust CLI runs
the same script and the two transcripts must match."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _call(name: str, **arguments: str) -> dict:
    return {"tool_calls": [{"index": 0, "id": f"c-{name}", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


SCRIPT = [
    [_call("read", path="a.txt")],
    [{"content": "Running it."}, _call("bash", command="seq 1 7")],
    [_call("edit", path="a.txt", find="two", replace="TWO\nmore")],
    [_call("write", path="b.txt", content="x\ny\n")],
    [_call("read", path="missing.txt")],
    [{"content": "# Done\n\nAll **good**.\n"}],
]


@pytest.fixture
def model():
    step = {"at": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            body = json.dumps({"data": [{"id": "m"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", 0)))
            parts = SCRIPT[min(step["at"], len(SCRIPT) - 1)]
            step["at"] += 1
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for part in parts:
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': part}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield step, server.server_address[1]
    server.shutdown()


def _run(argv: list[str], tmp_path: Path, port: int, name: str, stdin: str) -> str:
    home = tmp_path / name / "home"
    work = tmp_path / name / "work"
    (home / ".openagent").mkdir(parents=True)
    work.mkdir()
    (work / "a.txt").write_text("one\ntwo\nthree\n")
    (home / ".openagent" / "config.json").write_text(
        json.dumps(
            {
                "models": {
                    "custom_endpoints": [
                        {
                            "name": "mock",
                            "base_url": f"http://127.0.0.1:{port}/v1",
                            "local": True,
                            "requires_key": False,
                            "context_window": 32768,
                        }
                    ]
                }
            }
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI")}
    env.update(HOME=str(home), NO_COLOR="1")
    done = subprocess.run(
        [*argv, "-m", "mock/m", "-w", str(work)], input=stdin, capture_output=True, text=True, env=env, timeout=60
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.replace(str(work.resolve()), "WORK")


def _python_cli() -> list[str]:
    return [sys.executable, str(REPO / "cli_coder.py")]


def test_a_piped_session(model, tmp_path: Path) -> None:
    _, port = model
    out = _run(_python_cli(), tmp_path, port, "py", "/todo\ngo\ny\ny\nn\n/context\n/quit\n")
    assert "Using mock/m (context 32768)" in out
    assert "No active tasks." in out
    assert "● Read(a.txt)\n  ⎿ 3 lines" in out
    assert "Running it." in out
    assert "⚠ Bash wants to run" in out
    assert "· permission allowed (once)" in out
    assert "● Bash(seq 1 7)\n  ⎿ 1\n    2\n    3\n    4\n    5\n    … +2 lines" in out
    assert "     2 - two\n     2 + TWO\n     3 + more" in out
    assert "· permission denied (once)" in out
    assert "⎿ Error: File 'missing.txt' does not exist." in out
    assert "# Done" in out, "plain mode prints deltas verbatim"
    assert "░" in out
    assert "Goodbye!" in out


def test_one_prompt_saves_a_session(model, tmp_path: Path) -> None:
    step, port = model
    step["at"] = len(SCRIPT) - 1
    out = _run([*_python_cli(), "-p", "hi"], tmp_path, port, "once", "")
    assert "All **good**." in out
    session = re.search(r"session (sess_\w+)", out)
    assert session, out
    assert (tmp_path / "once" / "home" / ".openagent" / "sessions" / f"{session.group(1)}.json").exists()


def _normal(text: str) -> str:
    text = re.sub(r"sess_\w+", "SESSION", text)
    # Token counts come from each engine's own tool schemas and are not the contract.
    text = re.sub(r"context [\d,]+ / ([\d,]+) tokens \(\d+%\)", r"context N / \1 tokens", text)
    text = re.sub(r"context \d+%", "context N%", text)
    return re.sub(r"[█░]+", "BAR", text)


@pytest.mark.skipif(not os.environ.get("OPENAGENT_RS_BIN"), reason="OPENAGENT_RS_BIN is not set")
def test_both_engines_print_the_same_session(model, tmp_path: Path) -> None:
    step, port = model
    stdin = "go\ny\ny\nn\n/context\n/quit\n"
    python = _run(_python_cli(), tmp_path, port, "py", stdin)
    step["at"] = 0
    rust = _run([os.environ["OPENAGENT_RS_BIN"]], tmp_path, port, "rs", stdin)
    assert _normal(python) == _normal(rust)
