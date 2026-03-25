"""Integration tests for the MCP server and ask tool."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmcp.exceptions import ToolError

from sgr_agent_core.mcp_server.models import AskResponse


@pytest.fixture
def mock_global_config():
    """Create a mock GlobalConfig for MCP server tests."""
    from sgr_agent_core.mcp_server.config import MCPServerConfig

    config = Mock()
    config.mcp_server = MCPServerConfig(enabled=True)
    config.agents = {"test-agent": Mock(name="test-agent")}
    return config


@pytest.fixture
def mock_agent():
    """Create a mock agent that returns a fixed response."""
    agent = AsyncMock()
    agent.execute.return_value = "This is the agent's response."
    return agent


class TestMCPServerCreation:
    """Tests for MCP server setup."""

    def test_create_mcp_server_returns_fastmcp_instance(self, mock_global_config):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)
        assert mcp is not None
        assert mcp.name == "sgr-agent-mcp"

    @pytest.mark.asyncio
    async def test_server_has_ask_tool(self, mock_global_config):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)
        tools = await mcp.list_tools()
        tool_names = [tool.name for tool in tools]
        assert "ask" in tool_names
        assert len(tool_names) == 1


    @pytest.mark.asyncio
    async def test_custom_tool_name(self):
        from sgr_agent_core.mcp_server.config import MCPServerConfig
        from sgr_agent_core.mcp_server.server import create_mcp_server

        config = Mock()
        config.mcp_server = MCPServerConfig(enabled=True, tool_name="research")
        config.agents = {"a": Mock()}
        mcp = create_mcp_server(config)
        tools = await mcp.list_tools()
        assert tools[0].name == "research"
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_custom_tool_description(self):
        from sgr_agent_core.mcp_server.config import MCPServerConfig
        from sgr_agent_core.mcp_server.server import create_mcp_server

        config = Mock()
        config.mcp_server = MCPServerConfig(enabled=True, tool_description="My custom desc")
        config.agents = {"a": Mock()}
        mcp = create_mcp_server(config)
        tools = await mcp.list_tools()
        assert tools[0].description == "My custom desc"

    @pytest.mark.asyncio
    async def test_empty_tool_name_falls_back_to_default(self):
        from sgr_agent_core.mcp_server.config import MCPServerConfig
        from sgr_agent_core.mcp_server.server import create_mcp_server

        config = Mock()
        config.mcp_server = MCPServerConfig(enabled=True, tool_name="")
        config.agents = {"a": Mock()}
        mcp = create_mcp_server(config)
        tools = await mcp.list_tools()
        assert tools[0].name == "ask"


class TestAskTool:
    """Tests for the ask tool functionality."""

    @pytest.mark.asyncio
    async def test_successful_call_returns_valid_response(self, mock_global_config, mock_agent):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=mock_agent)
            result = await mcp.call_tool("ask", {"query": "What is AI?"})

        result_text = result.content[0].text
        data = json.loads(result_text)
        assert "response" in data
        assert "traceId" in data
        resp = AskResponse.model_validate(data)
        assert resp.response == "This is the agent's response."

    @pytest.mark.asyncio
    async def test_trace_id_echoed_from_request(self, mock_global_config, mock_agent):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=mock_agent)
            result = await mcp.call_tool("ask", {"query": "test", "traceId": "my-trace-xyz"})

        data = json.loads(result.content[0].text)
        assert data["traceId"] == "my-trace-xyz"

    @pytest.mark.asyncio
    async def test_default_trace_id_when_omitted(self, mock_global_config, mock_agent):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=mock_agent)
            result = await mcp.call_tool("ask", {"query": "test"})

        data = json.loads(result.content[0].text)
        assert data["traceId"] == "trace-default-001"

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self, mock_global_config):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)
        with pytest.raises(ToolError, match="Query must not be empty"):
            await mcp.call_tool("ask", {"query": ""})

    @pytest.mark.asyncio
    async def test_whitespace_query_returns_error(self, mock_global_config):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)
        with pytest.raises(ToolError, match="Query must not be empty"):
            await mcp.call_tool("ask", {"query": "   "})

    @pytest.mark.asyncio
    async def test_agent_execution_error_returns_tool_error(self, mock_global_config):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)
        failing_agent = AsyncMock()
        failing_agent.execute.side_effect = RuntimeError("LLM timeout")

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=failing_agent)
            with pytest.raises(ToolError, match="Agent execution failed"):
                await mcp.call_tool("ask", {"query": "test"})

    @pytest.mark.asyncio
    async def test_agent_error_does_not_leak_internal_details(self, mock_global_config):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mcp = create_mcp_server(mock_global_config)
        failing_agent = AsyncMock()
        failing_agent.execute.side_effect = RuntimeError("secret internal path /etc/config.yaml")

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=failing_agent)
            with pytest.raises(ToolError) as exc_info:
                await mcp.call_tool("ask", {"query": "test"})
            assert "/etc/config.yaml" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_agents_dict_returns_error(self):
        from sgr_agent_core.mcp_server.config import MCPServerConfig
        from sgr_agent_core.mcp_server.server import create_mcp_server

        config = Mock()
        config.mcp_server = MCPServerConfig(enabled=True)
        config.agents = {}
        mcp = create_mcp_server(config)

        with pytest.raises(ToolError, match="No agent definitions configured"):
            await mcp.call_tool("ask", {"query": "test"})

    @pytest.mark.asyncio
    async def test_misconfigured_default_agent_returns_error(self):
        from sgr_agent_core.mcp_server.config import MCPServerConfig
        from sgr_agent_core.mcp_server.server import create_mcp_server

        config = Mock()
        config.mcp_server = MCPServerConfig(enabled=True, default_agent="nonexistent-agent")
        config.agents = {"real-agent": Mock(name="real-agent")}
        mcp = create_mcp_server(config)

        with pytest.raises(ToolError, match="not found in agent definitions"):
            await mcp.call_tool("ask", {"query": "test"})

    @pytest.mark.asyncio
    async def test_default_agent_used_when_not_configured(self, mock_global_config, mock_agent):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mock_global_config.mcp_server.default_agent = None
        mcp = create_mcp_server(mock_global_config)

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=mock_agent)
            await mcp.call_tool("ask", {"query": "test"})
            call_args = mock_factory.create.call_args
            assert call_args[1]["agent_def"] == mock_global_config.agents["test-agent"]

    @pytest.mark.asyncio
    async def test_configured_default_agent_used(self, mock_global_config, mock_agent):
        from sgr_agent_core.mcp_server.server import create_mcp_server

        mock_global_config.mcp_server.default_agent = "test-agent"
        mcp = create_mcp_server(mock_global_config)

        with patch("sgr_agent_core.mcp_server.server.AgentFactory") as mock_factory:
            mock_factory.create = AsyncMock(return_value=mock_agent)
            await mcp.call_tool("ask", {"query": "test"})
            call_args = mock_factory.create.call_args
            assert call_args[1]["agent_def"] == mock_global_config.agents["test-agent"]
