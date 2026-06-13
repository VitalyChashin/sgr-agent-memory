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

1. ~~**Zero-call termination path of the production agent.**~~ **RESOLVED 2026-06-13.**
   `ToolCallingAgent` sets `tool_choice = "required"` (`agents/tool_calling_agent.py:35`), so
   the LLM must emit a tool call every turn — the empty-`tool_calls` path cannot occur and
   `tool_calls[0]` (`:65`) is safe. The graceful fallback exists only in `SGRToolCallingAgent`
   (`:116-125`), unused in production. Termination is therefore **always** via a terminal
   `SystemBaseTool` — `FinalAnswerTool` (`tools/final_answer_tool.py:33-36`) sets
   `state = COMPLETED/FAILED`; loop exits at `base_agent.py:503`. "Router answered itself" =
   `FinalAnswerTool` called with **zero** prior work-tool calls, where a work tool is one with
   `isSystemTool == False` (`base_tool.py:35`, `:55`). The `on_before_finish` hook fires right
   after a terminal tool drives a finish state, before the loop re-checks; veto resets `state`
   to a non-finish resume state + injects the corrective message. (Sub-question for the plan:
   which exact `AgentStatesEnum` member is the correct "resume" state.)

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
