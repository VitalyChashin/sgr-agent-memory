---
title: Tool definitions in Langfuse traces — settled design caveats
status: current
created: 2026-06-19
updated: 2026-06-19
related:
  - plans/langfuse-tool-definitions-in-traces.md
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/agents/sgr_tool_calling_agent.py
  - sgr_agent_core/observability/config.py
---

# Note: tool definitions in Langfuse traces (`_build_gen_input`)

**2026-06-19 —** The FC agents now embed the tool defs offered to the LLM in the
generation `input` so Langfuse renders its native **Available-tools** section.
Settled decisions worth remembering:

- **Input is a request-shaped object, not a bare list.** `_build_gen_input`
  returns `{"messages": [...], "tools": [...], "tool_choice": ...}`. Langfuse
  renders `messages` as the chat *and* detects `tools` as a separate collapsible
  section with output calls auto-numbered to match. Putting tools in `metadata`
  was rejected — it renders as raw JSON with no tool-aware view and no link to
  the calls made.

- **Do NOT flatten the tool shape.** Tools must stay in the full
  `{"type": "function", "function": {name, description, parameters}}` form
  (exactly what `pydantic_function_tool` emits) — that's what Langfuse's renderer
  keys on. Flattening to `{name, description}` kills the native section. The one
  place we *do* flatten is the large-toolset degrade path (see below), which
  knowingly trades the rich rendering for trace size.

- **Native section needs the Nov-2025 "Langfuse for Agents" release.** Cloud has
  it; self-hosted must be recent enough. On older instances nothing breaks —
  messages still render as chat, tools just show as plain JSON in the input.

- **Size guard, not silent truncation.** Above `BaseAgent._MAX_TRACED_TOOL_DEFS`
  (40) the tools degrade to compact `[{name, description}]` and the payload is
  flagged `_tools_truncated: true` so the capping is visible in the trace
  (project convention: never silently cap coverage).

- **Toggle:** `observability.capture_tool_definitions` (default `True`). When
  off, `input` reverts to the bare messages list (old behaviour). Gating lives
  in `_build_gen_input` so the agents stay dumb — they always pass `tool_defs`.

- **MCP tools need no special handling.** Their descriptions already flow
  correctly into the function defs at build time, so they appear in the input
  section automatically.

- **Scope:** the three FC seams (`ToolCallingAgent` action; `SGRToolCallingAgent`
  reasoning + action). `SGRAgent` (pure structured output, no `tools=`) is out of
  scope — it passes a response schema, not a tool list.
