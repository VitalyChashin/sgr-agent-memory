---
title: Implementation plan — message injection at the prepare-tools seam
status: archived
created: 2026-06-22
updated: 2026-06-22  # implemented: PrepareToolsResult seam, FC-agent reorder, guard announce, tests green
owner: Vitaly Chashin
supersedes: []
related:
  - plans/gaps/agent-context-processors.md   # closes gap #3
  - plans/agent-context-processors.md
  - sgr_agent_core/context_processors/base.py
  - notes/processor-plugin-pattern.md
tags: [architecture, processors, agent-loop, plan]
---

# Plan: message injection at the prepare-tools seam

## 1. Problem & motivation

The `on_prepare_tools` seam can only **drop** tools (returns `set[str]`); it has no
channel to mutate the conversation (gap #3). When `RepeatedToolCallGuard` drops a
repeatedly-failing tool, the tool silently disappears from `tool_defs` while the
turn history still contains that tool's **name** (every past call is an
`assistant`/`tool_calls[].function.name` entry, `tool_calling_agent.py:79`) plus
its arguments and `"Error: ..."` results. With `tool_choice="required"` the model
is left to reconcile three contradictory signals on its own — *history says call
X*, *toolset says X is gone*, *you must call something* — which produces the long,
token-heavy action-selection call observed in ~40–50% of runs (see conversation
analysis; `ToolCallingAgent` fuses all reasoning into that single constrained
generation, so the spike lands entirely there).

Injecting a short directive at drop time — *"Tool X disabled after repeated
failures; do not call it, finalize your answer"* — converts an inferred
contradiction (expensive to reason through) into a stated instruction (cheap to
follow). This closes gap #3's deferred *instruct* path.

## 2. Goal & scope

**In scope**
- Extend the prepare-tools seam so a processor can return **messages to inject**
  in addition to tool-name drops.
- Make the injected message land in the **same iteration's** action-selection
  call (not the next one) — the spike happens on the drop turn itself.
- Wire `RepeatedToolCallGuard.announce` to actually inject a one-time note
  (today it only tags a span).
- Update the two agents whose action-selection uses the seam, the chain, the
  built-in, tests, and docs/notes.

**Out of scope (deferred)**
- History rewriting / pruning of the dropped tool's failed turns (heavier hammer;
  must preserve `assistant`-tool_call ↔ `tool` pairing). Note in gaps if priming
  alone keeps dragging the model back.
- Injection from `on_tool_end` (only prepare-tools and the existing before-finish
  seam need it now).

## 3. Design

### 3.1 New return type for the seam

Introduce a small dataclass mirroring `FinishDecision`:

```python
@dataclass
class PrepareToolsResult:
    drop: set[str] = field(default_factory=set)
    inject_messages: list[dict[str, Any]] = field(default_factory=list)
```

- **Hook contract (`AgentContextProcessor.on_prepare_tools`)** stays
  backward-compatible: a hook may return **either** a legacy `set[str]` (drops
  only) **or** a `PrepareToolsResult`. The ABC default keeps returning `set()`.
- **Chain (`run_prepare_tools`)** normalizes each hook's return (set →
  `PrepareToolsResult(drop=…)`), merges all `drop` sets and concatenates all
  `inject_messages` in processor order, subtracts system/terminal tool names from
  `drop` (unchanged safety rule), and returns a single `PrepareToolsResult`.
  Per-processor fail-safe is unchanged: a throwing processor contributes neither
  drops nor messages.

### 3.2 Same-iteration delivery (the ordering fix)

Today each FC agent does `messages = _prepare_context()` **then**
`tool_defs = _prepare_tools()`. The seam runs inside `_prepare_tools`, so anything
it appends to `self.conversation` is too late for the `messages` already
snapshotted — it would only show up next turn, after the confused call already
happened.

**Fix:** in `base_agent._prepare_tools`, append `result.inject_messages` to
`self.conversation`, and **reorder** the two calls in the agents so tools are
prepared first:

```python
tool_defs = await self._prepare_tools()    # seam: drop + append inject msgs to self.conversation
messages = await self._prepare_context()   # now includes the injected directive
```

`_prepare_context` only reads `self.conversation`; `_prepare_tools` has no
dependency on `messages`, so the swap is safe. `_prepare_tools` may still
`raise RuntimeError("Max iterations reached")` — now raised marginally earlier,
same effect.

Reorder sites (the **only** two agents that call base `_prepare_tools`):
- `sgr_agent_core/agents/tool_calling_agent.py` (`_select_action_phase`, ~:43–44)
- `sgr_agent_core/agents/sgr_tool_calling_agent.py` (`_select_action_phase`, ~:95–96)

