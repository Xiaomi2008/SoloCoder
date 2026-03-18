"""Retry utilities for resilient API calls.

Provides exponential backoff with jitter for handling transient API failures.
"""

from __future__ import annotations

import asyncio
import random
import inspect
from functools import wraps
from typing import Any, Callable, TypeVar

# Updated import to relative
from .logging import logger

F = TypeVar("F", bound=Callable[..., Any])

# Default exceptions that should trigger a retry
DEFAULT_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
) -> Callable[[F], F]:
    """Decorator factory for retry logic with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 60.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        jitter: Whether to add random jitter to delays (default: True)
        retryable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorator that wraps functions with retry behavior (supports both sync and async)
    """
    if retryable_exceptions is None:
        retryable_exceptions = DEFAULT_RETRYABLE_EXCEPTIONS

    def decorator(func: F) -> F:
        is_async = asyncio.iscoroutinefunction(func)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Sync wrapper for synchronous functions."""
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            f"All {max_retries} retries exhausted for {func.__name__}: {e}"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base**attempt), max_delay)

                    # Add jitter if enabled (±25% of delay)
                    if jitter:
                        delay = delay * (0.75 + random.random() * 0.5)

                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay:.2f}s: {e}"
                    )
                    # Use time.sleep for sync functions to avoid mixing event loops
                    import time
                    time.sleep(delay)

            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry loop exit")

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Async wrapper for asynchronous functions."""
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            f"All {max_retries} retries exhausted for {func.__name__}: {e}"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base**attempt), max_delay)

                    # Add jitter if enabled (±25% of delay)
                    if jitter:
                        delay = delay * (0.75 + random.random() * 0.5)

                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay:.2f}s: {e}"
                    )
                    await asyncio.sleep(delay)

            if last_exception:
                raise last_exception
            raise RuntimeError("Unexpected retry loop exit")

        # Return the appropriate wrapper based on function type
        return sync_wrapper if not is_async else async_wrapper  # type: ignore[return-value]

    return decorator


def get_provider_retryable_exceptions(provider: str) -> tuple[type[Exception], ...]:
    """Get retryable exceptions for a specific provider.

    This imports provider-specific exceptions lazily to avoid import errors
    when the provider SDK is not installed.

    Args:
        provider: Provider name ('openai', 'anthropic', 'google')

    Returns:
        Tuple of exception types that should trigger retries
    """
    exceptions: list[type[Exception]] = list(DEFAULT_RETRYABLE_EXCEPTIONS)

    try:
        if provider == "openai":
            from openai import APIConnectionError, RateLimitError, APITimeoutError

            exceptions.extend([APIConnectionError, RateLimitError, APITimeoutError])
        elif provider == "anthropic":
            from anthropic import APIConnectionError, RateLimitError, APITimeoutError

            exceptions.extend([APIConnectionError, RateLimitError, APITimeoutError])
        elif provider == "google":
            # google-genai uses standard exceptions, no special handling needed
            pass
        elif provider == "ollama":
            try:
                from ollama import ResponseError

                exceptions.append(ResponseError)
            except ImportError:
                pass
            try:
                from httpx import ConnectError, TimeoutException

                exceptions.extend([ConnectError, TimeoutException])
            except ImportError:
                pass
    except ImportError:
        pass  # Provider SDK not installed, use defaults

    return tuple(exceptions)
