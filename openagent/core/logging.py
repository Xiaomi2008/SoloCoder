"""Logging utilities for OpenAgent framework.

This module provides optional structured logging for debugging agent behavior,
tool execution, and LLM API calls.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, TypeVar

# Context variable to track request correlation
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Default logger instance
logger = logging.getLogger("openagent")

F = TypeVar("F", bound=Callable[..., Any])


def configure_logging(
    level: int = logging.INFO,
    format_string: str | None = None,
    handler: logging.Handler | None = None,
) -> None:
    """Configure the OpenAgent logger.

    Args:
        level: Logging level (default: INFO)
        format_string: Custom format string (default: includes timestamp, level, message)
        handler: Custom handler (default: StreamHandler to stderr)
    """
    if format_string is None:
        format_string = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    if handler is None:
        handler = logging.StreamHandler()

    handler.setFormatter(logging.Formatter(format_string))
    logger.addHandler(handler)
    logger.setLevel(level)


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return _request_id.get()


def set_request_id(request_id: str) -> None:
    """Set the current request ID in context."""
    _request_id.set(request_id)


def log_tool_execution(func: F) -> F:
    """Decorator to log tool execution with timing."""

    @wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = kwargs.get("tool_call", args[1] if len(args) > 1 else None)
        tool_name = getattr(tool_name, "name", "unknown") if tool_name else "unknown"

        logger.debug(f"Tool '{tool_name}' execution started")
        start = time.perf_counter()

        try:
            result = await func(*args, **kwargs)
            elapsed = (time.perf_counter() - start) * 1000
            is_error = getattr(result, "is_error", False)

            if is_error:
                logger.warning(f"Tool '{tool_name}' failed in {elapsed:.1f}ms: {getattr(result, 'content', 'unknown error')}")
            else:
                logger.debug(f"Tool '{tool_name}' completed in {elapsed:.1f}ms")

            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(f"Tool '{tool_name}' raised exception in {elapsed:.1f}ms: {e}")
            raise

    return async_wrapper  # type: ignore[return-value]


def log_api_call(provider_name: str, model: str) -> Callable[[F], F]:
    """Decorator factory to log LLM API calls with timing."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.debug(f"API call to {provider_name} ({model}) started")
            start = time.perf_counter()

            try:
                result = await func(*args, **kwargs)
                elapsed = (time.perf_counter() - start) * 1000
                tool_calls = getattr(result, "tool_calls", [])
                tool_count = len(tool_calls) if tool_calls else 0

                logger.info(
                    f"API call to {provider_name} ({model}) completed in {elapsed:.1f}ms "
                    f"(tools: {tool_count})"
                )
                return result
            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                logger.error(f"API call to {provider_name} ({model}) failed in {elapsed:.1f}ms: {e}")
                raise

        return wrapper  # type: ignore[return-value]

    return decorator


class AgentLogger:
    """Helper class for logging agent-specific events."""

    def __init__(self, agent_id: str | None = None) -> None:
        self.agent_id = agent_id or "default"
        self._logger = logger.getChild(f"agent.{self.agent_id}")

    def turn_start(self, turn: int, max_turns: int) -> None:
        """Log the start of an agent turn."""
        self._logger.debug(f"Turn {turn}/{max_turns} started")

    def turn_end(self, turn: int, has_tool_calls: bool) -> None:
        """Log the end of an agent turn."""
        status = "tool calls pending" if has_tool_calls else "completed"
        self._logger.debug(f"Turn {turn} {status}")

    def run_start(self, user_input_preview: str) -> None:
        """Log the start of an agent run."""
        preview = user_input_preview[:50] + "..." if len(user_input_preview) > 50 else user_input_preview
        self._logger.info(f"Agent run started: {preview!r}")

    def run_end(self, turns_used: int) -> None:
        """Log the end of an agent run."""
        self._logger.info(f"Agent run completed in {turns_used} turn(s)")

    def max_turns_reached(self) -> None:
        """Log when max turns is reached."""
        self._logger.warning("Max turns reached, returning last response")

    def info(self, message: str) -> None:
        """Log info message."""
        self._logger.info(message)

    def debug(self, message: str) -> None:
        """Log debug message."""
        self._logger.debug(message)

    def warning(self, message: str) -> None:
        """Log warning message."""
        self._logger.warning(message)

    def error(self, message: str) -> None:
        """Log error message."""
        self._logger.error(message)
