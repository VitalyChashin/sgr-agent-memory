---
title: Tracing action-selection reasoning — provider-variance extraction
status: current
created: 2026-06-22
updated: 2026-06-22
related:
  - plans/action-selection-cot-tracing.md
  - research/action-selection-cot-tracing.md
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/observability/langfuse_provider.py
---

# Note: action-selection reasoning/CoT now traced (ToolCallingAgent)

`ToolCallingAgent._select_action_phase` records the model's internal CoT in the
Langfuse action-selection generation, so token-heavy spikes are explainable. Three
settled decisions worth remembering:

## 1. Reasoning extraction is provider-variant and must stay defensive
`BaseAgent._extract_reasoning(obj)` reads reasoning text from **either** a stream
`delta` **or** the final message, trying `reasoning_content` then `reasoning`, as a
direct attribute **and** via pydantic `model_extra`. Why all four paths:

- OpenAI returns reasoning **token counts** but no raw reasoning text — extraction
  yields `None` and we fall back to flat behaviour. Expected, not a bug.
- DeepSeek-style providers expose `reasoning_content`; others use `reasoning`.
- These are non-standard fields, so on the OpenAI SDK they arrive in `model_extra`,
  not as typed attributes. `getattr` alone misses them.

The helper never raises and skips empty strings (an empty CoT must not be recorded as
a falsy value). Streaming deltas are accumulated; if streaming yields nothing we read
the assembled message (`"".join(parts) or self._extract_reasoning(response_msg)`).

## 2. Output is a dict, not the old `content or tool_calls` string
The generation output is now `{"content", "tool_call": {name, arguments}}` plus
`"reasoning"` when present — built **after** the tool is validated so the chosen tool
is always named. The old collapse hid the CoT (clipped at 2000 chars) and dropped the
tool when content was also present. Reasoning is truncated separately at
`BaseAgent._REASONING_TRACE_MAX_LEN` (8000) — larger than the default 2000 cap but
bounded so a long CoT can't bloat traces. The **raw** reasoning never leaves the client
stream; only the truncated copy reaches the observability provider.

## 3. Usage breakdown rides in metadata, not the SDK usage object
`_extract_usage` keeps the `input`/`output` keys (`TokenEfficiencyProcessor` depends on
them) and adds `total`/`reasoning`/`cached` when the provider returns the detail
objects. **The Langfuse SDK's typed usage only understands `input`/`output`/`total`**,
so `LangfuseProvider.end_generation` filters to that subset before calling
`generation.end(usage=...)`. The richer breakdown is surfaced via the generation
**metadata** (`reasoning_tokens`, added in `BaseAgent._execution_step`) so spikes are
filterable in the UI without risking an SDK usage-shape rejection.

## Scope
Only `ToolCallingAgent` carries this today. The helpers live on `BaseAgent`, so
`SGRToolCallingAgent` / `IronAgent` / `SGRAgent` can adopt the same pattern later
without duplication.
