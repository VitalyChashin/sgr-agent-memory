---
title: Implementation plan — in-loop context eviction processor (ToolCallingAgent overflow)
status: active
created: 2026-06-19
updated: 2026-06-19
owner: Vitaly Chashin
supersedes: []
related:
  - research/toolcalling-context-growth.md
  - research/agent-context-processors.md
  - plans/agent-context-processors.md
  - notes/processor-plugin-pattern.md
  - sgr_agent_core/context_processors/base.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/base_agent.py
tags: [agent-loop, context-window, processors, eviction, tool-calling]
---

# Plan: in-loop context eviction as an AgentContextProcessor

Implements §4 of `research/toolcalling-context-growth.md`: stop unbounded transcript
growth (untruncated MCP/SQL tool results re-entering the prompt every step) by adding a
deterministic, model-free **context-eviction** `AgentContextProcessor`. This ports
pi-cwl's *structure-aware eviction* insight without its episode-graph annotation protocol
(which the research rules out for a pure function-calling loop).

## 1. Goal & scope

**In scope**

- A new built-in `ContextEvictionProcessor` (in `context_processors/`) that, when the
  running transcript exceeds a token budget, replaces the `content` of the **oldest
  tool-result messages** with a short stub, keeping the last *N* results intact.
- The **enabling seam change**: give context-processor hooks read-write access to the
  live transcript (`self.conversation`). Today they get `AgentContext` + `AgentConfig`
  only — no message list — so eviction is currently impossible. This is the load-bearing
  part of the plan.
- A pluggable token estimator (cheap char heuristic default; optional `tiktoken`).
- Config surface (`config`/`span_mode` on the existing `ContextProcessorDefinition`),
  an `agents.yaml.example` snippet, tests.
- (Optional, gated) cheap interim mitigation: hard cap each tool result length before it
  is appended.

**Out of scope (deferred)**

- pi-cwl's typed episode graph / `delimiter`-tool annotation — research §3b: poor fit for
  a single-required-tool agent; not needed to solve overflow. File as a gap if revisited
  on `SGRToolCallingAgent`.
- Tokenizer-exact parity with Qwen-3.5 — start with a heuristic; make the counter
  pluggable so exactness can be added later without touching the policy.
- LLM-based in-loop summarization / merging the rolling-summary path into the loop
  (research §6, last bullet) — eviction first; summarization is a separate, heavier lever.
- Compacting assistant `content`/reasoning blobs — start with tool results (the dominant
  mass per the diagnosis); revisit only if budgets are still blown.

## 2. The blocking gap: processors can't see the transcript

The transcript lives on the **agent** (`self.conversation`, a `list[dict]`), assembled
into the prompt by `base_agent.py:_prepare_context()` (215-230). But the context-processor
hooks are handed only `context: AgentContext` and `config: AgentConfig`
(`context_processors/base.py:83-114`). `AgentContext` (`models.py:47`) has no live
conversation reference — `recent_messages`/`conversation_summary` are pre-loop
rolling-memory artifacts, not the in-loop transcript.

**Decision: thread `self.conversation` into the chain calls as a keyword arg.** The chain
methods are invoked from `base_agent` where `self.conversation` is in scope
(`_execution_step` line 388-396 for `on_tool_end`; line 247-253 for `on_prepare_tools`).
Hooks already accept `**kw`, so passing `conversation=self.conversation` is fully
backward-compatible — existing built-ins ignore it, and the eviction processor reads it
from `kw`. The list is passed **by reference**; the processor mutates message dicts in
place (`msg["content"] = stub`), so edits persist into the exact list `_prepare_context`
re-reads next step. No new seam, no new `AgentContext` field.

Rejected alternatives:
- *Add a `conversation` field to `AgentContext`* — pollutes a Pydantic model with a
  mutable agent-owned list and risks it being serialized into `agent_state()`/responses.
- *New `on_prepare_context(messages) -> messages` seam* — cleaner conceptually but a
  larger surface change (new hook on ABC + chain + all call sites) and eviction at
  `on_tool_end` is sufficient and persistent. Note as a possible future seam if a
  processor ever needs to transform the *assembled* prompt (system+task) rather than just
  the transcript.

## 3. Module layout

| File | Change |
|---|---|
| `context_processors/base.py` | Add `conversation` kwarg to `AgentContextProcessorChain.run_on_tool_end` (and `run_prepare_tools` for symmetry); forward into the hook call. |
| `base_agent.py` | Pass `conversation=self.conversation` at the two chain call sites (lines ~247 and ~388). |
| `context_processors/context_eviction.py` | **New.** `ContextEvictionProcessor` + token estimator. |
| `context_processors/__init__.py` | Import new module (auto-register) + add to `__all__`. |
| `agents.yaml.example` | Config snippet. |
| `tests/...` | Unit + a loop-level integration test. |
| `notes/context-eviction.md` | Settled caveats (pairing, KV-cache batching). |

## 4. `ContextEvictionProcessor`

### 4.1 Behavior

On `on_tool_end` (after each tool, once the just-produced result is appended):

