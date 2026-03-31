"""Async HTTP client for the external memory microservice."""

from __future__ import annotations

import logging

import httpx

from sgr_agent_core.memory.config import MemoryConfig
from sgr_agent_core.memory.models import GetContextResponse, StoreMessageResponse

logger = logging.getLogger(__name__)


class MemoryServiceClient:
    """Thin async wrapper around the memory microservice HTTP API.

    Uses ``httpx.AsyncClient`` with connection pooling and a
    configurable per-request timeout.  The client is designed to
    fail-fast — no retries are performed.
    """

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.service_url,
            timeout=httpx.Timeout(config.timeout),
        )

    async def get_context(
        self,
        session_id: str,
        content: str,
        user_id: str | None = None,
        max_messages: int | None = None,
    ) -> GetContextResponse:
        """Store the current user message and retrieve topic-filtered context.

        Calls ``POST /context`` on the memory service.

        Raises:
            httpx.TimeoutException: If the request exceeds the configured timeout.
            httpx.ConnectError: If the memory service is unreachable.
            httpx.HTTPStatusError: If the memory service returns a non-2xx status.
        """
        payload: dict = {
            "session_id": session_id,
            "content": content,
        }
        if user_id is not None:
            payload["user_id"] = user_id
        if max_messages is not None:
            payload["max_messages"] = max_messages
        else:
            payload["max_messages"] = self._config.max_messages

        response = await self._client.post("/context", json=payload)
        response.raise_for_status()
        return GetContextResponse.model_validate_json(response.content)

    async def store_message(
        self,
        session_id: str,
        role: str,
        content: str,
        parent_message_id: str | None = None,
        user_id: str | None = None,
    ) -> StoreMessageResponse:
        """Store a single message (typically the assistant response).

        Calls ``POST /messages`` on the memory service.
        """
        payload: dict = {
            "session_id": session_id,
            "role": role,
            "content": content,
        }
        if user_id is not None:
            payload["user_id"] = user_id
        if parent_message_id is not None:
            payload["parent_message_id"] = parent_message_id

        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return StoreMessageResponse.model_validate_json(response.content)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
