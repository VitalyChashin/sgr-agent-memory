"""Pydantic configuration model for the memory subsystem."""

from pydantic import BaseModel, Field, field_validator


class RollingSummaryConfig(BaseModel):
    """Configuration for in-agent rolling memory buffer.

    When ``enabled`` is True the agent splits incoming conversation into
    a recent window (newest turns within the token budget) and older
    history. The older history is summarized and injected into the
    agent's context before reasoning. Disabled by default.
    """

    enabled: bool = Field(default=False, description="Whether rolling memory is active.")
    max_tokens_to_summarize: int = Field(
        default=2000, ge=100, le=32000, description="Token budget for the recent window."
    )
    summarization_model: str | None = Field(
        default=None, description="Model for summarization. None = agent's main model."
    )
    summarization_timeout_s: float = Field(
        default=10.0, gt=0.0, le=120.0, description="Hard timeout in seconds for the summarizer call."
    )


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

    rolling_summary: RollingSummaryConfig = Field(
        default_factory=RollingSummaryConfig,
        description="In-agent rolling memory buffer configuration.",
    )
