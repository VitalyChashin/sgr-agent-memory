---
title: Generalizing context modification into an Agent Context Processor plugin system
status: active
created: 2026-06-13
updated: 2026-06-13
owner: Vitaly Chashin
related:
  - notes/processor-plugin-pattern.md
  - plans/gaps/agent-context-processors.md
tags: [architecture, processors, agent-loop, multi-agent, sgr]
---

# Research: Generalizing context modification into an Agent Context Processor plugin system

## 1. Problem statement

We run a multi-agent system using the **agent-as-a-tool** pattern through a Context Forge
MCP gateway. Every agent is built on SGR Agent Core; in practice all of them are
`ToolCallingAgent` instances **with the reasoning tool disabled**.

Two recurring failures motivate this research:

- **Issue 1 — leaf-agent retry loop.** An agent at the bottom of the chain calls a real MCP
  tool (e.g. DB search). The tool returns an error or an access denial. The agent keeps
  re-issuing the *same* call, iteration after iteration, until it hits `max_iterations`.
  Wasted tokens, wasted latency, no recovery.

- **Issue 2 — router answers itself.** An agent one level up is supposed to act as a
  **router**: read the request, call a sub-agent (exposed as a tool), and relay the answer.
  Sometimes it skips the call entirely and answers from its own parametric knowledge. For a
  router that is always wrong.

### The two proposed point-fixes

- **Issue 1:** hash each tool call (name + args), count occurrences, and when a call has been
  issued **N** times, **remove that tool from the agent's context** so it can no longer pick
  it. `N` is configurable.

