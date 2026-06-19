---
title: ToolCallingAgent context-window growth on long MCP/SQL runs, and applicability of pi-cwl
status: active
created: 2026-06-19
updated: 2026-06-19
owner: Vitaly Chashin
related:
  - research/agent-context-processors.md
  - notes/processor-plugin-pattern.md
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/memory/rolling_summary.py
tags: [agent-loop, context-window, mcp, processors, memory, tool-calling]
---

# Research: ToolCallingAgent context growth + pi-cwl applicability

## 0. Context

A `ToolCallingAgent` deployment wired to MCP tools that query a SQL DB, running on
**Qwen-3.5-35B**, hits two failure modes on long runs:

1. **Step cap (20 iterations) reached.** Acceptable to raise — not the real concern.
2. **LLM context-window overflow** on some step. This is the real problem.

Two questions were asked:

1. Does the agent put all previous turns into context, so it grows on long runs?
2. If so, can the technique from [`Kiz8-Team/pi-cwl`](https://github.com/Kiz8-Team/pi-cwl)
   be applied to `ToolCallingAgent`?

## 1. Q1 — Context accumulation: **confirmed, unbounded**

The prompt is rebuilt every step but includes the entire running transcript.

`base_agent.py:215-230` — `_prepare_context()`:

```python
return [
    {"role": "system", ...},
    *self.task_messages,
    {"role": "user", ...},        # initial request
    *self.conversation,           # ← whole accumulated transcript
]
```

`self.conversation` only **grows** in `ToolCallingAgent`. Each iteration appends:

- assistant message carrying the tool call — `tool_calling_agent.py:69-84`
- `{"role": "tool", "content": result, ...}` — **the full tool result, untruncated** —
  `tool_calling_agent.py:91`

Two aggravating facts for the SQL/MCP setup:

- **Tool results are never truncated before re-entering the prompt.** `_truncate()`
  (`base_agent.py:85-91`) is used only for observability spans/logs, not for context.
  A fat SQL resultset lands verbatim and persists for every subsequent step.
- **Growth is linear in steps × result size.** Raising the step cap (the fix for
  problem 1) directly worsens problem 2. The two failure modes are coupled.

**Gotcha:** a rolling-summary mechanism exists (`RollingSummaryBuffer`,
`base_agent.py:513-556`) but runs **once, before the loop**, and only compacts the
initial `task_messages`. It does nothing about in-loop growth. There is currently
**no in-loop context management** — overflow on long runs is structural, not a
misconfiguration.

## 2. What pi-cwl (Context Window Lifecycle) is

A reference implementation of **CWL**, extending the pi.dev terminal agent. It models
the transcript as a **typed episode graph**:

- `expl` (exploratory: reads/searches) — agent provides a one-line summary on close
- `act` (durable actions: edits/shell) — agent declares dependencies on `expl`
  episodes on open

The agent **annotates its trajectory in real time via a single `delimiter` tool**.
When the token budget is exceeded, a **deterministic, model-free eviction policy**
strips content in graduated levels: reasoning traces → bulk outputs → intermediate
artifacts → whole episodes (oldest `act` first, respecting dependencies; user turns
never evicted). Selling points: no blocking summarization LLM call, and prefix/KV-cache
stability near the budget ceiling.

Claimed results (authors', unverified): 89 sequential tasks / 80M tokens with no
accuracy degradation; 20–70% inference-cost reduction. README cites
`arXiv:2606.11213` — not independently verified.

## 3. Q2 — Applicability to ToolCallingAgent

Split the technique into two halves.

### a) Eviction engine — **highly applicable; the seam already exists**

Large SQL outputs are exactly "bulk outputs" / closed exploratory episodes: once read
and acted on, raw rows are dead weight. Deterministic eviction of stale tool-result
bodies maps cleanly onto an **`AgentContextProcessor`** — the codebase's blessed
read-write extension point. It runs at loop seams (`on_tool_end`, `on_prepare_tools`)
and may mutate (`base_agent.py:388-396`; CLAUDE.md processor table). A processor can
walk `self.conversation`, estimate tokens, and when over budget replace the `content`
of the oldest tool-result messages with a short stub while keeping recent results
intact. This is the 80% win with **zero model cooperation**.

### b) Episode-graph / `delimiter`-tool annotation — **poor fit for ToolCallingAgent**

This half is model-driven: the agent must open/close episodes and declare
dependencies. `ToolCallingAgent` is a pure function-calling loop with
`tool_choice="required"` and **no reasoning phase** — one tool per turn, nowhere to
annotate. A `delimiter` tool becomes an extra call the model must reliably emit,
fighting the single-required-tool pattern and demanding discipline a 35B model like
Qwen-3.5 tends not to keep. If episode typing is ever wanted, `SGRToolCallingAgent`
(explicit reasoning step) is the natural host — but it is likely unnecessary here.

## 4. Recommended shape (pi-cwl's insight, not its full machinery)

- Implement a context-eviction `AgentContextProcessor` driven by a token budget (a
  fraction of Qwen's window). Evict/stub **oldest tool-result bodies first**; keep the
  last N intact. "Structured eviction" minus the graph.
- Correctness constraints:
  - **Message-pairing validity.** OpenAI format requires every assistant `tool_calls`
    message to be followed by matching `role:"tool"` messages with the same
    `tool_call_id`. Do **not** delete tool results — replace their `content` with a
    stub (e.g. `"[evicted: 142-row resultset for query X]"`) so pairing stays valid.
  - **Prefix/KV-cache trade-off.** pi-cwl prizes prefix stability, but any edit to an
    earlier message invalidates the KV-cache prefix from that point. Evict old messages
    in batches (not every turn) to limit cache churn. Relevant if Qwen runs with prefix
    caching.
- Cheap interim mitigation: truncate each tool result to a max length **before**
  appending in `_action_phase`. Crude (loses "keep recent full" intelligence) but a
  one-line change that stops the bleeding.

## 5. Bottom line

Diagnosis confirmed: unbounded transcript growth dominated by untruncated SQL outputs,
no in-loop compaction. pi-cwl's **deterministic, structure-aware eviction** is the
right mental model and ports well as an `AgentContextProcessor`; its **episode-graph
annotation protocol** does not fit a pure function-calling agent and is not needed to
solve the overflow.

## 6. Open questions / next steps

- Token-counting strategy for the budget (tokenizer parity with Qwen vs cheap char
  heuristic).
- How many recent tool results to keep intact (N) and budget threshold as fraction of
  context window.
- Whether to also stub assistant reasoning/`content` blobs, or tool results only.
- Decision: dedicated eviction processor vs extending the existing rolling-summary
  path to run in-loop.
