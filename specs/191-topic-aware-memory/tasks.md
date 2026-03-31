# Tasks: Topic-Aware Conversational Memory

**Input**: Design documents from `/specs/191-topic-aware-memory/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Included — the spec requires test coverage per constitution principle P9.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## User Story Mapping

| Story | Spec Priority | Title |
|-------|--------------|-------|
| US1 | P1 | Topic-Filtered Context in Multi-Topic Conversations |
| US2 | P1 | Transparent Fallback When Memory Is Unavailable |
| US3 | P2 | Server-Side Dialog Storage with Topic Tags |
| US4 | P2 | Configuration-Driven Memory Activation |

---

## Phase 1: Setup

**Purpose**: Create memory package structure and empty modules

- [x] T001 Create memory package directory and `sgr_agent_core/memory/__init__.py` with public exports stub
- [x] T002 [P] Create test package directory and `tests/test_memory/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented — config model, DTO models, and HTTP client

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 Implement `MemoryConfig` Pydantic model with `enabled`, `service_url`, `timeout`, `max_messages` fields and validation in `sgr_agent_core/memory/config.py` (per data-model.md entity 1)
- [x] T004 Add `memory: MemoryConfig` field to `GlobalConfig` in `sgr_agent_core/agent_config.py` (import `MemoryConfig`, add field with default `MemoryConfig()`)
- [x] T005 Implement all memory DTO models in `sgr_agent_core/memory/models.py`: `StoreMessageRequest`, `StoreMessageResponse`, `GetContextRequest`, `GetContextResponse`, `ContextMessage`, `TopicMetadata`, `PreprocessResult` (per data-model.md entities 2-7 and contracts/memory-middleware.md)
- [x] T006 Implement `MemoryServiceClient` in `sgr_agent_core/memory/client.py` with `httpx.AsyncClient`, connection pooling, configurable timeout, `get_context()` and `store_message()` methods (per contracts/memory-service-api.md endpoints POST /context and POST /messages)
- [x] T007 Add `session_id` and `user_id` optional fields (with `alias="sessionId"` / `alias="userId"`) to `ChatCompletionRequest` in `sgr_agent_core/server/models.py` and enable `populate_by_name=True` on the model config
- [x] T008 [P] Write unit tests for `MemoryConfig` validation (timeout > 0, max_messages > 0, defaults) in `tests/test_memory/test_config.py`
- [x] T009 [P] Write unit tests for `MemoryServiceClient` with mocked httpx responses (success, timeout, connection error, HTTP 500) in `tests/test_memory/test_client.py`

**Checkpoint**: Foundation ready — MemoryConfig loadable from YAML, DTOs defined, client testable in isolation

---

## Phase 3: User Story 1 — Topic-Filtered Context in Multi-Topic Conversations (Priority: P1) 🎯 MVP

**Goal**: When a user sends messages across multiple topics in a session, the agent receives only topic-relevant conversation history, reducing context noise and improving response quality.

**Independent Test**: Send a sequence of messages with a `sessionId` across 2-3 distinct topics. Verify the agent receives only messages relevant to the current topic (not the full history).

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T010 [P] [US1] Write unit tests for `MemoryMiddleware.preprocess()` success path in `tests/test_memory/test_middleware.py` — mock `MemoryServiceClient.get_context()` to return topic-filtered messages, assert `PreprocessResult.messages` contains only filtered messages and `used_memory=True`
- [x] T011 [P] [US1] Write integration test for chat completion with memory in `tests/test_memory/test_integration.py` — send request with `sessionId`, mock memory service HTTP response, verify agent receives filtered messages (not raw input)

### Implementation for User Story 1

- [x] T012 [US1] Implement `MemoryMiddleware` class in `sgr_agent_core/memory/middleware.py` with `__init__(config, client)` and `preprocess()` method: extract last user message, call `client.get_context()`, convert `ContextMessage` list to `ChatCompletionMessageParam` format, return `PreprocessResult` with topic metadata
- [x] T013 [US1] Initialize `MemoryServiceClient` and `MemoryMiddleware` in server lifespan in `sgr_agent_core/server/app.py` — create during startup when `GlobalConfig().memory.enabled`, store as module-level variable accessible to endpoints
- [x] T014 [US1] Integrate memory preprocessing into `create_chat_completion()` in `sgr_agent_core/server/endpoints.py` — call `memory_middleware.preprocess()` when `request.session_id` is present, pass `memory_result.messages` to `AgentFactory.create()` instead of `request.messages.root`
- [x] T015 [US1] Add topic metadata to SSE response: include `memory` field with `TopicMetadata` in final streaming chunk in `sgr_agent_core/server/endpoints.py` (additive field, ignored by standard OpenAI clients)
- [x] T016 [US1] Add structured logging for topic shifts: emit INFO log with `topic_id`, `topic_label`, `session_id` when `PreprocessResult.topic_metadata.topic_shift` is `True` in `sgr_agent_core/memory/middleware.py`

