"""Unit tests for MCP server models and configuration."""

import pytest
from pydantic import ValidationError

from sgr_agent_core.mcp_server.config import MCPServerConfig
from sgr_agent_core.mcp_server.models import AskRequest, AskResponse


class TestAskRequest:
    """Tests for AskRequest model."""

    def test_default_values(self):
        req = AskRequest(query="test query")
        assert req.query == "test query"
        assert req.traceId == "trace-default-001"
        assert req.userId == "user-default-001"

    def test_custom_values(self):
        req = AskRequest(query="test", traceId="trace-123", userId="user-456")
        assert req.traceId == "trace-123"
        assert req.userId == "user-456"

    def test_missing_query_raises_validation_error(self):
        with pytest.raises(ValidationError):
            AskRequest()

    def test_serialization_roundtrip(self):
        req = AskRequest(query="test query", traceId="t1", userId="u1")
        data = req.model_dump()
        assert data == {"query": "test query", "traceId": "t1", "userId": "u1"}
        req2 = AskRequest.model_validate(data)
        assert req2 == req

    def test_extra_fields_preserved(self):
        req = AskRequest(query="test", custom_field="extra_value", priority=5)
        data = req.model_dump()
        assert data["custom_field"] == "extra_value"
        assert data["priority"] == 5


class TestAskResponse:
    """Tests for AskResponse model."""

    def test_default_trace_id(self):
        resp = AskResponse(response="answer text")
        assert resp.response == "answer text"
        assert resp.traceId == "trace-default-001"

    def test_custom_trace_id(self):
        resp = AskResponse(response="answer", traceId="trace-abc")
        assert resp.traceId == "trace-abc"

    def test_serialization_roundtrip(self):
        resp = AskResponse(response="answer", traceId="t1")
        data = resp.model_dump()
        assert data == {"response": "answer", "traceId": "t1"}
        resp2 = AskResponse.model_validate(data)
        assert resp2 == resp

    def test_extra_fields_preserved(self):
        resp = AskResponse(response="ok", traceId="t1", extra_info="metadata")
        data = resp.model_dump()
        assert data["extra_info"] == "metadata"


class TestMCPServerConfig:
    """Tests for MCPServerConfig model."""

    def test_default_values(self):
        config = MCPServerConfig()
        assert config.enabled is False
        assert config.host == "0.0.0.0"
        assert config.port == 8011
        assert config.transport == "sse"
        assert config.default_agent is None

    def test_partial_override(self):
        config = MCPServerConfig(enabled=True, port=9000)
        assert config.enabled is True
        assert config.port == 9000
        assert config.host == "0.0.0.0"  # default preserved

    def test_with_default_agent(self):
        config = MCPServerConfig(default_agent="my-agent")
        assert config.default_agent == "my-agent"
