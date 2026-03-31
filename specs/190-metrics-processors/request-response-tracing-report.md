# Report: Request/Response Boundary Tracing

**Date**: 2026-03-26
**Branch**: 190-metrics-processors
**Status**: Research complete

## Goal

Capture the full lifecycle of an agent call at the API boundary:
1. Start a span when the request arrives (REST or MCP) — before the agent is even created
2. Capture the full request payload in the trace
3. Configure which request fields are used as Langfuse trace filters (userId, traceId, sessionId, etc.)
4. Capture the full final output (FinalAnswerTool response) as it would be returned to the client

---

## Part 1: Current State Analysis

### What exists today

| Aspect | REST (`/v1/chat/completions`) | MCP (`ask` tool) |
|--------|-------------------------------|-------------------|
| Request payload captured? | No — only message count | Partially — traceId, userId hardcoded |
| Full messages in trace? | No — only last message text (from feature 189) | No |
| Filterable fields? | userId/sessionId from request_metadata (MCP only) | traceId, userId hardcoded in `ask()` params |
| Final output in trace? | Partial — `execution_result[:1000]` truncated | Not in trace (returned to MCP client) |
| Request timing? | No span for the request itself | No span for the request itself |

### Key gaps

1. **No request-level span**: The observability trace starts inside `BaseAgent._execute()`, after the agent is created. The time spent in request parsing, agent creation, and tool resolution is invisible.

2. **REST endpoint doesn't pass request_metadata**: `AgentFactory.create()` is called without `request_metadata` from the REST endpoint (line 201 of endpoints.py):
   ```python
   agent = await AgentFactory.create(agent_def, request.messages.root)
   # No request_metadata parameter!
   ```
   Compare with MCP (line 68-72 of mcp_server/server.py):
   ```python
   agent = await AgentFactory.create(
       agent_def=agent_def,
       task_messages=[...],
       request_metadata={"traceId": traceId, "userId": userId},
   )
   ```

3. **Filterable fields are hardcoded**: The MCP endpoint hardcodes `traceId` and `userId` as the only metadata fields. The REST endpoint doesn't extract any metadata at all. There's no configuration for which request fields map to trace attributes.

4. **Full output not captured**: `FinalAnswerTool.__call__()` sets `context.execution_result = self.answer`, which flows to `end_trace(output={"result_preview": ...})` but is truncated. The full structured output (reasoning, completed_steps, answer, status) from `FinalAnswerTool` is not captured.

---

## Part 2: Request Payload at Each Entry Point

### REST Endpoint — `ChatCompletionRequest`

Available fields from the HTTP request:

| Field | Type | Langfuse Use | Notes |
|-------|------|-------------|-------|
| `messages` | list[dict] | Trace input | Full conversation history, can be large |
| `model` | str | Trace metadata / tag | Agent name (e.g., "sgr_tool_calling_agent") |
| `stream` | bool | N/A | Always true |
| `max_tokens` | int | Trace metadata | LLM parameter override |
| `temperature` | float | Trace metadata | LLM parameter override |
| HTTP headers | dict | Filterable fields | Custom headers like `X-User-Id`, `X-Session-Id` |

**Problem**: The OpenAI-compatible request format doesn't have dedicated fields for `userId` or `sessionId`. These would need to come from:
- HTTP headers (e.g., `X-User-Id`, `X-Session-Id`, `X-Trace-Id`)
- A custom field in the request body (breaks OpenAI compatibility)
- Extracted from message metadata (non-standard)

### MCP Endpoint — `AskRequest`

Available fields from the MCP tool call:

| Field | Type | Langfuse Use | Notes |
|-------|------|-------------|-------|
| `query` | str | Trace input | User's question |
| `traceId` | str | Langfuse trace ID | For cross-service correlation |
| `userId` | str | Langfuse user_id filter | For per-user filtering |
| Extra fields | Any | Configurable | `AskRequest` has `extra="allow"` |

**Advantage**: MCP's `AskRequest` already has `extra="allow"`, so any additional fields from the MCP caller flow through automatically.

---

## Part 3: Proposed Design

### 3.1 Request-Level Span (Wrapping Span)

Add a "request" span that wraps the entire agent lifecycle:

```
Trace: tool_calling_agent (16.36s)
├── request (16.36s)                    ← NEW: full request payload
│   ├── agent-creation (0.02s)          ← NEW: factory + tool resolution time
│   ├── iteration-1 (4.56s)
│   │   ├── reasoning (...)
│   │   ├── action-selection (...)
│   │   └── tool-xxx (...)
│   ├── iteration-2 (...)
│   └── ...
└── response                            ← NEW: full response payload
```

