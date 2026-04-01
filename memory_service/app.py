"""FastAPI application for the memory microservice."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from memory_service.config import ServiceConfig

logger = logging.getLogger(__name__)

# Set by __main__ before uvicorn starts, or by tests
_service_config: ServiceConfig | None = None
_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the active Redis client."""
    if _redis is None:
        raise RuntimeError("Redis not initialized — is the app running?")
    return _redis


def get_config() -> "ServiceConfig":
    """Return the active service configuration."""
    if _service_config is None:
        from memory_service.config import ServiceConfig

        return ServiceConfig()
    return _service_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _redis
    config = get_config()
    _redis = aioredis.from_url(config.redis.url, db=config.redis.db, decode_responses=True)
    logger.info("Connected to Redis at %s (db=%d)", config.redis.url, config.redis.db)

    yield

    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection closed")


app = FastAPI(title="Memory Microservice", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes are registered after endpoints module is imported
from memory_service.endpoints import router  # noqa: E402

app.include_router(router)
