# Implementation Plan: Metrics Processor Plugin System

**Branch**: `190-metrics-processors` | **Date**: 2026-03-26 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification + custom metrics plugin report from `specs/189-enrich-langfuse-traces/custom-metrics-plugin-report.md`

## Summary

Add an extensible metrics processor plugin system to SGR Agent Core that runs at defined hook points during agent execution (trace start/end, iteration end, tool end, generation end). Built-in processors track token efficiency and tool usage patterns. Custom processors are user-definable via the same pattern as MCP Payload Processors — ABC with auto-registration, YAML config, and fail-silent chain execution.

## Technical Context

**Language/Version**: Python >= 3.11, target 3.12
**Primary Dependencies**: Langfuse SDK v2.x (score API), Pydantic >= 2.0
**Storage**: N/A (metrics exported as Langfuse scores via existing provider)
**Testing**: pytest with `asyncio_mode = "auto"`
**Target Platform**: Linux server (Docker)
**Project Type**: Python library + web service
**Performance Goals**: Zero overhead when no processors configured; in-memory operations only for built-in processors
**Constraints**: All processor hooks fail-silent; new chain instance per agent execution for concurrency safety
**Scale/Scope**: New `observability/metrics/` subpackage, modifications to `BaseAgent._execute()` and `_execution_step()`, config extension

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| P1: Two-Phase Agent Architecture | PASS | Processors observe but don't modify execution flow |
| P2: Tool-Centric Capability Model | PASS | Processors are not tools — they're observability infrastructure |
| P3: OpenAI API Compatibility | PASS | No REST API changes |
| P4: Configuration Cascades | PASS | Config under `observability.metrics_processors` follows cascade |
| P7: Pydantic for All Data Contracts | PASS | ProcessorDefinition is a Pydantic model |
| P8: Async-First | PASS | All hooks are async methods |
| P9: Test Coverage | PASS | Tests for chain execution, built-in processors, fail-silent behavior |
| P10: Ruff | PASS | Standard code |
| P13: Backward-Compatible | PASS | No processors configured = zero behavior change |
| P15: Registry Auto-Discovery | PASS | Auto-registration via `__init_subclass__` matching existing pattern |

**Gate result: PASS — no violations.**

## Project Structure

### Documentation (this feature)

```text
specs/190-metrics-processors/
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
│   ├── config.py                          # MODIFIED — add metrics_processors field
│   └── metrics/                           # NEW — metrics processor subpackage
│       ├── __init__.py                    # Chain builder, registry import trigger
│       ├── processor.py                   # ABC, Chain, Registry, ProcessorDefinition
│       ├── token_efficiency.py            # Built-in: token usage metrics
│       └── tool_usage.py                  # Built-in: tool usage metrics
├── base_agent.py                          # MODIFIED — add chain init + hook calls

tests/
├── test_metrics_processors.py             # NEW — chain, built-in processors, fail-silent
```

**Structure Decision**: New `metrics/` subpackage under `observability/` following existing module pattern. Two built-in processors as separate files. One test file covering all scenarios.

## Complexity Tracking

No constitution violations — this section is empty.
