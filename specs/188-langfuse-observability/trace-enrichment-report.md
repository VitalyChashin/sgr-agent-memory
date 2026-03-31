# Trace Enrichment Report: Missing Data in Langfuse Traces

**Date**: 2026-03-26
**Branch**: 188-langfuse-observability
**Status**: Investigation complete, fixes pending

## Current State

The Langfuse trace tree shows the correct **structure** (trace -> iteration spans -> tool spans) but is missing important **data** at every level. The screenshot shows:

- Tool input has only `{"tool": "fast-time-convert-time"}` (just the tool name)
- Tool output has `{"result_preview": null}`
- Metadata is `empty object`
- No LLM generation spans (reasoning/action phases invisible)
- No task input text on the root trace
- No tool arguments or tool execution results

## Root Causes

### Issue 1: Tool Input Missing Tool Arguments

**File**: `sgr_agent_core/base_agent.py`, line 242-245

**Current code**:
```python
tool_span = provider.start_span(
    name=f"tool-{tool_name}",
    span_type="tool",
    input={"tool": tool_name},  # <-- Only passes the tool name
    _parent=iter_span,
)
```

**Problem**: The `input` dict only contains the tool name string. The actual tool arguments (the Pydantic model fields like `query`, `timezone`, etc.) are on the `action_tool` object but are never serialized into the span input.

**What should be captured**: `action_tool.model_dump(mode="json")` contains the full tool invocation parameters. For example, a time conversion tool would have `{"timezone": "UTC", "format": "iso8601"}`.

**Fix location**: `base_agent.py:_execution_step()` — add `action_tool.model_dump(mode="json")` to the `input` dict.

---

### Issue 2: Tool Output `result_preview` Is Null

**File**: `sgr_agent_core/base_agent.py`, line 249-253

**Current code**:
```python
await self._action_phase(action_tool)
result_preview = str(self._context.execution_result)[:500] if self._context.execution_result else None
provider.end_span(
    tool_span,
    output={"result_preview": result_preview},
)
```

**Problem**: `self._context.execution_result` is only set by `FinalAnswerTool` (the terminal tool). For non-terminal tools (like `get_system_time`, `convert_time`), `execution_result` remains `None` throughout intermediate iterations. The actual tool result is returned by `_action_phase()` but is **not captured** — the return value is discarded.

**What should be captured**: `_action_phase()` returns the tool result string. In subclasses like `SGRToolCallingAgent._action_phase()` (line 126), the result is stored in the `result` local variable and appended to the conversation, but it's never returned to the caller in `_execution_step()`.

**Fix location**: Capture the return value of `_action_phase()` and pass it to `end_span()`.

---

### Issue 3: No LLM Generation Spans

**File**: `sgr_agent_core/base_agent.py` — no generation instrumentation exists

**Problem**: The original architecture assumed the Langfuse OpenAI drop-in wrapper would auto-instrument all `chat.completions.create()` calls as "Generation" observations. This was abandoned because the wrapper doesn't support the `.stream()` helper used by all SGR agents. The current implementation uses the standard `openai.AsyncOpenAI` client (see `langfuse_provider.py:create_openai_client()`), so **no LLM calls are captured at all**.

Each agent iteration makes 1-2 LLM calls:
1. **Reasoning phase**: `_reasoning_phase()` calls `openai_client.chat.completions.stream()` with the ReasoningTool
2. **Action selection phase**: `_select_action_phase()` calls `openai_client.chat.completions.stream()` with available tools

Neither call is instrumented. This means:
- No model name in traces
- No token usage (input_tokens, output_tokens)
- No cost tracking
- No latency per LLM call
- No prompt/completion content
- No tool call arguments from the LLM response

**What should be captured**: For each LLM call, a Langfuse "Generation" span should record:
- `model`: The model name (e.g., "gpt-4o-mini")
- `model_parameters`: temperature, max_tokens, etc.
- `input`: The messages array sent to the LLM
- `output`: The LLM response (tool calls, content)
- `usage`: `{input_tokens: N, output_tokens: M}` from the response
- `completion_start_time`: Time-to-first-token (available from streaming)

**Fix approach**: Use `trace.generation()` / `span.generation()` from the Langfuse v2 SDK to manually create generation spans. This requires either:
- (a) Adding generation instrumentation in each agent subclass's `_reasoning_phase()` and `_select_action_phase()` (invasive but complete), or
- (b) Adding a wrapper around `openai_client.chat.completions.stream()` that intercepts calls and creates generation spans (non-invasive but complex), or
- (c) Adding generation span hooks in `BaseAgent._execution_step()` around the `_reasoning_phase()` and `_select_action_phase()` calls (moderate — instruments at the base class level but can only capture timing, not the actual messages/response without the subclass cooperation)

---

### Issue 4: Root Trace Input Missing Task Text

