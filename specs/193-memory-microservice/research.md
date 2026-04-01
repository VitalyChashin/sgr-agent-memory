# Research: Memory Microservice

**Feature Branch**: `193-memory-microservice`
**Date**: 2026-03-31

## R1: Storage Backend

**Decision**: Redis with key-based data model. All session data stored with TTL-based expiration.

**Rationale**: The architecture proposal specifies Redis for within-session memory. Redis provides sub-millisecond reads, natural TTL expiration for session cleanup, and simple operation. Session data is ephemeral — durability is not required.

**Alternatives considered**:
- **SQLite**: Simpler to deploy (no external process), but lacks native TTL, slower for concurrent access patterns. Rejected for latency reasons.
- **PostgreSQL**: Overkill for ephemeral session data. Reserved for Phase 4 (cross-session persistence). Rejected.
- **In-memory dict**: No persistence at all, lost on restart. Redis provides the right balance of speed and operational visibility. Rejected.

## R2: Topic Classification Model Integration

**Decision**: Use the OpenAI-compatible API (via the `openai` Python SDK) with a lightweight model (gpt-4.1-nano or gpt-4o-mini). Structured JSON output for reliable parsing.

**Rationale**: The architecture proposal targets <200ms latency and <$0.0001/call. Lightweight models meet both requirements. The OpenAI SDK provides async support and is already a project dependency. Using structured JSON output (or constrained output) ensures reliable parsing.

**Alternatives considered**:
- **Local embedding similarity**: Fast but less accurate for coarse topic detection. Would need a threshold tuning step. Could be a future optimization. Rejected for Phase 1.
- **Custom fine-tuned model**: Better accuracy but higher operational overhead. Premature for Phase 1. Rejected.

## R3: Project Structure

**Decision**: Create the microservice as a new top-level package `memory_service/` within the same repository, with its own `pyproject.toml` section or standalone setup, Dockerfile, and config.

**Rationale**: The architecture proposal specifies a separate process with independent lifecycle. Keeping it in the same repo simplifies CI/CD and shared test infrastructure while maintaining deployment independence via Docker.

**Alternatives considered**:
- **Separate repository**: Cleaner separation but adds coordination overhead for API contract changes. Rejected for Phase 1 — can be extracted later.
- **Subdirectory of sgr_agent_core**: Would create confusing coupling. The service must be independently deployable. Rejected.

## R4: Framework

**Decision**: FastAPI with uvicorn, matching the SGR Agent Core stack. Pydantic v2 for models.

**Rationale**: Consistency with the existing codebase. The team already knows FastAPI. Async handlers provide the concurrency model needed for LLM calls.

**Alternatives considered**:
- **Flask/Quart**: Less async-native. Rejected.
- **Starlette directly**: Lower-level, less ergonomic for Pydantic integration. Rejected.

## R5: Redis Client Library

**Decision**: `redis-py` (async mode via `redis.asyncio`) — the standard Redis client for Python.

**Rationale**: Well-maintained, async-native, and the most widely used Redis client in the Python ecosystem. Supports connection pooling, pipelining, and TTL operations natively.

**Alternatives considered**:
- **aioredis**: Merged into redis-py as of v4.2. No longer a separate package. N/A.

## R6: API Contract Alignment

**Decision**: The microservice API matches the contract defined in `specs/191-topic-aware-memory/contracts/memory-service-api.md`: `POST /context` (combined store + retrieve), `POST /messages` (store assistant response), `GET /health`.

**Rationale**: The SGR middleware (features 191/192) is already implemented against this contract. The microservice must conform to it exactly.

**Alternatives considered**: None — the contract is fixed by the consumer.
