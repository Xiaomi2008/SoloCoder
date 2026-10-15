"""Bash manager for persistent shell sessions and background process management."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _get_bash_executable() -> str:
    """Get the appropriate bash executable path for the current platform.

    On Windows with Git Bash or WSL, returns the correct path.
    Otherwise returns 'bash'.
    """
    if os.name == 'nt':  # Windows
        # Try common bash locations on Windows
        possible_paths = [
            r'C:\Program Files\Git\usr\bin\bash.exe',
            r'C:\Windows\System32\bash.exe',
            'bash',  # Fallback to PATH
        ]
        for path in possible_paths:
            try:
                result = subprocess.run([path, '--version'], capture_output=True, timeout=5)
                if result.returncode == 0 or b'GNU bash' in result.stdout:
                    return path
            except (FileNotFoundError, OSError):
                continue
        # Default to 'bash' and let the OS handle it
        return 'bash'
    return 'bash'


@dataclass
class BashSession:
    """Represents a bash session with its state."""

    session_id: str
    process: subprocess.Popen | None = None
    working_dir: str = ""
    output_buffer: list[str] = field(default_factory=list)
    is_running: bool = False
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())


class BashManager:
    """Manages persistent bash sessions and background processes.

    This manager allows the Agent to maintain shell sessions across multiple tool calls,
    enabling true background execution and output retrieval.
    """

    def __init__(self):
        self.sessions: dict[str, BashSession] = {}
        self._lock = asyncio.Lock()
        self._io_lock = threading.Lock()
        self._cleanup_task: asyncio.Task | None = None

    def _start_output_reader(self, session: BashSession) -> None:
        """Start a background thread to read output from the bash process."""
        if not session.process or not session.process.stdout:
            return

        def reader_loop():
            try:
                for line in iter(session.process.stdout.readline, ''):
                    if line:
                        with session._output_lock:
                            session.output_buffer.append(line.rstrip('\n'))
            except Exception:
                pass  # Process exited or pipe broken

        # Ensure the lock exists on the session
        if not hasattr(session, '_output_lock'):
            session._output_lock = threading.Lock()
        t = threading.Thread(target=reader_loop, daemon=True)
        t.start()

    async def start_session(
        self,
        command: str | None = None,
        working_dir: str | None = None,
    ) -> str:
        """Start a new bash session.

        Args:
            command: Optional initial command to run in the session
            working_dir: Working directory for the session

        Returns:
            Session ID for tracking the shell
        """
        async with self._lock:
            session_id = str(uuid.uuid4())[:8]

            cwd = Path(working_dir).resolve() if working_dir else Path.cwd()
            if not cwd.exists():
                raise ValueError(f"Working directory '{working_dir}' does not exist")

            # Start bash process using platform-appropriate executable
            bash_exec = _get_bash_executable()
            process = subprocess.Popen(
                [bash_exec],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                bufsize=1,
            )

            session = BashSession(
                session_id=session_id,
                process=process,
                working_dir=str(cwd),
                is_running=True,
            )
            session._output_lock = threading.Lock()
            self.sessions[session_id] = session

            # Start background thread to read output
            self._start_output_reader(session)

            # Send initial command if provided
            if command:
                await self._send_command(session_id, command)

            return session_id

    async def _send_command(self, session_id: str, command: str) -> None:
        """Send a command to a running bash session."""
        session = self.sessions.get(session_id)
        if not session or not session.process:
            raise ValueError(f"Session '{session_id}' not found or not running")

        try:
            stdin = session.process.stdin
            if stdin:
                stdin.write(command + "\n")
                stdin.flush()
        except Exception as e:
            raise RuntimeError(f"Failed to send command: {e}")

    async def execute_command(
        self,
        session_id: str,
        command: str,
    ) -> str:
        """Execute a command in an existing bash session.

        Args:
            session_id: The session to execute the command in
            command: Shell command to execute

        Returns:
            Command output
        """
        async with self._lock:
            await self._send_command(session_id, command)
            # Give it a moment to produce output
            await asyncio.sleep(0.1)
            return self.get_output(session_id, tail_lines=50)

    async def _send_command_and_wait(self, session_id: str, command: str, timeout: float = 30.0) -> str:
        """Send command and poll for output without killing the session."""
        await self._send_command(session_id, command)

        session = self.sessions.get(session_id)
        if not session or not session.process:
            return "Error: Session not found"

        # Poll output buffer instead of communicate() which kills the process
        elapsed = 0.0
        poll_interval = 0.2
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            output = self.get_output(session_id)
            if output != "(no output)" and output != "":
                return output

        return f"Error: Command timed out after {timeout:.0f} seconds."

    def get_output(
        self,
        session_id: str,
        tail_lines: int | None = None,
    ) -> str:
        """Get output from a bash session.

        Args:
            session_id: The session to get output from
            tail_lines: If set, only return the last N lines

        Returns:
            Command output or error message
        """
        session = self.sessions.get(session_id)
        if not session:
            return f"Error: Session '{session_id}' not found."

        output_lock = getattr(session, '_output_lock', threading.Lock())
        with output_lock:
            buffer_copy = list(session.output_buffer)

        if not buffer_copy:
            return "(no output)"

        output = "\n".join(buffer_copy)
        if tail_lines is not None and tail_lines > 0:
            lines = output.splitlines()[-tail_lines:]
            output = "\n".join(lines)

        return output

    async def execute(
        self,
        session_id: str | None,
        command: str,
        timeout: int | None = None,
    ) -> tuple[str, str]:
        """Execute a command and return (session_id, output).

        Args:
            session_id: Existing session ID or None to create new
            command: Shell command to execute
            timeout: Optional timeout in seconds

        Returns:
            Tuple of (session_id, output)
        """
        if session_id is None:
            session_id = await self.start_session()

        try:
            if timeout:
                output = await asyncio.wait_for(
                    self._send_command_and_wait(session_id, command),
                    timeout=timeout,
                )
            else:
                output = await self._send_command_and_wait(session_id, command)

            return (session_id, output)
        except asyncio.TimeoutExpired:
            session = self.sessions.get(session_id)
            if session and session.process:
                session.process.kill()
            return (session_id, "Error: Command timed out.")

    async def kill_session(self, session_id: str) -> str:
        """Terminate a bash session.

        Args:
            session_id: The session to terminate

        Returns:
            Result message
        """
        async with self._lock:
            session = self.sessions.get(session_id)
            if not session:
                return f"Error: Session '{session_id}' not found."

            try:
                if session.process and session.is_running:
                    session.process.terminate()
                    session.process.wait(timeout=5.0)
            except Exception as e:
                if session.process:
                    session.process.kill()
                return f"Error terminating session: {e}"

            del self.sessions[session_id]
            return f"Session '{session_id}' terminated successfully."

    async def cleanup(self):
        """Clean up all sessions and resources."""
        async with self._lock:
            for session in self.sessions.values():
                if session.process:
                    try:
                        session.process.terminate()
                        session.process.wait(timeout=5.0)
                    except Exception:
                        if session.process:
                            session.process.kill()
            self.sessions.clear()

    def get_session_info(self, session_id: str) -> dict[str, Any] | None:
        """Get information about a session."""
        session = self.sessions.get(session_id)
        if not session:
            return None

        return {
            "session_id": session.session_id,
            "working_dir": session.working_dir,
            "is_running": session.is_running,
            "output_lines": len(session.output_buffer),
            "created_at": session.created_at,
        }


# Global bash manager instance for use by tools
_global_bash_manager: BashManager | None = None


def get_bash_manager() -> BashManager:
    """Get or create the global bash manager."""
    global _global_bash_manager
    if _global_bash_manager is None:
        _global_bash_manager = BashManager()
    return _global_bash_manager


async def reset_bash_manager():
    """Reset the global bash manager (useful for testing)."""
    global _global_bash_manager
    if _global_bash_manager:
        await _global_bash_manager.cleanup()
    _global_bash_manager = None
