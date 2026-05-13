"""Tests for BashManager - session lifecycle, command execution."""

from __future__ import annotations

import asyncio
from unittest.mock import patch
from unittest.mock import AsyncMock, MagicMock

import pytest

from openagent.core.bash_manager import BashManager, BashSession


# ============================================================================
# BashSession
# ============================================================================


class TestBashSession:
    @patch("openagent.core.bash_manager.asyncio.get_event_loop")
    def test_create_session(self, mock_loop):
        mock_loop.return_value.time.return_value = 0.0
        session = BashSession(session_id="test-1")
        assert session.session_id == "test-1"
        assert session.process is None
        assert session.is_running is False

    @patch("openagent.core.bash_manager.asyncio.get_event_loop")
    def test_add_output(self, mock_loop):
        mock_loop.return_value.time.return_value = 0.0
        session = BashSession(session_id="test-1")
        session.output_buffer.append("hello\n")
        assert "hello" in "\n".join(session.output_buffer)


# ============================================================================
# BashManager basic
# ============================================================================


class TestBashManagerBasic:
    def test_get_bash_manager(self):
        from openagent.core.bash_manager import get_bash_manager
        bm = get_bash_manager()
        assert isinstance(bm, BashManager)

    async def test_start_session(self):
        """Starting a session should return a session ID."""
        bm = BashManager()
        session_id = await bm.start_session(working_dir="/tmp")
        assert isinstance(session_id, str)
        assert len(session_id) > 0

    async def test_execute_command(self):
        """Execute a simple command."""
        bm = BashManager()
        session_id = await bm.start_session(working_dir="/tmp")
        result = await bm.execute_command(session_id, "echo hello")
        assert "hello" in result

    async def test_get_session_info(self):
        """Get session info should return dict."""
        bm = BashManager()
        session_id = await bm.start_session(working_dir="/tmp")
        info = bm.get_session_info(session_id)
        assert info is not None
        assert "session_id" in info

    async def test_get_session_info_nonexistent(self):
        """Get session info for nonexistent session returns None."""
        bm = BashManager()
        info = bm.get_session_info("nonexistent")
        assert info is None

    async def test_kill_session(self):
        """Kill a session."""
        bm = BashManager()
        session_id = await bm.start_session(working_dir="/tmp")
        result = await bm.kill_session(session_id)
        assert isinstance(result, str)

    async def test_cleanup(self):
        """Cleanup should not raise."""
        bm = BashManager()
        await bm.start_session(working_dir="/tmp")
        await bm.cleanup()
