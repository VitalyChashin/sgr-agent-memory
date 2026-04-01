"""Integration tests for memory microservice API endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio

# Use a single isolated fakeredis server for the test module
_test_server = fakeredis.FakeServer()


@pytest_asyncio.fixture
async def test_client():
    """Create test client with fakeredis and mocked LLM."""
    import memory_service.app as app_module
    from memory_service.config import ServiceConfig

    app_module._service_config = ServiceConfig()
    fake_redis = fakeredis.aioredis.FakeRedis(server=_test_server, decode_responses=True)
    await fake_redis.flushall()
    app_module._redis = fake_redis

    mock_choice = MagicMock()
    mock_choice.message.content = '{"label": "Test topic"}'
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("memory_service.topic_detector.AsyncOpenAI") as mock_openai_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        from memory_service.app import app

        with TestClient(app, raise_server_exceptions=True) as client:
            yield client, mock_client

    await fake_redis.aclose()
    app_module._redis = None


def _sid() -> str:
    """Generate a unique session ID for test isolation."""
    return f"test-{uuid.uuid4().hex[:8]}"


class TestPostContext:
    async def test_first_message_creates_session(self, test_client):
        client, _ = test_client
        sid = _sid()
        resp = client.post("/context", json={"session_id": sid, "content": "What are AI trends?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_topic_id"] == "topic-001"
        assert data["topic_shift"] is False
        assert data["user_message_id"].startswith("msg-")
        assert len(data["messages"]) == 1

    async def test_same_topic_accumulates(self, test_client):
        client, mock_llm = test_client
        sid = _sid()

        label_choice = MagicMock()
        label_choice.message.content = '{"label": "AI trends"}'
        label_resp = MagicMock()
        label_resp.choices = [label_choice]

        same_choice = MagicMock()
        same_choice.message.content = '{"same_topic": true, "new_topic_label": null}'
        same_resp = MagicMock()
        same_resp.choices = [same_choice]

        mock_llm.chat.completions.create.side_effect = [label_resp, same_resp]

        client.post("/context", json={"session_id": sid, "content": "AI trends"})
        resp = client.post("/context", json={"session_id": sid, "content": "More about AI"})

        data = resp.json()
        assert data["topic_shift"] is False
        assert len(data["messages"]) == 2

    async def test_topic_shift_returns_new_topic_only(self, test_client):
        client, mock_llm = test_client
        sid = _sid()

        label_choice = MagicMock()
        label_choice.message.content = '{"label": "AI trends"}'
        label_resp = MagicMock()
        label_resp.choices = [label_choice]

        shift_choice = MagicMock()
        shift_choice.message.content = '{"same_topic": false, "new_topic_label": "Climate policy"}'
        shift_resp = MagicMock()
        shift_resp.choices = [shift_choice]

        mock_llm.chat.completions.create.side_effect = [label_resp, shift_resp]

        client.post("/context", json={"session_id": sid, "content": "AI trends"})
        resp = client.post("/context", json={"session_id": sid, "content": "Climate policy"})

        data = resp.json()
        assert data["topic_shift"] is True
        assert data["current_topic_id"] == "topic-002"
        assert data["current_topic_label"] == "Climate policy"
        assert len(data["messages"]) == 1


class TestPostMessages:
    async def test_store_assistant_response(self, test_client):
        client, mock_llm = test_client
        sid = _sid()

        label_choice = MagicMock()
        label_choice.message.content = '{"label": "AI trends"}'
        label_resp = MagicMock()
        label_resp.choices = [label_choice]
        mock_llm.chat.completions.create.return_value = label_resp

        ctx = client.post("/context", json={"session_id": sid, "content": "AI trends"})
        user_msg_id = ctx.json()["user_message_id"]

        resp = client.post(
            "/messages",
            json={
                "session_id": sid,
                "role": "assistant",
                "content": "Here are the AI trends...",
                "parent_message_id": user_msg_id,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["topic_id"] == "topic-001"
        assert data["message_id"].startswith("msg-")


class TestHealthCheck:
    async def test_health_when_redis_available(self, test_client):
        client, _ = test_client
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