**File**: `sgr_agent_core/base_agent.py`, line 312

**Current code**:
```python
trace = provider.start_trace(
    name=def_name,
    agent_id=self.id,
    input={"task_messages_count": len(self.task_messages), "agent_type": self.__class__.__name__},
    ...
)
```

**Problem**: The trace input only captures the **count** of task messages, not the actual content. When viewing a trace in Langfuse, operators cannot see what the user asked.

**What should be captured**: The actual task messages content. At minimum, the last user message. For example:
```python
input={
    "task": self.task_messages[-1].get("content", "") if self.task_messages else "",
    "agent_type": self.__class__.__name__,
    "messages_count": len(self.task_messages),
}
```

**Consideration**: Task messages may contain sensitive data. A truncation/redaction strategy should be applied.

---

### Issue 5: Iteration Span Missing Reasoning Data

**File**: `sgr_agent_core/base_agent.py`, line 332-338

**Current code**:
```python
iter_span = provider.start_span(
    name=f"iteration-{self._context.iteration}",
    span_type="span",
    input={"iteration": self._context.iteration, "state": self._context.state.value},
    metadata={"searches_used": self._context.searches_used},
    _parent=trace,
)
```

**Problem**: The iteration span captures the iteration number and state, but after the reasoning phase completes, the reasoning result (`self._context.current_step_reasoning`) is available and contains valuable data:
- `reasoning_steps`: What the agent is thinking
- `current_situation`: Agent's assessment
- `plan_status`: Plan progress
- `enough_data`: Whether agent has enough info
- `remaining_steps`: What's left to do
- `task_completed`: Whether the agent thinks it's done

This data is logged to `self.log` via `_log_reasoning()` but never sent to Langfuse.

**Fix location**: After `_reasoning_phase()` returns in `_execution_step()`, update the iteration span's metadata or output with the reasoning result.

---

### Issue 6: Tool Span Missing Execution Timing for `end_span`

**File**: `sgr_agent_core/observability/langfuse_provider.py`, line 139-147

**Current code**:
```python
def end_span(self, handle, *, output=None, status=None, level="DEFAULT"):
    if isinstance(handle, LangfuseSpanHandle) and handle.span is not None:
        handle.span.end(
            output=output,
            level=level,
            status_message=status,
        )
```

**Problem**: The `span.end()` call does not pass `end_time`. The Langfuse SDK auto-sets `end_time=datetime.now()` when `end()` is called, which is correct. However, the `start_span()` also doesn't pass `start_time`, relying on the SDK default. This means timing is correct but relies on SDK-side timestamps rather than application-side timestamps.

**Severity**: Low — timing is already being captured correctly by the SDK (as visible in the screenshot: "0.20s" for tool spans). This is informational only.

---

## Summary of Missing Data by Trace Level

| Level | What's Captured | What's Missing |
|-------|----------------|----------------|
| **Root Trace** | Agent name, agent_id, tags, model metadata | Task input text, final answer text, total token usage |
| **Iteration Span** | Iteration number, state, searches_used | Reasoning result, tool selected, LLM call details |
| **Tool Span** | Tool name | Tool arguments (model fields), tool execution result |
| **LLM Generation** | (not captured at all) | Model, messages, response, tokens, latency, cost |

## Recommended Fix Priority

1. **HIGH — Add LLM Generation spans**: This is the most impactful missing piece. Without it, there's no token usage, cost, or model visibility. Requires adding `trace.generation()` calls around LLM API calls.

2. **HIGH — Capture tool arguments and results**: Simple fix — serialize `action_tool.model_dump()` into span input, capture `_action_phase()` return value into span output.

3. **MEDIUM — Add task text to root trace input**: Simple fix — include actual message content (truncated) in `start_trace(input=...)`.

4. **MEDIUM — Add reasoning data to iteration spans**: After reasoning phase, update the iteration span with reasoning result summary.

5. **LOW — Add root trace output with final answer**: The trace `end_trace(output=...)` already captures `result_preview` but it's derived from `execution_result` which may be truncated. Consider capturing the full final answer.

## Langfuse v2 SDK API Reference (for fixes)

```python
# Create a generation span (for LLM calls):
generation = trace.generation(
    name="reasoning-phase",
    model="gpt-4o-mini",
    model_parameters={"temperature": 0.3, "max_tokens": 8000},
    input=[{"role": "user", "content": "..."}],  # messages
    output={"tool_calls": [...]},                  # LLM response
    usage={"input": 1250, "output": 180},          # token counts
    # or usage_details={"input": 1250, "output": 180}
)
generation.end()  # auto-sets end_time

# Update a span with more data after creation:
span.update(
    input={"tool": "web_search", "query": "AI trends"},
    output={"result": "..."},
    metadata={"reasoning": "..."},
)
```
