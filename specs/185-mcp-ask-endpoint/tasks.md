# Tasks: MCP Ask Endpoint

**Input**: Design documents from `/specs/185-mcp-ask-endpoint/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Tests are included as specified in the feature spec (Tasks 6 and 7 in the original spec).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the `mcp_server` package and foundational files

- [x] T001 Create package init file at `sgr_agent_core/mcp_server/__init__.py` — export `AskRequest`, `AskResponse`, `MCPServerConfig`, `create_mcp_server`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Data models and configuration that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 [P] Create `AskRequest` and `AskResponse` Pydantic models in `sgr_agent_core/mcp_server/models.py` — `AskRequest` has fields `query` (str, required), `traceId` (str, default `"trace-default-001"`), `userId` (str, default `"user-default-001"`); `AskResponse` has fields `response` (str, required), `traceId` (str, default `"trace-default-001"`); both use `ConfigDict(extra="allow")`
- [x] T003 [P] Create `MCPServerConfig` Pydantic model in `sgr_agent_core/mcp_server/config.py` — fields: `enabled` (bool, default `False`), `host` (str, default `"0.0.0.0"`), `port` (int, default `8011`), `transport` (str, default `"sse"`), `default_agent` (str | None, default `None`)
- [x] T004 Integrate `MCPServerConfig` into `GlobalConfig` in `sgr_agent_core/agent_config.py` — add field `mcp_server: MCPServerConfig = Field(default_factory=MCPServerConfig)` with import; ensure missing `mcp_server:` section in YAML results in defaults
- [x] T005 Add `mcp_server:` section with commented defaults to `config.yaml.example`

**Checkpoint**: Models and config ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Send Research Query with Trace Context (Priority: P1) 🎯 MVP

**Goal**: MCP clients can call the `ask` tool with a query, traceId, and userId, and receive a structured response with the agent's answer and echoed traceId.

**Independent Test**: Call the `ask` tool programmatically with a mocked agent and verify the response structure, traceId echo, and error handling.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T006 [P] [US1] Unit tests for `AskRequest`, `AskResponse`, and `MCPServerConfig` in `tests/test_mcp_models.py` — test default values, serialization round-trip, `extra="allow"` preserves unknown fields, validation error on missing required `query`
- [x] T007 [P] [US1] Integration test for the `ask` tool in `tests/test_mcp_server.py` — test: MCP server has exactly one tool "ask"; successful call returns valid `AskResponse` JSON with `response` and `traceId`; traceId echoed from request; empty query returns error; default traceId/userId applied when omitted; use mocked agent execution (patch `AgentFactory.create` to return a mock agent with a fixed `execute()` return value)

### Implementation for User Story 1

- [x] T008 [US1] Implement MCP server with `ask` tool in `sgr_agent_core/mcp_server/server.py` — create `create_mcp_server(config: GlobalConfig) -> FastMCP` function that: instantiates `FastMCP("sgr-agent-mcp")`; registers `ask` tool accepting `query` (str), `traceId` (str, default), `userId` (str, default); validates non-empty query; resolves agent def from `config.mcp_server.default_agent` or `next(iter(config.agents))`; creates agent via `AgentFactory.create(agent_def, [{"role": "user", "content": query}])`; awaits `agent.execute()`; returns `AskResponse` serialized as JSON string; catches agent execution errors and raises `ValueError` with original message
- [x] T009 [US1] Update `sgr_agent_core/mcp_server/__init__.py` to import and export `create_mcp_server` from `server.py`
- [x] T010 [US1] Verify all US1 tests pass — run `pytest tests/test_mcp_models.py tests/test_mcp_server.py -v`

**Checkpoint**: User Story 1 complete — `ask` tool works with mocked and real agents

---

## Phase 4: User Story 2 — MCP Server Co-Starts with REST API (Priority: P2)

**Goal**: When `mcp_server.enabled` is `true` in config, the MCP server starts alongside the FastAPI server on the configured host:port. When disabled (default), behavior is unchanged.

**Independent Test**: Start the system with MCP enabled and verify logs show both servers running; start with MCP disabled and verify only REST API starts.

### Implementation for User Story 2

- [x] T011 [US2] Modify the FastAPI lifespan in `sgr_agent_core/server/app.py` — on startup: check `GlobalConfig().mcp_server.enabled`; if true, call `create_mcp_server(config)` and start MCP server as asyncio background task using `mcp.run_sse_async(host=config.mcp_server.host, port=config.mcp_server.port)`; log MCP server status (enabled/disabled, host, port); on shutdown: cancel the MCP server task if running
- [x] T012 [US2] Verify co-start behavior — manually test or write a smoke test confirming: with `enabled: false` server starts identically to current behavior; with `enabled: true` both servers start; startup logs show MCP server host and port

**Checkpoint**: User Stories 1 AND 2 complete — full MCP server functionality operational

---

## Phase 5: User Story 3 — Extensible Payload Schema (Priority: P3)

**Goal**: Request and response models accept unknown fields without validation errors, enabling future payload extensions.

**Independent Test**: Instantiate models with extra fields and verify they are preserved through serialization.

### Implementation for User Story 3

- [x] T013 [US3] Verify extensibility in existing tests in `tests/test_mcp_models.py` — confirm tests from T006 already cover `extra="allow"` behavior: `AskRequest(query="test", custom_field="x").model_dump()` includes `custom_field`; `AskResponse(response="ok", traceId="t1", extra_info="y").model_dump()` includes `extra_info`. If not already covered, add these test cases.

**Checkpoint**: All user stories independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final cleanup and validation

- [x] T014 [P] Run `ruff check sgr_agent_core/mcp_server/ tests/test_mcp_models.py tests/test_mcp_server.py` and `ruff format` to fix any lint/format issues
- [x] T015 [P] Run full test suite `pytest` to verify no regressions
- [x] T016 Run quickstart.md validation — verify the documented configuration and usage in `specs/185-mcp-ask-endpoint/quickstart.md` matches the actual implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (package must exist) — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Phase 2 (models and config must exist)
- **User Story 2 (Phase 4)**: Depends on Phase 3 (MCP server module must exist)
- **User Story 3 (Phase 5)**: Depends on Phase 2 (models must exist); can run in parallel with US1/US2
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Foundational phase only — core MVP
- **User Story 2 (P2)**: Depends on US1 (`create_mcp_server` must exist before co-hosting it)
- **User Story 3 (P3)**: Independent of US1/US2 — validates model extensibility from Phase 2

### Within Each User Story

- Tests written and failing before implementation
- Models before services/server code
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T002 and T003 can run in parallel (different files, no dependencies)
- T006 and T007 can run in parallel (different test files)
- T014 and T015 can run in parallel (different concerns)
- US3 (Phase 5) can run in parallel with US1/US2 since it only validates Phase 2 models

---

## Parallel Example: Foundational Phase

```bash
# Launch model creation tasks together:
Task: "Create AskRequest/AskResponse models in sgr_agent_core/mcp_server/models.py"
Task: "Create MCPServerConfig model in sgr_agent_core/mcp_server/config.py"
```

## Parallel Example: User Story 1 Tests

```bash
# Launch both test files together:
Task: "Unit tests for models in tests/test_mcp_models.py"
Task: "Integration test for ask tool in tests/test_mcp_server.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T005)
3. Complete Phase 3: User Story 1 (T006–T010)
4. **STOP and VALIDATE**: Test `ask` tool independently with mocked agent
5. The `ask` tool works programmatically — this is a viable MVP

### Incremental Delivery

1. Setup + Foundational → Package and models ready
2. Add User Story 1 → `ask` tool works → Test independently (MVP!)
3. Add User Story 2 → MCP server co-hosts with REST API → Production-ready
4. Add User Story 3 → Verify extensibility → Future-proof
5. Polish → Lint, full test suite, documentation validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
