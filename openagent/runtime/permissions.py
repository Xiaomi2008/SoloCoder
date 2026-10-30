"""Permission policy for tool execution.

A general agent that runs `bash` and `write` without asking is a liability:
the model is not a security boundary, and a prompt injection in a file it just
read becomes a command it runs. So every tool call is classified by *risk*
before it executes, and a gate decides whether it may run.

Risk is derived from the tool name, not from the model's description of what
it wants to do. Unknown tools classify as ``exec`` — fail closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Risk = Literal["read", "write", "exec", "network", "gui", "browser", "interactive", "unknown"]

PermissionMode = Literal["allow_all", "allow_readonly", "ask"]

#: Tool name -> risk class. Anything absent is ``unknown`` -> treated as ``exec``.
TOOL_RISK: dict[str, Risk] = {
    # Inspecting state. Safe to run without asking.
    "read": "read",
    "glob": "read",
    "grep": "read",
    "recall": "read",
    "todo_list": "read",
    "bash_output": "read",
    "get_screen_resolution": "read",
    "get_screenshot_info": "read",
    "screenshot": "read",
    "skill": "read",
    "slash_command": "read",
    # Mutating files inside the session scope.
    "write": "write",
    "edit": "write",
    "notebook_edit": "write",
    "awk": "write",
    "sed": "write",
    "todo_write": "write",
    "todo_update": "write",
    # Arbitrary compute.
    "bash": "exec",
    "bash_background": "exec",
    "kill_shell": "exec",
    # Data leaving the machine.
    "web_search": "network",
    "web_fetch": "network",
    # Controlling the real desktop, outside any sandbox.
    "click": "gui",
    "double_click": "gui",
    "type_text": "gui",
    "key_combination": "gui",
    "move_mouse": "gui",
    "scroll": "gui",
    "wait": "read",
    # The agent's own browser. Reading and navigating are network; acting on a
    # page (clicks, typing, form submits) can send, buy, or delete.
    "browser_navigate": "network",
    "browser_back": "network",
    "browser_snapshot": "read",
    "browser_screenshot": "read",
    "browser_close": "read",
    "browser_click": "browser",
    "browser_type": "browser",
    "browser_select_option": "browser",
    "browser_press_key": "browser",
    # The model talking to the user.
    "ask_user_question": "interactive",
}

RISK_REASON: dict[str, str] = {
    "read": "read-only: inspects files or state, changes nothing",
    "write": "write: modifies files in the session directory",
    "exec": "exec: runs arbitrary commands",
    "network": "network: sends data off the machine",
    "gui": "gui: drives the real desktop, outside the session directory",
    "browser": "browser: acts on a web page (click, type, submit) in the agent's browser",
    "interactive": "interactive: interrupts the user",
    "unknown": "unknown tool: classified as exec",
}

#: Risks that need approval in ``ask`` mode. Reads and network are allowed:
#: a coding agent that asks per web search is noise, and the user already
#: chose a model that talks to a server.
ASK_BY_DEFAULT: frozenset[str] = frozenset({"write", "exec", "gui", "browser", "unknown"})


def tool_risk(name: str) -> Risk:
    """Risk class for a tool name; unknown tools are ``exec``-grade."""
    return TOOL_RISK.get(name, "unknown")


def risk_reason(name: str) -> str:
    risk = tool_risk(name)
    return f"{risk}: {RISK_REASON[risk].split(': ', 1)[-1]}"


@dataclass(frozen=True)
class PermissionRequest:
    """What a gate is asked to rule on."""

    tool_name: str
    arguments: dict[str, Any]
    risk: str = "unknown"
    reason: str = ""
    run_id: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class PermissionDecision:
    granted: bool
    #: ``once`` (this call), ``session`` (this tool, rest of the run),
    #: ``never`` (deny and stop asking for this tool).
    scope: str = "once"
    reason: str = ""


class PermissionGate(Protocol):
    """Decides whether a tool call may execute. Must never raise."""

    async def decide(self, request: PermissionRequest) -> PermissionDecision: ...


class AllowAllGate:
    """Unattended runs (CI, batch jobs). The user opted out of the gate."""

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision(granted=True, scope="session")


class ReadOnlyGate:
    """Explore mode: reads and searches, mutates nothing, runs nothing."""

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if request.risk in ("read", "interactive"):
            return PermissionDecision(granted=True, scope="session")
        return PermissionDecision(
            granted=False,
            scope="session",
            reason=f"permission mode 'allow_readonly' refused a {request.risk} tool",
        )


class RiskGate:
    """Ask a callback for the risky classes, allow the rest.

    The callback is where the service round-trips to the client. It must not
    raise; a raising callback is a denial, because the alternative is running
    a tool nobody approved.
    """

    def __init__(
        self,
        callback: Callable[[PermissionRequest], Awaitable[PermissionDecision]],
        *,
        ask_risks: frozenset[str] = ASK_BY_DEFAULT,
    ) -> None:
        self._callback = callback
        self._ask_risks = frozenset(ask_risks)
        self._granted: set[str] = set()
        self._denied: set[str] = set()

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if request.tool_name in self._denied:
            return PermissionDecision(granted=False, scope="never", reason="denied earlier in this run")
        if request.tool_name in self._granted:
            return PermissionDecision(granted=True, scope="session", reason="allowed earlier in this run")
        if request.risk not in self._ask_risks:
            return PermissionDecision(granted=True, scope="session")

        try:
            decision = await self._callback(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a broken approval path must not run the tool
            return PermissionDecision(granted=False, reason=f"permission prompt failed: {exc}")

        if decision.scope in ("session", "always"):
            self._granted.add(request.tool_name)
        elif decision.scope == "never":
            self._denied.add(request.tool_name)
        return decision

    def requires_prompt(self, request: PermissionRequest) -> bool:
        """Whether this call needs the user in the loop.

        The runner asks *before* calling :meth:`decide`, because in the service
        ``decide`` blocks on the client's answer: emit the prompt first or the
        client never learns there is anything to answer. Cached decisions and
        non-asked risk classes answer silently.
        """
        if request.tool_name in self._granted or request.tool_name in self._denied:
            return False
        return request.risk in self._ask_risks


def gate_for_mode(
    mode: PermissionMode = "ask",
    *,
    callback: Callable[[PermissionRequest], Awaitable[PermissionDecision]] | None = None,
    ask_risks: frozenset[str] = ASK_BY_DEFAULT,
) -> PermissionGate:
    """Build the gate a run uses, from the mode the client asked for.

    ``ask`` without a callback degrades to read-only: refusing is the only
    safe answer when there is nobody to ask.
    """
    if mode == "allow_all":
        return AllowAllGate()
    if mode == "allow_readonly":
        return ReadOnlyGate()
    if callback is None:
        return ReadOnlyGate()
    return RiskGate(callback, ask_risks=ask_risks)


__all__ = [
    "ASK_BY_DEFAULT",
    "AllowAllGate",
    "PermissionDecision",
    "PermissionGate",
    "PermissionMode",
    "PermissionRequest",
    "ReadOnlyGate",
    "Risk",
    "RiskGate",
    "TOOL_RISK",
    "gate_for_mode",
    "risk_reason",
    "tool_risk",
]
