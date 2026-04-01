"""Shared test fixtures for memory microservice tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

from memory_service.storage import RedisStorage


@pytest_asyncio.fixture
async def fake_redis():
    """Async fakeredis instance for testing."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def storage(fake_redis):
    """RedisStorage backed by fakeredis with short TTL."""
    return RedisStorage(fake_redis, session_ttl=3600)


@pytest.fixture
def mock_llm_same_topic():
    """Mock OpenAI client that always returns same_topic=True."""
    mock = AsyncMock()
    choice = MagicMock()
    choice.message.content = '{"same_topic": true, "new_topic_label": null}'
    response = MagicMock()
    response.choices = [choice]
    mock.chat.completions.create.return_value = response
    return mock


@pytest.fixture
def mock_llm_topic_shift():
    """Mock OpenAI client that always returns a topic shift."""
    mock = AsyncMock()
    choice = MagicMock()
    choice.message.content = '{"same_topic": false, "new_topic_label": "new topic"}'
    response = MagicMock()
    response.choices = [choice]
    mock.chat.completions.create.return_value = response
    return mock


@pytest.fixture
def mock_llm_label():
    """Mock OpenAI client that returns a label."""
    mock = AsyncMock()
    choice = MagicMock()
    choice.message.content = '{"label": "AI market trends"}'
    response = MagicMock()
    response.choices = [choice]
    mock.chat.completions.create.return_value = response
    return mock
