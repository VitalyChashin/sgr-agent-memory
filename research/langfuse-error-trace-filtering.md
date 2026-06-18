---
title: Filtering error/crash traces in Langfuse, and tracing processor work as spans
status: archived
created: 2026-06-18
updated: 2026-06-18  # acted on: plans/langfuse-error-trace-filtering.md implemented (A/B/C)
owner: Vitaly Chashin
related:
  - research/agent-context-processors.md
  - notes/processor-plugin-pattern.md
  - sgr_agent_core/observability/langfuse_provider.py
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/base_tool.py
tags: [observability, langfuse, error-handling, processors, tracing]
---

# Research: Filtering error/crash traces in Langfuse + tracing processor work

## 1. Problem statement

Two related needs:

1. **Filter failed traces.** When an MCP tool call or an LLM call returns an error
   (500, timeout, etc.), or when the agent crashes by hitting the maximum number of
   steps, we want a **simple, Langfuse-native way to find those traces** in the UI.
2. **Trace processor work.** The three processor-plugin families (MCP payload,
   metrics, agent context) do real work in the loop, but most of it is **invisible**
   in Langfuse. We want each processor's work to appear **as spans** under the trace.

## 2. How Langfuse logging works today

SDK is **Langfuse v2.x** (`langfuse>=2.0.0,<3.0.0`, `pyproject.toml:94`). The trace
lifecycle wraps one chat request in `base_agent.py::_execute()` (start `~454`, end
`~633 / 644 / 651`). Nesting:

```
trace (start_trace, base_agent.py:454)
 └─ iteration span (per loop turn, base_agent.py:540)
     ├─ generation: reasoning       (LLM call, base_agent.py:288)
     ├─ generation: action-select   (LLM call, base_agent.py:314)
     └─ tool span                   (tool execution, base_agent.py:340)
```

Filter-relevant primitives available in v2:

- **`tags`** on the trace — first-class **Tags filter** in the UI. Already plumbed:
  `trace_tags = [AgentClassName] + request_metadata["_tags"]` (`base_agent.py:449`),
  and `LangfuseConfig.tags_fields` promotes MCP request fields to tags
  (`observability/config.py:44`). **Error tags are never set, though.**
- **`level`** on observations (`DEFAULT` / `WARNING` / `ERROR`). Langfuse aggregates
  the max observation level up to the trace, exposed as the UI **Level filter**.
- **`metadata`** on trace/observation — filterable, good for dashboards.
- **`status` / `status_message`** — free text, ended via `trace.update(...)`. Weak as
  a filter (substring only).

## 3. How the three error scenarios surface today

| Scenario | Path | What Langfuse sees | Filterable? |
|---|---|---|---|
| **LLM error (500 etc.)** | raises → iteration span `level="ERROR"` (`base_agent.py:597`) → trace `status="failed: {e}"` (`:651`) | iteration span ERROR-level; trace status free-text | ⚠️ only via Level column or reading status text |
| **Max steps** | `RuntimeError("Max iterations reached")` (`base_agent.py:230`, `iron_agent.py:148`) → same failure path → `status="failed: Max iterations reached"` | identical to any other failure — **no distinct marker** | ❌ not distinguishable from other errors |
| **MCP tool error (500)** | `base_tool.py:90` **catches and returns `f"Error: {e}"`** — never raises | **nothing** — no ERROR level, no tag; trace can still end `completed` | ❌ completely invisible |

Terminal states (`models.py:35`): `COMPLETED`, `FAILED`, `CANCELLED` are used;
`ERROR` is defined but **never set**. All exceptions collapse to `FAILED` with a
free-text `status_message` — so max-steps vs. LLM-error vs. anything else are
indistinguishable without parsing the string.

### Two real gaps

1. **MCP errors are swallowed** (`base_tool.py:90`): returned as a string into the
   tool result so the loop continues. Nothing in the trace marks it — the single
   highest-value gap.
2. **No error categorization**: every failure is `FAILED` + free text; no tag, no
   distinct level for "max steps".

## 4. Recommendation for filtering: tag traces by error category

Tags are the simplest Langfuse-native filter. Add a small, fixed vocabulary on the
failure paths:

- `error` — any failed trace (broad)
- `error:llm` — LLM call raised
- `error:max_steps` — hit max iterations
- `error:mcp_tool` — MCP tool returned/raised an error

Three changes:

1. **General failure** (`base_agent.py:648` `except Exception`): classify the
   exception — `RuntimeError("Max iterations reached")` → `error:max_steps`,
   otherwise `error` / `error:llm` — and pass tags through `end_trace`. This
   requires **adding a `tags` param to `end_trace`** and mapping it to
   `trace.update(tags=...)` in `langfuse_provider.py:159` (today `end_trace` only
   forwards `output`/`status`).
2. **MCP errors** (`base_tool.py:90`): before swallowing, mark the active tool span
   `level="ERROR"` and add `error:mcp_tool` to the trace, then still return the
   error string so the loop continues. Fixes the blind spot.
3. **Level backstop**: Langfuse already aggregates observation `level` into the
   trace, so the **Level = ERROR** filter already catches LLM/max-step cases (the
   iteration span is ERROR-level). Setting `level="ERROR"` on the MCP path folds
   MCP errors into that same filter for free. Tags categorize; Level is the
   zero-config secondary filter.

Optionally add structured `metadata` (`error_type`, `failed_tool`) for dashboards.

## 5. How the processor families work — and how to trace them as spans

