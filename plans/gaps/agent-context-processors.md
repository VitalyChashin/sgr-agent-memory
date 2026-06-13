---
title: Open questions — Agent Context Processor design
status: open
created: 2026-06-13
updated: 2026-06-13
related:
  - research/agent-context-processors.md
---

# Gaps: Agent Context Processor design

Unresolved questions surfaced during research (`research/agent-context-processors.md`).
Close each by setting it resolved here (keep the node) once the plan/implementation settles it.

1. **Zero-call termination path of the production agent.** All production agents are
   `ToolCallingAgent` with reasoning disabled. `SGRToolCallingAgent` synthesises a
   `FinalAnswerTool` on empty `tool_calls` (`agents/sgr_tool_calling_agent.py:116-125`), but
   `ToolCallingAgent` indexes `tool_calls[0]` (`agents/tool_calling_agent.py:65`) and looks
   like it would raise `IndexError`. **Need to confirm how a zero-tool-call turn actually
   ends in production** — it determines where the Issue-2 `on_before_finish` hook must fire.

2. **Issue-1 counting scope.** Count *every* repeated identical call, or only repeated
   **failed** ones (result string starts with `Error:`, per `base_tool.py:90-92`)? Counting
   all is simpler and also catches stuck-on-success loops; failed-only is narrower.

3. **Drop-tool vs instruct.** Removing the tool from `_prepare_tools` is deterministic but
   shrinks capability and may confuse the model; injecting a "stop repeating tool X" message
   is softer but unreliable. Which does the LLM handle better in practice? Possibly both,
   gated by config.

4. **Provider / gateway side-effects of a shrinking tool list.** Does dropping a tool mid-run
   interact badly with prompt/tool-list caching, or with the Context Forge MCP gateway's
   expectations for the agent-as-tool surface? Needs a quick check before relying on it.

5. **Config merge semantics.** If we want a global baseline of context processors that
   per-agent config extends/overrides (rather than pure per-agent lists), define the merge
   rule. The existing `agent_level_config_override_validator` (`agent_definition.py:280`)
   does field-level `model_copy(update=...)`, which replaces lists wholesale — extend
   semantics would need explicit handling.

6. **Fail policy granularity.** Confirm per-hook failure behaviour: a throwing
   `on_prepare_tools` must not kill the agent (fall back to the unmodified toolkit), but a
   throwing `on_before_finish` veto should probably let the agent finish rather than loop
   forever. Decide and document.
