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


TITLE_MAX = 200


def derive_title(data: dict[str, Any]) -> str | None:
    """The session's first prompt, on one line and bounded; None if it has none.

    The sidebar needs a name and a session file has no name of its own, so the
    first thing the user asked is the name. Only `text` blocks count: a session
    that opens with a screenshot has no words to name it, and naming it after a
    base64 blob is worse than no title.

    The text is collapsed to one line and cut at `TITLE_MAX` — a payload bound,
    not a display one. A first message can be a pasted 50 KB file, and
    `session.list` returns every session in one frame under `MAX_FRAME_BYTES`.
    How the name *looks* — where it breaks, what it ends with — is the client's,
    and it has the width to answer with.
    """
    for message in data.get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = next(
                (
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ),
                "",
            )
        else:
            continue
        text = " ".join(text.split())
        if text:
            return text[:TITLE_MAX]
    return None


def session_title(data: dict[str, Any]) -> str | None:
    """The session's display name: what the file stores, else its first prompt.

    The one rule for the two places a title is read — the file's own, written
    once, and the fallback for files that predate titles. The bound is applied
    on the way out and not on the way in: a stored value is only as trustworthy
    as the writer that produced it, and `session.list` carries every session on
    the machine in one frame under `MAX_FRAME_BYTES`.
    """
    title = data.get("title") if isinstance(data, dict) else None
    if not (isinstance(title, str) and title.strip()):
        title = derive_title(data)
    return title[:TITLE_MAX] if isinstance(title, str) else None


def stored_title(path: Path) -> str | None:
    """The title already in the file, and None when there is none to keep.

    A title is derived once and then carried, because the derivation reads the
    first user message and compaction replaces the head of the history with a
    summary. A title derived on every save would therefore rename itself at
    compaction: the row for the session the user has been reading all afternoon
    changes under them because the conversation got long.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # A damaged file has no title to keep, and a save that cannot read it
        # still writes the session: the save is the promise, the title is not.
        return None
    title = data.get("title") if isinstance(data, dict) else None
    return title if isinstance(title, str) and title.strip() else None


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

    The title rides along: kept from the file when it has one, derived from the
    session being written when it does not.
    """
    target = Path(path).expanduser() if path else path_for(session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    title = stored_title(target) or derive_title({"messages": session.to_list()})
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        session.save(tmp, title=title)
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
                    "title": None,
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
                "title": session_title(data),
            }
        )
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return rows


def delete(session_id: str) -> bool:
    """Remove a session's file, and report whether there was one to remove.

    The id is the only key a client may use: `path_for` validates it, so
    `../../.ssh/id_rsa` is refused here exactly as it is by `load`. The row's
    `path` is a fact the list reports for the human's benefit, never an input.

    The file is never read. A damaged session is the row the user most wants to
    clear from the app, and a delete that parsed the JSON would refuse to remove
    the one file that needs removing.

    A missing file is a success for the caller's purpose — the session is gone —
    so it returns False rather than raising: a sidebar whose row was deleted by
    another client should refresh, not show an error. `load` stays strict;
    naming a session that does not exist is a typo worth reporting.
    """
    target = path_for(session_id)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True


__all__ = [
    "SESSION_ID_RE",
    "TITLE_MAX",
    "check_session_id",
    "delete",
    "derive_title",
    "list_sessions",
    "load",
    "new_session_id",
    "path_for",
    "save",
    "session_title",
    "sessions_dir",
    "stored_title",
]
