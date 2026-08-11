---
title: Implementation plan — Capture tool definitions in Langfuse generation traces
status: archived
created: 2026-06-19
updated: 2026-06-19  # implemented: _build_gen_input + capture_tool_definitions flag + tests
owner: Vitaly Chashin
supersedes: []
related:
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/agents/sgr_tool_calling_agent.py
  - sgr_agent_core/observability/config.py
  - sgr_agent_core/observability/langfuse_provider.py
tags: [observability, langfuse, tools, plan]
---

# Plan: Capture tool definitions in Langfuse generation traces

## Problem

The function-calling agents pass `tools=await self._prepare_tools()` to the
OpenAI API (`tool_calling_agent.py:46`, `sgr_tool_calling_agent.py:49,97`), but
the `_last_llm_call` dict they hand to observability records only
`messages` (`input`), `model_parameters` (`temperature`/`max_tokens`), `usage`,
and `output`. The `tools=` array — names, **descriptions**, and JSON parameter
schemas — is never forwarded to `provider.start_generation`
(`base_agent.py:300-308, 326-334`), so it is invisible in Langfuse.

Result: a trace shows *that* a tool was called but not *which tools were
available* to the LLM at decision time, nor their descriptions/schemas. (MCP
tool descriptions themselves are built and passed correctly — verified — so this
is purely a tracing gap, not a build bug.)

## Goal

Surface the tool definitions offered to the LLM in each FC generation, rendered
in Langfuse's **native available-tools section** at the top of the generation's
chat view — a distinct, collapsible section above the messages, with output tool
calls auto-linked to it. Cover all three FC seams:

- `SGRToolCallingAgent._reasoning_phase` — the single `ReasoningTool` def.
- `SGRToolCallingAgent._select_action_phase` — the full action toolset.
- `ToolCallingAgent._select_action_phase` — the full action toolset.

Out of scope: `SGRAgent` (pure structured output; passes a response schema, not
`tools=`) and `ToolCallingAgent` reasoning (it has none). Noted as a follow-up.

## Why the `input` object (revised after Langfuse research)

Langfuse's **"Langfuse for Agents"** release (2025-11-05) renders tool/function
definitions natively **when they live in the generation `input`** in the
OpenAI-request shape `{"messages": [...], "tools": [...], "tool_choice": ...}`.
Langfuse renders `messages` as chat bubbles *and* detects `tools` as a separate
**Available tools** section at the top of the chat view — click a tool to expand
its description/parameters; output tool calls are numbered to match. This is the
same structure Langfuse's own OpenAI auto-instrumentation captures.

| Target | Renders as | Fit |
|--------|-----------|-----|
| `input` object `{messages, tools, tool_choice}` | chat bubbles **+ dedicated Available-tools section** | ✓ purpose-built; collapsible; linked to output calls |
| `metadata` | generic Metadata panel (raw JSON) | ~ works, but no tool-aware rendering, not linked to calls — **fallback only** |
| `model_parameters` | flat key/value; stringifies nested values | ✗ nested schemas render poorly |

**Decision:** put tools in `input` as the request-shaped object. `pydantic_function_tool(...)`
already emits the exact `{"type":"function","function":{name,description,parameters}}`
shape Langfuse expects, so no transformation is needed.

> **Version caveat.** The dedicated section needs a Langfuse build with the
> Nov-2025 agent tool rendering (Langfuse Cloud has it; self-hosted must be
> recent enough). On older versions nothing breaks — `messages` still render as
> chat and `tools` render as plain JSON in the input, just without the
> collapsible section. The metadata approach (below) remains available as a
> fallback if the target instance is too old.

---

## Step 1 — Hoist `_prepare_tools()` so the same list feeds API + trace

Today `_prepare_tools()` is called inline inside the `stream(...)` call and the
result is discarded. Capture it in a local so we can both send it and trace it.

**`tool_calling_agent.py:41-64`** (`_select_action_phase`):

```python
messages = await self._prepare_context()
tool_defs = await self._prepare_tools()          # NEW: hoisted
async with self.openai_client.chat.completions.stream(
    messages=messages,
    tools=tool_defs,                              # was: await self._prepare_tools()
    tool_choice=self.tool_choice,
    **self.config.llm.to_openai_client_kwargs(),
) as stream:
    ...
self._last_llm_call = {
    "name": "action-selection",
    "model": self.config.llm.model,
    "model_parameters": {"temperature": ..., "max_tokens": ...},
    "usage": ...,
    "input": self._build_gen_input(messages, tool_defs),   # CHANGED: see Step 2
    "output": ...,
}
```

**`sgr_tool_calling_agent.py`** — identical hoist in two places:

- `_reasoning_phase` (lines 47-68): hoist
  `tool_defs = [pydantic_function_tool(self.ReasoningTool, name=self.ReasoningTool.tool_name)]`,
  pass it as `tools=tool_defs`, and set `"input": self._build_gen_input(messages, tool_defs)`.
- `_select_action_phase` (lines 95-115): same pattern as `ToolCallingAgent`.

`ChatCompletionFunctionToolParam` is a plain TypedDict (JSON-serializable), so
the list can be embedded as-is.

