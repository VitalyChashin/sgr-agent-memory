---
title: The processor-plugin skeleton (and its three quadrants)
status: current
created: 2026-06-13
updated: 2026-06-13
related:
  - research/agent-context-processors.md
---

# Note: the processor-plugin skeleton

**2026-06-13 —** SGR Agent Core has **two** processor plugin systems that share one identical
skeleton, and we are adding a third. Worth remembering so we don't reinvent or mis-copy it.

## The shared skeleton

Both `MCPPayloadProcessor` (`sgr_agent_core/mcp_payload_processor.py:24`) and
`MetricsProcessor` (`sgr_agent_core/observability/metrics/processor.py:24`) are built the same
way:

1. An ABC that auto-registers concrete subclasses via `__init_subclass__` (skips abstract
   classes) into a `Registry[T]` keyed by class name.
2. A `*Definition` Pydantic model with `class:` (aliased to `class_name`) + `config: dict`
   (payload processors also add `managed_fields`).
3. A `*Chain` runner holding the ordered processor instances.
4. Resolution order: **registry by class name first, then dotted import string**
   (`services/mcp_service.py:19`, `observability/metrics/__init__.py:34`).
5. Built-ins are imported once (in a package `__init__` / `processors/`) purely to trigger
   self-registration.

When adding any new plugin family, copy this skeleton — don't invent a new mechanism.

## The three quadrants (observe × mutate)

The two existing systems differ on **two independent axes** — *when* they run and *whether
they can change anything*:

| | Observer (`-> None`, fail-silent) | Transformer (returns value / mutates, fail-fast) |
|---|---|---|
| **Per tool-call** | — | **MCP payload processor** (`pre_call`/`post_call`, `base_tool.py:68`) |
| **At loop seams** | **Metrics processor** (5 hooks in `base_agent.py`) | **← the empty quadrant** |

The empty quadrant — *read-write at loop seams* — is exactly what context modification (drop a
repeated tool, inject a corrective message, veto a finish) needs, and is why neither existing
system can do it. The planned **Agent Context Processor** fills it. See
`research/agent-context-processors.md`.

## Sharp edges

- **Metrics hooks are fire-and-forget.** `MetricsProcessorChain.run_hook` swallows every
  exception (`processor.py:63`). Do **not** assume the metrics pattern's fail-silent policy is
  right for a processor that affects control flow — silence there hides bugs.
- **Chains are rebuilt per run.** `build_metrics_chain` runs inside `_execute()`
  (`base_agent.py:442`), so processor instance state is per-request. Good for per-run counters;
  don't stash cross-request state on a processor instance.
- **MCP errors are not exceptions.** `MCPBaseTool.__call__` returns `f"Error: {e}"` as the
  tool result string (`base_tool.py:90-92`) — denials look like ordinary `role:"tool"`
  messages, not raised exceptions. Any "did this call fail" logic must inspect the result
  string, not catch.
