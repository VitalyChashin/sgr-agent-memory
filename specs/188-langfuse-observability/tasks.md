# Tasks: Langfuse Observability Integration

**Input**: Design documents from `/specs/188-langfuse-observability/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Add the observability module skeleton and optional dependency

- [x] T001 Add `langfuse>=4.0.0` as optional dependency under `[project.optional-dependencies] observability` in pyproject.toml
- [x] T002 Create observability package directory with `sgr_agent_core/observability/__init__.py` (empty initially)

**Checkpoint**: Package installable with `pip install .[observability]`, observability module importable

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core abstractions and config that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Create `ObservabilityProvider` ABC, `TraceHandle`, and `SpanHandle` classes in sgr_agent_core/observability/provider.py per the interface contract (start_trace, start_span, end_span, end_trace, score_trace, flush, shutdown, create_openai_client)
- [x] T004 Create `NoOpProvider` in sgr_agent_core/observability/noop.py — all methods are zero-cost no-ops, start_trace/start_span return shared singleton handles, create_openai_client returns standard `openai.AsyncOpenAI`
- [x] T005 [P] Create `ObservabilityConfig` and `LangfuseConfig` Pydantic models in sgr_agent_core/observability/config.py per data-model.md (enabled, provider, langfuse sub-config with public_key, secret_key, base_url, environment, flush_at, flush_interval, sample_rate, debug)
- [x] T006 Integrate `ObservabilityConfig` into `GlobalConfig` in sgr_agent_core/agent_config.py — add `observability: ObservabilityConfig` field with `default_factory=ObservabilityConfig` so missing YAML section defaults to disabled
- [x] T007 Implement `init_provider()` and `get_provider()` in sgr_agent_core/observability/__init__.py — `init_provider(config)` selects provider based on config, catches ImportError for langfuse and falls back to NoOpProvider with warning; `get_provider()` returns active provider or NoOpProvider if not initialized
- [x] T008 Write unit tests for `NoOpProvider` in tests/test_noop_provider.py — verify all methods are callable with no side effects, start_trace/start_span return valid handles, create_openai_client returns standard AsyncOpenAI
- [x] T009 [P] Write unit tests for `ObservabilityConfig` and `LangfuseConfig` in tests/test_observability_config.py — verify defaults, sample_rate validation (0.0–1.0), YAML parsing round-trip, missing section defaults to enabled=false

**Checkpoint**: Foundation ready — provider abstraction, config, NoOp implementation, and init/get functions all working. User story implementation can now begin.

---

## Phase 3: User Story 1 — Trace a Complete Agent Execution (Priority: P1)

**Goal**: Produce a structured trace tree (root trace → iteration spans → tool spans) for every agent execution when observability is enabled.

**Independent Test**: Run a single agent request with observability enabled and verify trace hierarchy in mocked Langfuse backend.

### Implementation for User Story 1

- [x] T010 [US1] Implement `LangfuseProvider` in sgr_agent_core/observability/langfuse_provider.py — implement start_trace (using `start_as_current_observation` + `propagate_attributes`), start_span, end_span, end_trace, flush, shutdown methods; all wrapped in try/except with warning-level logging on failure
- [x] T011 [US1] Add `stream_options={"include_usage": True}` to `LLMConfig.to_openai_client_kwargs()` in sgr_agent_core/agent_definition.py to enable accurate token tracking in streaming responses
- [x] T012 [US1] Instrument `BaseAgent._execute()` in sgr_agent_core/base_agent.py — add 6 provider call sites: start_trace at entry with agent metadata, start_span/end_span for each iteration, start_span/end_span for each tool invocation in _execution_step, end_trace at exit (success and error paths), flush after end_trace. Ensure end_trace is called in finally block before _save_agent_log
- [x] T013 [US1] Add `init_provider(config)` call to server startup in sgr_agent_core/server/app.py lifespan context manager (after config load, before MCP server start) and `get_provider().shutdown()` call to the shutdown/cleanup phase
- [x] T014 [US1] Write unit test for `init_provider()` in tests/test_init_provider.py — test with disabled config returns NoOp, test with langfuse config but missing package catches ImportError and returns NoOp, test with valid langfuse config returns LangfuseProvider
- [x] T015 [US1] Write integration test in tests/test_langfuse_provider.py — mock the Langfuse SDK, run a full agent execution with LangfuseProvider, verify trace tree structure: root trace exists with agent metadata, iteration spans are nested, tool spans are nested within iterations, all spans are properly closed

**Checkpoint**: Agent executions produce structured traces. User Story 1 is fully functional and testable independently.

---

## Phase 4: User Story 2 — Track LLM Token Usage and Cost (Priority: P1)

**Goal**: Every LLM call automatically captures token usage, model, latency, and cost via the instrumented OpenAI client.

**Independent Test**: Run an agent and verify each LLM generation in the trace includes token counts and model parameters.

### Implementation for User Story 2

- [x] T016 [US2] Implement `create_openai_client()` in `LangfuseProvider` in sgr_agent_core/observability/langfuse_provider.py — return `langfuse.openai.AsyncOpenAI` with api_key, base_url, and optional http_client (for proxy support)
- [x] T017 [US2] Modify `AgentFactory._create_client()` in sgr_agent_core/agent_factory.py — replace direct `AsyncOpenAI` construction with `get_provider().create_openai_client(api_key=..., base_url=..., http_client=...)`, passing the same parameters currently used
- [x] T018 [US2] Write integration test in tests/test_llm_instrumentation.py — mock Langfuse SDK, run an agent with LangfuseProvider, verify that LLM calls are captured as Generation observations with model name, token usage fields (input_tokens, output_tokens), and latency

**Checkpoint**: LLM calls are auto-instrumented. Token usage and cost are visible per generation and aggregated per trace.

---

## Phase 5: User Story 3 — Correlate Traces to Users and Sessions (Priority: P2)

**Goal**: Traces are tagged with user_id and session_id from request metadata, enabling filtering in the observability backend.

**Independent Test**: Send requests with different user/session metadata and verify traces carry those identifiers.

### Implementation for User Story 3

- [x] T019 [US3] Update `BaseAgent._execute()` instrumentation in sgr_agent_core/base_agent.py — pass `user_id=self._context.request_metadata.get("userId")` and `session_id=self._context.request_metadata.get("sessionId")` to `provider.start_trace()` (done as part of T012)
- [x] T020 [US3] Verify MCP `ask` handler passes userId and traceId through `request_metadata` to agents — confirmed at mcp_server/server.py:71
- [x] T021 [US3] Write integration test in tests/test_trace_correlation.py — create two agents with different user_id/session_id in request_metadata, verify each trace carries the correct identifiers via mocked Langfuse SDK assertions

**Checkpoint**: Traces are filterable by user and session in Langfuse.

---

## Phase 6: User Story 4 — Zero-Impact Default (Priority: P2)

**Goal**: Existing deployments with no observability config experience zero behavioral change — no new imports, no overhead, no dependency requirements.

**Independent Test**: Run the full existing test suite with observability disabled and no langfuse package installed; all tests pass identically.

### Implementation for User Story 4

- [x] T022 [US4] Write integration test in tests/test_observability_noop_path.py — run a full agent execution with `observability.enabled: false` in config, verify: NoOpProvider is used, no langfuse imports attempted, agent produces identical output to baseline, no performance regression
- [x] T023 [US4] Verify existing test suite passes with observability changes — confirmed no import path changes to existing modules; observability is additive only (new config field with default, lazy imports in base_agent/agent_factory)

**Checkpoint**: Zero-impact guarantee validated. All existing tests pass without modification.

---

## Phase 7: User Story 5 — Attach Quality Scores to Traces (Priority: P3)

**Goal**: Operators can attach quality scores to completed agent traces via the provider API.

**Independent Test**: Run an agent, then call score_trace() on the resulting trace handle and verify the score is recorded.

### Implementation for User Story 5

- [x] T024 [US5] Implement `score_trace()` in `LangfuseProvider` in sgr_agent_core/observability/langfuse_provider.py — already implemented in T010; verified
- [x] T025 [US5] Write unit test for `score_trace()` in tests/test_score_trace.py — test NoOpProvider.score_trace() is a no-op, test LangfuseProvider.score_trace() calls SDK score method with correct params, test failure in SDK is caught and logged as warning

**Checkpoint**: Quality scores can be attached to any trace. Self-improving loop foundation is in place.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, example config, and final validation

- [x] T026 [P] Update example config at examples/sgr_deep_research/config.yaml.example — add commented-out `observability:` section with all available options and explanatory comments
- [x] T027 [P] Run `ruff check .` and `ruff format .` across all new and modified files to ensure code style compliance
- [x] T028 Validate quickstart.md — quickstart.md documents the steps; full validation requires a running Langfuse instance (manual step)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — core tracing implementation
- **US2 (Phase 4)**: Depends on Foundational + partially on US1 (LangfuseProvider must exist from T010)
- **US3 (Phase 5)**: Depends on Foundational + US1 (trace instrumentation must exist from T012)
- **US4 (Phase 6)**: Depends on Foundational + US1 + US2 (all code changes must be in place to verify no regression)
- **US5 (Phase 7)**: Depends on Foundational + US1 (LangfuseProvider must exist)
- **Polish (Phase 8)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — no dependencies on other stories
- **US2 (P1)**: Requires LangfuseProvider from US1 T010, but the create_openai_client method (T016) is independent
- **US3 (P2)**: Requires trace instrumentation from US1 T012 to propagate metadata
- **US4 (P2)**: Requires all code changes to be in place — validates the zero-impact property
- **US5 (P3)**: Requires LangfuseProvider from US1 T010 — independent of US2/US3/US4

### Within Each User Story

- Models/abstractions before implementations
- Implementations before tests (except TDD sections if added)
- Core implementation before integration with other components

### Parallel Opportunities

- T005 (config models) and T003/T004 (provider ABC + NoOp) can run in parallel — different files
- T008 (NoOp tests) and T009 (config tests) can run in parallel — different test files
- T026 and T027 (polish tasks) can run in parallel — different concerns
- US5 can run in parallel with US3/US4 once US1 is complete

---

## Parallel Example: Foundational Phase

```bash
# These can run in parallel (different files):
Task T003: "Create ObservabilityProvider ABC in sgr_agent_core/observability/provider.py"
Task T005: "Create ObservabilityConfig in sgr_agent_core/observability/config.py"

# Then after T003 completes:
Task T004: "Create NoOpProvider in sgr_agent_core/observability/noop.py"

# Tests in parallel (different files):
Task T008: "Unit tests for NoOpProvider in tests/unit/test_noop_provider.py"
Task T009: "Unit tests for ObservabilityConfig in tests/unit/test_observability_config.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + 2 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (trace structure)
4. Complete Phase 4: User Story 2 (token/cost tracking)
5. **STOP and VALIDATE**: Run agent with Langfuse, verify traces with LLM generations
6. Deploy/demo if ready — operators get immediate observability value

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 (tracing) → Test with mocked SDK → First traces visible
3. Add US2 (token/cost) → Full LLM instrumentation → Cost dashboard works
4. Add US3 (correlation) → Multi-user filtering enabled
5. Add US4 (zero-impact validation) → Confidence for production rollout
6. Add US5 (scoring) → Self-improving loop foundation
7. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- The architecture document (003-langfuse-observability-architecture.md) contains detailed code examples for each implementation task