## Step 2 — Build the request-shaped `input` (`_build_gen_input`)

Replace the bare-list `input` with the OpenAI-request object so Langfuse renders
the **Available tools** section. One helper in `base_agent.py` keeps the shape
consistent across all three seams and centralizes the toggle + size guard.

**`base_agent.py`** — new helper (near `_prepare_tools`):

```python
def _build_gen_input(self, messages: list, tool_defs: list[dict], tool_choice=None) -> Any:
    """Shape the generation input so Langfuse renders an Available-tools section.

    Returns the OpenAI-request object {messages, tools, tool_choice} when tool
    capture is on and tools exist; otherwise the bare messages list (unchanged
    behaviour, e.g. NoOp/structured-output paths).
    """
    if not tool_defs or not GlobalConfig().observability.capture_tool_definitions:
        return messages
    payload = {"messages": messages, "tools": tool_defs}
    tc = tool_choice if tool_choice is not None else getattr(self, "tool_choice", None)
    if tc is not None:
        payload["tool_choice"] = tc
    return payload
```

Notes:
- Keep `tools` in the **full** `{"type":"function","function":{...}}` shape —
  that exact structure is what Langfuse's tool renderer keys on, so do **not**
  flatten to `{name, description}` here (that's what kills the native section).
- `messages` stays under the `messages` key → Langfuse still renders the chat.
- **No change** to `base_agent._execution_step` metadata, `start_generation`, or
  the provider — they forward `input` through untouched
  (`base_agent.py:300-308, 326-334`; `langfuse_provider.py:214-228`). NoOp
  ignores it.

## Step 3 — Size guard

The native renderer wants full schemas, so we keep them — but guard against a
pathologically large toolset blowing up trace size. Apply inside
`_build_gen_input` before embedding:

- If `len(tool_defs)` exceeds a threshold (e.g. > 40 tools) **or** the serialized
  size is very large, fall back to a compact `[{name, description}]` list and add
  a `"_tools_truncated": true` marker so it's visible in the trace (don't
  silently drop — per project convention, log/flag any capping).
- Otherwise embed full defs.

This keeps the rich rendering in the common case and degrades visibly in the
rare large-toolset case.

## Step 4 — Config toggle

Add to `ObservabilityConfig` (`observability/config.py:50`):

```python
capture_tool_definitions: bool = Field(
    default=True,
    description="Embed the tool/function definitions offered to the LLM in the "
                "generation's Langfuse input (renders as the Available-tools "
                "section). When false, input is the bare messages list.",
)
```

Read via `GlobalConfig().observability...` inside `_build_gen_input` (same access
pattern `MCPBaseTool` already uses). Default `True` so the gap closes out of the
box; flip to `False` to suppress for trace-size/noise control. Gating in the
helper keeps the agents dumb — they always pass `tool_defs`, the helper decides.

---

## Files touched

- `sgr_agent_core/agents/tool_calling_agent.py` — hoist `tool_defs`; wrap `input` via `_build_gen_input`.
- `sgr_agent_core/agents/sgr_tool_calling_agent.py` — same, in both phases (reasoning + action).
- `sgr_agent_core/base_agent.py` — add `_build_gen_input` (+ size-guard logic). No `_execution_step` change.
- `sgr_agent_core/observability/config.py` — `capture_tool_definitions` flag.

## Tests

- New `tests/test_tool_definitions_in_traces.py`:
  - Fake provider capturing `start_generation(input=...)`. Run an FC agent step;
    assert `input["tools"]` contains the expected tool names + descriptions in
    the `{"type":"function","function":{...}}` shape, and `input["messages"]` is
    the chat list.
  - Reasoning seam: assert the `ReasoningTool` def appears in the reasoning
    generation's `input["tools"]`.
  - `capture_tool_definitions=False` → `input` is the bare messages list (no
    `tools` key) — regression guard for the old behaviour.
  - Size guard: a toolset over the threshold → compact `[{name, description}]` +
    `_tools_truncated` marker.
  - NoOp provider path: no crash, no-op (extend `test_observability_noop_path.py`).
- Confirm existing observability tests still pass — any test asserting `input ==
  messages` for an FC generation must be updated to the object shape (or relaxed
  to check `input["messages"]`).

## Risks / notes

- **Langfuse version.** The dedicated Available-tools section needs the Nov-2025
  agent rendering. Older instances still work (tools show as JSON in input);
  metadata is the fallback if needed. Worth a `notes/` entry once landed.
- **Trace size.** Full schemas add bytes per generation. Mitigated by the flag +
  the size guard in `_build_gen_input`.
- **Per-iteration repetition.** The toolset is usually identical every
  iteration, so it repeats across generations. Acceptable (each generation is
  self-describing); a future optimization could log the set once on the trace
  and only deltas per generation — out of scope here.
- **`tool_choice`** is included as a cheap, useful extra (Langfuse shows it
  alongside tools); drop if undesired.
- **MCP descriptions** already flow correctly into the function defs, so once
  this lands they appear in the input automatically — no MCP-side change.
```

