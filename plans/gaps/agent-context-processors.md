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

2. ~~**Issue-1 counting scope.**~~ **RESOLVED 2026-06-13.** Both, gated by config:
   `RepeatedToolCallGuard` counts every call by default and exposes `failed_only: bool`
   (`repeated_tool_call_guard.py`) to narrow to results starting with `"Error:"`.

3. **Drop-tool vs instruct.** **Partially resolved 2026-06-13.** v1 ships the deterministic
   *drop* (`on_prepare_tools` returns tool names to drop). The softer *instruct* path is
   **deferred**: the prepare-tools seam returns only a `set[str]` and has no message-injection
   channel (only `on_before_finish` injects). `RepeatedToolCallGuard` accepts an `announce`
   flag but it currently only tags the emitted span — conversation injection of a "tool X
   disabled" note still needs a seam that can mutate the conversation. Reopen if needed.

4. **Provider / gateway side-effects of a shrinking tool list.** STILL OPEN. Dropping a tool
   mid-run is implemented and unit/loop tested, but the interaction with prompt/tool-list
   caching and the Context Forge MCP gateway's agent-as-tool surface is unverified — check
   before relying on drop-mid-run in production.

5. ~~**Config merge semantics.**~~ **DEFERRED (decided) 2026-06-13.** v1 is per-agent
   *replace*: `context_processors` is a plain `list` field on `AgentConfig`, so the override
   validator (`agent_definition.py`) passes it through untouched (per-agent value replaces the
   global default). Global-baseline + per-agent-extend merge is future work.

6. ~~**Fail policy granularity.**~~ **RESOLVED 2026-06-13.** Implemented in
   `AgentContextProcessorChain` (`context_processors/base.py`): all three hooks are
   fail-safe-but-visible (logged at WARNING). A throwing `on_prepare_tools` contributes no
   drops (agent keeps full toolkit); a throwing `on_before_finish` is treated as no veto
   (agent finishes — fail toward termination, never an infinite loop).
