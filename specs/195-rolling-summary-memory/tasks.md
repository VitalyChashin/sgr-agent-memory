# Tasks: Rolling Summary Memory (v2 — Context Injection)

**Input**: Design documents from `/specs/195-rolling-summary-memory/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Included — constitution principle P9 requires pytest tests for all new features.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup

**Purpose**: Create/update module files for the v2 rolling memory feature.

- [x] T001 [P] Update summarizer system prompt in sgr_agent_core/memory/prompts.py — adjust to instruct the LLM to summarize conversation history that will be injected as context for the agent (not just an external summary). Keep XML turn delimiters from v1.
- [x] T002 [P] Update RollingSummaryConfig in sgr_agent_core/memory/config.py — remove `parallel_with_first_iteration` field (incompatible with context injection per research R6). Keep enabled, max_tokens_to_summarize, summarization_model, summarization_timeout_s.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that ALL user stories depend on.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T003 Add `recent_messages: list[dict] | None = None` field to AgentContext in sgr_agent_core/models.py (alongside existing conversation_summary field)
- [x] T004 [P] Add `recentMessages: list[dict] | None = None` field to AskResponse in sgr_agent_core/mcp_server/models.py
- [x] T005 [P] Update sgr_agent_core/memory/__init__.py exports if any new symbols added

**Checkpoint**: Foundation ready — config, context fields, and response models updated.

---

## Phase 3: User Story 1 — Agent Reasons with Compacted Context (Priority: P1) MVP

**Goal**: When enabled, the system splits the conversation, summarizes older history, and rewrites the agent's task_messages to `[system] → [summary msg] → [recent window]` before the reasoning loop.

**Independent Test**: Send a 20-turn conversation with token budget covering ~5 turns; verify the agent's LLM input contains system prompt + summary message + only the recent turns.

### Implementation for User Story 1

- [x] T006 [US1] Rewrite RollingSummaryBuffer in sgr_agent_core/memory/rolling_summary.py — implement three methods: (1) `split_conversation(messages, max_tokens) → (system_msgs, older_history, recent_window)` that separates system messages, then walks non-system messages from newest to oldest accumulating tokens until budget is reached, returning older messages and recent window; (2) `summarize(older_history) → str | None` that calls the LLM with the summarizer prompt and older history, with timeout and fail-open; (3) `compact_messages(system_msgs, summary_text, recent_window) → list[dict]` that builds the rewritten message list as `[system_msgs] + [{"role": "system", "content": "[Conversation history summary]: {summary}"}] + [recent_window]`
- [x] T007 [US1] Rewrite rolling summary hooks in BaseAgent._execute() in sgr_agent_core/base_agent.py — before the reasoning loop: read rolling_summary config (with dict coercion from v1 fix), if enabled create RollingSummaryBuffer, call split_conversation on self.task_messages, if older_history is non-empty call summarize(), then call compact_messages() and replace self.task_messages (saving original to self._original_task_messages). Store summary on self._context.conversation_summary and recent window on self._context.recent_messages. On failure: restore self.task_messages from self._original_task_messages and log warning. If conversation fits within budget (no older_history): skip summarization, set recent_messages to all non-system messages, set conversation_summary to None
- [x] T008 [US1] Unit test: split_conversation in tests/memory/test_rolling_summary.py — test conversation longer than budget (correct split), conversation within budget (empty older_history, all in recent_window), system messages separated, single oversized message truncated, empty message list
- [x] T009 [US1] Unit test: compact_messages in tests/memory/test_rolling_summary.py — verify output is [system msgs] + [summary system msg with prefix] + [recent window], verify summary message has role "system" and content starts with "[Conversation history summary]:"
- [x] T010 [US1] Unit test: summarize() happy path in tests/memory/test_rolling_summary.py — mock AsyncOpenAI client, verify correct model/temperature/max_tokens, verify summary string returned, verify fail-open on timeout and error
- [x] T011 [US1] Integration test: agent receives compacted context in tests/memory/test_rolling_summary_integration.py — create a minimal agent with rolling memory enabled, send a long conversation, mock LLM for both summarization and reasoning, capture the messages passed to the reasoning LLM call, verify they contain system prompt + summary message + recent window only (not full conversation)

**Checkpoint**: Core context injection working — agent reasons with compacted context.

---

## Phase 4: User Story 2 — Summary and Recent Messages on Response (Priority: P1)

**Goal**: Both `conversationSummary` and `recentMessages` appear on REST SSE and MCP responses.

**Independent Test**: Send a long conversation; verify both fields on REST metadata event and MCP JSON.

### Implementation for User Story 2

- [x] T012 [US2] Update metadata event emission in BaseAgent._execute() finally block in sgr_agent_core/base_agent.py — emit both conversationSummary and recentMessages from AgentContext via add_metadata_event (existing method from v1)
- [x] T013 [US2] Forward recentMessages in MCP server response in sgr_agent_core/mcp_server/server.py — read agent._context.recent_messages after execution and include in response_kwargs alongside conversationSummary
- [x] T014 [P] [US2] Integration test: REST SSE metadata event contains both fields in tests/memory/test_rolling_summary_integration.py — verify metadata event JSON has conversationSummary (string) and recentMessages (list of message dicts)
- [x] T015 [P] [US2] Integration test: conversation within budget returns null summary and all messages as recentMessages in tests/memory/test_rolling_summary_integration.py — short conversation, verify conversationSummary is null and recentMessages contains all non-system turns

**Checkpoint**: Both response paths deliver summary + recent messages.

---

## Phase 5: User Story 3 — Feature Off by Default (Priority: P1)

**Goal**: Default config means no context changes, no response fields, no LLM calls.

**Independent Test**: Run with default config; verify task_messages unchanged and no metadata event.

### Implementation for User Story 3

- [x] T016 [P] [US3] Unit test: RollingSummaryConfig defaults in tests/memory/test_rolling_summary_config.py — verify enabled=False, max_tokens=2000, model=None, timeout=10.0, verify parallel_with_first_iteration field does NOT exist
- [x] T017 [P] [US3] Unit test: config validation bounds in tests/memory/test_rolling_summary_config.py — verify max_tokens rejects <100 and >32000, timeout rejects <=0 and >120
- [x] T018 [US3] Integration test: disabled agent has unchanged task_messages and no metadata in tests/memory/test_rolling_summary_integration.py — create agent with default config, execute, verify self.task_messages is original list unchanged, verify no metadata event, verify no summarization LLM call

**Checkpoint**: Zero-impact when disabled verified.

---

## Phase 6: User Story 4 — Per-Agent Configuration (Priority: P2)

**Goal**: Individual agents override global rolling memory config.

**Independent Test**: Two agents with different configs; verify each behaves per its own settings.

### Implementation for User Story 4

- [x] T019 [US4] Unit test: per-agent config override in tests/memory/test_rolling_summary_config.py — AgentConfig with memory as extra field (MemoryConfig obj), AgentConfig without memory (getattr returns None), dict coercion from YAML cascade
- [x] T020 [US4] Integration test: agent A enabled with budget 4000, agent B disabled in tests/memory/test_rolling_summary_integration.py — verify A compacts context, B passes full messages

**Checkpoint**: Per-agent config verified.

---

## Phase 7: User Story 5 — Graceful Degradation (Priority: P2)

**Goal**: Summarization failures fall back to full messages, agent completes normally.

**Independent Test**: Simulate failures; verify agent uses full messages and produces correct results.

### Implementation for User Story 5

- [x] T021 [P] [US5] Unit test: summarize() fail-open on timeout in tests/memory/test_rolling_summary.py — mock timeout, verify returns None
- [x] T022 [P] [US5] Unit test: summarize() fail-open on LLM error in tests/memory/test_rolling_summary.py — mock exception, verify returns None
- [x] T023 [US5] Integration test: agent falls back to full messages on summarization failure in tests/memory/test_rolling_summary_integration.py — create agent with summary enabled, mock summarization to timeout, verify agent receives original full task_messages (not compacted), verify agent completes normally, verify no summary fields on response

**Checkpoint**: Fail-open with full message fallback verified.

---

## Phase 8: User Story 6 — Independence from Topic-Aware Memory (Priority: P2)

**Goal**: Rolling memory works without the topic-aware memory microservice.

**Independent Test**: Enable rolling memory with memory.enabled=false; verify it works.

### Implementation for User Story 6

- [x] T024 [US6] Integration test: rolling memory works with memory.enabled=false in tests/memory/test_rolling_summary_integration.py — create agent with memory.enabled=false and rolling_summary.enabled=true, execute, verify context compaction works normally

**Checkpoint**: Independence verified.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final cleanup and documentation.

- [x] T025 [P] Update config.yaml.example with revised rolling_summary section (no parallel field) at repo root
- [x] T026 [P] Run ruff check . and ruff format . across all new and modified files
- [x] T027 Run full test suite (pytest) to verify no regressions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — core context injection (MVP)
- **US2 (Phase 4)**: Depends on US1 — needs context fields to be populated
- **US3 (Phase 5)**: Depends on Foundational — can run in parallel with US1
- **US4 (Phase 6)**: Depends on Foundational — can run in parallel with US1
- **US5 (Phase 7)**: Depends on US1 — tests failure paths of the buffer
- **US6 (Phase 8)**: Depends on US1 — tests independence
- **Polish (Phase 9)**: Depends on all user stories

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — core mechanic
- **US2 (P1)**: Depends on US1 — needs context fields populated to return them
- **US3 (P1)**: Can start after Foundational — tests disabled path (independent of US1)
- **US4 (P2)**: Can start after Foundational — tests config cascade (independent of US1)
- **US5 (P2)**: Depends on US1 — tests failure paths of the buffer
- **US6 (P2)**: Depends on US1 — tests independence scenario

### Within Each User Story

- Implementation tasks before integration tests
- Unit tests can run in parallel with implementation
- Config/model tasks before service/hook tasks

### Parallel Opportunities

- T001 and T002 (Setup) in parallel
- T004 and T005 (Foundational) in parallel after T003
- US3, US4 can start in parallel after Foundational
- All unit tests within a phase marked [P] in parallel

---

## Parallel Example: User Story 1

```bash
# After Foundational, launch parallel US1 tasks:
Task T008: "Unit test: split_conversation"
Task T009: "Unit test: compact_messages"
Task T010: "Unit test: summarize()"

# Sequential:
Task T006: "Rewrite RollingSummaryBuffer" (core implementation)
Task T007: "Rewrite BaseAgent hooks" (depends on T006)
Task T011: "Integration test" (depends on T007)
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Complete Phase 1: Setup (T001-T002)
2. Complete Phase 2: Foundational (T003-T005)
3. Complete Phase 3: US1 — context injection (T006-T011)
4. Complete Phase 4: US2 — response fields (T012-T015)
5. **STOP and VALIDATE**: Test with MCP Inspector — verify agent receives compacted context and response includes both fields

### Incremental Delivery

1. Setup + Foundational → Infrastructure ready
2. US1 → Context injection works → MVP
3. US2 → Response fields on REST + MCP
4. US3 → Disabled path verified
5. US4 → Per-agent config verified
6. US5 → Fail-open verified
7. US6 → Independence verified
8. Polish → Lint, config example, full suite

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- `parallel_with_first_iteration` removed from config — sequential summarization only (research R6)
- The v1 implementation on branch 194 provides reference code for token counting, prompt injection mitigation, config coercion, and SSE metadata events
- Key difference from v1: `self.task_messages` is REWRITTEN (not just side-channel output)
