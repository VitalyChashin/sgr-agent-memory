"""Tests for MemoryServiceClient with mocked httpx responses."""

import httpx
import pytest

from sgr_agent_core.memory.client import MemoryServiceClient
from sgr_agent_core.memory.config import MemoryConfig

pytestmark = pytest.mark.asyncio


@pytest.fixture
def config():
    return MemoryConfig(enabled=True, service_url="http://localhost:9100", timeout=0.3)


@pytest.fixture
def mock_transport_success():
    """Transport that returns a successful /context response."""

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/context":
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "message_id": "msg-001",
                            "role": "user",
                            "content": "Tell me about AI",
                            "topic_id": "topic-001",
                            "timestamp": "2026-03-31T10:00:00Z",
                        }
                    ],
                    "current_topic_id": "topic-001",
                    "current_topic_label": "AI trends",
                    "topic_shift": False,
                    "user_message_id": "msg-002",
                },
            )
        if request.url.path == "/messages":
            return httpx.Response(
                200,
                json={
                    "message_id": "msg-003",
                    "topic_id": "topic-001",
                    "topic_label": "AI trends",
                    "topic_shift": False,
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handle)


@pytest.fixture
def mock_transport_500():
    """Transport that returns HTTP 500."""

    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    return httpx.MockTransport(handle)


@pytest.fixture
def mock_transport_timeout():
    """Transport that raises a timeout."""

    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out")

    return httpx.MockTransport(handle)


@pytest.fixture
def mock_transport_connect_error():
    """Transport that raises a connection error."""

    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    return httpx.MockTransport(handle)


class TestGetContext:
    async def test_success(self, config, mock_transport_success):
        client = MemoryServiceClient(config)
        client._client = httpx.AsyncClient(transport=mock_transport_success, base_url=config.service_url)
        try:
            result = await client.get_context(session_id="sess-1", content="Tell me about AI")
            assert result.current_topic_id == "topic-001"
            assert result.current_topic_label == "AI trends"
            assert result.topic_shift is False
            assert result.user_message_id == "msg-002"
            assert len(result.messages) == 1
            assert result.messages[0].role == "user"
        finally:
            await client.close()

    async def test_timeout(self, config, mock_transport_timeout):
        client = MemoryServiceClient(config)
        client._client = httpx.AsyncClient(transport=mock_transport_timeout, base_url=config.service_url)
        try:
            with pytest.raises(httpx.TimeoutException):
                await client.get_context(session_id="sess-1", content="Hello")
        finally:
            await client.close()

    async def test_connection_error(self, config, mock_transport_connect_error):
        client = MemoryServiceClient(config)
        client._client = httpx.AsyncClient(transport=mock_transport_connect_error, base_url=config.service_url)
        try:
            with pytest.raises(httpx.ConnectError):
                await client.get_context(session_id="sess-1", content="Hello")
        finally:
            await client.close()

    async def test_http_500(self, config, mock_transport_500):
        client = MemoryServiceClient(config)
        client._client = httpx.AsyncClient(transport=mock_transport_500, base_url=config.service_url)
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_context(session_id="sess-1", content="Hello")
        finally:
            await client.close()


class TestStoreMessage:
    async def test_success(self, config, mock_transport_success):
        client = MemoryServiceClient(config)
        client._client = httpx.AsyncClient(transport=mock_transport_success, base_url=config.service_url)
        try:
            result = await client.store_message(
                session_id="sess-1",
                role="assistant",
                content="Here is the answer...",
                parent_message_id="msg-002",
            )
            assert result.message_id == "msg-003"
            assert result.topic_id == "topic-001"
        finally:
            await client.close()

    async def test_store_timeout(self, config, mock_transport_timeout):
        client = MemoryServiceClient(config)
        client._client = httpx.AsyncClient(transport=mock_transport_timeout, base_url=config.service_url)
        try:
            with pytest.raises(httpx.TimeoutException):
                await client.store_message(session_id="sess-1", role="assistant", content="answer")
        finally:
            await client.close()
