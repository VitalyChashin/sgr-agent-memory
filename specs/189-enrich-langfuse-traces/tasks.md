# Tasks: Enrich Langfuse Observability Traces

**Input**: Design documents from `/specs/189-enrich-langfuse-traces/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Extend the provider interface with generation support and add the `_last_llm_call` hook mechanism in BaseAgent

- [x] T001 Add `GenerationHandle` class to sgr_agent_core/observability/provider.py
- [x] T002 Add `start_generation()` and `end_generation()` abstract methods to `ObservabilityProvider` ABC in sgr_agent_core/observability/provider.py
- [x] T003 Add `start_generation()` and `end_generation()` no-op implementations to `NoOpProvider` in sgr_agent_core/observability/noop.py
- [x] T004 Implement `start_generation()` and `end_generation()` in `LangfuseProvider` in sgr_agent_core/observability/langfuse_provider.py
- [x] T005 Add `_last_llm_call: dict | None` attribute initialized to `None` in `BaseAgent.__init__()` in sgr_agent_core/base_agent.py
- [x] T006 Add `_truncate(value, max_len=2000) -> str` helper method to `BaseAgent` in sgr_agent_core/base_agent.py

**Checkpoint**: Provider interface extended, NoOp and Langfuse implementations ready, BaseAgent has the _last_llm_call hook and truncation helper.

---

## Phase 2: User Story 1 — See Tool Invocation Details (Priority: P1)

**Goal**: Tool spans show full arguments in Input and actual execution result in Output.

**Independent Test**: Run an agent, open trace, tool span Input shows arguments, Output shows result text (not null).

### Implementation for User Story 1

- [x] T007 [US1] Enrich tool span input in `BaseAgent._execution_step()` in sgr_agent_core/base_agent.py
- [x] T008 [US1] Capture tool execution result in `BaseAgent._execution_step()` in sgr_agent_core/base_agent.py
- [ ] T009 [US1] Write test in tests/test_trace_enrichment.py

**Checkpoint**: Tool spans show arguments and results. US1 complete.

---

## Phase 3: User Story 2 — See What the User Asked (Priority: P1)

**Goal**: Root trace Input shows user's task text, Output shows final answer.

**Independent Test**: Run an agent, open trace, root trace Input shows user message text.

### Implementation for User Story 2

- [x] T010 [US2] Enrich root trace input in `BaseAgent._execute()` in sgr_agent_core/base_agent.py
- [x] T011 [US2] Enrich root trace output in `BaseAgent._execute()` in sgr_agent_core/base_agent.py

**Checkpoint**: Root trace shows what was asked and what was answered. US2 complete.

---

## Phase 4: User Story 3 — Track LLM Token Usage and Cost (Priority: P1)

**Goal**: Each LLM call appears as a Generation observation with model, tokens, and latency.

**Independent Test**: Run an agent, open trace, generation spans visible under iteration spans with model name and token counts.

### Implementation for User Story 3

- [x] T012 [US3] Populate `self._last_llm_call` in `SGRToolCallingAgent._reasoning_phase()` in sgr_agent_core/agents/sgr_tool_calling_agent.py
- [x] T013 [US3] Populate `self._last_llm_call` in `SGRToolCallingAgent._select_action_phase()` in sgr_agent_core/agents/sgr_tool_calling_agent.py
- [x] T014 [P] [US3] Populate `self._last_llm_call` in `ToolCallingAgent._select_action_phase()` in sgr_agent_core/agents/tool_calling_agent.py
- [x] T015 [US3] Add generation span creation in `BaseAgent._execution_step()` in sgr_agent_core/base_agent.py
- [ ] T016 [US3] Write test in tests/test_generation_spans.py

**Checkpoint**: LLM calls visible as generation spans with model and token data. US3 complete.

---

## Phase 5: User Story 4 — See Agent Reasoning in Trace (Priority: P2)

**Goal**: Iteration spans include reasoning summary in output metadata.

**Independent Test**: Run an SGRToolCallingAgent, open trace, iteration span output shows reasoning data.

### Implementation for User Story 4

- [x] T017 [US4] Enrich iteration span output in `BaseAgent._execution_step()` in sgr_agent_core/base_agent.py
- [ ] T018 [US4] Write test in tests/test_trace_enrichment.py

**Checkpoint**: Iteration spans show reasoning data for agents with explicit reasoning. US4 complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T019 [P] Run `ruff check .` and `ruff format .` across all new and modified files — all checks passed
- [ ] T020 Verify all enrichment is fail-silent by testing with a mock provider that raises on every call

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — can start immediately
- **US1 (Phase 2)**: Depends on T005 (\_last_llm_call) and T006 (\_truncate) from Foundational
- **US2 (Phase 3)**: Depends on T006 (\_truncate) from Foundational
- **US3 (Phase 4)**: Depends on T001-T005 from Foundational (generation handle + provider methods + \_last_llm_call)
- **US4 (Phase 5)**: Depends on T006 (\_truncate) from Foundational
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **US1**: Independent after Foundational
- **US2**: Independent after Foundational — can run in parallel with US1
- **US3**: Independent after Foundational — can run in parallel with US1/US2
- **US4**: Independent after Foundational — can run in parallel with US1/US2/US3

### Parallel Opportunities

- T001/T005/T006 can run in parallel (different files/methods)
- US1, US2, US3, US4 can all run in parallel after Foundational
- T012/T013/T014 can run in parallel (different agent subclass files)
- T019/T020 can run in parallel

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Foundational
2. Complete Phase 2: US1 (tool details) + Phase 3: US2 (task text)
3. **STOP and VALIDATE**: Rebuild Docker, check traces — tools and root trace should be enriched
4. Deploy if ready — operators immediately see what tools did and what was asked

### Full Delivery

1. Foundational → All user stories in parallel → Polish
2. Each story is independently testable
3. Generation spans (US3) provide the highest additional value after US1/US2

---

## Notes

- All enrichment uses data already in memory — no new external calls
- All enrichment is wrapped in the provider's fail-silent try/except
- The `_truncate` helper ensures no field exceeds 2000 chars
- `_last_llm_call` pattern avoids modifying subclass method signatures
- Generation spans require modifying 3 agent subclass files (sgr_tool_calling_agent, tool_calling_agent) — SGRAgent and IronAgent can be added later as follow-up
