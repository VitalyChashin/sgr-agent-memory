---
title: Implementation plan — trace the ToolCallingAgent action-selection CoT in Langfuse
status: archived
created: 2026-06-22
updated: 2026-06-22  # implemented: A+B+C in ToolCallingAgent; helpers on BaseAgent; tests green
owner: Vitaly Chashin
supersedes: []
related:
  - research/action-selection-cot-tracing.md
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/observability/langfuse_provider.py
  - sgr_agent_core/observability/metrics/token_efficiency.py
tags: [observability, langfuse, tracing, tool-calling, reasoning, cot, plan]
---

# Plan: trace the action-selection chain-of-thought

## 1. Problem & motivation

After the gap-#3 fix some action-selection calls still spike (minute+, large token
burn) inside a **single** LLM call, and the Langfuse generation for that step cannot
explain why. Three blind spots (full analysis in
`research/action-selection-cot-tracing.md`):

- **A.** Usage is recorded as flat `{input, output}` — reasoning tokens
  (`usage.completion_tokens_details.reasoning_tokens`) and cached prompt tokens are not
  broken out, so a reasoning-token spike is indistinguishable from a normal call.
- **B.** The model's reasoning *content* (`message.reasoning_content` / `reasoning`,
  `delta.reasoning_content` / `delta.reasoning`) — literally the CoT — is never captured.
- **C.** Output is `content or str(tool_calls)` truncated to 2000 chars: collapses
  content vs. tool call (loses which tool won) and clips long CoT.

## 2. Goal & scope

**In scope** — `ToolCallingAgent` only. Implement **A + B + C**:
1. Capture the usage breakdown (reasoning / cached / detail tokens).
2. Capture reasoning content (stream deltas + final message), defensively.
3. Restructure the generation output so reasoning, content, and the chosen tool are
   all visible and separately truncated.

**Out of scope**
- `SGRToolCallingAgent`, `IronAgent`, `SGRAgent` — they build their own `_last_llm_call`
  and are explicitly deferred (parity can follow later using the shared helper).
- Option **D** (spike-flagging MetricsProcessor) — deferred until A confirms spikes are
  reasoning-token-driven.
- Changing what is streamed to the **client** — only the *traced* copy changes.

## 3. Design

### 3.1 Shared extraction helper (BaseAgent)

Add two small static/helper methods on `BaseAgent` so the logic is centralized and
unit-testable, and so the other three agents can adopt it later without duplication:

```python
@staticmethod
def _extract_reasoning(obj: Any) -> str | None:
    """Pull reasoning text from a message or a stream delta, defensively.

    Providers differ: OpenAI omits raw reasoning text; DeepSeek-style providers
    expose `reasoning_content`; some use `reasoning`. Non-standard fields arrive
    via the pydantic model's `model_extra`, not as typed attributes. Never raises.
    """
    if obj is None:
        return None
    for attr in ("reasoning_content", "reasoning"):
        val = getattr(obj, attr, None)
        if val is None and hasattr(obj, "model_extra"):
            val = (obj.model_extra or {}).get(attr)
        if val:
            return str(val)
    return None

@staticmethod
def _extract_usage(usage: Any) -> dict[str, Any] | None:
    """Flat input/output plus a best-effort token breakdown. Never raises.

    Keeps `input`/`output` (TokenEfficiencyProcessor depends on them) and adds
    `reasoning`, `cached`, and the raw detail dicts when the provider returns them.
    """
    if usage is None:
        return None
    out: dict[str, Any] = {
        "input": getattr(usage, "prompt_tokens", None),
        "output": getattr(usage, "completion_tokens", None),
        "total": getattr(usage, "total_tokens", None),
    }
    ctd = getattr(usage, "completion_tokens_details", None)
    if ctd is not None:
        out["reasoning"] = getattr(ctd, "reasoning_tokens", None)
    ptd = getattr(usage, "prompt_tokens_details", None)
    if ptd is not None:
        out["cached"] = getattr(ptd, "cached_tokens", None)
    return {k: v for k, v in out.items() if v is not None}
```

Rationale: `getattr`/`model_extra` everywhere — provider variance and missing detail
objects must never break the action-selection path (observability is fail-silent).

### 3.2 `ToolCallingAgent._select_action_phase`

**(B) Accumulate reasoning during the existing chunk loop.** The loop already iterates
`event.chunk`; add reasoning-delta accumulation alongside the existing
`add_chunk` call (client streaming unchanged):

```python
reasoning_parts: list[str] = []
async for event in stream:
    if event.type == "chunk":
        self.streaming_generator.add_chunk(event.chunk, phase_id)
        delta = event.chunk.choices[0].delta if event.chunk.choices else None
        rc = self._extract_reasoning(delta)
        if rc:
            reasoning_parts.append(rc)
completion = await stream.get_final_completion()
```

Fall back to the final message if streaming yielded nothing (some providers only put
reasoning on the assembled message):

```python
reasoning_text = "".join(reasoning_parts) or self._extract_reasoning(response_msg)
```

**(A + C) Build a richer `_last_llm_call`:**

```python
self._last_llm_call = {
    "name": "action-selection",
    "model": self.config.llm.model,
    "model_parameters": {"temperature": ..., "max_tokens": ...},
    "usage": self._extract_usage(usage),
    "input": self._build_gen_input(messages, tool_defs),
    "output": {
        "reasoning": self._truncate(reasoning_text, self._reasoning_trace_max_len),
        "content": self._truncate(response_msg.content, ...),
        "tool_call": {
            "name": tool.tool_name,
            "arguments": self._truncate(tool.model_dump_json(), ...),
        },
    },
}
```

