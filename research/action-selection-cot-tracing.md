---
title: Making the ToolCallingAgent action-selection CoT traceable in Langfuse
status: archived
created: 2026-06-22
updated: 2026-06-22  # acted on: plans/action-selection-cot-tracing.md implemented (A+B+C, ToolCallingAgent)
owner: Vitaly Chashin
related:
  - research/toolcalling-context-growth.md
  - research/langfuse-error-trace-filtering.md
  - plans/gaps/agent-context-processors.md
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/observability/langfuse_provider.py
tags: [observability, langfuse, tracing, tool-calling, reasoning, cot]
---

# Research: tracing the action-selection chain-of-thought

## 1. Problem statement

`RepeatedToolCallGuard` + the prepare-tools message injection (gap #3) reduced the
max-step blowups, but **only partially**: ~some action-selection calls still burn a
minute+ and a large number of tokens inside a **single** LLM call. We earlier
identified that the model does a lot of *internal* chain-of-thought on that step
(reconciling the forced `tool_choice="required"` against an enormous context, or
synthesizing a final answer under constrained decoding).

Today the Langfuse generation for that step **does not reveal why** it was expensive.
Goal: make the internal CoT/reasoning of the action-selection step observable so the
spikes are explainable and filterable in Langfuse.

## 2. What action-selection traces capture today

The generation span for action-selection is built in two places.

`ToolCallingAgent._select_action_phase` (`agents/tool_calling_agent.py:41`) populates
`self._last_llm_call`:

```python
usage = completion.usage
response_msg = completion.choices[0].message
self._last_llm_call = {
    "name": "action-selection",
    "model": self.config.llm.model,
    "model_parameters": {"temperature": ..., "max_tokens": ...},
    "usage": {"input": usage.prompt_tokens, "output": usage.completion_tokens} if usage else None,
    "input": self._build_gen_input(messages, tool_defs),
    "output": self._truncate(response_msg.content or str(response_msg.tool_calls)),
}
```

`BaseAgent._execution_step` (`base_agent.py:374-396`) then emits the generation:
`start_generation(input=...)` → `end_generation(output=..., usage=...)`.

So the trace records:

| Field | Source | Quality |
|-------|--------|---------|
| `input` | `_build_gen_input` → `{messages, tools, tool_choice}` | Good — full prompt + Available-tools |
| `output` | `content or str(tool_calls)`, **truncated to 2000 chars** | Lossy (see §3) |
| `usage` | `{input: prompt_tokens, output: completion_tokens}` | Flat totals only — no breakdown |

The streamed chunks (`streaming_generator.add_chunk`) are restreamed to the **client**,
never to Langfuse — they are not a tracing channel.

## 3. Why the CoT is invisible — three blind spots

A spike means the model spent many completion tokens before the forced tool call.
Those tokens are reasoning tokens, visible pre-call content, or both — and all three
are under-captured:

1. **Reasoning-token usage is not broken out.** A reasoning-capable model spends tokens
   in an internal reasoning channel. OpenAI reports them in
   `usage.completion_tokens_details.reasoning_tokens` (and cached prompt tokens in
   `usage.prompt_tokens_details.cached_tokens`). We record only the flat
   `usage.completion_tokens`. A 50k-reasoning-token spike therefore looks like an
   ordinary call with a big output number and **no explanation**. (`LLMConfig` is
   `extra="allow"`, so `reasoning_effort`/`reasoning` flow through
   `to_openai_client_kwargs` — reasoning models are explicitly in scope.)

2. **Reasoning *content* is dropped entirely.** Providers that expose the reasoning text
   surface it as `message.reasoning_content` / `message.reasoning` on the final message
   and `delta.reasoning_content` / `delta.reasoning` on stream chunks. We never read it.
   With the OpenAI SDK these are non-standard fields, reachable via `model_extra` /
   `getattr`, not by attribute on the typed model. This is the single most valuable
   missing signal: it is literally the CoT.

3. **Output is collapsed and over-truncated.** `content or str(tool_calls)` shows **only
   one** of the two, and the 2000-char `_truncate` cap clips a long pre-call CoT. In the
   exact spike case — long content blob *plus* a final tool call — we keep the clipped
   content and **lose which tool was chosen**. The 2k cap is precisely what hides the CoT.

Net: the trace cannot answer "why did this call cost a minute and N tokens."

## 4. Options

Ordered by value/effort. A+C are the minimum useful set; B is the high-value addition.

### A. Capture the usage breakdown (low effort, high signal)
In `_select_action_phase`, read `usage.completion_tokens_details` and
`usage.prompt_tokens_details` defensively (`getattr`, both may be absent on
non-OpenAI providers) and add them to `_last_llm_call["usage"]` and/or generation
metadata. Surfacing `reasoning_tokens` and `cached_tokens` makes a spike
self-explanatory at a glance and **filterable** in the Langfuse UI.

Caveat: the provider currently forwards `usage={"input","output"}` straight to the
Langfuse SDK (`end_generation`). Langfuse v2 accepts richer usage
(`input`/`output`/`total` + details) — confirm the key mapping or route the breakdown
through `metadata` to avoid SDK shape mismatches. `token_efficiency` metric reads
`usage["input"]`/`usage["output"]` (`metrics/token_efficiency.py:24`), so keep those
keys and **add** new ones rather than renaming.

### B. Capture the reasoning content (medium effort, highest signal)
Accumulate `delta.reasoning_content` / `delta.reasoning` during the existing chunk loop
in `_select_action_phase` (natural seam — it already iterates `event.chunk`), and read
the final `message.reasoning_content` / `message.reasoning` via `model_extra`. Store it
on the generation output as a structured dict instead of the current collapse:

```python
"output": {
    "reasoning": self._truncate(reasoning_text, max_len=...),  # the CoT
    "content": self._truncate(response_msg.content, ...),       # visible pre-call text
    "tool_call": {"name": tool.tool_name, "arguments": ...},    # what was actually chosen
}
```

Extraction must be defensive — never assume the field exists (provider variance, §6).

### C. Stop collapsing/over-truncating the output (low effort)
Independent of B: record content **and** the chosen tool separately, and raise (or make
configurable) the truncation for the diagnostic field. Even without reasoning content,
this removes the "which tool won?" blind spot and lets long content through.

### D. Flag spikes for filtering (optional, ties to MetricsProcessor)
A `MetricsProcessor` (`observability/metrics/`) observing `on_generation_end` could emit
a trace-level score or attribute when action-selection latency or
output/input token ratio crosses a threshold — making spikes findable without scanning
every trace. Reuses the existing fail-silent metrics seam; no agent-loop changes.

## 5. Where the changes land

- `agents/tool_calling_agent.py:_select_action_phase` — richer `_last_llm_call` (A/B/C);
  accumulate reasoning deltas in the chunk loop.
- `agents/sgr_tool_calling_agent.py`, `agents/iron_agent.py`, `agents/sgr_agent.py` —
  same pattern in their selection/reasoning calls if we want parity (each builds its own
  `_last_llm_call`).
- `base_agent.py:_execution_step` (action-selection block ~374-396) — pass usage details
  / structured output through to `start_generation`/`end_generation`; add to `metadata`.
- `observability/langfuse_provider.py:end_generation` — verify/extend the `usage` mapping
  for detailed token counts (or accept the breakdown via `metadata`).
- A small helper (e.g. `_extract_reasoning(message_or_delta)`) is worth centralizing,
  since four agents would otherwise duplicate the defensive extraction.

## 6. Risks / caveats

- **Trace bloat.** Reasoning content can be very large. Truncate the **traced** copy
  (keep the raw text only for the client stream), consistent with the existing
  `_MAX_TRACED_TOOL_DEFS` philosophy. Make the cap configurable.
- **Provider variance.** OpenAI returns reasoning **token counts** but not raw reasoning
  text; DeepSeek-style providers return `reasoning_content`; others differ or omit both.
  Extraction must be `getattr`/`model_extra`-based and never raise.
- **Missing `*_tokens_details`.** Non-OpenAI providers may not return the details objects
  — guard every access.
- **Keep observability fail-silent.** All of this is diagnostic; a malformed usage object
  or missing field must never break the action-selection path.

## 7. Recommendation

Do **A + C** first (cheap, removes the worst blind spots: no usage breakdown, collapsed
output). Add **B** as the real fix — the reasoning content is the CoT we actually want to
see. Defer **D** until A–C confirm whether spikes are reasoning-token-driven (expect yes)
vs. content-driven; the breakdown from A tells us which, and that decides whether D keys
on token ratio or latency.

Next: a plan doc (`plans/action-selection-cot-tracing.md`) once scope (which agents,
A+C vs A+B+C) is confirmed.
