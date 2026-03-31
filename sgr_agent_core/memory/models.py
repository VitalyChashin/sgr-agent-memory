"""Pydantic DTOs for communication with the memory microservice."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StoreMessageRequest(BaseModel):
    """Request sent to POST /messages on the memory service."""

    session_id: str
    user_id: str | None = None
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    parent_message_id: str | None = None


class StoreMessageResponse(BaseModel):
    """Response from POST /messages."""

    message_id: str
    topic_id: str
    topic_label: str
    topic_shift: bool


class GetContextRequest(BaseModel):
    """Request sent to POST /context on the memory service."""

    session_id: str
    user_id: str | None = None
    content: str = Field(min_length=1)
    max_messages: int | None = None


class ContextMessage(BaseModel):
    """A single message inside a GetContextResponse."""

    message_id: str
    role: Literal["user", "assistant"]
    content: str
    topic_id: str
    timestamp: str


class GetContextResponse(BaseModel):
    """Response from POST /context."""

    messages: list[ContextMessage]
    current_topic_id: str
    current_topic_label: str
    topic_shift: bool
    user_message_id: str


class TopicMetadata(BaseModel):
    """Topic metadata included in the chat completion response."""

    topic_id: str
    topic_label: str
    topic_shift: bool


class PreprocessResult(BaseModel):
    """Result of memory preprocessing."""

    messages: list[dict[str, Any]]
    """Topic-filtered messages (or original messages on fallback).

    Each dict follows the OpenAI ChatCompletionMessageParam format
    (at minimum ``{"role": ..., "content": ...}``).
    """

    topic_metadata: TopicMetadata | None = None
    """Topic metadata for the response. None if memory was skipped/failed."""

    user_message_id: str | None = None
    """ID of the stored user message. Needed for postprocess linkage."""

    used_memory: bool = False
    """Whether memory was actually used (vs fallback)."""