This is the requested addition. Three processor-plugin families share one skeleton
(see `notes/processor-plugin-pattern.md`) but differ sharply in observability wiring.

### 5.1 AgentContextProcessor — *has* span plumbing, opt-in / event-only

`context_processors/base.py`. Runs read-write at three loop seams. The chain is
**already handed `provider` + `parent_span`** at every seam:

| Hook | Call site | `parent_span` passed |
|---|---|---|
| `on_prepare_tools` | `base_agent.py:235` | `self._current_iter_span` (`:240`) |
| `on_tool_end` | `base_agent.py:365` | `tool_span` (`:371`) |
| `on_before_finish` | `base_agent.py:558` | `iter_span` (`:559`) |

There's a helper `emit_event_span(provider, parent_span, name, metadata)`
(`base.py:246`) that emits a **zero-duration "event" span** and is a no-op under
`NoOpProvider`. Built-ins call it **only when they actually fire** —
`RepeatedToolCallGuard` on a drop (`repeated_tool_call_guard.py:88`),
`MandatoryToolCallProcessor` on a veto (`mandatory_tool_call.py:71`).

**State:** span support exists, but it's (a) opt-in per processor and (b) only an
instantaneous marker, not a span wrapping the processor's actual work/duration.

**To trace their work as spans:** wrap each processor invocation (or each chain run)
in a real start/end span in `AgentContextProcessorChain`, so even processors that
don't call `emit_event_span` show up with duration and drop/veto output. Because the
chain already receives `provider`/`parent_span`, this is contained to the chain
runner — no `base_agent.py` change. Keep `emit_event_span` for the
fired-an-action markers; add a wrapping span for *ran-and-took-time*.

### 5.2 MCPPayloadProcessor — *no* span plumbing at all (the gap)

`mcp_payload_processor.py`. `pre_call` (top-down) / `post_call` (bottom-up) transform
the outgoing payload and the response around an MCP call. Invoked from
`base_tool.py:74` (`run_pre_call`) and `:85` (`run_post_call`) — **with no
`provider` and no `parent_span`**. The chain signatures don't accept them either
(`run_pre_call`/`run_post_call` take only `payload/result, context, config,
**kwargs`).

**State:** completely invisible in Langfuse — no event spans, no wrapping spans, no
access to the provider.

**To trace their work as spans (more work than 5.1):**
1. Thread `provider` + the active tool span handle into `base_tool.__call__`. Today
   `BaseTool.__call__(context, config, **kwargs)` (`base_tool.py:68`) has neither.
   The tool span is created in `base_agent.py:340` and is **not** passed down to the
   tool — so the natural parent for MCP processor spans has to be plumbed in.
2. Add optional `provider`/`parent_span` kwargs to `run_pre_call`/`run_post_call`
   and wrap each processor's `pre_call`/`post_call` in a span (mirroring the context
   chain). Reuse a shared `emit_event_span`-style helper.
3. Decide parenting: child of the **tool span** (`base_agent.py:340`) is the right
   home so payload-transform spans nest under the MCP call they wrap.

This is the bigger lift because the provider currently stops at the agent loop and
never reaches the tool layer.

### 5.3 MetricsProcessor — observe-only (reference, no change needed)

`observability/metrics/processor.py`. Already receives `provider`, `trace_handle`,
`tool_span_handle` at loop seams (`base_agent.py:476/585/354/298/603`), is fail-silent,
and **must not mutate**. It's the model for "given the provider, emit observations" —
but it's about metrics, not about making each processor's own execution a span. Use it
as the wiring reference, not as something to change.

### Summary: span-readiness of each family

| Family | Gets `provider`/`parent_span`? | Emits spans today? | Work to "trace as spans" |
|---|---|---|---|
| AgentContextProcessor | ✅ all 3 seams | event-only, opt-in, when-fired | wrap each invocation in a real span (chain-local) |
| MCPPayloadProcessor | ❌ none | ❌ never | thread provider + tool span into `base_tool`, then wrap |
| MetricsProcessor | ✅ | ✅ (its purpose) | n/a — reference only |

## 6. Proposed scope (for the plan phase)

1. **Error filtering** — add `tags` to `end_trace` (provider + base + noop); classify
   failures in `base_agent.py:648` (`error`, `error:max_steps`, `error:llm`); mark +
   tag MCP errors at `base_tool.py:90` with `level="ERROR"` + `error:mcp_tool`.
2. **Context processor spans** — wrap each processor invocation in
   `AgentContextProcessorChain` (keep `emit_event_span` for fired-action markers).
3. **MCP payload processor spans** — thread `provider` + active tool span into
   `BaseTool.__call__` and the MCP chain; wrap each `pre_call`/`post_call`.
4. Consolidate the span-wrapping helper so context + MCP chains share one
   implementation.

## 7. Open questions / gaps

- **Wrap-every-invocation vs. only-when-fired.** Wrapping every processor call adds
  span volume (esp. MCP payload processors on every tool call). Do we want a config
  flag (e.g. extend the existing per-processor span toggle) to opt in?
- **Plugin vs. inline for error tagging.** Error tagging *could* live in an
  `AgentContextProcessor` (`on_before_finish`), but there is **no `on_error` seam**
  and MCP errors don't raise — so a pure-plugin route needs a new seam. Inline in
  `base_agent.py`/`base_tool.py` is the minimal reliable path; revisit a seam later.
- **v2 → v3 SDK.** Recommendations target v2's imperative API. A future SDK bump
  would revisit `trace.update(tags=...)` and level semantics.
