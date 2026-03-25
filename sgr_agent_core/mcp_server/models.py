"""Pydantic request/response models for the MCP ask tool."""

from pydantic import BaseModel, ConfigDict


class AskRequest(BaseModel):
    """Request payload for the MCP ask tool."""

    model_config = ConfigDict(extra="allow")

    query: str
    traceId: str = "trace-default-001"
    userId: str = "user-default-001"


class AskResponse(BaseModel):
    """Response payload from the MCP ask tool."""

    model_config = ConfigDict(extra="allow")

    response: str
    traceId: str = "trace-default-001"
