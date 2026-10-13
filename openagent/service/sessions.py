"""Session persistence for the service.

Reconnect is a first-class case: the app quits, crashes, or the sidecar
restarts, and the conversation must come back. A run's *event stream* is not
resumable (documented limit); the *session* is, and that is what makes a
reconnect a new run on the same conversation instead of a lost afternoon.

Session ids arrive from clients, so they are validated before they touch the
filesystem. An id is a filename; treating it as one is how `../../.ssh/id_rsa`
becomes a "session".
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openagent.core.session import Session
from openagent.runtime.loop import next_id
from openagent.service.protocol import ErrorCode, RpcError

SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def sessions_dir() -> Path:
    """``~/.openagent/sessions`` — next to the config file, same owner-only dir."""
    from openagent.core.config import ConfigManager

    return ConfigManager.get_config_path().parent / "sessions"


def check_session_id(session_id: object) -> str:
    text = str(session_id or "")
    if not SESSION_ID_RE.match(text):
        raise RpcError(
            ErrorCode.INVALID_PARAMS,
            "session_id must be 1-64 characters of [A-Za-z0-9_-]",
            {"session_id": text},
        )
    return text


def new_session_id() -> str:
    return next_id("sess")


def path_for(session_id: str) -> Path:
    return sessions_dir() / f"{check_session_id(session_id)}.json"


def save(session: Session, session_id: str, *, path: str | Path | None = None) -> Path:
    """Write the session so a kill in the middle of it costs nothing.

    The service saves after every run, and a sidecar gets killed — by the app
    quitting, by `stop()`, by the machine sleeping through a disk flush. A plain
    `write_text` truncates the file first, so a kill mid-write leaves a
    half-written (often empty) file where a conversation used to be; that is the
    one thing this module promises not to lose.

    So: serialize to a sibling temp file, then `os.replace`, which is atomic
    within a filesystem. The reader sees the old session or the new one, never a
    prefix of it.
    """
    target = Path(path).expanduser() if path else path_for(session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        session.save(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def load(session_id: str) -> Session:
    target = path_for(session_id)
    if not target.exists():
        raise RpcError(
            ErrorCode.SESSION_NOT_FOUND,
            f"no session named {session_id!r}",
            {"session_id": str(session_id)},
        )
    try:
        return Session.load(target)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RpcError(
            ErrorCode.SESSION_NOT_FOUND,
            f"session {session_id!r} is unreadable: {exc}",
            {"session_id": str(session_id)},
        ) from exc


def load_or_start(session_id: str) -> Session:
    """Resume a stored session, or start one under the id the client chose.

    Naming a session for its *first* run is the common case — `--session
    my-name`, or an app that keys sessions to projects — and refusing that run
    is a bad trade. `load()` stays strict, so `session.load` on a typo still
    says not-found.
    """
    target = path_for(session_id)
    if not target.exists():
        # Session carries no id of its own; the service owns the id and saves
        # the file under it.
        return Session()
    return load(session_id)


def list_sessions() -> list[dict[str, Any]]:
    folder = sessions_dir()
    if not folder.exists():
        return []
    rows: list[dict[str, Any]] = []
    for file in sorted(folder.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt session is listed with an error, not dropped: a user
            # looking for a conversation must be told it is damaged, not that
            # it never existed.
            rows.append(
                {
                    "session_id": file.stem,
                    "path": str(file),
                    "updated_at": file.stat().st_mtime,
                    "message_count": 0,
                    "error": "unreadable session file",
                }
            )
            continue
        messages = data.get("messages") or []
        rows.append(
            {
                "session_id": file.stem,
                "path": str(file),
                "updated_at": file.stat().st_mtime,
                "message_count": len(messages),
            }
        )
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return rows


__all__ = [
    "SESSION_ID_RE",
    "check_session_id",
    "list_sessions",
    "load",
    "new_session_id",
    "path_for",
    "save",
    "sessions_dir",
]
