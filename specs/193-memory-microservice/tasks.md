# Tasks: Memory Microservice

**Input**: Design documents from `/specs/193-memory-microservice/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Included — constitution principle P9 requires test coverage for new features.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4, US5)
- Include exact file paths in descriptions

## User Story Mapping

| Story | Spec Priority | Title |
|-------|--------------|-------|
| US1 | P1 | Store and Retrieve Topic-Filtered Conversation Context |
| US2 | P1 | Store Assistant Response After Agent Execution |
| US3 | P1 | Topic Detection via Lightweight Classification |
| US4 | P2 | Automatic Session Cleanup |
| US5 | P2 | Health Check and Operational Readiness |

---

## Phase 1: Setup

**Purpose**: Create project skeleton, configuration, and shared infrastructure

- [x] T001 Create `memory_service/` package directory with `__init__.py` at repository root
- [x] T002 [P] Create `tests/test_memory_service/` test package with `__init__.py`
- [x] T003 [P] Create `memory-config.yaml.example` with full configuration template (server, redis, topic_detection, retrieval sections) at repository root
- [x] T004 Implement `ServiceConfig` Pydantic model with server, redis, topic_detection, and retrieval sections in `memory_service/config.py` — include YAML loading and environment variable override support
- [x] T005 Implement `__main__.py` entry point in `memory_service/__main__.py` — parse config file path from CLI args, load config, start uvicorn
- [x] T006 Create FastAPI app with lifespan (startup: connect Redis, shutdown: close Redis) in `memory_service/app.py` — register router, add CORS middleware

**Checkpoint**: Service skeleton starts and shuts down cleanly with config loading.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Data models, storage layer, and shared test fixtures — needed by all user stories

- [x] T007 Implement `Message` data model and API request/response Pydantic models in `memory_service/models.py` — `Message`, `ContextRequest`, `ContextResponse`, `ContextMessage`, `StoreMessageRequest`, `StoreMessageResponse`, `HealthResponse` per contracts/memory-api.md
- [x] T008 Implement Redis storage layer in `memory_service/storage.py` — `RedisStorage` class with async methods: `get_session_state()`, `set_session_state()`, `store_message()`, `get_topic_messages()`, `get_message()`, `reset_session_ttl()`. Use key schema from data-model.md
- [x] T009 [P] Create shared test fixtures in `tests/test_memory_service/conftest.py` — fakeredis async fixture, mock LLM client fixture, sample messages factory
- [x] T010 [P] Write unit tests for `RedisStorage` in `tests/test_memory_service/test_storage.py` — test CRUD operations, session state management, topic message indexing, TTL behavior (using fakeredis)
- [x] T011 [P] Write unit tests for API models validation in `tests/test_memory_service/test_models.py` — test required fields, defaults, content validation

**Checkpoint**: Storage layer tested and working. All API models defined.

---

## Phase 3: User Story 1 — Store and Retrieve Topic-Filtered Context (Priority: P1) 🎯 MVP

**Goal**: `POST /context` stores a user message, detects topic, and returns topic-filtered messages.

**Independent Test**: Send messages across 2-3 topics; verify topic-filtered retrieval.

### Tests for User Story 1

- [x] T012 [P] [US1] Write unit tests for `Retriever` in `tests/test_memory_service/test_retriever.py` — test message retrieval by topic, max_messages limit, chronological ordering, new session initialization
- [x] T013 [P] [US1] Write integration tests for `POST /context` endpoint in `tests/test_memory_service/test_endpoints.py` — test first message creates session, same-topic accumulates messages, topic shift returns only new-topic messages

### Implementation for User Story 1

- [x] T014 [US1] Implement `Retriever` class in `memory_service/retriever.py` — `get_topic_context()` method that retrieves messages for the current topic (bounded by max_messages), converts to ContextMessage format, returns chronologically ordered
- [x] T015 [US1] Implement `POST /context` endpoint in `memory_service/endpoints.py` — accept ContextRequest, handle new session (skip LLM, create topic-001), handle existing session (classify topic, store message, retrieve context), return ContextResponse
- [x] T016 [US1] Register routes in `memory_service/app.py` — include router from endpoints module

**Checkpoint**: `POST /context` endpoint works end-to-end — stores messages, filters by topic.

---

## Phase 4: User Story 2 — Store Assistant Response (Priority: P1)

**Goal**: `POST /messages` stores an assistant response linked to the session and topic.

**Independent Test**: Store a user message via `/context`, then store assistant response via `/messages`. Verify both appear in subsequent `/context` calls.

### Tests for User Story 2

- [x] T017 [P] [US2] Write integration tests for `POST /messages` endpoint in `tests/test_memory_service/test_endpoints.py` — test assistant response stored with correct topic, parent_message_id linkage, appears in subsequent context retrieval

### Implementation for User Story 2

- [x] T018 [US2] Implement `POST /messages` endpoint in `memory_service/endpoints.py` — accept StoreMessageRequest, store message under current session topic, return StoreMessageResponse with message_id, topic_id, topic_label

**Checkpoint**: Assistant responses are stored and appear in topic-filtered context.

---

## Phase 5: User Story 3 — Topic Detection via Lightweight Classification (Priority: P1)

**Goal**: Detect coarse domain shifts using a lightweight LLM call with fail-open behavior.

**Independent Test**: Send messages across clearly different domains (e.g., "AI" → "climate"). Verify topic shifts are detected. Simulate LLM failures and verify fail-open behavior.

### Tests for User Story 3

- [x] T019 [P] [US3] Write unit tests for `TopicDetector` in `tests/test_memory_service/test_topic_detector.py` — test same-topic detection, topic-shift detection, LLM timeout fail-open, LLM error fail-open, malformed JSON fail-open, topic label generation
- [x] T020 [P] [US3] Write integration test for multi-turn conversation with topic shifts in `tests/test_memory_service/test_e2e.py` — send 5 messages on topic A, then 1 on topic B, verify context filtering

### Implementation for User Story 3

- [x] T021 [US3] Implement `TopicDetector` class in `memory_service/topic_detector.py` — async method `classify(current_topic_label, recent_messages, new_message_content)` that calls the configured LLM with the classification prompt, parses JSON response `{"same_topic": bool, "new_topic_label": str|null}`, returns `TopicClassificationResult`
- [x] T022 [US3] Add fail-open error handling to `TopicDetector` — catch all LLM exceptions (timeout, connection, HTTP error, malformed JSON), log warning, return `TopicClassificationResult(same_topic=True, new_topic_label=None)` as default
- [x] T023 [US3] Integrate `TopicDetector` into `POST /context` endpoint flow in `memory_service/endpoints.py` — call detector for existing sessions (skip for first message), use result to determine topic assignment before storing and retrieving

**Checkpoint**: Topic detection working end-to-end with fail-open on errors.

---

## Phase 6: User Story 4 — Automatic Session Cleanup (Priority: P2)

**Goal**: Session data expires after configurable TTL with sliding expiration.

**Independent Test**: Create a session, configure short TTL, wait, verify data is gone.

### Tests for User Story 4

- [x] T024 [P] [US4] Write unit tests for session TTL in `tests/test_memory_service/test_storage.py` — test TTL is set on session creation, TTL is reset on new message (sliding expiration), expired sessions are treated as new

### Implementation for User Story 4

- [x] T025 [US4] Implement TTL management in `RedisStorage` in `memory_service/storage.py` — set TTL on all `session:*` keys when created, reset TTL on every `store_message()` call (sliding expiration), use configured `session_ttl` from config
- [x] T026 [US4] Handle expired sessions in `POST /context` endpoint — when session state is not found (expired or never existed), treat as new session and initialize fresh

**Checkpoint**: Sessions auto-expire after TTL, new messages reset the timer.

---

## Phase 7: User Story 5 — Health Check (Priority: P2)

**Goal**: `GET /health` reports service and dependency status.

**Independent Test**: Call `/health` with Redis running → healthy. Stop Redis → unhealthy.

### Tests for User Story 5

- [x] T027 [P] [US5] Write tests for `GET /health` endpoint in `tests/test_memory_service/test_endpoints.py` — test healthy when Redis available, unhealthy when Redis unavailable

### Implementation for User Story 5

- [x] T028 [US5] Implement `GET /health` endpoint in `memory_service/endpoints.py` — ping Redis, return `{"status": "healthy"}` on success, `{"status": "unhealthy", "details": "..."}` with 503 status on failure

**Checkpoint**: Health check operational for container orchestration.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Containerization, linting, and full validation

- [x] T029 [P] Create `Dockerfile` for memory service in `memory_service/Dockerfile` — multi-stage build, copy memory_service package, install deps, expose port, CMD runs the service
- [x] T030 [P] Create or extend `docker-compose.yaml` at repository root — add memory-service and redis services with networking
- [x] T031 [P] Run `ruff check .` and `ruff format .` on all files in `memory_service/` and `tests/test_memory_service/`
- [x] T032 Run full test suite `pytest tests/test_memory_service/` and verify all tests pass
- [x] T033 Run quickstart.md validation — start service, execute curl examples from quickstart, verify responses match
- [x] T034 Verify API contract compliance — compare endpoint responses against `specs/193-memory-microservice/contracts/memory-api.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — core store+retrieve flow
- **US2 (Phase 4)**: Depends on US1 — adds assistant storage to the same endpoint module
- **US3 (Phase 5)**: Depends on US1 — adds topic detection to the `/context` flow
- **US4 (Phase 6)**: Depends on Foundational — TTL in storage layer (can parallel with US1)
- **US5 (Phase 7)**: Depends on Setup — health endpoint (can parallel with US1)
- **Polish (Phase 8)**: Depends on all user stories

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 — core feature
- **US2 (P1)**: Depends on US1 — uses same endpoint module and storage
- **US3 (P1)**: Depends on US1 — integrates into /context flow
- **US4 (P2)**: Depends on Phase 2 only — storage-level concern
- **US5 (P2)**: Depends on Phase 1 only — standalone endpoint

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services
- Services before endpoints
- Core logic before integration

