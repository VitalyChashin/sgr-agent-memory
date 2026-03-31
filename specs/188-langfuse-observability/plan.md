# Implementation Plan: Langfuse Observability Integration

**Branch**: `188-langfuse-observability` | **Date**: 2026-03-26 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/188-langfuse-observability/spec.md`

## Summary

Add structured observability tracing to SGR Agent Core via a provider abstraction with a Langfuse implementation. The integration uses two complementary mechanisms: (1) automatic LLM call instrumentation via the Langfuse OpenAI drop-in wrapper (`langfuse.openai.AsyncOpenAI`), and (2) explicit agent loop instrumentation via 6 `ObservabilityProvider` call sites in `BaseAgent._execute()`. A `NoOpProvider` default ensures zero impact on existing deployments. Langfuse is an optional dependency — the framework functions identically without it.

## Technical Context

**Language/Version**: Python ≥ 3.11, target 3.12
**Primary Dependencies**: FastAPI ≥ 0.116.1, OpenAI SDK ≥ 1.0, Pydantic ≥ 2.0, langfuse ≥ 4.0.0 (optional)
**Storage**: N/A (traces are exported to external Langfuse backend)
**Testing**: pytest with `asyncio_mode = "auto"`
**Target Platform**: Linux server (Docker), also runs on macOS/Windows for development
**Project Type**: Python library + web service
**Performance Goals**: < 5% latency overhead when observability enabled; zero overhead when disabled
**Constraints**: Observability must never crash or block agent execution; all tracing operations fail-silent
**Scale/Scope**: 8–14+ LLM calls per agent execution, concurrent agent runs in single process

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1: Two-Phase Agent Architecture | PASS | Observability wraps the existing execution cycle — does not alter the reasoning→action flow |
| P2: Tool-Centric Capability Model | PASS | No new tools introduced. Provider is infrastructure, not a tool |
| P3: OpenAI API Compatibility | PASS | No changes to REST API surface. Endpoint behavior unchanged |
| P4: Configuration Cascades | PASS | `ObservabilityConfig` integrates into `GlobalConfig` following existing cascade pattern |
| P5: MCP as Extension Protocol | N/A | Observability is internal infrastructure, not an external integration |
| P6: Python ≥ 3.11 | PASS | Uses standard Python features (type hints, asyncio, contextvars) |
| P7: Pydantic for All Data Contracts | PASS | `ObservabilityConfig` and `LangfuseConfig` are Pydantic models |
| P8: Async-First | PASS | All provider operations are synchronous but non-blocking (in-memory writes with background export). No blocking calls on the hot path |
| P9: Test Coverage | PASS | Unit tests for NoOp/Config, integration tests for trace structure |
| P10: Ruff | PASS | Standard code, no special formatting concerns |
| P11: SSE Streaming | PASS | Langfuse OpenAI wrapper is streaming-transparent — wraps the async stream and captures after consumption |
| P12: Stateful Agents, Stateless API | PASS | Provider is process-global singleton, not request-scoped state. Traces are exported externally |
| P13: Backward-Compatible | PASS | New `observability` config section is additive. Missing section = `enabled: false` = zero behavior change |
| P14: Spec-Driven | PASS | Following full Spec Kit workflow |
| P15: Registry Auto-Discovery | N/A | Provider is not a tool/agent — uses explicit factory initialization |

**Gate result: PASS — no violations.**

## Project Structure

### Documentation (this feature)

```text
specs/188-langfuse-observability/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
sgr_agent_core/
├── observability/                    # NEW — observability module
│   ├── __init__.py                   # Provider registry: init_provider(), get_provider()
│   ├── provider.py                   # ObservabilityProvider ABC, TraceHandle, SpanHandle
│   ├── noop.py                       # NoOpProvider — zero-cost default
│   ├── langfuse_provider.py          # LangfuseProvider — Langfuse SDK integration
│   └── config.py                     # ObservabilityConfig, LangfuseConfig (Pydantic)
├── agent_config.py                   # MODIFIED — add ObservabilityConfig to GlobalConfig
├── agent_factory.py                  # MODIFIED — use provider.create_openai_client()
├── base_agent.py                     # MODIFIED — add 6 provider instrumentation calls
└── server/
    └── app.py                        # MODIFIED — init_provider() at startup, shutdown() at cleanup

tests/
├── unit/
│   ├── test_noop_provider.py         # NEW — NoOpProvider method contracts
│   └── test_observability_config.py  # NEW — Config defaults, validation, YAML parsing
├── integration/
│   ├── test_langfuse_provider.py     # NEW — Trace tree structure with mocked SDK
│   └── test_observability_noop_path.py # NEW — Full agent run with disabled observability
```

**Structure Decision**: New `observability/` subpackage within `sgr_agent_core/` following existing module layout (e.g., `tools/`, `agents/`, `server/`). Tests mirror the source structure under `tests/unit/` and `tests/integration/`.

## Complexity Tracking

No constitution violations — this section is empty.
