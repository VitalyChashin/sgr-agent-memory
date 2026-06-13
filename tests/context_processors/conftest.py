"""Shared fixtures and stubs for context processor tests."""

from typing import Any
from unittest.mock import Mock

import pytest

from sgr_agent_core.base_tool import BaseTool, SystemBaseTool


class StubWorkTool(BaseTool):
    """A non-system (work) tool that echoes a query."""

    tool_name = "stub_work_tool"
    query: str = ""

    async def __call__(self, context, config, **kw) -> str:
        return f"ok: {self.query}"


class StubFailingTool(BaseTool):
    """A work tool whose result looks like an MCP error string."""

    tool_name = "stub_failing_tool"
    query: str = ""

    async def __call__(self, context, config, **kw) -> str:
        return "Error: backend unavailable"


class StubSystemTool(SystemBaseTool):
    """A system tool — must never be counted or dropped."""

    tool_name = "stub_system_tool"
    payload: str = ""

    async def __call__(self, context, config, **kw) -> str:
        return "system ok"


class FakeProvider:
    """Records start_span calls so tests can assert emission."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    def start_span(self, *, name, span_type="span", input=None, metadata=None, _parent=None):
        self.spans.append({"name": name, "metadata": metadata})
        return object()

    def end_span(self, handle, *, output=None, status=None, level="DEFAULT"):
        pass


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def mock_context():
    ctx = Mock()
    ctx.iteration = 1
    return ctx


@pytest.fixture
def mock_config():
    return Mock()
