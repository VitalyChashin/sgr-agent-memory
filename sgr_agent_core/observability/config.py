"""Pydantic configuration models for observability."""

from typing import Any

from pydantic import BaseModel, Field, SecretStr


class LangfuseConfig(BaseModel):
    """Langfuse-specific configuration."""

    public_key: str | None = Field(
        default=None,
        description="Langfuse public key. Falls back to LANGFUSE_PUBLIC_KEY env var.",
    )
    secret_key: SecretStr | None = Field(
        default=None,
        description="Langfuse secret key. Falls back to LANGFUSE_SECRET_KEY env var.",
    )
    base_url: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse server URL. Falls back to LANGFUSE_BASE_URL env var.",
    )
    environment: str = "development"
    flush_at: int = Field(default=512, gt=0)
    flush_interval: float = Field(default=5.0, gt=0.0)
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    debug: bool = False


class McpFieldMapping(BaseModel):
    """Maps MCP request fields to Langfuse trace filter attributes.

    Field values are the names of fields in the MCP request payload
    that should be extracted and used as Langfuse trace attributes.
    """

    user_id: str = Field(default="userId", description="MCP field name → Langfuse user_id")
    session_id: str | None = Field(default=None, description="MCP field name → Langfuse session_id")
    trace_id: str = Field(default="traceId", description="MCP field name → Langfuse trace correlation ID")
    extra_fields: list[str] = Field(
        default_factory=list,
        description="Additional MCP field names to include in request_metadata",
    )
    tags_fields: list[str] = Field(
        default_factory=list,
        description="MCP field names whose values become Langfuse tags (format: 'field:value'). Tags are filterable.",
    )


class ObservabilityConfig(BaseModel):
    """Top-level observability configuration."""

    enabled: bool = Field(
        default=False,
        description="Enable observability tracing. When false, all tracing is no-op.",
    )
    provider: str = Field(
        default="langfuse",
        description="Observability provider. Currently supported: 'langfuse', 'noop'.",
    )
    capture_tool_definitions: bool = Field(
        default=True,
        description="Embed the tool/function definitions offered to the LLM in the generation's "
        "Langfuse input (renders as the Available-tools section). When false, input is the bare "
        "messages list.",
    )
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    metrics_processors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of metrics processor definitions. Each entry has 'class' and optional 'config'.",
    )
    mcp_field_mapping: McpFieldMapping = Field(
        default_factory=McpFieldMapping,
        description="Maps MCP request fields to Langfuse trace filter attributes.",
    )