`iron_agent` and `sgr_agent` override `_prepare_tools` (return a structured stub,
never call the chain) → unaffected. `dialog_agent` inherits an FC
`_select_action_phase` → covered by the reorder above.

### 3.3 Injected message shape & role

Mirror the before-finish path (`mandatory_tool_call.py:91`): inject
`{"role": "user", "content": <directive>}`. The previous appended message is
always a completed `role: tool` result, so a standalone `user` message keeps the
conversation valid (no dangling tool_call). `user` is the most provider-portable
role for an instruction.

### 3.4 `RepeatedToolCallGuard` changes

- Honor the existing `announce` flag: when `True`, inject a note the **first time**
  each tool is dropped.
- Add `_announced: set[str]` so the directive is injected **once per tool**, while
  the full `drop` set keeps being returned every turn (X must stay out of
  `tool_defs` until finish). `newly = drop - self._announced`; build messages for
  `newly`; then `self._announced |= newly`.
- New config `message` (template, default below) with a `{tool}` placeholder.
  Default: `"The tool '{tool}' has been disabled after repeated failed calls. Do
  not attempt to call it again — finalize your answer with the information you
  already have."` Multiple tools dropped in one turn → one message per newly
  dropped tool (or a single joined message; pick one in impl, default per-tool).
- Keep emitting the existing `...dropped` event span every turn; add the injected
  directive to its metadata when fired so the trace shows the announce.

### 3.5 Observability

No new provider surface. The injected directive is visible in the next
generation's `input` (it's now in `messages`), and the guard's existing
`emit_event_span` marker gains an `announced`/`message` field.

## 4. Files to touch

| File | Change |
|------|--------|
| `context_processors/base.py` | Add `PrepareToolsResult`; normalize + merge in `run_prepare_tools`; update return type & docstring; export from `__init__`. |
| `context_processors/__init__.py` | Export `PrepareToolsResult`. |
| `context_processors/repeated_tool_call_guard.py` | `_announced` set; build inject_messages for newly-dropped tools when `announce`; `message` config; return `PrepareToolsResult`. |
| `base_agent.py` (`_prepare_tools`) | Consume `result.drop`; append `result.inject_messages` to `self.conversation`. |
| `agents/tool_calling_agent.py` | Reorder: `_prepare_tools()` before `_prepare_context()`. |
| `agents/sgr_tool_calling_agent.py` | Same reorder. |
| `agents.yaml.example` | Document `announce` + `message` on the guard. |
| `plans/gaps/agent-context-processors.md` | Mark gap #3 resolved (keep node). |
| `notes/` | New note: prepare-tools injection ordering caveat (why tools-before-context). |

## 5. Tests

**Update (return-type churn):**
- `tests/context_processors/test_repeated_tool_call_guard.py` — `on_prepare_tools`
  now returns `PrepareToolsResult`; assert on `result.drop` (was a bare set).
- `tests/context_processors/test_base.py`, `test_span_modes.py` —
  `run_prepare_tools` returns `PrepareToolsResult`; assert `result.drop`.

**Add:**
- Guard injects a directive exactly **once** per dropped tool; `drop` still
  returned on later turns; no message when `announce=False`.
- Chain merges drops + concatenates messages across multiple processors; throwing
  processor contributes neither; system tools never dropped.
- Loop/integration (`test_loop_integration.py` or `test_base_agent.py`): after a
  tool crosses `max_repeats`, the **same** iteration's prepared `messages` contain
  the directive (proves the reorder), and the dropped tool is absent from
  `tool_defs`.
- Conversation stays API-valid after injection (no dangling tool_call).

## 6. Risks & mitigations

- **Provider/cache interaction of a shrinking tool list + injected message**
  (relates to open gap #4) — verify against the gateway before relying in prod;
  injection only adds a `user` message, low risk.
- **Double-injection across reorder + future seams** — gated by `_announced`.
- **Other custom agents that build `messages` before `_prepare_tools`** — only the
  two shipped FC agents do; document the ordering rule in the new note so custom
  agents follow it.

## 7. Acceptance

- An `announce: true` guard run shows, on the drop turn, the directive in the
  action-selection `input` and X absent from the tools — confirmed by a trace
  and the integration test.
- Backward compat: a processor returning a plain `set[str]` still drops tools with
  no behavior change.
- `pytest`, `ruff check .`, `ruff format .`, `mypy` clean.
