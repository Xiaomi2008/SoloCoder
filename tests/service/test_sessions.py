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

    def killed_mid_write(self, path, *, title=None):
        # The real signature, `save(path, *, title=None)`: a fake that drifts
        # from the method it replaces passes a test the code would fail.
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


# ---------------------------------------------------------------------- titles
#
# The sidebar names a session, and the name has to be a fact the service owns:
# the first prompt, written once. Derived on every save it would move —
# compaction replaces the head of the history with a summary, so the row for
# the session being read all afternoon would rename itself when the
# conversation got long.


def test_the_first_prompt_becomes_the_title():
    sessions.save(session_with("fix the flaky test in test_agent.py"), "sess_title")
    data = json.loads(sessions.path_for("sess_title").read_text(encoding="utf-8"))
    assert data["title"] == "fix the flaky test in test_agent.py"


def test_the_title_is_written_at_the_head_of_the_file():
    sessions.save(session_with("first"), "sess_head")
    head = sessions.path_for("sess_head").read_text(encoding="utf-8")[:40]
    assert head.startswith('{\n  "title"'), "a reader can find the name without parsing the file"


def test_the_title_survives_the_next_save():
    sessions.save(session_with("the name that sticks"), "sess_keep")
    sessions.save(session_with("the name that sticks"), "sess_keep")
    data = json.loads(sessions.path_for("sess_keep").read_text(encoding="utf-8"))
    assert data["title"] == "the name that sticks"


def test_a_title_does_not_move_when_the_history_is_compacted():
    """The reason the title is stored and not derived per save."""
    session = session_with("the original ask")
    sessions.save(session, "sess_compact")

    session.replace_history(
        [Message(role="system", content="Conversation summary: a lot happened.")]
    )
    sessions.save(session, "sess_compact")

    data = json.loads(sessions.path_for("sess_compact").read_text(encoding="utf-8"))
    assert data["title"] == "the original ask", "the row keeps the name the user recognised"
    assert sessions.list_sessions()[0]["title"] == "the original ask"


def test_a_session_that_opens_with_an_image_has_no_title():
    from openagent.core.types import ImageBlock

    session = Session()
    session.add_message(Message(role="user", content=[ImageBlock(data="AAAA")]))
    sessions.save(session, "sess_image")
    assert sessions.list_sessions()[0]["title"] is None


def test_an_empty_first_prompt_is_skipped_for_the_title():
    session = Session()
    session.add_message(Message(role="user", content="   \n  "))
    session.add_message(Message(role="user", content="the one with words"))
    sessions.save(session, "sess_blank")
    assert sessions.list_sessions()[0]["title"] == "the one with words"


def test_a_pasted_file_is_bounded():
    """`session.list` carries every session in one frame under MAX_FRAME_BYTES,
    and a first message can be a 50 KB paste."""
    sessions.save(session_with("x" * 50_000), "sess_paste")
    title = sessions.list_sessions()[0]["title"]
    assert len(title) == sessions.TITLE_MAX


def test_a_session_saved_before_titles_existed_derives_one_on_listing():
    path = sessions.path_for("sess_legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"system_prompt": "", "messages": [{"role": "user", "content": "legacy ask"}]}),
        encoding="utf-8",
    )
    assert sessions.list_sessions()[0]["title"] == "legacy ask"


def test_listing_bounds_a_title_it_did_not_write():
    path = sessions.path_for("sess_huge")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"title": "y" * 10_000, "system_prompt": "", "messages": []}), encoding="utf-8"
    )
    assert len(sessions.list_sessions()[0]["title"]) == sessions.TITLE_MAX


def test_a_damaged_file_has_no_title_to_keep_and_the_save_still_happens():
    path = sessions.path_for("sess_broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    sessions.save(session_with("written over the damage"), "sess_broken")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "written over the damage"


# ---------------------------------------------------------------------- delete


def test_delete_removes_the_file_and_says_so():
    sessions.save(session_with("to be removed"), "sess_remove")
    assert sessions.delete("sess_remove") is True
    assert not sessions.path_for("sess_remove").exists()


def test_deleting_a_session_that_is_not_there_is_not_an_error():
    """The caller wants it gone; a row another client already deleted should
    refresh, not show an error. `load` stays strict — see the id tests."""
    assert sessions.delete("sess_already_gone") is False


def test_a_damaged_session_is_deletable():
    """The one file the user most wants removed, and the reason delete never
    reads the JSON: a delete that parsed it would refuse the damaged file."""
    path = sessions.path_for("sess_wrecked")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert sessions.delete("sess_wrecked") is True
    assert not path.exists()


@pytest.mark.parametrize("bad", ["", "../evil", "../../.ssh/id_rsa", "a/b", "/abs/path"])
def test_delete_takes_an_id_and_not_a_path(bad):
    with pytest.raises(RpcError) as exc:
        sessions.delete(bad)
    assert exc.value.code == ErrorCode.INVALID_PARAMS


def test_delete_leaves_the_temp_files_of_other_sessions_alone():
    """A killed save leaves a `.sess_x.json.tmp`; the list ignores it (it does
    not match `*.json`) and delete must not reach for it."""
    sessions.save(session_with("keeper"), "sess_tmp")
    tmp = sessions.sessions_dir() / ".sess_tmp.json.tmp"
    tmp.write_text("", encoding="utf-8")

    sessions.delete("sess_tmp")
    assert tmp.exists(), "delete removes <id>.json, which is the session"
