---
title: Implementation plan — Langfuse error-trace filtering + processor-work spans
status: archived
created: 2026-06-18
updated: 2026-06-18  # A/B/C implemented; D partial (shared helpers landed, emit_event_span left as-is)
owner: Vitaly Chashin
supersedes: []
related:
  - research/langfuse-error-trace-filtering.md
  - notes/processor-plugin-pattern.md
  - sgr_agent_core/observability/langfuse_provider.py
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/base_tool.py
tags: [observability, langfuse, error-handling, processors, plan]
---

# Plan: Langfuse error-trace filtering + processor-work spans

Derived from `research/langfuse-error-trace-filtering.md`. Three independent
deliverables, each landable on its own:

- **A** — make failed/crashed traces filterable (tags + level).
- **B** — trace AgentContextProcessor work as real spans.
- **C** — trace MCPPayloadProcessor work as real spans.

Order: **A → B → C** (A is the user's primary ask and the smallest; C is the
biggest because the provider doesn't currently reach the tool layer).

---

## Deliverable A — Filterable error/crash traces

> **Status: implemented (2026-06-18).** `tags` added to `end_trace` (provider/noop/
> langfuse); `_classify_error_tags` + `AgentContext.error_tags`; MCP errors marked via
> `observability/context.py::mcp_call_errored` (tool span → ERROR, trace tagged
> `error:mcp_tool`). Tests in `tests/test_error_trace_tags.py` (8 passing). The
> contextvar landed as a single `mcp_call_errored` flag (not the full
> `_current_tool_span`/`_current_trace` pair) — the flag suffices for A; the span/trace
> contextvars remain to be added by C.

### Goal

Every failed trace carries a stable **tag** (`error` + a category) and an
**ERROR-level** observation, so both the Langfuse **Tags** filter and the **Level**
filter surface them. Categories: `error:llm`, `error:max_steps`, `error:mcp_tool`.

### A1. Add `tags` to the `end_trace` contract

`end_trace` currently forwards only `output`/`status`. Add an optional
`tags: list[str] | None = None`.

- `observability/provider.py:76` — add `tags` param to the abstract signature.
- `observability/noop.py:49` — add `tags` param (ignored).
- `observability/langfuse_provider.py:159` — forward to the v2 imperative API:
  ```python
  def end_trace(self, handle, *, output=None, status=None, tags=None):
      ...
      if isinstance(handle, LangfuseTraceHandle) and handle.trace is not None:
          update_kwargs: dict[str, Any] = {"output": output, "status_message": status}
          if tags:
              update_kwargs["tags"] = tags
          handle.trace.update(**update_kwargs)
  ```
  Note: `trace.update(tags=...)` **replaces** the tag list, so merge with the
  start-time tags (see A4) rather than passing only error tags.

### A2. Classify failures in the agent loop

`base_agent.py:648` (`except Exception`) and `:641` (`CancelledError`). Add a tiny
classifier and pass tags + base tags to `end_trace`:

```python
except Exception as e:
    self.logger.error(f"❌ Agent execution error: {str(e)}")
    self._context.state = AgentStatesEnum.FAILED
    err_tags = _classify_error_tags(e)          # ["error", "error:max_steps"] | ["error", "error:llm"]
    provider.end_trace(
        trace,
        output={"error": str(e)},
        status=f"failed: {e}",
        tags=trace_tags + err_tags,             # merge with start-time tags (A4)
    )
```

`_classify_error_tags` (module-level helper in `base_agent.py`):
- `RuntimeError` whose message starts with `"Max iterations reached"` → `["error", "error:max_steps"]`
- everything else → `["error", "error:llm"]` (LLM/tool exceptions both bubble here)

Leave `CancelledError` **untagged** as an error (it's a user cancel, not a failure) —
keep its current `status="cancelled"`.

### A3. Surface + tag MCP tool errors (the blind spot)

MCP errors are swallowed at `base_tool.py:90` and never reach the loop's `except`.
Mark them at the source so they show up without changing the swallow-and-continue
behaviour:

```python
except Exception as e:
    logger.error(f"Error processing MCP tool {self.tool_name}: {e}")
    _mark_mcp_error(self.tool_name, e)   # see below
    return f"Error: {e}"
```

`_mark_mcp_error` uses the global provider + the current tool span (via the contextvar
introduced in Deliverable C, see C1 — if C lands first this is free; if A lands first,
mark via `get_provider()` only and add a trace tag through a contextvar holding the
trace handle). Minimal A-only version: set the **tool span** level to ERROR and append
`error:mcp_tool` to the trace tags. Because A and C share the tool-span contextvar,
**recommend implementing C1's contextvar as part of A** so this is clean.

Concretely, A introduces a `ContextVar[SpanHandle | None]` and a
`ContextVar[TraceHandle | None]` set in `base_agent._execution_step` around the tool
call; `_mark_mcp_error` reads them:
```python
provider = get_provider()
span = _current_tool_span.get()
if span is not None:
    provider.end_span(span, output={"error": str(e)}, level="ERROR",
                      status=f"MCP tool failed: {e}")
# tag the trace
trace = _current_trace.get()
if trace is not None:
    provider.end_trace(trace, tags=[..., "error", "error:mcp_tool"])  # see A4 note
```
**Caveat:** `end_trace` here would close the trace prematurely. Instead expose a
lightweight `add_trace_tags(handle, tags)` on the provider (append-merge) for
mid-flight tagging, OR accumulate error tags on `self._context` and apply them once at
the real `end_trace`. **Decision: accumulate on `self._context.error_tags`** (a
`set[str]`) and merge at the single `end_trace` call sites. This keeps one trace-close
path and avoids a new provider method.

Revised A3: `_mark_mcp_error` only sets the tool-span level=ERROR (via contextvar) and
adds `"error"`, `"error:mcp_tool"` to `self._context.error_tags`. Because MCP errors
don't fail the run, apply `error_tags` at the **success** `end_trace` too (A4).

### A4. Single tag-merge at every `end_trace`

Add `error_tags: set[str] = set()` to `AgentContext` (`models.py`). At all three
`end_trace` sites (success `:633`, cancelled `:644`, failed `:651`) pass
`tags=trace_tags + sorted(self._context.error_tags | <path-specific tags>)`. This gives
a trace that *completed* but had a swallowed MCP 500 the `error:mcp_tool` tag while
still showing `status="completed"` — exactly the case that's invisible today.

### A Tests

- `tests/test_score_trace.py` / `test_langfuse_provider.py` style: assert `end_trace`
  forwards `tags` to `trace.update`.
- New `tests/test_error_trace_tags.py`:
  - max-iterations run → trace tagged `error:max_steps`.
  - LLM raises → trace tagged `error:llm`.
  - MCP tool returns `Error:` → trace tagged `error:mcp_tool` + tool span level ERROR,
    run still `completed`.
- NoOp path: all of the above are no-ops (use existing `test_observability_noop_path.py`
  pattern).

---

## Deliverable B — Spans for AgentContextProcessor work

> **Status: implemented (2026-06-18).** Per-processor `span_mode`
> (`off`/`fired`/`always`, default `fired`) on `ContextProcessorDefinition`, propagated
> to `processor._span_mode` by the builder. `AgentContextProcessorChain` wraps each
> invocation via `observability/context.py::processor_span_start`/`processor_span_end`
> when `always`; `off` withholds the provider so fired-action markers also fall silent.
> Helpers live in `observability/context.py` (shared, ready for C). `emit_event_span`
> left intact (consolidation into the helpers deferred to D/C). Tests:
> `tests/context_processors/test_span_modes.py` (8 passing); existing 33 still green.
> Config documented in `agents.yaml.example`.

### Goal

Each context-processor invocation appears as a span with duration + output, not just
the current "event" markers that fire only on a drop/veto.

### Background (already in place)

The chain already receives `provider` + `parent_span` at all three seams
(`base_agent.py:240/371/559`) and ships `emit_event_span` (`context_processors/base.py:246`)
for fired-action markers. So B is **chain-local** — no `base_agent.py` change.

### B1. Wrap each processor call in the chain

In `AgentContextProcessorChain` (`context_processors/base.py:111`), wrap each
`await p.on_*` in a start/end span:

```python
span = _processor_span_start(provider, parent_span,
                             name=f"ctx-processor.{type(p).__name__}.{hook}")
try:
    drop = await p.on_prepare_tools(...)
    _processor_span_end(provider, span, output={"dropped": sorted(drop)})
except Exception as e:
    _processor_span_end(provider, span, output={"error": str(e)}, level="ERROR")
    logger.warning(...)
```

Apply to `run_prepare_tools`, `run_on_tool_end`, `run_before_finish`.

### B2. Shared helper

Generalize `emit_event_span` into a small start/end pair (`_processor_span_start` /
`_processor_span_end`) living in a shared module (see D — consolidation). Keep
`emit_event_span` as a thin wrapper (zero-duration) for the existing fired-action use
so built-ins don't change.

### B3. Volume control

Wrapping *every* invocation adds spans even when a processor is a no-op. Add a config
toggle (extend the existing per-processor span notion mentioned in CLAUDE.md):
`span_mode: "off" | "fired" | "always"` (default `"fired"` = today's behaviour) on
`ContextProcessorDefinition` (`base.py:193`). `"always"` enables B1 wrapping.

### B Tests

- `run_prepare_tools` with `span_mode="always"` → one span per processor with
  `output.dropped`.
- A throwing processor → span ends with level ERROR and the chain still fail-safe
  (no drops contributed).
- `span_mode="fired"` (default) → no wrapping spans, existing `emit_event_span` markers
  unchanged.

---

## Deliverable C — Spans for MCPPayloadProcessor work

> **Status: implemented (2026-06-18).** `current_tool_span` contextvar
> (`observability/context.py`) set by `BaseAgent` around `_action_phase` (tight
> try/finally, reset once); `MCPBaseTool.__call__` reads it + `get_provider()` and passes
> `provider`/`parent_span` into the chain. `MCPPayloadProcessorChain.run_pre_call`/
> `run_post_call` gained keyword-only `provider`/`parent_span` and wrap each processor via
> the shared `processor_span_*` helpers when `span_mode == "always"` (re-raise on error
> to preserve propagation). `span_mode` added to `PayloadProcessorDefinition` (default
> `off`) and `MCPPayloadProcessor._span_mode`; builder (`services/mcp_service.py`) sets it.
> No `_action_phase` signatures touched. Tests: `tests/test_payload_processor.py`
> (`TestMCPProcessorSpans`, +span_mode defaults). Config: `config.yaml.example`.

### Goal

`pre_call` / `post_call` transforms appear as spans nested under the **tool span**.

### The obstacle

`base_tool.MCPBaseTool.__call__` (`base_tool.py:68`) has **no provider and no parent
span**. The tool span is created in `base_agent.py:340` and never passed down;
`_action_phase` (5 subclass implementations: `iron/sgr/sgr_tool_calling/tool_calling`,
+ `dialog` override) calls `tool(self._context, self.config, **tool_configs)`.

### C1. Reach the provider + tool span without touching 5 subclasses

Use task-local **contextvars** set in `base_agent._execution_step` around the tool call
(introduced jointly with A3):

```python
# base_agent.py, module level
_current_tool_span: ContextVar[Any] = ContextVar("current_tool_span", default=None)
_current_trace: ContextVar[Any] = ContextVar("current_trace", default=None)
```

Set `_current_trace` once after `start_trace` (`:454`); set/reset `_current_tool_span`
around `tool_span` (`:340`–`:380`). contextvars are task-local and inherited by the
same coroutine, so concurrent agents in the server don't collide. The provider itself
is already a global singleton via `get_provider()` (`base_agent.py:276`) — `base_tool`
calls that directly.

> Rationale for contextvars over threading kwargs: avoids editing all 5 `_action_phase`
> signatures and avoids polluting the `**kwargs` that flow into `pre_call`/`post_call`
> (currently `tool_configs`).

### C2. Wrap pre/post in `base_tool`

In `MCPBaseTool.__call__`, pass provider + parent span into the chain:

```python
provider = get_provider()
parent = _current_tool_span.get()
if self._processor_chain:
    payload = await self._processor_chain.run_pre_call(
        payload, context, config, provider=provider, parent_span=parent, **kwargs)
...
    result_str = await self._processor_chain.run_post_call(
        result_str, payload, context, config, provider=provider, parent_span=parent, **kwargs)
```

Import is local (`from sgr_agent_core.base_agent import _current_tool_span`) — watch for
a circular import (`base_agent` imports tools). Put the contextvars in a small neutral
module (e.g. `observability/context.py`) to break the cycle.

### C3. Wrap each processor in the MCP chain

`MCPPayloadProcessorChain.run_pre_call` / `run_post_call`
(`mcp_payload_processor.py:70/81`): add keyword-only `provider=None, parent_span=None`,
and wrap each `processor.pre_call` / `post_call` with the shared span helper (D), e.g.
`mcp-processor.{ClassName}.pre_call`. Keep pass-through semantics on error? **No** —
MCP payload processors currently propagate exceptions (no try/except in the chain). Add
a span that ends level=ERROR but **re-raise** to preserve current behaviour (the MCP
error then gets swallowed at `base_tool.py:90` and tagged by A3).

### C4. Same volume toggle as B

Add `span_mode` to `PayloadProcessorDefinition` (`mcp_payload_processor.py:94`). Default
`"off"` for MCP processors (they run on every tool call — high volume); opt into
`"always"`.

### C Tests

- `run_pre_call` with provider + `span_mode="always"` → one span per processor parented
  to the passed `parent_span`.
- `base_tool` integration: contextvar set → MCP processor span nests under the tool
  span; contextvar unset (no provider) → no spans, no error.
- Circular-import guard: importing `base_tool` and `base_agent` in either order works.

---

## Deliverable D — Consolidate the span helper

> **Status: partially done (2026-06-18).** Shared `processor_span_start`/
> `processor_span_end` live in `observability/context.py` and are used by **both** the
> context chain (B) and the MCP chain (C). `emit_event_span` (context_processors) was
> **left as-is** (still its own zero-duration "event" implementation) rather than
> rewired through the shared pair — deferred as low-value cleanup; noted so it isn't
> mistaken for an oversight.

Both B and C need "wrap a callable in a start/end span, ERROR on raise." Extract one
helper (proposed `observability/context.py` alongside the contextvars):

```python
def processor_span_start(provider, parent_span, *, name, input=None): ...
def processor_span_end(provider, span, *, output=None, level="DEFAULT", status=None): ...
```

`emit_event_span` (`context_processors/base.py:246`) becomes a thin wrapper over these
(start+immediate end). MCP chain and context chain both import from here. Do D first if
implementing B and C together; otherwise inline in B and refactor when C lands.

---

## Sequencing & risk

1. **A** (tags + level + contextvars for trace/tool-span). Self-contained, primary ask.
   Risk: low. The contextvars introduced here are reused by C.
2. **B** (context-processor spans). Chain-local, low risk.
3. **D** then **C** (MCP-processor spans). Highest risk: circular imports + per-tool-call
   span volume. Gate behind `span_mode` default-off.

## Open questions (carry to gaps if unresolved at implementation)

- **`add_trace_tags` vs. accumulate-on-context.** Plan picks accumulate-on-`context.error_tags`
  to keep one `end_trace` path. Revisit if other mid-flight tag needs appear.
- **Default `span_mode`.** `"fired"` for context (preserves today), `"off"` for MCP
  (volume). Confirm with whoever owns the Langfuse cost budget.
- **v2→v3 SDK.** `trace.update(tags=...)` replace-semantics and `level` mapping are v2;
  a bump revisits A1/A3.
