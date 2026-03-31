# Implementation Plan: Enrich Langfuse Observability Traces

**Branch**: `189-enrich-langfuse-traces` | **Date**: 2026-03-26 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification + trace enrichment report from `specs/188-langfuse-observability/trace-enrichment-report.md`

## Summary

Enrich the existing Langfuse trace structure with detailed data at every level: tool arguments and results in tool spans, user task text in root traces, LLM generation spans with model/tokens/cost per call, and reasoning data in iteration spans. All enrichment is fail-silent and uses data already available in memory — no new external calls required.

## Technical Context

**Language/Version**: Python >= 3.11, target 3.12
**Primary Dependencies**: Langfuse SDK v2.x (already installed via feature 188), Pydantic >= 2.0, OpenAI SDK >= 1.0
**Storage**: N/A (traces exported to external Langfuse backend)
**Testing**: pytest with `asyncio_mode = "auto"`
**Target Platform**: Linux server (Docker)
**Project Type**: Python library + web service
**Performance Goals**: Zero additional latency — all data is already in memory, enrichment is serialization only
**Constraints**: All enrichment must be fail-silent; truncation at 2000 chars per field default
**Scale/Scope**: Modifies `BaseAgent._execute()`, `_execution_step()`, and `ObservabilityProvider` interface

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1: Two-Phase Agent Architecture | PASS | Enrichment wraps existing phases — does not alter execution flow |
| P2: Tool-Centric Capability Model | PASS | No new tools; enrichment captures existing tool data |
| P3: OpenAI API Compatibility | PASS | No REST API changes |
| P4: Configuration Cascades | PASS | No new config needed (uses existing observability config) |
| P7: Pydantic for All Data Contracts | PASS | Tool arguments come from Pydantic model_dump() |
| P8: Async-First | PASS | All enrichment is synchronous serialization within existing async methods |
| P9: Test Coverage | PASS | Tests will verify enriched trace data |
| P10: Ruff | PASS | Standard code |
| P13: Backward-Compatible | PASS | Enrichment is additive — existing traces gain more data |

**Gate result: PASS — no violations.**

## Project Structure

### Documentation (this feature)

```text
specs/189-enrich-langfuse-traces/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
sgr_agent_core/
├── observability/
│   ├── provider.py              # MODIFIED — add start_generation() to ABC
│   ├── noop.py                  # MODIFIED — add start_generation() no-op
│   └── langfuse_provider.py     # MODIFIED — implement start_generation() via SDK v2 generation()
├── base_agent.py                # MODIFIED — enrich all 6 instrumentation points + add generation spans

tests/
├── test_trace_enrichment.py     # NEW — verify enriched data at each span level
├── test_generation_spans.py     # NEW — verify LLM generation span creation
```

**Structure Decision**: No new files in the observability package — all changes are to existing files from feature 188. Two new test files.

## Complexity Tracking

No constitution violations — this section is empty.
