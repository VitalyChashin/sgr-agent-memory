"""Lightweight in-memory session store for MCP conversation history.

The REST API is stateless — the client replays full conversation on every
request.  MCP clients send only a single query string, so the server must
accumulate turns itself to give the agent multi-turn context.

Sessions are keyed by ``sessionId``.  Each session stores a list of
message dicts (OpenAI format).  A configurable cap prevents unbounded
growth.

When the topic-aware memory middleware is also active, the session store
acts as a **fallback** — the middleware's returned messages take priority,
but the store keeps accumulating so it can provide context if the memory
service becomes temporarily unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Hard limit on number of concurrent sessions (LRU eviction)
_MAX_SESSIONS = 1000
# Default per-session message cap (user + assistant turns only — no system messages)
_DEFAULT_MAX_MESSAGES = 200


class MCPSessionStore:
    """Async-safe, LRU-bounded conversation store for MCP sessions.

    Only user and assistant messages should be stored — system messages
    belong in the agent config and should not be accumulated in history.
    """

    def __init__(self, max_sessions: int = _MAX_SESSIONS, max_messages_per_session: int = _DEFAULT_MAX_MESSAGES):
        self._max_sessions = max_sessions
        self._max_messages = max_messages_per_session
        self._sessions: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Return a copy of the conversation history for *session_id*."""
        async with self._lock:
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
                return list(self._sessions[session_id])
            return []

    async def append(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Append messages to the session, creating it if needed.

        Only stores messages with role ``user`` or ``assistant``.
        System messages are filtered out to prevent them from being
        trimmed during rolling window eviction.
        """
        # Filter to user/assistant only
        filtered = [m for m in messages if m.get("role") in ("user", "assistant")]
        if not filtered:
            return

        async with self._lock:
            if session_id not in self._sessions:
                # Evict oldest session if at capacity
                if len(self._sessions) >= self._max_sessions:
                    evicted_id, _ = self._sessions.popitem(last=False)
                    logger.debug("MCP session store evicted session %s (LRU)", evicted_id)
                self._sessions[session_id] = []
            self._sessions.move_to_end(session_id)
            session = self._sessions[session_id]
            session.extend(filtered)
            # Trim oldest messages if over cap
            if len(session) > self._max_messages:
                overflow = len(session) - self._max_messages
                del session[:overflow]

    async def clear_session(self, session_id: str) -> None:
        """Remove a session entirely."""
        async with self._lock:
            self._sessions.pop(session_id, None)


# Module-level singleton — initialized eagerly to avoid first-access race
_store = MCPSessionStore()


def get_session_store() -> MCPSessionStore:
    """Return the module-level session store singleton."""
    return _store
