"""Session persistence: durability, ids, and damaged files.

The service saves after every run, so these are the properties that matter:
a kill in the middle of a save costs nothing, an id from a client is a filename
and gets treated like one, and a damaged file is reported as damaged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openagent.core.session import Session
from openagent.core.types import Message
from openagent.service import sessions
from openagent.service.protocol import ErrorCode, RpcError


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


def session_with(text: str) -> Session:
    session = Session()
    session.add_message(Message(role="user", content=text))
    session.add_message(Message(role="assistant", content=f"reply: {text}"))
    return session


# ------------------------------------------------------------------ durability


def test_save_then_load_round_trips():
    sessions.save(session_with("the first thing"), "sess_round")
    loaded = sessions.load("sess_round")
    assert [m["content"] for m in loaded.to_list()] == ["the first thing", "reply: the first thing"]


def test_a_save_killed_midwrite_leaves_the_previous_session_intact(monkeypatch):
    """The whole point of the temp-file dance.

    A plain `write_text` truncates first, so a SIGTERM (the app quitting, a
    sidecar being stopped) between the truncate and the last byte replaces a
    conversation with a prefix of it — usually an empty file.
    """
    sessions.save(session_with("the keeper"), "sess_atomic")
    keeper = json.loads(sessions.path_for("sess_atomic").read_text(encoding="utf-8"))

    def killed_mid_write(self, path):
        Path(path).write_text('{"messages": [{"role": "user"', encoding="utf-8")
        raise RuntimeError("SIGTERM between the bytes")

    monkeypatch.setattr(Session, "save", killed_mid_write)
    with pytest.raises(RuntimeError):
        sessions.save(session_with("the one that never lands"), "sess_atomic")

    on_disk = json.loads(sessions.path_for("sess_atomic").read_text(encoding="utf-8"))
    assert on_disk == keeper, "the old session survived the interrupted save"
    assert not list(sessions.sessions_dir().glob("*.tmp")), "the temp file was cleaned up"


def test_a_good_save_leaves_no_temp_file():
    sessions.save(session_with("clean"), "sess_clean")
    assert list(sessions.sessions_dir().glob("*.tmp")) == []
    assert sessions.path_for("sess_clean").exists()


def test_save_creates_the_directory():
    target = sessions.sessions_dir() / "nested" / "deep.json"
    sessions.save(session_with("nested"), "sess_nested", path=target)
    assert json.loads(target.read_text(encoding="utf-8"))["messages"]


# ------------------------------------------------------------------------ ids


@pytest.mark.parametrize(
    "bad",
    ["", "../evil", "../../.ssh/id_rsa", "a/b", "/abs/path", "sess name", "sess\nname", "x" * 65, ".hidden", "-lead"],
)
def test_a_session_id_is_a_filename_and_is_validated_like_one(bad):
    with pytest.raises(RpcError) as exc:
        sessions.check_session_id(bad)
    assert exc.value.code == ErrorCode.INVALID_PARAMS


def test_path_for_stays_inside_the_session_directory():
    with pytest.raises(RpcError):
        sessions.path_for("../outside")


def test_naming_a_session_starts_it_and_load_stays_strict():
    """`--session my-name` on its first run is the common case; a typo in
    `session.load` must still say not-found."""
    started = sessions.load_or_start("sess_brand_new")
    assert len(started) == 0

    with pytest.raises(RpcError) as exc:
        sessions.load("sess_brand_new")
    assert exc.value.code == ErrorCode.SESSION_NOT_FOUND

    sessions.save(session_with("now it exists"), "sess_brand_new")
    assert len(sessions.load_or_start("sess_brand_new")) == 2


def test_a_damaged_session_is_reported_as_damaged():
    path = sessions.path_for("sess_damaged")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    with pytest.raises(RpcError) as exc:
        sessions.load_or_start("sess_damaged")
    assert exc.value.code == ErrorCode.SESSION_NOT_FOUND
    assert "unreadable" in exc.value.message


def test_list_sessions_reports_a_damaged_file_rather_than_dropping_it():
    sessions.save(session_with("good"), "sess_good")
    damaged = sessions.path_for("sess_damaged")
    damaged.write_text("{not json", encoding="utf-8")

    rows = {row["session_id"]: row for row in sessions.list_sessions()}
    assert rows["sess_good"]["message_count"] == 2
    assert "error" in rows["sess_damaged"], "a damaged conversation must be visible, not absent"
