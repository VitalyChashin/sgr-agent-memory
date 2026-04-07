"""Lightweight in-memory session store for MCP conversation history.

The REST API is stateless — the client replays full conversation on every
request.  MCP clients send only a single query string, so the server must
accumulate turns itself to give the agent multi-turn context.

Sessions are keyed by ``sessionId``.  Each session stores a list of
message dicts (OpenAI format).  A configurable cap prevents unbounded
growth.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Hard limit on number of concurrent sessions (LRU eviction)
_MAX_SESSIONS = 1000
# Default per-session message cap
_DEFAULT_MAX_MESSAGES = 200


class MCPSessionStore:
    """Thread-safe, LRU-bounded conversation store for MCP sessions."""

    def __init__(self, max_sessions: int = _MAX_SESSIONS, max_messages_per_session: int = _DEFAULT_MAX_MESSAGES):
        self._max_sessions = max_sessions
        self._max_messages = max_messages_per_session
        self._sessions: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Return a copy of the conversation history for *session_id*."""
        with self._lock:
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
                return list(self._sessions[session_id])
            return []

    def append(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Append messages to the session, creating it if needed."""
        with self._lock:
            if session_id not in self._sessions:
                # Evict oldest session if at capacity
                if len(self._sessions) >= self._max_sessions:
                    evicted_id, _ = self._sessions.popitem(last=False)
                    logger.debug("MCP session store evicted session %s (LRU)", evicted_id)
                self._sessions[session_id] = []
            self._sessions.move_to_end(session_id)
            session = self._sessions[session_id]
            session.extend(messages)
            # Trim oldest messages if over cap
            if len(session) > self._max_messages:
                overflow = len(session) - self._max_messages
                del session[:overflow]

    def clear_session(self, session_id: str) -> None:
        """Remove a session entirely."""
        with self._lock:
            self._sessions.pop(session_id, None)


# Module-level singleton — created once, shared across all MCP tool calls
_store: MCPSessionStore | None = None


def get_session_store() -> MCPSessionStore:
    """Return (or create) the module-level session store singleton."""
    global _store
    if _store is None:
        _store = MCPSessionStore()
    return _store
