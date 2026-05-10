"""Tests for BashManager - thread safety, session lifecycle, non-destructive commands."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openagent.core.bash_manager import BashManager, BashSession


# ============================================================================
# Thread safety
# ============================================================================


class TestBashManagerThreadSafety:
    def test_session_has_output_lock(self):
        """Sessions created by start_session should have an _output_lock."""
        manager = BashManager()
        session = BashSession(session_id="test")
        session._output_lock = threading.Lock()
        manager.sessions["test"] = session

        # get_output should use the lock without crashing
        output = manager.get_output("test")
        assert output == "(no output)"

    def test_get_output_uses_lock(self):
        """get_output should acquire the lock before reading buffer."""
        manager = BashManager()
        session = BashSession(session_id="test")
        session._output_lock = threading.Lock()
        session.output_buffer = ["line1", "line2"]
        manager.sessions["test"] = session

        output = manager.get_output("test")
        assert "line1" in output
        assert "line2" in output

    def test_get_output_returns_copy(self):
        """get_output should return a copy, not a reference to the buffer."""
        manager = BashManager()
        session = BashSession(session_id="test")
        session._output_lock = threading.Lock()
        session.output_buffer = ["original"]
        manager.sessions["test"] = session

        output1 = manager.get_output("test")
        session.output_buffer.append("added later")
        output2 = manager.get_output("test")

        assert "added later" not in output1
        assert "added later" in output2

    def test_get_output_missing_session(self):
        """get_output should return error for unknown session."""
        manager = BashManager()
        output = manager.get_output("nonexistent")
        assert "not found" in output.lower()


# ============================================================================
# Non-destructive command execution
# ============================================================================


class TestNonDestructiveCommand:
    async def test_send_command_and_wait_does_not_kill(self):
        """_send_command_and_wait should poll instead of communicate()."""
        manager = BashManager()

        # Mock session with a fake process
        mock_process = MagicMock()
        mock_process.communicate = MagicMock(side_effect=AssertionError(
            "communicate() should not be called - it kills the session"
        ))

        session = BashSession(session_id="test", process=mock_process)
        session._output_lock = threading.Lock()
        session.output_buffer = []
        manager.sessions["test"] = session

        # Mock _send_command to do nothing, but fill buffer on first call
        async def mock_send(sid, cmd):
            session.output_buffer.append("command output")

        manager._send_command = mock_send  # type: ignore

        result = await manager._send_command_and_wait("test", "echo hello")
        assert "command output" in result
        # Verify communicate() was never called
        mock_process.communicate.assert_not_called()

    async def test_send_command_and_wait_timeout(self):
        """_send_command_and_wait should timeout and return error."""
        manager = BashManager()

        session = BashSession(session_id="test", process=MagicMock())
        session._output_lock = threading.Lock()
        session.output_buffer = []
        manager.sessions["test"] = session

        # Don't fill buffer — output stays empty forever
        manager._send_command = AsyncMock()

        result = await manager._send_command_and_wait("test", "sleep 100", timeout=0.3)
        assert "timed out" in result.lower()


# ============================================================================
# Session lifecycle
# ============================================================================


class TestSessionLifecycle:
    async def test_cleanup(self):
        """cleanup should terminate all sessions and clear dict."""
        manager = BashManager()
        mock_process = MagicMock()
        session = BashSession(session_id="test", process=mock_process, is_running=True)
        manager.sessions["test"] = session

        await manager.cleanup()
        assert len(manager.sessions) == 0
        mock_process.terminate.assert_called()

    async def test_kill_session(self):
        """kill_session should terminate and remove session."""
        manager = BashManager()
        mock_process = MagicMock()
        session = BashSession(session_id="test", process=mock_process, is_running=True)
        manager.sessions["test"] = session

        result = await manager.kill_session("test")
        assert "terminated" in result.lower()
        assert "test" not in manager.sessions

    async def test_kill_unknown_session(self):
        """kill_session should return error for unknown session."""
        manager = BashManager()
        result = await manager.kill_session("nonexistent")
        assert "not found" in result.lower()

    def test_get_session_info(self):
        """get_session_info should return dict for existing session."""
        manager = BashManager()
        session = BashSession(session_id="test", is_running=True)
        manager.sessions["test"] = session

        info = manager.get_session_info("test")
        assert info is not None
        assert info["session_id"] == "test"
        assert info["is_running"] is True

    def test_get_session_info_unknown(self):
        """get_session_info should return None for unknown session."""
        manager = BashManager()
        info = manager.get_session_info("nonexistent")
        assert info is None


# ============================================================================
# Tool async compatibility
# ============================================================================


class TestToolAsyncCompatibility:
    def test_bash_background_is_async(self):
        """bash_background tool must be async to avoid asyncio.run() crash."""
        from openagent.tools import bash_background
        import inspect
        assert inspect.iscoroutinefunction(bash_background)

    def test_kill_shell_is_async(self):
        """kill_shell tool must be async to avoid asyncio.run() crash."""
        from openagent.tools import kill_shell
        import inspect
        assert inspect.iscoroutinefunction(kill_shell)

    def test_bash_output_is_sync(self):
        """bash_output is a simple getter and can remain sync."""
        from openagent.tools import bash_output
        import inspect
        assert not inspect.iscoroutinefunction(bash_output)