1. Read `conversation` from `kw`. If absent, no-op (fail-safe — older agents / callers).
2. Estimate total transcript tokens (§4.3).
3. If under the **high-watermark**, return. Otherwise evict **oldest-first** until under
   the **low-watermark** (hysteresis — §4.4): for each `role:"tool"` message whose
   `content` is not already a stub and which is **not** among the last `keep_recent`
   tool-results, replace `content` with the stub template (§4.2). Never touch the
   assistant `tool_calls` messages (they're small and pairing-critical), never touch
   `role:"system"`/`role:"user"` messages.
4. Emit a `ctx-processor.context_eviction.evicted` event span **only when it fires**
   (count, tokens before/after, iteration) via `emit_event_span` — mirrors the other
   built-ins.

Eviction at `on_tool_end` (not `on_prepare_tools`) so the edit is in place before the
next `_prepare_context()` and the stub persists for all subsequent steps.

### 4.2 Message-pairing validity (hard constraint)

OpenAI format requires every assistant `tool_calls` message to be followed by `role:"tool"`
messages with matching `tool_call_id`. **Never delete** a tool message — only rewrite its
`content`. Stub example:

```
[evicted: result of <tool_name> at step <n> — <orig_len> chars omitted to fit context]
```

`tool_name`/step are recoverable from the paired assistant message and message order; if
not cheaply available, fall back to a generic stub. Keep stubs short and stable so a
re-evicted message is idempotent (already-stub → skip).

### 4.3 Token estimation (pluggable)

- Default: char heuristic — `ceil(total_chars / chars_per_token)`, `chars_per_token`
  configurable (default 4). Zero deps, good enough to bound growth.
- Optional: `counter: "tiktoken"` — lazy-import; if unavailable, log once and fall back to
  the heuristic (fail-safe).
- Estimate over the **conversation** plus a fixed `reserve_tokens` headroom for the
  system prompt + `task_messages` + model output (those aren't passed to the hook). The
  budget is expressed against the model's context window, so:
  `budget = context_window - reserve_tokens`.

### 4.4 KV-cache / prefix churn

Any edit to an earlier message invalidates the prefix KV-cache from that point. To limit
churn, use **hysteresis**: only start evicting at `high_watermark` (e.g. 0.9 × budget) and
evict down to `low_watermark` (e.g. 0.6 × budget) in one batched pass, then leave the
transcript alone until it crosses `high_watermark` again. This converts per-step nibbling
into occasional batch edits. Document in `notes/context-eviction.md`.

### 4.5 Config

Via the existing `ContextProcessorDefinition` (`class`, `config`, `span_mode`):

```yaml
context_processors:
  - class: ContextEvictionProcessor
    span_mode: fired
    config:
      context_window: 32768      # model window; required (no safe default per-model)
      reserve_tokens: 4096       # headroom for system+task+output
      high_watermark: 0.90       # fraction of (context_window - reserve) to trigger
      low_watermark: 0.60        # evict down to this fraction
      keep_recent: 3             # most-recent tool results kept verbatim
      counter: heuristic         # "heuristic" | "tiktoken"
      chars_per_token: 4
```

All knobs are validated in `__init__` with safe coercion + defaults (matching the existing
built-ins' style). Configured **per-agent** (per the processor family's per-agent-replace
semantics).

## 5. Interim mitigation (optional, separate toggle)

Research §4 last bullet: a crude per-result cap. Implement as a tiny, independent path so
it can ship before/without the processor — but prefer the processor as the real fix.
Option: a `max_tool_result_chars` knob honored in `ToolCallingAgent._action_phase`
(`tool_calling_agent.py:91`) truncating `result` before append. **Recommendation:** skip
this unless an immediate stopgap is needed before the processor lands — it loses the
"keep recent full" intelligence and adds a second, overlapping mechanism. Note it as a
gap rather than building both.

## 6. Tests

- **Unit (estimator):** char heuristic and watermark math; tiktoken-absent fallback.
- **Unit (eviction policy):** synthetic conversation (assistant/tool pairs + system/user);
  assert (a) oldest tool contents stubbed first, (b) last `keep_recent` untouched,
  (c) assistant `tool_calls` and `tool_call_id` pairing preserved, (d) under low-watermark
  after a pass, (e) idempotent (second pass with no growth → no further edits),
  (f) no-op when `conversation` kwarg absent.
- **Integration (loop):** a `ToolCallingAgent` with a fake tool returning a large result,
  small `context_window`; run several steps; assert transcript token estimate stays
  bounded and old results become stubs while the agent still finishes. Follow the existing
  loop-integration test in the agent-context-processors test suite as the template.

## 7. Phasing

1. **Seam** — add `conversation` kwarg through the chain + base_agent call sites; assert
   existing built-ins/tests unaffected. (Small, isolated; lands first.)
2. **Processor** — `context_eviction.py` (estimator + policy + span), register, config.
3. **Tests** — unit + integration.
4. **Docs** — `agents.yaml.example` snippet; `notes/context-eviction.md` (pairing +
   KV-cache batching); update CLAUDE.md "Processor Plugins" built-ins list to mention
   `ContextEvictionProcessor`; flip `research/toolcalling-context-growth.md` to
   `status: archived` when implemented.

## 8. Open questions / gaps (carry to `plans/gaps/` if unresolved at build time)

- `context_window` has no safe cross-model default — require it in config (fail loud if
  missing) vs. derive from `config.llm`? Check whether the LLM config already carries a
  context-window value before requiring a duplicate knob.
- `keep_recent` (N) and watermark fractions — defaults above are guesses; validate against
  a real Qwen-3.5 SQL run.
- Whether to also stub large **assistant `content`** blobs (out of scope now; revisit if
  tool-result eviction alone doesn't fit the budget).
- Multi-agent reuse: confirm `SGRToolCallingAgent` and others route through
  `base_agent._execution_step` (so they inherit the seam) and don't override it.