**Implementation**: Add observability instrumentation to `endpoints.py` and `mcp_server/server.py`, creating spans that wrap the entire `AgentFactory.create()` + `agent.execute()` flow.

### 3.2 Configurable Field Mapping

Add a configuration section for mapping request fields to Langfuse trace attributes:

```yaml
observability:
  enabled: true
  provider: "langfuse"

  # Map request fields to Langfuse trace attributes for filtering
  trace_field_mapping:
    # REST endpoint: extract from HTTP headers
    rest:
      user_id: "X-User-Id"          # HTTP header name → Langfuse user_id
      session_id: "X-Session-Id"     # HTTP header name → Langfuse session_id
      trace_id: "X-Trace-Id"        # HTTP header name → Langfuse trace ID
      tags_header: "X-Agent-Tags"   # Comma-separated tags from header

    # MCP endpoint: extract from request payload fields
    mcp:
      user_id: "userId"             # Field name in AskRequest → Langfuse user_id
      session_id: "sessionId"       # Field name in AskRequest → Langfuse session_id
      trace_id: "traceId"           # Field name in AskRequest → Langfuse trace ID
```

**How it works**:
- REST: the endpoint reads configured HTTP header names from the request and passes them as `request_metadata`
- MCP: the endpoint reads configured field names from the `AskRequest` payload and passes them as `request_metadata`
- `BaseAgent._execute()` already reads `userId` and `sessionId` from `request_metadata` to set on the trace

### 3.3 Full Request Payload in Trace Input

#### REST Endpoint

Capture the full `ChatCompletionRequest` as trace input:

```python
request_input = {
    "endpoint": "rest",
    "model": request.model,
    "messages": request.messages.model_dump(),  # Full conversation
    "max_tokens": request.max_tokens,
    "temperature": request.temperature,
    "stream": request.stream,
}
```

**Concern**: Messages can be very large (full conversation history with images). Need truncation strategy:
- Truncate message content at 2000 chars per message
- Strip base64 image data (already done by `MessagesList.serialize_root`)
- Keep metadata fields in full

#### MCP Endpoint

Capture the full `AskRequest` as trace input:

```python
request_input = {
    "endpoint": "mcp",
    "query": query,
    "traceId": traceId,
    "userId": userId,
    **extra_fields,  # Any additional fields from extra="allow"
}
```

### 3.4 Full Output in Trace

#### Current: Only `execution_result` (the answer string) is captured

```python
# base_agent.py current:
provider.end_trace(trace, output={"result_preview": result_preview})
```

#### Proposed: Capture the full FinalAnswerTool output

When `FinalAnswerTool` executes, it has:
- `self.reasoning` — why the task is complete
- `self.completed_steps` — summary of steps
- `self.answer` — the final answer
- `self.status` — COMPLETED or FAILED

This structured output should be captured in the trace:

```python
# After execution completes:
final_output = {
    "answer": self._truncate(self._context.execution_result),
    "status": self._context.state.value,
    "iterations": self._context.iteration,
}
# If the last tool was FinalAnswerTool, include its structured fields:
if hasattr(self._context, "last_tool") and isinstance(self._context.last_tool, FinalAnswerTool):
    final_output["reasoning"] = self._truncate(self._context.last_tool.reasoning)
    final_output["completed_steps"] = self._context.last_tool.completed_steps
```

**Alternative (simpler)**: The tool span for `tool-finalanswertool` already captures the tool arguments (from feature 189). The trace output just needs to ensure the full answer is not truncated beyond usefulness.

---

## Part 4: Implementation Approach

### Option A: Metrics Processor (Plugin-Based)

Add a `RequestResponseProcessor` as a metrics processor that captures request/response data.

**Pros**: Follows existing plugin pattern, configurable, no core code changes
**Cons**: Processors run inside `BaseAgent._execute()`, after the agent is created. They cannot capture the request-level span (pre-agent-creation) or the raw HTTP request/MCP payload.

**Verdict**: Not suitable for request-level spans. Processors only see `AgentContext`, not the raw HTTP request.

### Option B: Endpoint-Level Instrumentation

Add observability calls directly in `endpoints.py` and `mcp_server/server.py`.

