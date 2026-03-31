# Implementation Plan: Topic-Aware Conversational Memory

**Branch**: `191-topic-aware-memory` | **Date**: 2026-03-31 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/191-topic-aware-memory/spec.md`

## Summary

Add a topic-aware conversational memory layer to SGR Agent Core that intercepts chat completion requests, communicates with an external memory microservice to store messages and retrieve topic-filtered context, and falls back transparently to raw message history when the memory service is disabled or unavailable. The memory layer operates as a preprocessing/postprocessing wrapper around the existing endpoint handler — no agent internals are modified.

## Technical Context

**Language/Version**: Python 3.12 (target), 3.11 (minimum)
**Primary Dependencies**: FastAPI ≥ 0.116.1, Pydantic ≥ 2.0, httpx[socks] ≥ 0.25.0 (already in project)
**Storage**: External memory microservice (in-memory store, out of scope for this project)
**Testing**: pytest with `asyncio_mode = "auto"`
**Target Platform**: Linux server (Docker), Windows dev
**Project Type**: Web service (FastAPI)
**Performance Goals**: Memory preprocessing < 300ms p95 (SC-002), post-processing adds 0ms to response latency (SC-003)
**Constraints**: Memory disabled by default, zero overhead when disabled, fail-fast with graceful fallback
**Scale/Scope**: Per-session topic tracking, configurable message limits (default 50 messages)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Pre-Design | Post-Design | Notes |
|-----------|-----------|-------------|-------|
| P1: Two-Phase Agent Architecture | PASS | PASS | Memory layer is pre/post-processing; no agent loop changes |
| P2: Tool-Centric Capability Model | PASS | PASS | No new tools; memory is infrastructure |
| P3: OpenAI API Compatibility | PASS | PASS | Additive optional fields only (`sessionId`, `userId`, topic metadata) |
| P4: Configuration Cascades | PASS | PASS | `MemoryConfig` nested in `GlobalConfig`, follows `ObservabilityConfig` pattern |
| P5: MCP as Extension Protocol | PASS | PASS | Direct HTTP client to purpose-built microservice (not an external tool) |
| P6: Python ≥ 3.11, Target 3.12 | PASS | PASS | Modern async, type hints throughout |
| P7: Pydantic for All Data Contracts | PASS | PASS | All memory models are Pydantic: `MemoryConfig`, request/response DTOs |
| P8: Async-First | PASS | PASS | httpx.AsyncClient, async middleware, fire-and-forget with asyncio.create_task |
| P9: Test Coverage | PASS | PASS | Unit tests for middleware, integration tests for endpoint fallback behavior |
| P10: Ruff | PASS | PASS | Standard compliance |
| P11: SSE Streaming | PASS | PASS | No impact on streaming — memory operates before/after |
| P12: Stateful Agents, Stateless API | PASS | PASS | Memory state is in external service, not server-side |
| P13: Backward-Compatible | PASS | PASS | All additive, disabled by default |
| P14: Spec-Driven Development | PASS | PASS | Following Spec Kit workflow |
| P15: Registry Auto-Discovery | N/A | N/A | No new agents or tools |

**Gate Result**: PASS — No violations.

## Project Structure

### Documentation (this feature)

```text
specs/191-topic-aware-memory/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 research output
├── data-model.md        # Phase 1 data model
├── quickstart.md        # Phase 1 quickstart guide
├── contracts/           # Phase 1 interface contracts
│   ├── memory-service-api.md    # Memory service HTTP API contract
│   └── memory-middleware.md     # Internal middleware interface
└── tasks.md             # Phase 2 task list (created by /speckit.tasks)
```

### Source Code (repository root)

```text
sgr_agent_core/
├── memory/                      # NEW: Memory subsystem package
│   ├── __init__.py              # Public exports
│   ├── config.py                # MemoryConfig Pydantic model
│   ├── client.py                # MemoryServiceClient (httpx async)
│   ├── middleware.py            # MemoryMiddleware (pre/post processing)
│   └── models.py               # Memory DTOs (requests/responses to memory service)
├── server/
│   ├── endpoints.py             # MODIFIED: Integrate memory middleware
│   ├── models.py                # MODIFIED: Add sessionId, userId to ChatCompletionRequest
│   └── app.py                   # MODIFIED: Initialize memory client in lifespan
└── agent_config.py              # MODIFIED: Add MemoryConfig to GlobalConfig

tests/
├── test_memory/                 # NEW: Memory subsystem tests
│   ├── test_config.py           # MemoryConfig tests
│   ├── test_client.py           # MemoryServiceClient tests (mocked httpx)
│   ├── test_middleware.py       # MemoryMiddleware unit tests
│   └── test_integration.py     # Endpoint integration tests with memory
└── ...
```

**Structure Decision**: Memory is a new package under `sgr_agent_core/memory/` following the project's existing flat-package structure (similar to `sgr_agent_core/observability/`, `sgr_agent_core/processors/`). Tests mirror the source structure under `tests/test_memory/`.

## Complexity Tracking

No constitution violations to justify. The design is additive and follows established patterns.
