"""Pydantic models for the memory microservice API and data layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class Message(BaseModel):
    """A stored conversation message."""

    message_id: str
    session_id: str
    role: Literal["user", "assistant"]
    topic_id: str
    content: str
    timestamp: float
    parent_message_id: str | None = None
    user_id: str | None = None


class SessionTopicState(BaseModel):
    """Per-session topic tracking."""

    session_id: str
    current_topic_id: str
    topic_counter: int
    topic_labels: dict[str, str]


class TopicClassificationResult(BaseModel):
    """Output from the LLM topic classifier."""

    same_topic: bool
    new_topic_label: str | None = None


# ---------------------------------------------------------------------------
# API request/response models (matching feature 191 consumer contract)
# ---------------------------------------------------------------------------


_ID_PATTERN = r"^[a-zA-Z0-9_\-\.]+$"


class ContextRequest(BaseModel):
    """POST /context request body."""

    session_id: str = Field(max_length=256, pattern=_ID_PATTERN)
    user_id: str | None = Field(default=None, max_length=256, pattern=_ID_PATTERN)
    content: str = Field(min_length=1, max_length=100_000)
    max_messages: int | None = None


class ContextMessage(BaseModel):
    """A single message in the context response."""

    message_id: str
    role: Literal["user", "assistant"]
    content: str
    topic_id: str
    timestamp: str  # ISO 8601


class ContextResponse(BaseModel):
    """POST /context response body."""

    messages: list[ContextMessage]
    current_topic_id: str
    current_topic_label: str
    topic_shift: bool
    user_message_id: str


class StoreMessageRequest(BaseModel):
    """POST /messages request body."""

    session_id: str = Field(max_length=256, pattern=_ID_PATTERN)
    user_id: str | None = Field(default=None, max_length=256, pattern=_ID_PATTERN)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)
    parent_message_id: str | None = None


class StoreMessageResponse(BaseModel):
    """POST /messages response body."""

    message_id: str
    topic_id: str
    topic_label: str
    topic_shift: bool


class HealthResponse(BaseModel):
    """GET /health response body."""

    status: Literal["healthy", "unhealthy"]
    details: str | None = None
