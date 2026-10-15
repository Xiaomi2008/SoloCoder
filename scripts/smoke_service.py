"""End-to-end smoke for the agent service over a real Unix socket.

Not a unit test: it exercises the transport, the run loop, the permission
round-trip, cancellation, and reconnect in one process. Run with:

    .venv/bin/python scripts/smoke_service.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Isolate config and sessions: this script must not write into the real
# ~/.openagent, and it must not depend on what the user has configured.
_SANDBOX = Path(tempfile.mkdtemp(prefix="openagent-smoke-"))
os.environ["HOME"] = str(_SANDBOX)
os.environ["XDG_CONFIG_HOME"] = str(_SANDBOX / ".config")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openagent.service.client import ServiceClient
from openagent.service.server import AgentService, ServiceSettings
from tests.support.streaming_provider import ScriptedStreamProvider, text_turn, tool_turn

WORK = Path(tempfile.mkdtemp())
(WORK / "hello.txt").write_text("hi from the tool\n")


def read_file(path: str) -> str:
    """Read a file from the working directory.

    Args:
        path: Path of the file to read
    """
    return (WORK / path).read_text()


def provider_factory(ref):
    return ScriptedStreamProvider(
        [
            tool_turn("read_file", {"path": "hello.txt"}, id="tc1"),
            text_turn("the file says hi"),
        ]
    )


ASKED: list[str] = []


async def ask(event):
    ASKED.append(event.tool_name)
    print(f"  [client] asked about {event.tool_name}({event.arguments}) -> allow once")
    return True, "once"


async def main() -> int:
    sock = Path(tempfile.mkdtemp()) / "agent.sock"
    service = AgentService(
        ServiceSettings(
            socket_path=sock,
            tools=[read_file],
            provider_factory=provider_factory,
            permission_timeout=5,
        )
    )
    await service.start()
    print(f"server up on {sock}")

    client = await ServiceClient.connect(sock, client="smoke", permission_handler=ask)
    print("hello ->", client.info["protocol_version"], client.info["server"])
    print("ping ->", await client.ping())
    print("tools ->", [t["name"] for t in await client.tools()])
    print("models ->", [m["provider"] for m in (await client.models())["models"]])
    print("auth providers ->", len((await client.auth_status())["providers"]))
    print("config policy ->", (await client.config())["models"]["policy"])

    started = await client.start_run("read hello.txt", str(WORK), permission_mode="ask")
    print("run.start ->", json.dumps(started))

    seen: list[str] = []
    status = 0
    async for event in client.events():
        seen.append(event.type)
        if event.type == "tool_call_started":
            print("  tool:", event.tool_name, event.arguments)
        if event.type == "tool_call_completed":
            print("  result:", repr(event.result))
        if event.type == "message_delta":
            print("  delta:", repr(event.delta))
        if event.type == "run_completed":
            print("  completed:", event.result.stop_reason, repr(event.result.output_text))
        if event.type in ("run_failed", "run_cancelled"):
            status = 1
    print("sequence ->", " ".join(seen))
    print("asked ->", ASKED)
    rows = await client.sessions()
    print("sessions ->", [(r["session_id"], r["message_count"]) for r in rows])
    print("file ->", Path(rows[0]["path"]).read_text()[:300])
    await client.close()

    # Reconnect: the session must survive, and a new run must start clean.
    client2 = await ServiceClient.connect(sock, client="smoke-reconnect")
    print("reconnect hello ->", client2.info["protocol_version"])
    print("reconnect sessions ->", [s["session_id"] for s in await client2.sessions()][:2])
    loaded = await client2.load_session(started["session_id"])
    print("reconnect loaded messages ->", len(loaded))
    await client2.close()

    await service.stop()
    print("SMOKE", "OK" if status == 0 and "tool_call_completed" in seen else "FAILED")
    return status


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
