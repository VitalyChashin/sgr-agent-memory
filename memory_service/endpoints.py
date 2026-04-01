"""API route handlers for the memory microservice."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from memory_service.app import get_config, get_redis
from memory_service.models import (
    ContextRequest,
    ContextResponse,
    HealthResponse,
    Message,
    StoreMessageRequest,
    StoreMessageResponse,
)
from memory_service.retriever import Retriever
from memory_service.storage import RedisStorage
from memory_service.topic_detector import TopicDetector

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_storage() -> RedisStorage:
    config = get_config()
    return RedisStorage(get_redis(), session_ttl=config.redis.session_ttl)


def _get_detector() -> TopicDetector:
    config = get_config()
    return TopicDetector(config.topic_detection)


def _get_retriever() -> Retriever:
    return Retriever(_get_storage())


# ---------------------------------------------------------------------------
# POST /context
# ---------------------------------------------------------------------------


@router.post("/context", response_model=ContextResponse)
async def post_context(request: ContextRequest):
    """Store user message and retrieve topic-filtered context."""
    storage = _get_storage()
    retriever = _get_retriever()
    config = get_config()
    max_messages = request.max_messages or config.retrieval.max_messages

    # Check for existing session
    state = await storage.get_session_state(request.session_id)

    if state is None:
        # First message — skip LLM, create new session
        detector = _get_detector()
        label = await detector.generate_label(request.content)
        state = await storage.init_session(request.session_id, label)
        topic_shift = False
    else:
        # Existing session — classify topic
        detector = _get_detector()
        recent = await storage.get_topic_messages(request.session_id, state.current_topic_id, max_messages=5)
        recent_dicts = [{"role": m.role, "content": m.content} for m in recent]
        current_label = state.topic_labels.get(state.current_topic_id, "Unknown")

        classification = await detector.classify(current_label, recent_dicts, request.content)

        if classification.same_topic:
            topic_shift = False
        else:
            new_label = classification.new_topic_label or request.content[:50]
            state = await storage.advance_topic(request.session_id, new_label)
            topic_shift = True

    # Store the user message
    msg_id = storage.generate_message_id()
    msg = Message(
        message_id=msg_id,
        session_id=request.session_id,
        role="user",
        topic_id=state.current_topic_id,
        content=request.content,
        timestamp=storage.now_timestamp(),
        user_id=request.user_id,
    )
    await storage.store_message(msg)

    # Retrieve topic-filtered context
    context_messages = await retriever.get_topic_context(
        session_id=request.session_id,
        topic_id=state.current_topic_id,
        max_messages=max_messages,
    )

    current_label = state.topic_labels.get(state.current_topic_id, "Unknown")

    return ContextResponse(
        messages=context_messages,
        current_topic_id=state.current_topic_id,
        current_topic_label=current_label,
        topic_shift=topic_shift,
        user_message_id=msg_id,
    )


# ---------------------------------------------------------------------------
# POST /messages
# ---------------------------------------------------------------------------


@router.post("/messages", response_model=StoreMessageResponse)
async def post_messages(request: StoreMessageRequest):
    """Store an assistant response message."""
    storage = _get_storage()

    state = await storage.get_session_state(request.session_id)
    if state is None:
        raise HTTPException(status_code=400, detail="Session not found")

    msg_id = storage.generate_message_id()
    msg = Message(
        message_id=msg_id,
        session_id=request.session_id,
        role=request.role,
        topic_id=state.current_topic_id,
        content=request.content,
        timestamp=storage.now_timestamp(),
        parent_message_id=request.parent_message_id,
        user_id=request.user_id,
    )
    await storage.store_message(msg)

    current_label = state.topic_labels.get(state.current_topic_id, "Unknown")

    return StoreMessageResponse(
        message_id=msg_id,
        topic_id=state.current_topic_id,
        topic_label=current_label,
        topic_shift=False,
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def get_health():
    """Health check — verifies Redis connectivity."""
    try:
        redis = get_redis()
        await redis.ping()
        return HealthResponse(status="healthy")
    except Exception:
        logger.warning("Health check failed — Redis unavailable", exc_info=True)
        return HealthResponse(status="unhealthy", details="Storage unavailable")
