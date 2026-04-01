# Implementation Plan: Memory Microservice

**Branch**: `193-memory-microservice` | **Date**: 2026-03-31 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/193-memory-microservice/spec.md`

## Summary

Build a standalone memory microservice that stores conversation messages with topic tags, detects coarse domain shifts via a lightweight LLM classifier, and returns topic-filtered context to the SGR Agent Core middleware. The service exposes a REST API (`POST /context`, `POST /messages`, `GET /health`) consumed by the SGR `MemoryServiceClient`, uses Redis for ephemeral session storage with automatic TTL-based cleanup, and follows a fail-open pattern at every layer.

## Technical Context

**Language/Version**: Python 3.12 (target), 3.11 (minimum)
**Primary Dependencies**: FastAPI, uvicorn, Pydantic ≥ 2.0, redis-py (async), openai SDK (for classification)
**Storage**: Redis (ephemeral, session-scoped, TTL-based expiration)
**Testing**: pytest with `asyncio_mode = "auto"`, fakeredis for unit tests
**Target Platform**: Linux server (Docker), Windows dev
**Project Type**: Web service (FastAPI microservice)
**Performance Goals**: `POST /context` < 300ms p95 (with LLM), < 10ms first message; `POST /messages` < 10ms
**Constraints**: Fail-open at every layer, per-call classification cost < $0.0001
**Scale/Scope**: 100 concurrent sessions, 50 messages/topic default limit, 24h session TTL

## Constitution Check

*The SGR constitution governs the core framework. The microservice is a separate service but follows compatible conventions.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P6: Python ≥ 3.11 | PASS | Same version targets |
| P7: Pydantic for Data Contracts | PASS | All API models are Pydantic |
| P8: Async-First | PASS | Async FastAPI handlers, async Redis client |
| P9: Test Coverage | PASS | Unit + integration tests required |
| P10: Ruff | PASS | Same linting standards |
| P13: Backward-Compatible | PASS | API contract fixed by consumer (feature 191) |

**Gate Result**: PASS

## Project Structure

### Documentation (this feature)

```text
specs/193-memory-microservice/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── memory-api.md    # REST API contract
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
memory_service/                    # NEW: Standalone microservice package
├── __init__.py                    # Package init
├── __main__.py                    # Entry point (uvicorn runner)
├── app.py                         # FastAPI app creation, lifespan, routes
├── config.py                      # ServiceConfig Pydantic model (YAML + env)
├── models.py                      # API request/response models + Message data model
├── storage.py                     # Redis storage layer (session state, message CRUD)
├── topic_detector.py              # LLM-based topic classification with fail-open
├── retriever.py                   # Topic-filtered message retrieval logic
└── endpoints.py                   # Route handlers (POST /context, POST /messages, GET /health)

tests/
└── test_memory_service/           # NEW: Microservice tests
    ├── __init__.py
    ├── conftest.py                # Shared fixtures (fakeredis, mock LLM)
    ├── test_storage.py            # Redis storage unit tests
    ├── test_topic_detector.py     # Topic detection unit tests (mock LLM)
    ├── test_retriever.py          # Retrieval logic unit tests
    ├── test_endpoints.py          # API endpoint integration tests
    └── test_e2e.py                # Multi-turn conversation integration tests

Dockerfile                         # Service container build (in memory_service/)
docker-compose.yaml                # Service + Redis (or extend existing)
memory-config.yaml.example         # Config template
```

**Structure Decision**: New top-level package `memory_service/` at repo root, parallel to `sgr_agent_core/`. Tests under `tests/test_memory_service/`. This maintains deployment independence while sharing CI/CD infrastructure.

## Complexity Tracking

No constitution violations to justify. The service is a straightforward FastAPI + Redis microservice.
