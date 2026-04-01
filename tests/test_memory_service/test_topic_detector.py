"""Unit tests for TopicDetector."""

import pytest

from memory_service.config import TopicDetectionConfig
from memory_service.topic_detector import TopicDetector

pytestmark = pytest.mark.asyncio


class TestTopicDetector:
    async def test_same_topic_detection(self, mock_llm_same_topic):
        config = TopicDetectionConfig()
        detector = TopicDetector(config)
        detector._client = mock_llm_same_topic

        result = await detector.classify("AI trends", [{"role": "user", "content": "AI stuff"}], "More about AI")
        assert result.same_topic is True
        assert result.new_topic_label is None

    async def test_topic_shift_detection(self, mock_llm_topic_shift):
        config = TopicDetectionConfig()
        detector = TopicDetector(config)
        detector._client = mock_llm_topic_shift

        result = await detector.classify("AI trends", [{"role": "user", "content": "AI stuff"}], "What about climate?")
        assert result.same_topic is False
        assert result.new_topic_label == "new topic"

    async def test_llm_timeout_fails_open(self):
        config = TopicDetectionConfig()
        detector = TopicDetector(config)
        from unittest.mock import AsyncMock

        mock = AsyncMock()
        mock.chat.completions.create.side_effect = TimeoutError("timeout")
        detector._client = mock

        result = await detector.classify("AI", [], "test")
        assert result.same_topic is True

    async def test_malformed_json_fails_open(self):
        config = TopicDetectionConfig()
        detector = TopicDetector(config)
        from unittest.mock import AsyncMock, MagicMock

        mock = AsyncMock()
        choice = MagicMock()
        choice.message.content = "not valid json"
        response = MagicMock()
        response.choices = [choice]
        mock.chat.completions.create.return_value = response
        detector._client = mock

        result = await detector.classify("AI", [], "test")
        assert result.same_topic is True

    async def test_generate_label(self, mock_llm_label):
        config = TopicDetectionConfig()
        detector = TopicDetector(config)
        detector._client = mock_llm_label

        label = await detector.generate_label("What are the AI market trends?")
        assert label == "AI market trends"

    async def test_generate_label_fallback(self):
        config = TopicDetectionConfig()
        detector = TopicDetector(config)
        from unittest.mock import AsyncMock

        mock = AsyncMock()
        mock.chat.completions.create.side_effect = Exception("error")
        detector._client = mock

        label = await detector.generate_label("What are the AI market trends in 2025?")
        assert label == "What are the AI market trends in 2025?"[:50]
