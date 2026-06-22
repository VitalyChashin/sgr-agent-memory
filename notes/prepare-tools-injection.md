---
title: Prepare-tools seam message injection — ordering caveat
status: current
created: 2026-06-22
updated: 2026-06-22
related:
  - plans/prepare-tools-message-injection.md
  - plans/gaps/agent-context-processors.md
  - sgr_agent_core/context_processors/base.py
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/agents/sgr_tool_calling_agent.py
---

# Note: prepare-tools seam can inject conversation messages

**2026-06-22 —** The `on_prepare_tools` seam can now mutate the conversation, not
just drop tools. A processor returns a `PrepareToolsResult(drop, inject_messages)`
(legacy bare `set[str]` still accepted — normalized by `PrepareToolsResult.coerce`).
`base_agent._prepare_tools` removes `drop` from the toolkit *and* appends
`inject_messages` to `self.conversation`.

**The sharp edge: ordering.** The seam runs inside `_prepare_tools`. For an
injected directive to reach the **same** iteration's action-selection LLM call,
the agent must call `_prepare_tools()` **before** `_prepare_context()` — otherwise
`_prepare_context` snapshots `self.conversation` before the message is appended and
the directive only lands one turn late (after the confused turn it was meant to
fix). The two FC agents that use the seam were reordered accordingly:

```python
tool_defs = await self._prepare_tools()    # may drop + inject into self.conversation
messages  = await self._prepare_context()  # snapshots the now-augmented conversation
```

`iron_agent` / `sgr_agent` override `_prepare_tools` (return a structured stub,
never hit the chain) so they don't carry injection. **Any custom FC agent that
builds `messages` before `_prepare_tools` must follow the same tools-before-context
order**, or injected directives silently slip to the next iteration.

**Why this exists.** `RepeatedToolCallGuard` drops a repeatedly-failing tool, but
the turn history still names that tool (every past `assistant`/`tool_calls` entry)
while `tool_choice="required"` forces a call. Without a directive, the model
reconciles "history says call X / X is gone / you must call something" on its own —
the long, token-heavy action-selection call observed in ~40–50% of guard runs.
`announce: true` injects a one-time "tool X disabled — finalize now" note that
collapses that ambiguity. Injected once per tool (gated by `_announced`); the drop
itself keeps applying every turn until finish.