- **Issue 2:** a **corrective slice** — count the tool/sub-agent calls a router made; if the
  agent tries to finish with a count of `0`, **inject a message** ("you did not call any tool
  on the previous step; a tool call is required") and **re-run** the agent.

### The actual research question (the goal)

Both fixes are special cases of the same mechanism: **inspect the agent's running state at a
loop boundary and mutate its context (messages and/or available tools) or its control flow.**

> How do we generalize this into a first-class, configurable extension point — the same way
> we already generalized MCP payload handling and metrics — so future cases of context
> modification are config-driven plugins rather than bespoke agent subclasses?

---

## 2. What the codebase already gives us

> All anchors verified against the working tree on 2026-06-13. `base_agent.py` line numbers
> are from the current `195-rolling-summary-memory` branch.

### 2.1 The execution loop and its seams

`BaseAgent._execute()` (`sgr_agent_core/base_agent.py:389`) drives:

```
while state not in FINISH_STATES:          # base_agent.py:503
    iteration += 1                          # :504
    _execution_step(iter_span, metrics_chain, trace)   # :517
        reasoning      = _reasoning_phase()             # :264
        action_tool    = _select_action_phase(reasoning)# :291
        tool_result    = _action_phase(action_tool)     # :331
```

Context and tools are assembled by two **overridable** methods, re-run every iteration:

- `_prepare_context()` (`base_agent.py:197`) — builds the OpenAI message list:
  `[system, *task_messages, initial_user_request, *self.conversation]`. `self.conversation`
  (`base_agent.py:60`) is the single mutable in-memory transcript; assistant tool-call entries
  and `role:"tool"` results are appended here by each agent subclass
  (`agents/tool_calling_agent.py:69-91`, `agents/sgr_tool_calling_agent.py:129-151`).
- `_prepare_tools()` (`base_agent.py:214`) — returns the tool schemas offered to the LLM.
  Today it is `tools = set(self.toolkit)` every iteration, then the `max_iterations` guard,
  then `pydantic_function_tool(...)`. **The full toolkit is re-offered every turn; nothing
  ever drops a tool.**

These two methods are the natural **read-write seams**: tool removal (Issue 1) belongs in
`_prepare_tools()`; message injection (Issue 2) belongs in `_prepare_context()` or at the top
of the loop body before `_execution_step()`.

### 2.2 The processor pattern we want to mirror (the template)

There are **two** existing processor plugin systems, and they share an identical skeleton.
This skeleton is the template the user is pointing at.

**A. MCP payload processor** — `sgr_agent_core/mcp_payload_processor.py`
- ABC `MCPPayloadProcessor` (`:24`) with `pre_call(payload, context, config, **kw) -> dict`
  (required) and `post_call(result, payload, context, config, **kw) -> str` (optional).
- Auto-registration via `__init_subclass__` (`:35`) into `ProcessorRegistry` (`:20`) — any
  concrete subclass registers itself by class name.
- Config model `PayloadProcessorDefinition` (`:94`): `class:` (aliased to `class_name`),
  `config: dict`, `managed_fields: list`.
- `MCPPayloadProcessorChain` (`:64`): `run_pre_call` top-down, `run_post_call` reversed.
- Invoked in `MCPBaseTool.__call__` (`base_tool.py:68-92`); chain built in
  `services/mcp_service.py:19` (`_build_processor_chain`) — resolves **registry first, then
  dotted import string**.
- **Read-write, fail-fast**: a throwing `pre_call` aborts the tool call.

**B. Metrics processor** — `sgr_agent_core/observability/metrics/processor.py`
- ABC `MetricsProcessor` (`:24`) with five **optional** lifecycle hooks: `on_trace_start`,
  `on_generation_end`, `on_tool_end`, `on_iteration_end`, `on_trace_end` — all
  `async (**kwargs) -> None`.
- Same `__init_subclass__` auto-registration (`:33`) into `MetricsProcessorRegistry` (`:20`).
- Config model `MetricsProcessorDefinition` (`:86`): `class:` + `config:`.
- `MetricsProcessorChain.run_hook(hook_name, **kwargs)` (`:63`) runs every processor,
  **fail-silent** (exceptions logged, never raised).
- Built **once per run** by `build_metrics_chain(GlobalConfig)`
  (`observability/metrics/__init__.py:34`), invoked at the five loop seams in
  `base_agent.py` (`:444 on_trace_start`, `:281/:307 on_generation_end`, `:337 on_tool_end`,
  `:537 on_iteration_end`, `:555 on_trace_end`).

**Shared skeleton** (the thing to reuse):

| Piece | MCP payload | Metrics |
|---|---|---|
| ABC + `__init_subclass__` auto-register | ✅ | ✅ |
| `Registry[T]` keyed by class name | `ProcessorRegistry` | `MetricsProcessorRegistry` |
| `*Definition` model (`class` alias + `config` dict) | ✅ | ✅ |
| `*Chain` runner | ✅ | ✅ |
| Resolve: registry → dotted import string | ✅ | ✅ |
| Built-ins imported once to self-register | `processors/` | `observability/metrics/__init__.py` |

The **critical axis of difference** is the contract:

- Metrics hooks are **observers** — `-> None`, fail-silent, cannot change the loop.
- Payload processors are **transformers** — return the new value, fail-fast, but scoped to a
  single tool call, with no view of the loop.

Our new need sits in the **empty quadrant**: a hook that runs **at loop seams** (like metrics)
but is **read-write on conversation/tools/control-flow** (like payload processors). Neither
existing system can do Issue 1 or Issue 2 as-is.

### 2.3 Config hierarchy (where a new block lives)

`GlobalConfig` (`agent_config.py:17`) → `AgentConfig` (`agent_definition.py:154`) →
`AgentDefinition` (`agent_definition.py:211`). Per-agent overrides merge over global via
`agent_level_config_override_validator` (`:280`) using `model_copy(update=...)`. The running
agent reads `self.config` (`base_agent.py:53`); limits like `max_iterations` come from
`self.config.execution.max_iterations` (`agent_definition.py:140`).

- **Metrics processors are global** (`config.observability.metrics_processors`).
- **MCP payload processors are per-server** (`mcp.mcpServers.<name>.payload_processors`).

For context processors the right scope is **per-agent** (`AgentDefinition`), because the rules
differ by role: the **router** needs "must call a tool", the **leaf** needs "drop a tool after
N repeats". A single global list cannot express that. This is the main config-placement
decision (see §4).

### 2.4 Tool-call mechanics relevant to the two issues

- **Call identity for hashing (Issue 1).** A tool instance is a Pydantic model; the loop
  already computes `tool_name = action_tool.tool_name` and `tool_args =
  action_tool.model_dump(mode="json")` (`base_agent.py:318-320`). A stable key is
  `sha1(tool_name + json.dumps(tool_args, sort_keys=True))`. Note: hash the **LLM-visible**
  args (`model_dump`), *before* MCP payload processors inject `managed_fields`, so we measure
  the model's intent, not gateway-added fields.
- **Error/denial surfacing.** `MCPBaseTool.__call__` swallows exceptions and returns
  `f"Error: {e}"` as the tool result string (`base_tool.py:90-92`). So a denial is **not** an
  exception — it is an ordinary `role:"tool"` message whose content starts with `Error:`.
  Detecting "this call failed" means string/heuristic inspection of the result, or counting
  repeats regardless of success (simpler, and arguably correct — a router that repeats an
  identical *successful* call is also stuck).
- **Repeated-failure detection today: none.** The loop runs to `max_iterations`
  (`base_agent.py:224`, raises `RuntimeError`). No dedup, no per-call counters.
- **Zero-tool-call / "answer without calling" (Issue 2).** Behaviour differs by agent class:
  - `SGRToolCallingAgent` (`agents/sgr_tool_calling_agent.py:116-125`) catches the empty
    `tool_calls` case and **synthesises a `FinalAnswerTool`** — i.e. it lets the agent finish
    with zero calls. This is exactly the router-answers-itself path.
  - `ToolCallingAgent` indexes `tool_calls[0]` (`agents/tool_calling_agent.py:65`) and would
    raise `IndexError` on an empty list. **Confirm in plan phase** how the production
    `ToolCallingAgent` actually terminates a zero-call turn — the fix's trigger point depends
    on it.
  - Tool-call count per turn is derivable from `self.conversation` (assistant entries bearing
    `tool_calls`) or from the execution log (`step_type == "tool_execution"`,
    `base_agent.py:167`).

---

## 3. Proposed generalization: **Agent Context Processor**

A third processor family, mirroring the existing skeleton, occupying the read-write-at-loop-
seams quadrant.

### 3.1 Shape (reuse the skeleton verbatim)

- ABC `AgentContextProcessor` with `__init_subclass__` auto-registration into a new
  `AgentContextProcessorRegistry(Registry[...])`.
- `ContextProcessorDefinition(BaseModel)`: `class:` (alias) + `config: dict` — same as the
  other two.
- `AgentContextProcessorChain` built **once per run** (per-agent), holding per-run state
  (the call-count map lives in the processor instance, fresh each `_execute()` — same
  lifecycle as metrics chains, which are rebuilt per run at `base_agent.py:442`).
- Resolution: registry first, dotted import string fallback. Built-ins imported once to
  self-register.

### 3.2 The hook surface (the one real design question)

Unlike metrics, these hooks must be able to **change** things. Two viable contracts:

- **(a) Mutate-in-place.** Hooks receive the live `conversation` list and the working tool set
  and mutate them; a finish-veto hook returns a small directive object.
- **(b) Return-a-directive.** Hooks are pure-ish and return a typed result
  (`drop_tools=[...]`, `inject_messages=[...]`, `force_continue=True`) that the loop applies.
  Cleaner to test, mirrors `pre_call`'s return-the-value style. **Recommended.**

Proposed seams (each maps to an existing call site):

| Hook | Seam (file:line) | Can affect | Serves |
|---|---|---|---|
| `on_prepare_tools(toolkit, counters, ctx, cfg)` | inside `_prepare_tools` `base_agent.py:223` | drop tools from the offered set | **Issue 1** |
| `on_tool_end(tool_name, tool_args, result, ...)` | `base_agent.py:337` (already a metrics seam) | update per-run counters | **Issue 1** bookkeeping |
| `on_before_finish(state, ctx, cfg, tool_calls_this_run)` | new seam before accepting `FINISH_STATES` at `base_agent.py:503/519` | veto finish → inject message + `force_continue` | **Issue 2** |

`on_before_finish` is the only genuinely new interception point; the loop currently has no
"the agent wants to stop — should we let it?" hook. Everything else reuses existing seams.

### 3.3 The two issues as built-in processors

- **`RepeatedToolCallGuard`** (Issue 1). `on_tool_end` increments a count keyed by the
  call hash; `on_prepare_tools` drops any tool whose hash count ≥ `max_repeats`. Config:
  `{max_repeats: 3, scope: "exact_args" | "tool_name"}`. Guard: never drop the terminal
  tool (`FinalAnswerTool`), or the agent can't end. Decide whether to also inject a
  "tool X disabled after N repeats" note so the model understands the toolkit shrank.
- **`MandatoryToolCallProcessor`** (Issue 2, router enforcement). `on_before_finish`: if
  `tool_calls_this_run == 0` (excluding the final-answer tool) and retries-used <
  `max_retries`, inject the corrective message and `force_continue=True`. Config:
  `{min_tool_calls: 1, max_retries: 2}`. Per-agent: enabled only on routers.

### 3.4 Why this is the right generalization

Future context-modification needs slot in as new processors with **no agent-subclass and no
loop edits**: context-window trimming, PII redaction before send, forced-clarification rules,
deduplating near-identical messages, injecting tool-use hints after stalls, etc. — all are
"observe at a seam, optionally mutate context/tools/flow", which is exactly this contract.
It also unifies three sprawling-but-identical skeletons under one mental model the team
already knows.

---

## 4. Key decisions & trade-offs (for the plan)

1. **Config scope: per-agent.** Put `context_processors:` on `AgentDefinition` (merged via the
   existing override validator), not on `GlobalConfig`. Routers and leaves need different
   rules. (A global default list that agents extend is a possible refinement.)
2. **Hook contract: return-a-directive** over mutate-in-place — testable, explicit, mirrors
   payload processors.
3. **Fail policy: fail-safe, not fail-silent.** Metrics swallow everything; a context
   processor that silently no-ops could mask a router bug. Prefer: log + skip the individual
   processor, but surface in trace. Decide per-hook (a throwing `on_prepare_tools` should
   *not* take the agent down, but should be visible).
4. **State lifecycle: per-run instance.** Counters live on the processor instance; the chain
   is rebuilt each `_execute()` (as metrics chains already are), so no cross-request leakage.
5. **Reuse, don't fork, the skeleton.** The ABC/`__init_subclass__`/`Registry`/`Definition`/
   resolution code is duplicated between the two existing systems already; consider factoring
   a tiny shared `processor_base` helper while adding the third, rather than a third copy.

---

## 5. Open questions → filed as gaps

See `plans/gaps/agent-context-processors.md`. Summary:

- How does the **production `ToolCallingAgent`** (reasoning disabled) actually terminate a
  zero-tool-call turn? The Issue-2 trigger depends on it (§2.4).
- Should Issue-1 dedup count **all** repeats or only **failed** (`Error:`-prefixed) repeats?
- "Drop a tool" vs "inject a stop-repeating instruction" — which does the LLM handle better?
  (Dropping is deterministic; instructing is softer but keeps capability.)
- Does dropping a tool mid-run confuse providers that cache the tool list, or break the
  agent-as-tool gateway's expectations?
- Global-default + per-agent-extend merge semantics, if we want shared baseline rules.

---

## 6. Recommendation & next step

Build an **Agent Context Processor** plugin family that mirrors the existing processor
skeleton but occupies the read-write-at-loop-seams quadrant, configured **per-agent**, with a
**return-a-directive** hook contract and three seams (`on_prepare_tools`, `on_tool_end`,
`on_before_finish`). Ship Issues 1 and 2 as the two reference built-ins
(`RepeatedToolCallGuard`, `MandatoryToolCallProcessor`).

**Next:** move to the **plan** phase → `plans/agent-context-processors.md` (data model, the
`on_before_finish` seam wiring, config schema + override merge, built-ins, tests). When this
research is acted on, set `status: archived` here.
