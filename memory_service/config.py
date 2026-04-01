"""Configuration model for the memory microservice."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 9100


class RedisConfig(BaseModel):
    url: str = "redis://localhost:6379"
    db: int = 0
    session_ttl: int = Field(default=86400, gt=0, description="Session TTL in seconds")


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4.1-nano"
    temperature: float = 0.0
    max_tokens: int = 50


class TopicDetectionConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    max_history_messages: int = Field(default=5, gt=0)
    fail_open: bool = True


class RetrievalConfig(BaseModel):
    max_messages: int = Field(default=50, gt=0)


class ServiceConfig(BaseModel):
    """Root configuration for the memory microservice."""

    server: ServerConfig = ServerConfig()
    redis: RedisConfig = RedisConfig()
    topic_detection: TopicDetectionConfig = TopicDetectionConfig()
    retrieval: RetrievalConfig = RetrievalConfig()

    @classmethod
    def from_yaml(cls, path: str | Path) -> ServiceConfig:
        """Load configuration from a YAML file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if data is None:
            data = {}
        return cls(**data)
