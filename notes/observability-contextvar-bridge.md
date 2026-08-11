---
title: The observability contextvar bridge between the agent loop and the tool layer
status: current
created: 2026-06-18
updated: 2026-06-18
related:
  - plans/langfuse-error-trace-filtering.md
  - notes/processor-plugin-pattern.md
---

# Note: observability contextvar bridge (`observability/context.py`)

**2026-06-18 —** Two pieces of Langfuse plumbing deliberately use **task-local
contextvars** instead of threading parameters. Worth remembering because the "obvious"
alternatives are wrong here.

## Why contextvars, not function params

The MCP tool span is created in `BaseAgent._execution_step` (`base_agent.py`), but the
code that needs it — `MCPBaseTool.__call__` and the MCP payload-processor chain — is
reached through `_action_phase`, which has **5 separate subclass implementations**
(`iron`, `sgr`, `sgr_tool_calling`, `tool_calling`, + `dialog`'s `_execution_step`
override). Threading a `parent_span` kwarg would touch all of them *and* pollute the
`**kwargs` that already flow into `pre_call`/`post_call` (those carry `tool_configs`).

So `observability/context.py` holds:

- `current_tool_span` — set by the loop around the `_action_phase` call (tight
  try/finally, reset exactly once), read by `MCPBaseTool.__call__` so payload-processor
  spans nest under the tool span. The provider itself is the global `get_provider()`
  singleton, so only the *parent span* needs bridging.
- `mcp_call_errored` — a boolean flag (see below).

The module imports **nothing** from `base_agent`/`base_tool`, which is what breaks the
agent↔tool import cycle. Put new shared loop/tool observability helpers here for the
same reason (the `processor_span_start`/`processor_span_end` pair already lives here).

## The swallowed-MCP-error flag (the sharp edge)

MCP tool errors are **swallowed** in `MCPBaseTool.__call__` — caught and returned as
`"Error: …"` so the agent loop continues (it never raises). That means the loop's tool
span ends on the **success** path. If `base_tool` tried to mark the span ERROR itself,
the loop's subsequent `end_span` (success branch) would **overwrite it back to
DEFAULT**.

Fix: `base_tool` sets `mcp_call_errored = True` (and adds `error:mcp_tool` to
`context.error_tags`); the loop reads + resets the flag right after `_action_phase` and
chooses the span level at its single `end_span` call. Don't "simplify" this by ending
the span inside `base_tool` — it will be clobbered.
