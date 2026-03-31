"""Topic-aware conversational memory subsystem."""

from sgr_agent_core.memory.client import MemoryServiceClient
from sgr_agent_core.memory.config import MemoryConfig
from sgr_agent_core.memory.middleware import MemoryMiddleware
from sgr_agent_core.memory.models import PreprocessResult, TopicMetadata

__all__ = [
    "MemoryConfig",
    "MemoryMiddleware",
    "MemoryServiceClient",
    "PreprocessResult",
    "TopicMetadata",
]
