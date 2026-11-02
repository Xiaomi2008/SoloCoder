"""Atomic session checkpoints, using SoloCoder's existing session format."""
from pathlib import Path

from openagent.core.session import Session
from openagent.core.types import ToolResultBlock

from .storage import atomic_json


def restore(agent, path: Path) -> None:
    if not path.exists():
        return
    session = Session.load(path)
    # A crash can leave an assistant tool call without its result. Never replay
    # that tool: it could already have performed an external side effect.
    results = {r.tool_use_id for m in session.messages for r in m.tool_results}
    missing = [c for m in session.messages for c in m.tool_calls if c.id not in results]
    if missing:
        session.add_tool_results([ToolResultBlock(
            tool_use_id=c.id, tool_name=c.name, is_error=True,
            content='Host interrupted: outcome unknown. Inspect before repeating any side effect.',
        ) for c in missing])
    session.system_prompt = agent.session.system_prompt
    agent.session = session


def save(agent, path: Path) -> None:
    atomic_json(path, {'system_prompt': agent.session.system_prompt,
                       'messages': agent.session.to_list()})


class CheckpointProvider:
    """Checkpoint and heartbeat around each model call."""
    def __init__(self, provider, agent, path: Path, heartbeat=None):
        self._provider = provider
        self._agent = agent
        self._path = path
        self._heartbeat = heartbeat

    def __getattr__(self, name):
        return getattr(self._provider, name)

    async def chat(self, *args, **kwargs):
        save(self._agent, self._path)
        if self._heartbeat is not None:
            self._heartbeat('Waiting for model response')
        try:
            return await self._provider.chat(*args, **kwargs)
        finally:
            if self._heartbeat is not None:
                self._heartbeat('Model response received')
