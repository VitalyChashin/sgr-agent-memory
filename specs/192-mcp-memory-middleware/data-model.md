# Data Model: Memory Middleware for MCP Endpoint

**Feature Branch**: `192-mcp-memory-middleware`
**Date**: 2026-03-31

## Entity Changes

### 1. AskRequest (modified)

Existing Pydantic model in `mcp_server/models.py`. Add one optional field.

| Field | Type | Default | Change | Description |
|-------|------|---------|--------|-------------|
| `query` | `str` | (required) | Existing | Research question |
| `traceId` | `str` | `"trace-default-001"` | Existing | Trace correlation ID |
| `userId` | `str` | `"user-default-001"` | Existing | User identifier |
| `sessionId` | `str` | `""` | **NEW** | Optional session ID for memory-enabled sessions |

**Validation**: Empty string treated as absent (memory skipped).

---

### 2. AskResponse (modified)

Existing Pydantic model in `mcp_server/models.py`. Add three optional fields.

| Field | Type | Default | Change | Description |
|-------|------|---------|--------|-------------|
| `response` | `str` | (required) | Existing | Agent response text |
| `traceId` | `str` | `"trace-default-001"` | Existing | Trace correlation ID |
| `topicId` | `str \| None` | `None` | **NEW** | Current topic identifier |
| `topicLabel` | `str \| None` | `None` | **NEW** | Human-readable topic label |
| `topicShift` | `bool \| None` | `None` | **NEW** | Whether a topic shift was detected |

**Serialization**: `None` fields are excluded from JSON output (standard Pydantic behavior with `exclude_none=True`).

---

### 3. ask() Tool Signature (modified)

The `ask` function in `mcp_server/server.py` gains one optional parameter.

| Parameter | Type | Default | Change | Description |
|-----------|------|---------|--------|-------------|
| `query` | `str` | (required) | Existing | Research question |
| `traceId` | `str` | `"trace-default-001"` | Existing | Trace correlation ID |
| `userId` | `str` | `"user-default-001"` | Existing | User identifier |
| `sessionId` | `str` | `""` | **NEW** | Session ID for memory |

---

## No New Entities

This feature reuses all existing memory entities from feature 191:
- `MemoryConfig` — configuration (unchanged)
- `MemoryServiceClient` — HTTP client (unchanged)
- `MemoryMiddleware` — preprocess/postprocess (unchanged)
- `PreprocessResult` — middleware output (unchanged)
- `TopicMetadata` — response metadata (unchanged)

## Request Flow

```
MCP ask(query, sessionId, userId, traceId)
    │
    ├─ sessionId empty? ──► SKIP memory (current behavior)
    │
    ├─ MemoryMiddleware.preprocess(messages, sessionId, userId)
    │   ├─ Success ──► Use filtered messages as task_messages
    │   └─ Failure ──► Fallback to [{"role": "user", "content": query}]
    │
    ├─ AgentFactory.create(agent_def, task_messages)
    ├─ agent.execute()
    │
    ├─ MemoryMiddleware.postprocess(sessionId, user_message_id, result, userId)
    │   ├─ Success ──► (silent)
    │   └─ Failure ──► Log warning (no user impact)
    │
    └─ Return AskResponse(response, traceId, topicId?, topicLabel?, topicShift?)
```
