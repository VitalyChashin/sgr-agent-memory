"""Pydantic request/response models for the MCP ask tool."""

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    """Request payload for the MCP ask tool."""

    model_config = ConfigDict(extra="allow")

    query: str
    traceId: str = "trace-default-001"
    userId: str = "user-default-001"
    sessionId: str = Field(default="", max_length=256, pattern=r"^[^\x00-\x1f]*$")


class AskResponse(BaseModel):
    """Response payload from the MCP ask tool."""

    model_config = ConfigDict(extra="allow")

    response: str
    traceId: str = "trace-default-001"
    topicId: str | None = None
    topicLabel: str | None = None
    topicShift: bool | None = None