### Parallel Opportunities

- T001, T002, T003: Setup tasks are parallelizable
- T009, T010, T011: Foundational tests are parallelizable
- T012, T013: US1 tests are parallelizable
- T019, T020: US3 tests are parallelizable
- T024: US4 test can parallel with US1 implementation
- T027: US5 test can parallel with US1 implementation
- T029, T030, T031: Polish tasks are parallelizable

---

## Parallel Example: User Story 1

```bash
# Launch tests first (should fail):
Task: "T012 [P] [US1] Unit tests for Retriever in test_retriever.py"
Task: "T013 [P] [US1] Integration tests for POST /context in test_endpoints.py"

# Then implement sequentially:
Task: "T014 [US1] Retriever class in retriever.py"
Task: "T015 [US1] POST /context endpoint in endpoints.py"
Task: "T016 [US1] Register routes in app.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (project skeleton)
2. Complete Phase 2: Foundational (models, storage, fixtures)
3. Complete Phase 3: User Story 1 (store + retrieve without LLM)
4. **STOP and VALIDATE**: Send multi-turn messages, verify topic-filtered retrieval
5. Deploy/demo if ready (topic detection can be added next)

### Incremental Delivery

1. Setup + Foundational → Project running
2. US1 (Store/Retrieve) → Basic context filtering (MVP!)
3. US3 (Topic Detection) → Intelligent topic classification
4. US2 (Store Assistant) → Complete conversation history
5. US4 (Cleanup) → Production-ready TTL
6. US5 (Health) → Container orchestration ready

### Parallel Team Strategy

With multiple developers:
1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 → US3 (core flow + topic detection)
   - Developer B: US4 + US5 (storage TTL + health, independent)
   - Developer C: US2 tests (can write while US1 in progress)
3. Polish: Docker + linting after stories complete

---

## Notes

- New project: `memory_service/` at repo root, separate from `sgr_agent_core/`
- Dependencies: FastAPI, uvicorn, redis-py (async), openai SDK, pydantic, fakeredis (test)
- Redis is the only external dependency — use fakeredis in tests for isolation
- LLM calls are mocked in unit tests — only integration/e2e tests optionally use real LLM
- The API contract is already fixed by feature 191's consumer contract
- All topic detection must fail-open (assume same-topic on any error)
