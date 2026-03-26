"""Observability module for SGR Agent Core.

Provides structured tracing for agent execution loops via an
ObservabilityProvider abstraction. The default NoOpProvider ensures
zero overhead when observability is not enabled.

Usage:
    from sgr_agent_core.observability import init_provider, get_provider

    # At server startup:
    provider = init_provider(config)

    # In agent code:
    provider = get_provider()
    trace = provider.start_trace(name="my_agent", agent_id="...")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sgr_agent_core.observability.noop import NoOpProvider
from sgr_agent_core.observability.provider import GenerationHandle, ObservabilityProvider, SpanHandle, TraceHandle

if TYPE_CHECKING:
    from sgr_agent_core.agent_config import GlobalConfig

logger = logging.getLogger(__name__)

_active_provider: ObservabilityProvider | None = None

__all__ = [
    "ObservabilityProvider",
    "TraceHandle",
    "SpanHandle",
    "GenerationHandle",
    "NoOpProvider",
    "init_provider",
    "get_provider",
]


def init_provider(config: GlobalConfig) -> ObservabilityProvider:
    """Initialize the global observability provider from config.

    Called once at server startup. When observability is disabled or
    the provider package is not installed, falls back to NoOpProvider.
    """
    global _active_provider

    obs_config = config.observability
    if not obs_config.enabled:
        logger.info("Observability disabled")
        _active_provider = NoOpProvider()
        return _active_provider

    provider_type = obs_config.provider
    if provider_type == "langfuse":
        try:
            from sgr_agent_core.observability.langfuse_provider import LangfuseProvider

            _active_provider = LangfuseProvider(obs_config.langfuse)
            return _active_provider
        except ImportError:
            logger.warning(
                "Langfuse package not installed. Install with: pip install sgr-agent-core[observability]. "
                "Falling back to NoOpProvider."
            )
            _active_provider = NoOpProvider()
            return _active_provider
        except Exception as e:
            logger.warning("Failed to initialize LangfuseProvider (non-fatal): %s. Falling back to NoOpProvider.", e)
            _active_provider = NoOpProvider()
            return _active_provider

    logger.info("Unknown observability provider '%s', using NoOpProvider", provider_type)
    _active_provider = NoOpProvider()
    return _active_provider


def get_provider() -> ObservabilityProvider:
    """Get the active observability provider. Returns NoOp if not initialized."""
    global _active_provider
    if _active_provider is None:
        _active_provider = NoOpProvider()
    return _active_provider