Notes:
- `tool` is already parsed below in the method; compute `_last_llm_call` after parsing
  so `tool.tool_name` is available, or capture `response_msg.tool_calls` raw if we prefer
  to keep the assignment before the `isinstance` guard. **Decision:** move the
  `_last_llm_call` assignment to *after* the tool is validated, so the output always
  names the actually-selected tool (closes the "which tool won?" blind spot).
- Drop only-empty keys to keep the trace tidy (e.g. omit `reasoning` when `None`).

### 3.3 Truncation cap

Add a configurable cap so reasoning content can't bloat traces:

```python
# BaseAgent
_REASONING_TRACE_MAX_LEN: int = 8000   # diagnostic field; larger than the 2000 default
```

Expose via `self._reasoning_trace_max_len = getattr(...)` resolved from
`GlobalConfig().observability` if we want config control (mirror `capture_tool_definitions`).
**Decision:** start with the class constant (no new config key); promote to
`observability.reasoning_trace_max_len` only if needed. Keep the raw reasoning out of the
client stream — only the truncated copy reaches Langfuse.

### 3.4 Generation emission (`base_agent.py:_execution_step`)

The action-selection block (~`base_agent.py:374-396`) already forwards
`input=llm_info["input"]`, `output=llm_info["output"]`, `usage=llm_info["usage"]`.
Output is now a dict (Langfuse renders dict output fine). Two touch-ups:

- Pass the token breakdown into the generation. Safest path: keep
  `usage={"input","output"}` going to the SDK's typed `usage` and put the full breakdown
  (`reasoning`, `cached`, `total`) into the generation **metadata** to avoid SDK
  usage-shape mismatches. Verify against the installed Langfuse v2 SDK whether
  `usage_details`/extended keys are accepted; if yes, prefer native usage, else metadata.
- Add `"reasoning_tokens"` to the `metadata` dict already passed at
  `base_agent.py:383` so it is filterable in the Langfuse UI.

`LangfuseProvider.end_generation` (`langfuse_provider.py:234`) already forwards `usage`
verbatim — confirm it tolerates the extra keys (it passes them straight to
`generation.end(usage=...)`); if the SDK rejects unknown keys, filter to the SDK-known
subset there and rely on metadata for the rest.

### 3.5 Metrics compatibility

`TokenEfficiencyProcessor` reads `usage["input"]`/`usage["output"]`
(`token_efficiency.py:24`). `_extract_usage` **keeps** those keys, so the metric is
unaffected. (Optional follow-up, not in this plan: have it also sum `usage["reasoning"]`.)

## 4. Files to change

| File | Change |
|------|--------|
| `sgr_agent_core/base_agent.py` | Add `_extract_reasoning`, `_extract_usage`, `_REASONING_TRACE_MAX_LEN`; add `reasoning_tokens` to action-selection generation metadata |
| `sgr_agent_core/agents/tool_calling_agent.py` | Accumulate reasoning deltas; build structured `_last_llm_call` (A+B+C) after tool validation |
| `sgr_agent_core/observability/langfuse_provider.py` | Verify/adjust `end_generation` usage mapping for the breakdown (metadata fallback) |

No changes to the other three agents, the streaming generator, or client-facing output.

## 5. Test plan

`tests/` (unit, no live LLM):
- `_extract_reasoning`: returns `reasoning_content`, returns `reasoning`, reads from
  `model_extra`, returns `None` for plain object / `None` input — never raises.
- `_extract_usage`: flat keys preserved; `reasoning`/`cached` populated when details
  present; missing details objects → keys absent, no raise; `None` usage → `None`.
- `_select_action_phase` (mock stream): given chunks carrying `reasoning_content` deltas
  and a final tool call, `_last_llm_call["output"]` contains `reasoning`, `content`, and
  `tool_call.name`; reasoning is truncated at the cap.
- Regression: `TokenEfficiencyProcessor.on_generation_end` still accumulates from the new
  usage dict (existing test or add one).
- Provider-variance: a stream with **no** reasoning fields → `output.reasoning` omitted,
  behaviour otherwise unchanged (back-compat with non-reasoning models).

Manual: run `scripts/langfuse_tool_trace_demo.py` against local Langfuse
(`http://localhost:3000`) with a reasoning-capable model + `reasoning_effort` set;
confirm the action-selection generation shows reasoning content, the tool call, and the
reasoning-token count.

## 6. Risks & mitigations

- **Trace bloat** → reasoning truncated to `_REASONING_TRACE_MAX_LEN`; raw text never
  leaves the client stream.
- **Provider variance / missing fields** → all extraction via `getattr`/`model_extra`,
  fail-silent; non-reasoning models degrade to today's behaviour (content + tool_call).
- **Langfuse SDK usage shape** → breakdown routed through metadata if native usage keys
  are rejected; `input`/`output` preserved for the SDK and for `TokenEfficiencyProcessor`.
- **Output type change (str → dict)** → Langfuse renders dict output; confirm no internal
  consumer of `_last_llm_call["output"]` assumes a string (grep: only the generation
  emission reads it).

## 7. Done criteria

- A reasoning-model action-selection trace in local Langfuse shows: reasoning content,
  the selected tool name+args, and a reasoning-token count in metadata.
- Non-reasoning models trace unchanged except output is now `{content, tool_call}`.
- `ruff check`/`format` clean, `mypy` clean on changed files, new + existing tests green.
- `research/action-selection-cot-tracing.md` → `status: archived`; this plan → archived;
  file a `notes/` entry on the provider-variance reasoning-extraction gotcha.
