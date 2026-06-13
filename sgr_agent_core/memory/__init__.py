"""Memory subsystem — topic-aware memory and rolling summary buffer."""

from sgr_agent_core.memory.client import MemoryServiceClient
from sgr_agent_core.memory.config import MemoryConfig, RollingSummaryConfig
from sgr_agent_core.memory.middleware import MemoryMiddleware
from sgr_agent_core.memory.models import PreprocessResult, TopicMetadata
from sgr_agent_core.memory.rolling_summary import RollingSummaryBuffer

__all__ = [
    "MemoryConfig",
    "MemoryMiddleware",
    "MemoryServiceClient",
    "PreprocessResult",
    "RollingSummaryBuffer",
    "RollingSummaryConfig",
    "TopicMetadata",
]
