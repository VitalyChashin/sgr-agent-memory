# Tasks: Metrics Processor Plugin System

**Input**: Design documents from `/specs/190-metrics-processors/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Create the plugin framework — ABC, registry, chain, config, and integration hooks in BaseAgent

- [x] T001 Create `MetricsProcessorRegistry` and `MetricsProcessor` ABC in sgr_agent_core/observability/metrics/processor.py
- [x] T002 Create `MetricsProcessorChain` in sgr_agent_core/observability/metrics/processor.py
- [x] T003 Create chain builder in sgr_agent_core/observability/metrics/__init__.py
- [x] T004 Add `metrics_processors` to `ObservabilityConfig` in sgr_agent_core/observability/config.py
- [x] T005 Integrate metrics chain into `BaseAgent._execute()` in sgr_agent_core/base_agent.py
- [x] T006 Integrate metrics hooks into `BaseAgent._execution_step()` in sgr_agent_core/base_agent.py

**Checkpoint**: Plugin framework complete — ABC, chain, config, and all 5 hook points wired in BaseAgent.

---

## Phase 2: User Story 1 — Track Token Usage (Priority: P1)

**Goal**: Built-in TokenEfficiencyProcessor attaches total_tokens and token_efficiency_ratio to traces.

**Independent Test**: Run agent with processor configured, verify trace has token scores.

### Implementation for User Story 1

- [x] T007 [US1] Create `TokenEfficiencyProcessor` in sgr_agent_core/observability/metrics/token_efficiency.py
- [ ] T008 [US1] Write test for TokenEfficiencyProcessor in tests/test_metrics_processors.py

**Checkpoint**: Token metrics visible on traces. US1 complete.

---

## Phase 3: User Story 2 — Track Tool Usage (Priority: P1)

**Goal**: Built-in ToolUsageProcessor attaches unique_tools_used and total_tool_calls to traces.

**Independent Test**: Run agent with processor configured, verify trace has tool usage scores.

### Implementation for User Story 2

- [x] T009 [P] [US2] Create `ToolUsageProcessor` in sgr_agent_core/observability/metrics/tool_usage.py
- [ ] T010 [US2] Write test for ToolUsageProcessor in tests/test_metrics_processors.py

**Checkpoint**: Tool usage metrics visible on traces. US2 complete.

---

## Phase 4: User Story 3 — Plugin Interface for Custom Metrics (Priority: P1)

**Goal**: External developers can create, register, and deploy custom processors.

**Independent Test**: Create a minimal custom processor, configure in YAML, verify it runs.

### Implementation for User Story 3

- [ ] T011 [US3] Write test for custom processor discovery in tests/test_metrics_processors.py — create a test processor class in the test file, verify it auto-registers in MetricsProcessorRegistry, verify build_metrics_chain resolves it by class name
- [ ] T012 [US3] Write test for import-path resolution in tests/test_metrics_processors.py — verify build_metrics_chain can resolve a fully-qualified class path (e.g., "tests.test_metrics_processors.MyTestProcessor") via importlib fallback
- [ ] T013 [US3] Write test for fail-silent behavior in tests/test_metrics_processors.py — create a processor that raises in on_tool_end, verify chain.run_hook completes without raising, verify other processors in chain still execute, verify warning is logged

**Checkpoint**: Plugin interface validated — custom processors work, fail-silent confirmed. US3 complete.

---

## Phase 5: User Story 4 — YAML Configuration (Priority: P2)

**Goal**: Processors configurable via YAML without code changes.

**Independent Test**: Add processors to config.yaml, restart, verify they run.

### Implementation for User Story 4

- [ ] T014 [US4] Write test for config parsing in tests/test_metrics_processors.py — verify ObservabilityConfig parses metrics_processors list from dict (simulated YAML), verify empty list defaults correctly, verify ProcessorDefinition parses "class" alias
- [x] T015 [US4] Update example config at examples/sgr_deep_research/config.yaml.example
- [ ] T016 [US4] Write test for unknown processor handling in tests/test_metrics_processors.py — verify build_metrics_chain logs warning and skips unknown class names without crashing

**Checkpoint**: YAML configuration validated. US4 complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T017 [P] Run `ruff check .` and `ruff format .` — all checks passed
- [ ] T018 Verify zero overhead when no processors configured — run agent without metrics_processors in config, verify no MetricsProcessorChain is created and no hook calls occur

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — can start immediately
- **US1 (Phase 2)**: Depends on Foundational (T001-T006)
- **US2 (Phase 3)**: Depends on Foundational — can run in parallel with US1
- **US3 (Phase 4)**: Depends on Foundational — can run in parallel with US1/US2
- **US4 (Phase 5)**: Depends on Foundational + at least one built-in processor (US1 or US2)
- **Polish (Phase 6)**: Depends on all user stories

### User Story Dependencies

- **US1**: Independent after Foundational
- **US2**: Independent after Foundational — can run in parallel with US1
- **US3**: Independent after Foundational — can run in parallel with US1/US2
- **US4**: Needs at least one built-in processor for config example

### Parallel Opportunities

- T007 (TokenEfficiency) and T009 (ToolUsage) can run in parallel — different files
- T011/T012/T013 (US3 tests) can run in parallel — different test scenarios
- US1, US2, US3 can all run in parallel after Foundational

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Foundational
2. Complete Phase 2: US1 (token metrics) + Phase 3: US2 (tool metrics) in parallel
3. **STOP and VALIDATE**: Rebuild Docker, configure processors, check traces for scores
4. Deploy — operators get immediate cost and tool usage visibility

### Full Delivery

1. Foundational → US1 + US2 in parallel → US3 (validation) → US4 (config) → Polish
2. Total: 18 tasks

---

## Notes

- Built-in processors import in `sgr_agent_core/observability/metrics/__init__.py` to trigger auto-registration
- Each processor is a separate file for clean imports and testing
- Chain is created fresh per `_execute()` call — no shared state between agent runs
- All hooks receive `provider` and `trace_handle` so processors can attach scores at any level
- The `_execution_step` signature needs updating to accept `metrics_chain` and `trace` params (T006)
