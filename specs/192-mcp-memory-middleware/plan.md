# Implementation Plan: Memory Middleware for MCP Endpoint

**Branch**: `192-mcp-memory-middleware` | **Date**: 2026-03-31 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/192-mcp-memory-middleware/spec.md`

## Summary

Extend the topic-aware memory middleware (feature 191) to the MCP `ask` tool endpoint. When an MCP client provides a `sessionId`, the system queries the memory service for topic-filtered context, provides it to the agent, and stores the assistant response after execution. The integration is a small, localized change to the `ask` handler in `mcp_server/server.py` — reusing all existing memory infrastructure with no new abstractions.

## Technical Context

**Language/Version**: Python 3.12 (target), 3.11 (minimum)
**Primary Dependencies**: FastMCP, Pydantic ≥ 2.0 (already in project); reuses memory subsystem from feature 191
**Storage**: External memory microservice (unchanged)
**Testing**: pytest with `asyncio_mode = "auto"` (strict markers)
**Target Platform**: Linux server (Docker), Windows dev
**Project Type**: Web service (FastAPI + MCP server)
**Performance Goals**: Memory preprocessing < 300ms p95 (SC-002)
**Constraints**: Zero overhead when memory disabled, backward-compatible MCP tool schema
**Scale/Scope**: Per-session topic tracking via existing memory service

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Pre-Design | Post-Design | Notes |
|-----------|-----------|-------------|-------|
| P1: Two-Phase Agent Architecture | PASS | PASS | Memory is pre/post-processing in ask handler |
| P3: OpenAI API Compatibility | N/A | N/A | MCP endpoint, not OpenAI API |
| P4: Configuration Cascades | PASS | PASS | Reuses existing MemoryConfig in GlobalConfig |
| P5: MCP as Extension Protocol | PASS | PASS | Integration within MCP server's own tool handler |
| P7: Pydantic for All Data Contracts | PASS | PASS | AskRequest/AskResponse are Pydantic models |
| P8: Async-First | PASS | PASS | Handler and middleware are async |
| P9: Test Coverage | PASS | PASS | Tests included |
| P13: Backward-Compatible | PASS | PASS | All additive, optional fields |

**Gate Result**: PASS — No violations.

## Project Structure

### Documentation (this feature)

```text
specs/192-mcp-memory-middleware/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── mcp-ask-tool.md  # MCP ask tool contract
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
sgr_agent_core/
├── mcp_server/
│   ├── server.py            # MODIFIED: Add memory preprocess/postprocess to ask handler
│   └── models.py            # MODIFIED: Add sessionId to AskRequest, topic fields to AskResponse
└── memory/                  # UNCHANGED: Reused from feature 191
    ├── middleware.py
    ├── client.py
    ├── config.py
    └── models.py

tests/
└── test_mcp_memory.py       # NEW: Tests for MCP memory integration
```

**Structure Decision**: Minimal changes — 2 modified files, 1 new test file. All memory logic is reused from the `sgr_agent_core/memory/` package.

## Complexity Tracking

No constitution violations to justify. The design is purely additive — a small integration within the existing MCP handler using established memory infrastructure.
