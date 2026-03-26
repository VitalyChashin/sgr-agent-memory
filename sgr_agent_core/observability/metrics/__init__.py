"""Metrics processor plugin system.

Built-in processors are imported here to trigger auto-registration.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

import sgr_agent_core.observability.metrics.token_efficiency  # noqa: F401, E402
import sgr_agent_core.observability.metrics.tool_usage  # noqa: F401, E402
from sgr_agent_core.observability.metrics.processor import (
    MetricsProcessor,
    MetricsProcessorChain,
    MetricsProcessorDefinition,
    MetricsProcessorRegistry,
)

if TYPE_CHECKING:
    from sgr_agent_core.agent_config import GlobalConfig

logger = logging.getLogger(__name__)

__all__ = [
    "MetricsProcessor",
    "MetricsProcessorChain",
    "MetricsProcessorRegistry",
    "build_metrics_chain",
]


def build_metrics_chain(config: GlobalConfig) -> MetricsProcessorChain | None:
    """Build a metrics processor chain from config.

    Returns None if no processors are configured (zero overhead path).
    """
    processor_defs = config.observability.metrics_processors
    if not processor_defs:
        return None

    processors: list[MetricsProcessor] = []
    for raw_def in processor_defs:
        try:
            defn = MetricsProcessorDefinition.model_validate(raw_def)
            # Resolve class: registry first, then import string
            processor_cls = MetricsProcessorRegistry.get(defn.class_name)
            if processor_cls is None:
                try:
                    module_path, class_name = defn.class_name.rsplit(".", 1)
                    module = importlib.import_module(module_path)
                    processor_cls = getattr(module, class_name)
                except (ValueError, ImportError, AttributeError) as e:
                    logger.warning(
                        "Metrics processor '%s' not found in registry and cannot be imported: %s. Skipping.",
                        defn.class_name,
                        e,
                    )
                    continue
            processors.append(processor_cls(defn.config))
        except Exception as e:
            logger.warning("Failed to initialize metrics processor: %s. Skipping.", e)

    if not processors:
        return None

    logger.info("Metrics processor chain initialized with %d processor(s)", len(processors))
    return MetricsProcessorChain(processors)
