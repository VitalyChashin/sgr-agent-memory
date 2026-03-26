"""Unit tests for NoOpProvider."""

from openai import AsyncOpenAI

from sgr_agent_core.observability.noop import NoOpProvider
from sgr_agent_core.observability.provider import SpanHandle, TraceHandle


class TestNoOpProvider:
    def setup_method(self):
        self.provider = NoOpProvider()

    def test_start_trace_returns_trace_handle(self):
        handle = self.provider.start_trace(name="test", agent_id="agent-1")
        assert isinstance(handle, TraceHandle)

    def test_start_span_returns_span_handle(self):
        handle = self.provider.start_span(name="iteration-1")
        assert isinstance(handle, SpanHandle)

    def test_start_trace_returns_singleton(self):
        h1 = self.provider.start_trace(name="a", agent_id="1")
        h2 = self.provider.start_trace(name="b", agent_id="2")
        assert h1 is h2

    def test_start_span_returns_singleton(self):
        h1 = self.provider.start_span(name="a")
        h2 = self.provider.start_span(name="b")
        assert h1 is h2

    def test_end_span_no_side_effects(self):
        handle = self.provider.start_span(name="test")
        self.provider.end_span(handle, output={"result": "ok"}, status="done", level="ERROR")

    def test_end_trace_no_side_effects(self):
        handle = self.provider.start_trace(name="test", agent_id="1")
        self.provider.end_trace(handle, output={"result": "ok"}, status="completed")

    def test_score_trace_no_side_effects(self):
        handle = self.provider.start_trace(name="test", agent_id="1")
        self.provider.score_trace(handle, name="accuracy", value=0.95, comment="good")

    def test_flush_no_side_effects(self):
        self.provider.flush()

    def test_shutdown_no_side_effects(self):
        self.provider.shutdown()

    def test_create_openai_client_returns_standard_client(self):
        client = self.provider.create_openai_client(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )
        assert isinstance(client, AsyncOpenAI)

    def test_start_trace_accepts_all_kwargs(self):
        handle = self.provider.start_trace(
            name="agent",
            agent_id="id-1",
            input={"task": "hello"},
            user_id="user-42",
            session_id="session-abc",
            tags=["test"],
            metadata={"model": "gpt-4o"},
        )
        assert isinstance(handle, TraceHandle)

    def test_start_span_accepts_all_kwargs(self):
        handle = self.provider.start_span(
            name="tool-search",
            span_type="tool",
            input={"query": "test"},
            metadata={"tool": "web_search"},
        )
        assert isinstance(handle, SpanHandle)
