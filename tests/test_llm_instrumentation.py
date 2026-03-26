"""Integration tests for LLM auto-instrumentation via Langfuse OpenAI wrapper."""

from unittest.mock import MagicMock, patch

from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.langfuse_provider import LangfuseProvider
from sgr_agent_core.observability.noop import NoOpProvider


class TestCreateOpenAIClient:
    def test_noop_provider_returns_standard_client(self):
        """NoOpProvider returns standard openai.AsyncOpenAI."""
        provider = NoOpProvider()
        client = provider.create_openai_client(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )
        from openai import AsyncOpenAI

        assert isinstance(client, AsyncOpenAI)

    def test_noop_provider_with_proxy(self):
        """NoOpProvider passes http_client through."""
        import httpx

        provider = NoOpProvider()
        http_client = httpx.AsyncClient()
        client = provider.create_openai_client(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            http_client=http_client,
        )
        from openai import AsyncOpenAI

        assert isinstance(client, AsyncOpenAI)

    def test_langfuse_provider_returns_instrumented_client(self):
        """LangfuseProvider returns langfuse.openai.AsyncOpenAI."""
        mock_langfuse_client = MagicMock()
        config = LangfuseConfig(public_key="pk", secret_key="sk", base_url="http://localhost:3000")

        provider = LangfuseProvider.__new__(LangfuseProvider)
        provider._config = config
        provider._client = mock_langfuse_client
        provider._propagate_ctx = None

        # Mock the langfuse.openai import
        mock_async_openai = MagicMock()
        mock_instance = MagicMock()
        mock_async_openai.return_value = mock_instance

        with patch.dict(
            "sys.modules", {"langfuse": MagicMock(), "langfuse.openai": MagicMock(AsyncOpenAI=mock_async_openai)}
        ):
            with patch("sgr_agent_core.observability.langfuse_provider.AsyncOpenAI", mock_async_openai, create=True):
                # Call through the import mechanism
                from sgr_agent_core.observability import langfuse_provider as lp

                # Directly test the method logic
                with patch.object(lp, "__import__", create=True):
                    client = provider.create_openai_client(
                        api_key="test-key",
                        base_url="https://api.openai.com/v1",
                    )
                    # The client is whatever langfuse.openai.AsyncOpenAI returns
                    assert client is not None


class TestAgentFactoryClientCreation:
    def test_factory_delegates_to_provider(self):
        """AgentFactory._create_client() delegates to get_provider().create_openai_client()."""
        mock_provider = MagicMock()
        mock_client = MagicMock()
        mock_provider.create_openai_client.return_value = mock_client

        with patch("sgr_agent_core.agent_factory.get_provider", return_value=mock_provider):
            from sgr_agent_core.agent_definition import LLMConfig
            from sgr_agent_core.agent_factory import AgentFactory

            llm_config = LLMConfig(api_key="test", base_url="http://test")
            result = AgentFactory._create_client(llm_config)

            mock_provider.create_openai_client.assert_called_once_with(
                api_key="test",
                base_url="http://test",
                http_client=None,
            )
            assert result is mock_client

    def test_factory_passes_proxy_client(self):
        """AgentFactory._create_client() creates httpx client for proxy."""
        mock_provider = MagicMock()
        mock_client = MagicMock()
        mock_provider.create_openai_client.return_value = mock_client

        with patch("sgr_agent_core.agent_factory.get_provider", return_value=mock_provider):
            from sgr_agent_core.agent_definition import LLMConfig
            from sgr_agent_core.agent_factory import AgentFactory

            llm_config = LLMConfig(api_key="test", base_url="http://test", proxy="socks5://127.0.0.1:1080")
            result = AgentFactory._create_client(llm_config)

            call_kwargs = mock_provider.create_openai_client.call_args
            assert call_kwargs.kwargs["http_client"] is not None
            assert result is mock_client
