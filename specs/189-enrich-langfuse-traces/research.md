# Research: Enrich Langfuse Observability Traces

**Feature**: 189-enrich-langfuse-traces
**Date**: 2026-03-26

## R1: How to Create Generation Spans in Langfuse SDK v2

**Decision**: Use `parent.generation()` from the Langfuse v2 SDK to create generation observations nested under iteration spans.

**Rationale**: The v2 SDK provides `StatefulTraceClient.generation()` and `StatefulSpanClient.generation()` which create dedicated "Generation" observations with fields for model, model_parameters, input (messages), output (response), usage (tokens), and timing. These are first-class citizens in the Langfuse UI, distinct from generic spans.

**API**:
```python
generation = parent_span.generation(
    name="reasoning-phase",
    model="gpt-4o-mini",
    model_parameters={"temperature": 0.3, "max_tokens": 8000},
    input=messages,           # list[dict] — OpenAI messages format
    output=response_content,  # dict or str — LLM response
    usage={"input": N, "output": M},  # token counts
)
generation.end()  # sets end_time
```

**Alternatives considered**:
- Using the Langfuse OpenAI drop-in wrapper (`langfuse.openai.AsyncOpenAI`): Rejected — incompatible with `.stream()` helper used by all SGR agents.
- Creating generic spans instead of generations: Rejected — generations have special UI treatment in Langfuse (cost calculation, model filtering, token aggregation).

---

## R2: Where to Capture LLM Call Data in the Agent Architecture

**Decision**: Add generation span creation in `BaseAgent._execution_step()` around `_reasoning_phase()` and `_select_action_phase()` calls, with a new hook method for subclasses to provide LLM call details.

**Rationale**: The LLM calls happen inside subclass methods (`SGRToolCallingAgent._reasoning_phase()`, etc.) which return domain objects (ReasoningTool, BaseTool), not raw LLM responses. The base class cannot access the raw completion object. Two approaches:

**Approach chosen**: Add a lightweight context object that subclass phase methods can populate with LLM call details (model, tokens, messages). The base class reads this after each phase and creates generation spans. This avoids modifying every subclass's method signatures.

Specifically: use `self._last_llm_call` as a dict that phase methods populate with `{"model": ..., "usage": ..., "messages": ..., "response": ...}`. After each phase call in `_execution_step`, the base class checks this dict and creates a generation span if populated.

**Alternatives considered**:
- Modifying subclass return types to include LLM metadata: Rejected — would break the existing method contracts.
- Instrumenting the OpenAI client itself: Rejected — the Langfuse wrapper doesn't support `.stream()`.
- Requiring subclasses to call provider directly: Rejected — breaks separation of concerns and requires changing all agent implementations.

---

## R3: How to Capture Streaming Token Usage

**Decision**: Token usage is available from the OpenAI streaming response's final chunk when `stream_options={"include_usage": True}` is set (already added in feature 188 via `LLMConfig.to_openai_client_kwargs()`).

**Rationale**: The `get_final_completion()` method called by SGR agents returns the assembled `ChatCompletion` object which includes `usage.prompt_tokens` and `usage.completion_tokens` when stream_options is set. Subclass phase methods already have access to this completion object.

**Key finding**: The `final_completion` / `completion` objects in `SGRToolCallingAgent._reasoning_phase()` (line 55) and `_select_action_phase()` (line 91) contain the usage data. These methods just need to populate `self._last_llm_call` before returning.

---

## R4: Truncation Strategy

**Decision**: Use a simple `str(value)[:max_len]` truncation with a default of 2000 characters. Apply to tool arguments, tool results, task messages, and LLM message content.

**Rationale**: 2000 characters is enough to see the meaningful content of most tool calls and results while preventing multi-megabyte payloads from being sent to Langfuse. The Langfuse SDK handles serialization; we just need to limit string lengths.

**Alternatives considered**:
- Configurable per-field truncation: Over-engineered for initial implementation. Can be added later if needed.
- No truncation: Risk of sending very large payloads (e.g., full web page extraction results) to Langfuse.

---

## R5: Provider Interface Extension for Generation Spans

**Decision**: Add `start_generation()` and `end_generation()` methods to `ObservabilityProvider` ABC, with a new `GenerationHandle` type.

**Rationale**: Generations are conceptually different from spans — they have model-specific fields (model name, parameters, token usage) that don't fit the generic span interface. A dedicated method makes the intent clear and maps directly to the Langfuse SDK's `generation()` API.

**Interface**:
```python
def start_generation(
    self, *, name: str, model: str | None = None,
    model_parameters: dict | None = None,
    input: Any = None, metadata: dict | None = None,
    _parent: TraceHandle | SpanHandle | None = None,
) -> GenerationHandle

def end_generation(
    self, handle: GenerationHandle, *,
    output: Any = None, usage: dict | None = None,
    level: str = "DEFAULT", status: str | None = None,
) -> None
```
