"""Pydantic configuration model for the memory subsystem."""

from pydantic import BaseModel, Field, field_validator


class MemoryConfig(BaseModel):
    """Configuration for topic-aware conversational memory.

    Nested within GlobalConfig as ``memory: MemoryConfig``.
    Disabled by default — when ``enabled`` is False the memory layer
    adds zero overhead to request processing.
    """

    enabled: bool = Field(
        default=False,
        description="Whether the memory layer is active.",
    )
    service_url: str = Field(
        default="http://localhost:9100",
        description="Memory microservice base URL.",
    )

    @field_validator("service_url")
    @classmethod
    def _validate_service_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("service_url must use http:// or https:// scheme")
        return v.rstrip("/")

    timeout: float = Field(
        default=0.3,
        gt=0,
        description="HTTP timeout in seconds for memory service calls.",
    )
    max_messages: int = Field(
        default=50,
        gt=0,
        description="Maximum messages returned per topic.",
    )
