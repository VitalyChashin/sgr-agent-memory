# Tasks: MCP Payload Processor

**Input**: Design documents from `/specs/187-mcp-payload-processor/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md

**Tests**: Tests are included — the spec requires test coverage per constitution principle P9.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the payload processor package and foundational abstractions

- [x] T001 Create `MCPPayloadProcessor` ABC, `MCPPayloadProcessorChain`, and `ProcessorRegistry` in `sgr_agent_core/mcp_payload_processor.py` — ABC has `__init__(self, processor_config: dict)`, abstract `async pre_call(payload, context, config, **kwargs) -> dict`, default `async post_call(result, payload, context, config, **kwargs) -> str` (pass-through). Chain has `run_pre_call` (forward order) and `run_post_call` (reverse order). Registry uses `__init_subclass__` auto-registration pattern matching `ToolRegistry`.
- [x] T002 Create `sgr_agent_core/processors/__init__.py` — import and re-export built-in processors so they auto-register in `ProcessorRegistry`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Extend core models and tool base class that ALL user stories depend on

- [x] T003 [P] Add `request_metadata: dict[str, Any] = Field(default_factory=dict)` field to `AgentContext` in `sgr_agent_core/models.py`
- [x] T004 [P] Add `_processor_chain: ClassVar[MCPPayloadProcessorChain | None] = None` and `_managed_fields: ClassVar[list[str]] = []` to `MCPBaseTool` in `sgr_agent_core/base_tool.py`
- [x] T005 [P] Create `PayloadProcessorDefinition` Pydantic model in `sgr_agent_core/mcp_payload_processor.py` (or extend existing file) — fields: `class_name: str = Field(alias="class")`, `config: dict[str, Any] = Field(default_factory=dict)`, `managed_fields: list[str] = Field(default_factory=list)`
- [x] T006 Modify `MCPBaseTool.__call__()` in `sgr_agent_core/base_tool.py` — after `payload = self.model_dump(mode="json")`, if `self._processor_chain` is not None, call `payload = await self._processor_chain.run_pre_call(payload, context, config, **kwargs)`; after getting result, call `result_str = await self._processor_chain.run_post_call(result_str, payload, context, config, **kwargs)`. Preserve existing behavior when `_processor_chain` is None.

**Checkpoint**: Core abstractions ready — processor chain can be manually attached and invoked

---

## Phase 3: User Story 1 — Inject Trace Context into MCP Calls (Priority: P1) — MVP

**Goal**: Configure a trace context processor on an MCP server, and verify it injects traceId into outgoing MCP payloads, overriding LLM-generated values.

**Independent Test**: Create a processor chain with TraceContextProcessor, attach to a mock MCP tool, call it, and verify the payload contains the processor-injected traceId.

### Tests for User Story 1

- [x] T007 [P] [US1] Unit tests for `MCPPayloadProcessor`, `MCPPayloadProcessorChain`, and `ProcessorRegistry` in `tests/test_payload_processor.py` — test: chain runs pre_call in order and post_call in reverse; empty chain is pass-through; processor errors propagate; registry auto-discovers subclasses
- [x] T008 [P] [US1] Unit tests for `TraceContextProcessor` in `tests/test_builtin_processors.py` — test: injects traceId from processor_config default; overwrites LLM-generated traceId; generates fallback when no config or metadata

### Implementation for User Story 1

- [x] T009 [US1] Implement `TraceContextProcessor` in `sgr_agent_core/processors/trace_context.py` — reads `context.request_metadata.get("traceId")`, falls back to `self.processor_config.get("default_trace_id")`, then generates `f"trace-{context.iteration}"`. Always overwrites `payload["traceId"]`.
- [x] T010 [US1] Implement `AuthContextProcessor` in `sgr_agent_core/processors/auth_context.py` — reads `context.request_metadata.get("userId")`, falls back to `self.processor_config.get("default_user_id", "user-default-001")`. Always overwrites `payload["userId"]`.
- [x] T011 [US1] Update `sgr_agent_core/processors/__init__.py` to import both processors so they auto-register
- [x] T012 [US1] Verify US1 tests pass — run `pytest tests/test_payload_processor.py tests/test_builtin_processors.py -v`

**Checkpoint**: Processor chain works end-to-end with built-in processors on manually-attached chains

---

## Phase 4: User Story 2 — Request Metadata Propagation (Priority: P2)

**Goal**: Request-scoped metadata (traceId, userId) flows from agent creation through to processors.

**Independent Test**: Create an agent with `request_metadata={"traceId": "test-123"}`, trigger a mock MCP tool call with a processor, verify the processor reads the metadata.

### Implementation for User Story 2

- [x] T013 [US2] Add `request_metadata: dict[str, Any] | None = None` parameter to `AgentFactory.create()` in `sgr_agent_core/agent_factory.py` — after agent creation, if `request_metadata` is provided, set `agent._context.request_metadata = request_metadata`
- [x] T014 [US2] Modify MCP `ask` handler in `sgr_agent_core/mcp_server/server.py` — pass `request_metadata={"traceId": traceId, "userId": userId}` to `AgentFactory.create()` so incoming MCP call metadata flows to the child agent
- [x] T015 [US2] Add test in `tests/test_mcp_server.py` — verify that when the `ask` tool is called with traceId/userId, those values appear in the agent's `request_metadata` (mock `AgentFactory.create` and inspect the `request_metadata` kwarg)

**Checkpoint**: Metadata flows from incoming requests to processors via AgentContext

---

## Phase 5: User Story 3 — Configure Processors via YAML (Priority: P3)

**Goal**: Processors configured per MCP server in YAML are automatically built and attached to discovered tools.

**Independent Test**: Define processors in a mock config, run `MCP2ToolConverter.build_tools_from_mcp()`, and verify the returned tool classes have `_processor_chain` populated with the correct processors.

### Implementation for User Story 3

- [x] T016 [US3] Add processor chain building logic to `MCP2ToolConverter.build_tools_from_mcp()` in `sgr_agent_core/services/mcp_service.py` — extract `payload_processors` from server config dict, resolve each processor class via `ProcessorRegistry` (by name) or import string, instantiate with config, build `MCPPayloadProcessorChain`, attach as `ToolCls._processor_chain`. Collect `managed_fields` from all processor definitions and attach as `ToolCls._managed_fields`.
- [x] T017 [US3] Add `payload_processors` example to `config.yaml.example` under the MCP server section with commented TraceContextProcessor and AuthContextProcessor examples
- [x] T018 [US3] Add integration test in `tests/test_payload_processor.py` — test processor resolution from config: mock `ProcessorRegistry.get()`, verify chain is built correctly from `PayloadProcessorDefinition` list; test error on unresolvable class name

**Checkpoint**: Processors auto-attach from YAML config at startup

---

## Phase 6: User Story 4 — Hide Managed Fields from LLM (Priority: P4)

**Goal**: Fields declared as `managed_fields` by processors are excluded from the LLM-facing tool schema.

**Independent Test**: Create an MCP tool with `_managed_fields = ["traceId", "userId"]`, verify `model_json_schema()` output does not contain those fields, but `model_dump()` still includes them.

### Implementation for User Story 4

- [x] T019 [US4] Override `model_json_schema()` on dynamically created MCPBaseTool subclasses in `sgr_agent_core/services/mcp_service.py` — after setting `_managed_fields` on the tool class, add a `@classmethod` override of `model_json_schema` that calls `super().model_json_schema()` then removes properties listed in `_managed_fields` from the `properties` dict and `required` list
- [x] T020 [US4] Add test in `tests/test_payload_processor.py` — create a dynamic MCP tool class with managed fields, verify `model_json_schema()` excludes them, verify `model_dump()` includes them

**Checkpoint**: LLM never sees managed fields; processors still populate them

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T021 [P] Run `ruff check sgr_agent_core/mcp_payload_processor.py sgr_agent_core/processors/ tests/test_payload_processor.py tests/test_builtin_processors.py` and `ruff format` to fix lint/format issues
- [x] T022 [P] Run full test suite `pytest` to verify no regressions
- [x] T023 Ensure `sgr_agent_core/processors/` is imported in `sgr_agent_core/__init__.py` so processors auto-register at startup

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — ABC and registry created first
- **Foundational (Phase 2)**: Depends on Phase 1 — extends core models/tools
- **US1 (Phase 3)**: Depends on Phase 2 — built-in processors and chain tests
- **US2 (Phase 4)**: Depends on Phase 2 — metadata propagation (can parallel with US1)
- **US3 (Phase 5)**: Depends on Phase 1 + 2 — YAML config resolution (can parallel with US1/US2)
- **US4 (Phase 6)**: Depends on Phase 5 — managed fields set during tool building
- **Polish (Phase 7)**: Depends on all user stories

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational only — core MVP
- **US2 (P2)**: Depends on Foundational only — can parallel with US1
- **US3 (P3)**: Depends on Foundational only — can parallel with US1/US2
- **US4 (P4)**: Depends on US3 (managed_fields set during tool building in mcp_service.py)

### Parallel Opportunities

- T003, T004, T005 can run in parallel (different files)
- T007 and T008 can run in parallel (different test files)
- US1, US2, US3 can run in parallel after Phase 2

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: ABC + registry (T001-T002)
2. Phase 2: Core model extensions (T003-T006)
3. Phase 3: Built-in processors + tests (T007-T012)
4. **STOP and VALIDATE**: Processor chain works with manually-attached processors

### Incremental Delivery

1. Setup + Foundational → Core abstractions ready
2. US1 → Processors work, trace injection proven (MVP!)
3. US2 → Metadata flows from requests to processors
4. US3 → YAML config auto-attaches processors at startup
5. US4 → LLM schema optimized, managed fields hidden
6. Polish → Lint, full test suite

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- The `processors/` package must be imported at startup for auto-registration
- `MCPConfig` from fastmcp uses `extra="allow"` — no model changes needed for `payload_processors` key