**Pros**: Access to full raw request, can create spans before agent creation, can capture response after execution
**Cons**: Requires modifying two endpoint files, not plugin-based

**Verdict**: This is the correct approach. The request/response boundary is infrastructure, not a plugin concern.

### Option C: FastAPI Middleware

Add observability middleware that wraps all requests.

**Pros**: Automatic for all endpoints, captures timing
**Cons**: Too broad (captures /health, /agents, etc.), doesn't have agent-specific context, harder to integrate with Langfuse trace hierarchy

**Verdict**: Too coarse-grained. Endpoint-level instrumentation (Option B) is more precise.

### Recommended: Option B + Config Extension

1. **Modify `endpoints.py`**: Before creating the agent, extract metadata from headers per config, pass as `request_metadata`. Capture full request payload.
2. **Modify `mcp_server/server.py`**: Extract metadata from request fields per config, pass as `request_metadata`. Already partially done.
3. **Modify `BaseAgent._execute()`**: Enrich trace input with full request payload from `request_metadata`. Enrich trace output with full final answer.
4. **Add config model**: `TraceFieldMapping` in `ObservabilityConfig` for configurable field extraction.

---

## Part 5: Files Requiring Changes

| File | Change | Scope |
|------|--------|-------|
| `sgr_agent_core/observability/config.py` | Add `TraceFieldMapping` config model | New Pydantic model |
| `sgr_agent_core/server/endpoints.py` | Extract metadata from headers, pass to AgentFactory.create, capture request payload | Modify `create_chat_completion` handler |
| `sgr_agent_core/mcp_server/server.py` | Use configurable field names instead of hardcoded `traceId`/`userId` | Modify `ask` handler |
| `sgr_agent_core/base_agent.py` | Include `request_metadata` payload in trace input, include full answer in trace output | Modify `_execute()` |

---

## Part 6: Configuration Model

```yaml
observability:
  trace_field_mapping:
    rest:
      user_id: "X-User-Id"        # HTTP header → Langfuse user_id
      session_id: "X-Session-Id"   # HTTP header → Langfuse session_id
      tags_header: "X-Agent-Tags"  # HTTP header → Langfuse tags (comma-separated)
      # Additional headers to include in request_metadata:
      extra_headers:
        - "X-Trace-Id"
        - "X-Request-Source"
    mcp:
      user_id: "userId"            # Request field → Langfuse user_id
      session_id: "sessionId"      # Request field → Langfuse session_id
      trace_id: "traceId"          # Request field → Langfuse trace ID
      # Additional fields to include in request_metadata:
      extra_fields:
        - "organizationId"
        - "projectId"
```

**Pydantic Model**:
```python
class RestFieldMapping(BaseModel):
    user_id: str | None = "X-User-Id"
    session_id: str | None = "X-Session-Id"
    tags_header: str | None = None
    extra_headers: list[str] = Field(default_factory=list)

class McpFieldMapping(BaseModel):
    user_id: str = "userId"
    session_id: str | None = "sessionId"
    trace_id: str = "traceId"
    extra_fields: list[str] = Field(default_factory=list)

class TraceFieldMapping(BaseModel):
    rest: RestFieldMapping = Field(default_factory=RestFieldMapping)
    mcp: McpFieldMapping = Field(default_factory=McpFieldMapping)
```

---

## Part 7: Complexity Assessment

| Component | Effort | Risk |
|-----------|--------|------|
| Config model (TraceFieldMapping) | Low | None |
| REST endpoint metadata extraction | Medium | Must preserve OpenAI compatibility |
| MCP endpoint field mapping | Low | Backward-compatible (defaults match current behavior) |
| BaseAgent trace input/output enrichment | Low | Already partially done |
| Full answer capture | Low | Data already available |

**Total estimate**: ~10-12 tasks, small-medium feature.

---

## Part 8: Summary

The core need is to **close the observability gap at the API boundary**:

1. **REST endpoint needs `request_metadata`** — currently it passes nothing. Headers are the OpenAI-compatible way to include userId/sessionId.
2. **Field names should be configurable** — not everyone uses `X-User-Id`. The `trace_field_mapping` config lets operators map their header/field naming conventions.
3. **Full request payload in trace input** — operators should see the complete request (messages, model, params) in Langfuse.
4. **Full structured output in trace** — the FinalAnswerTool's reasoning + completed_steps + answer should be captured, not just a truncated string.
5. **Request-level timing** is already implicitly captured by the trace start/end, but adding the full payload makes it useful.
