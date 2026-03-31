# Tasks: Memory Middleware for MCP Endpoint

**Input**: Design documents from `/specs/192-mcp-memory-middleware/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Included — the project constitution (P9) requires test coverage for new features.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## User Story Mapping

| Story | Spec Priority | Title |
|-------|--------------|-------|
| US1 | P1 | Topic-Filtered Context for MCP Tool Calls |
| US2 | P1 | Transparent Fallback for MCP When Memory Is Unavailable |
| US3 | P2 | Topic Metadata in MCP Response |

---

## Phase 1: Setup

**Purpose**: No project setup needed — this feature modifies existing files only

*(No tasks — feature 191 already provides the memory package and test infrastructure)*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Update MCP models to accept sessionId and return topic metadata (needed by all user stories)

- [x] T001 Add optional `sessionId` field (default `""`) to `AskRequest` model in `sgr_agent_core/mcp_server/models.py`
- [x] T002 Add optional `topicId`, `topicLabel`, `topicShift` fields (default `None`) to `AskResponse` model in `sgr_agent_core/mcp_server/models.py`
- [x] T003 Add `sessionId` parameter (default `""`) to the `ask()` tool function signature in `sgr_agent_core/mcp_server/server.py`
- [x] T004 [P] Write unit tests for updated `AskRequest` and `AskResponse` models in `tests/test_mcp_memory.py` — verify sessionId is optional, topicId/topicLabel/topicShift are optional and excluded from JSON when None

**Checkpoint**: MCP models accept sessionId and can return topic metadata. Existing behavior unchanged.

---

## Phase 3: User Story 1 — Topic-Filtered Context for MCP Tool Calls (Priority: P1) 🎯 MVP

**Goal**: When an MCP client provides a sessionId, the agent receives topic-filtered conversation history instead of a single-message context.

**Independent Test**: Send multiple `ask` calls with `sessionId` across topics; verify the agent receives filtered messages.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T005 [P] [US1] Write unit test for `ask()` with sessionId — mock `get_memory_middleware()` to return a middleware whose `preprocess()` returns filtered messages; verify `AgentFactory.create()` receives filtered messages instead of single-query message in `tests/test_mcp_memory.py`
- [x] T006 [P] [US1] Write unit test for `ask()` postprocess — mock middleware and verify `postprocess()` is called with the agent's result after execution in `tests/test_mcp_memory.py`

### Implementation for User Story 1

- [x] T007 [US1] Add memory preprocessing to `ask()` handler in `sgr_agent_core/mcp_server/server.py` — import `get_memory_middleware` from endpoints, call `preprocess(messages, session_id, user_id)` when `sessionId` is non-empty and middleware is available, use filtered `task_messages` for `AgentFactory.create()`
- [x] T008 [US1] Add memory postprocessing to `ask()` handler in `sgr_agent_core/mcp_server/server.py` — after `agent.execute()`, call `postprocess(session_id, user_message_id, result, user_id)` synchronously when memory was used; catch and log errors without propagating

**Checkpoint**: MCP ask tool provides topic-filtered context and stores responses when sessionId is provided

---

## Phase 4: User Story 2 — Transparent Fallback for MCP When Memory Is Unavailable (Priority: P1)

**Goal**: When memory is disabled, unavailable, or sessionId is absent, MCP behavior is identical to current.

**Independent Test**: Send `ask` calls without sessionId and with a failing memory service; verify identical behavior to pre-feature system.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US2] Write unit test for `ask()` without sessionId — verify no memory calls are made and agent receives single-query message `[{"role": "user", "content": query}]` in `tests/test_mcp_memory.py`
- [x] T010 [P] [US2] Write unit test for `ask()` with empty sessionId — verify memory is skipped when sessionId is `""` in `tests/test_mcp_memory.py`
- [x] T011 [P] [US2] Write unit test for `ask()` when memory middleware is None (disabled) — verify single-query message is used in `tests/test_mcp_memory.py`
- [x] T012 [P] [US2] Write unit test for `ask()` when preprocess fails — mock preprocess to return fallback result, verify agent still receives single-query message in `tests/test_mcp_memory.py`

### Implementation for User Story 2

- [x] T013 [US2] Add guard clause to `ask()` handler in `sgr_agent_core/mcp_server/server.py` — skip memory when `sessionId` is empty or `get_memory_middleware()` returns None; ensure existing behavior is preserved exactly (single `[{"role": "user", "content": query}]` message)

**Checkpoint**: Existing MCP clients without sessionId experience zero behavior change

---

## Phase 5: User Story 3 — Topic Metadata in MCP Response (Priority: P2)

**Goal**: MCP response includes topic metadata when memory is active.

**Independent Test**: Send `ask` call with sessionId that triggers topic shift; verify response JSON includes topicId, topicLabel, topicShift.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T014 [P] [US3] Write unit test for topic metadata in response — mock preprocess to return topic metadata, verify AskResponse includes topicId/topicLabel/topicShift fields in `tests/test_mcp_memory.py`
- [x] T015 [P] [US3] Write unit test for no topic metadata when memory unused — verify AskResponse does NOT include topic fields when sessionId is absent in `tests/test_mcp_memory.py`

### Implementation for User Story 3

- [x] T016 [US3] Add topic metadata to AskResponse construction in `sgr_agent_core/mcp_server/server.py` — when `preprocess_result.topic_metadata` is available, include `topicId`, `topicLabel`, `topicShift` in the response; use `model_dump(exclude_none=True)` for JSON serialization to omit fields when memory is inactive

**Checkpoint**: MCP response includes topic metadata when memory is active, standard response when not

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation

- [x] T017 [P] Run `ruff check .` and `ruff format .` on all new and modified files
- [x] T018 Run full test suite `pytest tests/test_mcp_memory.py` and verify all tests pass
- [x] T019 Run existing MCP server tests `pytest tests/test_mcp_server.py` to confirm no regressions
- [x] T020 Run quickstart.md validation — verify MCP tool schema includes sessionId parameter

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 2)**: No dependencies — can start immediately
- **US1 (Phase 3)**: Depends on Foundational (Phase 2)
- **US2 (Phase 4)**: Depends on US1 (Phase 3) — adds guard clauses to the handler modified in US1
- **US3 (Phase 5)**: Depends on US1 (Phase 3) — adds response metadata to the handler modified in US1
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 only — core integration
- **US2 (P1)**: Depends on US1 — adds fallback to the same handler
- **US3 (P2)**: Depends on US1 — adds response metadata to the same handler

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Model changes before handler changes
- Core implementation before integration

### Parallel Opportunities

- T001, T002: Model changes can run in parallel (same file but different classes)
- T005, T006: US1 tests can run in parallel
- T009, T010, T011, T012: US2 tests can run in parallel
- T014, T015: US3 tests can run in parallel
- T017: Polish lint can run in parallel with test runs

---

## Parallel Example: User Story 1

```bash
# Launch tests first (should fail):
Task: "T005 [P] [US1] Unit test for ask() with sessionId"
Task: "T006 [P] [US1] Unit test for ask() postprocess"

# Then implement sequentially:
Task: "T007 [US1] Memory preprocessing in ask() handler"
Task: "T008 [US1] Memory postprocessing in ask() handler"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (model changes)
2. Complete Phase 3: User Story 1 (topic-filtered context)
3. **STOP and VALIDATE**: Test with `sessionId` — verify agent gets filtered context
4. Deploy/demo if ready

### Incremental Delivery

1. Complete Foundational → Models ready
2. Add US1 (Topic Filtering) → Test independently → Deploy/Demo (MVP!)
3. Add US2 (Fallback) → Test independently → Deploy/Demo (production-safe!)
4. Add US3 (Response Metadata) → Test independently → Deploy/Demo (full feature!)

---

## Notes

- This is a small feature: 2 modified files, 1 new test file, ~20 tasks total
- All memory logic is reused from feature 191 — no new memory code needed
- MCP execution is synchronous, simplifying the storage pattern (no fire-and-forget)
- The `ask()` handler in `server.py` is the single integration point for all changes
- Tests mock `get_memory_middleware()` at the module level, same pattern as REST endpoint tests