**Checkpoint**: Topic-filtered context working end-to-end — agent receives only relevant messages when `sessionId` is provided

---

## Phase 4: User Story 2 — Transparent Fallback When Memory Is Unavailable (Priority: P1)

**Goal**: When the memory layer is disabled, unreachable, or errors, the system falls back to current behavior — full client-provided message history, no errors, no degradation.

**Independent Test**: Disable the memory service (or don't provide `sessionId`) and verify agent behavior is identical to the system without the memory feature.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T017 [P] [US2] Write unit tests for `MemoryMiddleware.preprocess()` fallback paths in `tests/test_memory/test_middleware.py` — test: (a) memory disabled returns raw messages, (b) no session_id returns raw messages, (c) client timeout returns raw messages with warning log, (d) client connection error returns raw messages with error log, (e) malformed response returns raw messages
- [x] T018 [P] [US2] Write integration test for chat completion without memory in `tests/test_memory/test_integration.py` — send request without `sessionId`, verify behavior is identical to current system (no memory calls made)
- [x] T019 [P] [US2] Write integration test for memory service failure in `tests/test_memory/test_integration.py` — send request with `sessionId`, mock memory service to return 500/timeout, verify agent still receives raw messages and responds normally

### Implementation for User Story 2

- [x] T020 [US2] Add skip conditions to `MemoryMiddleware.preprocess()` in `sgr_agent_core/memory/middleware.py` — return raw messages immediately when `config.enabled is False` or `session_id` is `None`
- [x] T021 [US2] Add error handling to `MemoryMiddleware.preprocess()` in `sgr_agent_core/memory/middleware.py` — wrap `client.get_context()` in try/except, catch `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.HTTPStatusError`, and `Exception`; log warning/error; return `PreprocessResult(messages=original, used_memory=False)`
- [x] T022 [US2] Add structured logging for fallback events in `sgr_agent_core/memory/middleware.py` — WARNING for timeout/connection errors, ERROR for unexpected failures, include `session_id` and error details in log entries (FR-013)
- [x] T023 [US2] Ensure zero overhead when memory is disabled: verify in `sgr_agent_core/server/endpoints.py` that no memory-related code executes when `session_id` is `None` or memory config is disabled (guard clause before calling `preprocess()`)

**Checkpoint**: System behaves identically to pre-memory behavior when memory is disabled or unavailable — no errors, no degradation

---

## Phase 5: User Story 3 — Server-Side Dialog Storage with Topic Tags (Priority: P2)

**Goal**: All conversation messages (user and assistant) are stored server-side with topic identifiers, enabling future analytics and session resumption.

**Independent Test**: Send messages through the system and verify both user and assistant messages are persisted via the memory service with correct topic tags, session IDs, and role attribution.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T024 [P] [US3] Write unit tests for `MemoryMiddleware.postprocess()` in `tests/test_memory/test_middleware.py` — mock `client.store_message()`, verify assistant response is sent with correct `session_id`, `role="assistant"`, `parent_message_id`, and content
- [x] T025 [P] [US3] Write unit test for postprocess skip conditions in `tests/test_memory/test_middleware.py` — verify postprocess is skipped when `user_message_id` is `None` or `assistant_content` is empty
- [x] T026 [P] [US3] Write unit test for postprocess error handling in `tests/test_memory/test_middleware.py` — mock `client.store_message()` to raise exception, verify error is logged but no exception propagates

### Implementation for User Story 3

- [x] T027 [US3] Implement `MemoryMiddleware.postprocess()` in `sgr_agent_core/memory/middleware.py` — call `client.store_message()` with session_id, role="assistant", content, parent_message_id; catch all exceptions and log warnings
- [x] T028 [US3] Implement fire-and-forget assistant response storage in `sgr_agent_core/server/endpoints.py` — add `_store_assistant_response()` async helper that awaits agent execution completion, extracts final assistant message from `agent.conversation`, and calls `memory_middleware.postprocess()`; schedule via `asyncio.create_task()` after `agent.execute()` is dispatched
- [x] T029 [US3] Add structured logging for storage operations in `sgr_agent_core/memory/middleware.py` — WARNING on store failures, DEBUG on successful stores with `message_id` and `topic_id`

**Checkpoint**: Both user messages (via preprocess) and assistant responses (via postprocess) are stored in the memory service with topic metadata

---

## Phase 6: User Story 4 — Configuration-Driven Memory Activation (Priority: P2)

**Goal**: Operators can enable/disable the memory layer through configuration without code changes. Disabled by default with zero overhead.

**Independent Test**: Toggle `memory.enabled` in config.yaml on/off and verify system behavior changes accordingly without redeployment.

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T030 [P] [US4] Write test for config loading from YAML in `tests/test_memory/test_config.py` — verify `GlobalConfig.from_yaml()` with `memory:` section correctly populates `MemoryConfig` fields
- [x] T031 [P] [US4] Write test for default config (no memory section) in `tests/test_memory/test_config.py` — verify `GlobalConfig` without `memory:` section has `memory.enabled == False`
- [x] T032 [P] [US4] Write test for environment variable override in `tests/test_memory/test_config.py` — verify `SGR__MEMORY__ENABLED=true` activates memory

### Implementation for User Story 4

- [x] T033 [US4] Verify `MemoryConfig` integrates with YAML loading in `sgr_agent_core/agent_config.py` — ensure `from_yaml()` correctly parses `memory:` section and passes to `GlobalConfig`
- [x] T034 [US4] Conditional initialization in `sgr_agent_core/server/app.py` lifespan — only create `MemoryServiceClient` and `MemoryMiddleware` when `config.memory.enabled is True`; log INFO on activation, DEBUG on skip
- [x] T035 [US4] Verify timeout configuration is respected in `sgr_agent_core/memory/client.py` — ensure `httpx.AsyncClient` uses `config.timeout` for request-level timeout on all memory service calls

**Checkpoint**: Memory can be toggled on/off via config.yaml or environment variables; disabled by default with zero overhead

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T036 [P] Update `sgr_agent_core/memory/__init__.py` with final public exports: `MemoryConfig`, `MemoryMiddleware`, `MemoryServiceClient`, `TopicMetadata`, `PreprocessResult`
- [x] T037 [P] Run `ruff check .` and `ruff format .` on all new and modified files
- [x] T038 Run full test suite `pytest tests/test_memory/` and verify all tests pass
- [ ] T039 Run quickstart.md validation — verify curl examples from `specs/191-topic-aware-memory/quickstart.md` match actual endpoint behavior (manual or scripted)
- [x] T040 Verify existing tests still pass: run `pytest` (full suite) to confirm no regressions in existing functionality

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational (Phase 2) — core feature
- **US2 (Phase 4)**: Depends on US1 (Phase 3) — adds error handling to the middleware created in US1
- **US3 (Phase 5)**: Depends on Foundational (Phase 2) — can run in parallel with US1/US2 for test writing, but implementation depends on middleware from US1
- **US4 (Phase 6)**: Depends on Foundational (Phase 2) — can run in parallel with US1 for test writing, but integration tests depend on lifespan init from US1
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 only — core feature, no cross-story dependencies
- **US2 (P1)**: Depends on US1 — adds fallback handling to the middleware created in US1
- **US3 (P2)**: Depends on US1 — adds postprocess to the middleware; uses the same client
- **US4 (P2)**: Depends on Phase 2 — config tests are independent; integration verifies lifespan init from US1

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core implementation before integration

### Parallel Opportunities

- T001, T002: Package setup can run in parallel
- T008, T009: Foundational tests can run in parallel
- T010, T011: US1 tests can run in parallel
- T017, T018, T019: US2 tests can run in parallel
- T024, T025, T026: US3 tests can run in parallel
- T030, T031, T032: US4 tests can run in parallel
- T036, T037: Polish tasks can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch tests first (should fail):
Task: "T010 [P] [US1] Unit tests for preprocess() success path in tests/test_memory/test_middleware.py"
Task: "T011 [P] [US1] Integration test for chat completion with memory in tests/test_memory/test_integration.py"

# Then implement sequentially:
Task: "T012 [US1] MemoryMiddleware.preprocess() in sgr_agent_core/memory/middleware.py"
Task: "T013 [US1] Initialize in server lifespan in sgr_agent_core/server/app.py"
Task: "T014 [US1] Integrate into endpoint in sgr_agent_core/server/endpoints.py"
Task: "T015 [US1] Topic metadata in SSE response"
Task: "T016 [US1] Structured logging for topic shifts"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (topic-filtered context)
4. **STOP and VALIDATE**: Test US1 independently — send multi-topic messages with `sessionId`, verify agent gets filtered context
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add US1 (Topic Filtering) → Test independently → Deploy/Demo (MVP!)
3. Add US2 (Fallback) → Test independently → Deploy/Demo (production-safe!)
4. Add US3 (Storage) → Test independently → Deploy/Demo (full persistence!)
5. Add US4 (Config) → Test independently → Deploy/Demo (operator-ready!)
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 (Topic Filtering) → then US2 (Fallback)
   - Developer B: US3 tests (can write tests while US1 in progress) → then US3 implementation after US1 done
   - Developer C: US4 tests (independent) → then US4 implementation
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- No new dependencies required — httpx and Pydantic are already in the project
- The memory microservice is external — all tests mock the HTTP calls
