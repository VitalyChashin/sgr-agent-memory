"""Bounded retry-with-backoff for transient MCP connection/transport failures.

Only *transport* errors are retried (connection refused/reset, timeouts, broken
sessions). Genuine tool-logic errors — fastmcp's ``ToolError``/``FastMCPError``
family — are never retried: they reached the server and failed deterministically,
so retrying just wastes time and risks double side-effects.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import httpx
from mcp import McpError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Transport/connection exceptions worth retrying. Notably absent: fastmcp's
# ToolError/FastMCPError (logic failures) and plain ValueError/TypeError.
RETRYABLE_MCP_EXC: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    McpError,
    httpx.HTTPError,
)

# fastmcp wraps session init/teardown failures in bare RuntimeError; retry only
# these known-transient messages rather than blanket-retrying every RuntimeError.
_RETRYABLE_RUNTIME_SIGNATURES = (
    "Failed to initialize server session",
    "Server session was closed",
)


def is_retryable_mcp_error(exc: BaseException) -> bool:
    """True for transient transport errors that a reconnect might fix."""
    if isinstance(exc, RETRYABLE_MCP_EXC):
        return True
    if isinstance(exc, RuntimeError):
        return any(sig in str(exc) for sig in _RETRYABLE_RUNTIME_SIGNATURES)
    return False


@dataclass
class RetryPolicy:
    """Bounded exponential-backoff policy. ``attempts=1`` disables retry."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    backoff_factor: float = 2.0


async def with_mcp_retry(fn: Callable[[], Awaitable[T]], policy: RetryPolicy, *, what: str) -> T:
    """Run ``fn`` with retry on transient MCP errors.

    Non-retryable exceptions (e.g. ``ToolError``) and the final attempt's failure
    propagate unchanged. ``what`` is a short label used in retry log lines.
    """
    delay = policy.base_delay
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below unless retryable
            if attempt >= policy.attempts or not is_retryable_mcp_error(exc):
                raise
            logger.warning(
                "MCP %s failed (attempt %d/%d): %s — retrying in %.1fs",
                what,
                attempt,
                policy.attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * policy.backoff_factor, policy.max_delay)
    raise AssertionError("unreachable")  # loop either returns or raises
