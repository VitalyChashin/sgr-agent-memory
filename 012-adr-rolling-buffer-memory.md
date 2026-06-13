# Architecture Proposal: Rolling Buffer Memory with History Summarization

> **Feature:** 012-rolling-buffer-memory  
> **Status:** Draft  
> **Created:** 2026-04-07  
> **Depends on:** None (standalone; complements 004-topic-aware-memory but does not require it)  
> **Related:** ADR-004 (response-augmentation pattern), ADR-006 (cache-friendly prompt structure), ADR-008 (tool result truncation)

---

## 1. Problem Statement

SGR Agent Core is stateless across requests: each `POST /v1/chat/completions` call rebuilds the agent from `task_messages`, and the conversation grows on the client by accumulating raw turns. As dialogs lengthen, three problems compound:

1. **Unbounded context growth.** Every turn appends the full prior turn (user message + assistant response, often containing large tool outputs) to the next request. By turn 15–20 the prefix dominates the context window, degrading reasoning ("Lost in the Middle") and inflating cost on every iteration of the reasoning-action loop.

2. **No compact recall surface.** A client that wants to persist, display, or reason about "what happened so far" has only the raw message list. There is no model-generated narrative of the older history that can be logged, stored, or shown in a UI.

3. **Topic-aware memory (Feature 004) is heavyweight for the common case.** Many deployments do not need a Redis-backed microservice, topic detection, or session persistence. They need a single in-process knob: *"keep the last N tokens verbatim, summarize everything older, and inject both into the agent."*

The proposed solution is a **rolling buffer memory** owned by `BaseAgent`: the agent splits the incoming `task_messages` into a *recent window* (newest turns up to a configured token budget, preserved verbatim) and a *history tail* (everything older). The history tail is condensed by a configurable LLM into a short summary. Both the summary and the recent window are **injected into the agent's context** as the conversation prefix the reasoning loop sees, and both are **returned to the client** as structured fields on the REST and MCP responses — using the same augmentation envelope Feature 004 established for `topicId`.

The feature is fully optional (off by default), runs entirely in-process (no external service, no Redis), and adds no changes to the reasoning-action loop itself.

---

## 2. How SGR Currently Handles Conversation State — Analysis

### 2.1 Stateless Per-Request Model

SGR follows the OpenAI convention: the client owns history and replays it on every call. The server-side flow is:

```
1. Parse request → extract model, messages, stream, temperature, etc.
2. Resolve agent definition from model name (or agent_id for continuations)
3. AgentFactory.create(agent_def, task_messages=messages, ...)
4. agent.execute() → reasoning loop → result
5. Stream result to client via SSE (REST) or return via MCP envelope
```

`task_messages` is passed verbatim to `BaseAgent._prepare_context()` and used as the conversation prefix on every iteration of the reasoning-action loop.

### 2.2 No Server-Side Compaction

`BaseAgent` has no awareness of "recent vs. old" turns. The full message list flows through every iteration. ADR-008 (`ToolResultProcessor`) truncates *tool result blocks* to bound observation size, but the user/assistant message pairs themselves are untouched. There is no mechanism that:

- Splits history into a recent window and an older tail.
- Summarizes the older tail.
- Replaces the older tail with its summary in the agent's prompt.
- Emits a structured summary back to the caller.

### 2.3 Existing Response-Augmentation Pattern (ADR-004)

Feature 004 established the pattern this proposal reuses: when memory is enabled, the server attaches structured fields (`topicId`, `topicLabel`, `topicChanged`) to the response. For SSE the fields ride in a metadata event; for non-streaming JSON they sit at the top level of the envelope alongside `choices`. Both REST and MCP endpoints already know how to pass additional metadata alongside the primary payload.

### 2.4 What Does Not Exist Today

| Capability | Current State |
|-----------|---------------|
| In-agent rolling window (recent turns verbatim) | ❌ |
| LLM-based summarization of older turns | ❌ |
| Prompt-side replacement of old turns with their summary | ❌ |
| Summary returned on REST response | ❌ |
| Summary returned on MCP response | ❌ |
| Configurable summarization LLM (separate from agent LLM) | ❌ |
| Summary feature toggle | ❌ |

---

## 3. Proposed Architecture: `RollingBufferMemory` Inside the Agent

### 3.1 Core Concept

Introduce a single new component, `RollingBufferMemory`, owned by `BaseAgent` and activated by config. On each agent invocation (when enabled):

1. **Split.** Walk `task_messages` from newest to oldest. Include messages in the *recent window* while the cumulative token count stays within `max_tokens_to_summarize`. Everything older becomes the *history tail*.
2. **Summarize the tail.** If the history tail is non-empty, call the configured summarization LLM once with a fixed system prompt. The output is a short narrative (`history_summary`) capturing goals, facts, decisions, and unresolved sub-questions.
3. **Rewrite the agent's context.** Replace `task_messages` with:
   ```
   [ system_summary_message(history_summary), *recent_window ]
   ```
   where `system_summary_message` is a synthetic message of role `system` (or `user`, see §4.4) carrying the `history_summary` with a clear framing tag. The reasoning-action loop now sees: a compact summary of old history, followed by the last N tokens of verbatim dialog.
4. **Emit.** Attach both `history_summary` and a description of the recent window to the response envelope as `rolling_memory: { summary, recent_window_tokens, summarized_message_count, ... }` on REST and MCP endpoints alike.

If the history tail is empty (short conversation under the budget), step 2 is skipped, `history_summary` is `null`, and the recent window equals the original `task_messages` — the agent sees exactly what it would have seen with the feature off.

### 3.2 Architecture Overview

```
Client
  │
  │  POST /v1/chat/completions  (REST)
  │  or MCP `ask` call           (MCP, Feature 001)
  │  messages: [turn_1 ... turn_N]
  │
  ▼
┌──────────────────────────────────────────────────────────────┐
│  SGR Agent Core Server                                       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  BaseAgent.execute()                                    │  │
│  │                                                         │  │
│  │  ┌───────────────────────────────────────────────────┐  │  │
│  │  │  RollingBufferMemory.apply(task_messages)         │  │  │
│  │  │                                                   │  │  │
│  │  │  1. Split by token budget:                        │  │  │
│  │  │     history_tail    = [turn_1 ... turn_K]         │  │  │
│  │  │     recent_window   = [turn_K+1 ... turn_N]       │  │  │
│  │  │                                                   │  │  │
│  │  │  2. Summarize history_tail via LLM                │  │  │
│  │  │     → history_summary (4–8 sentences)             │  │  │
│  │  │                                                   │  │  │
│  │  │  3. Rewrite context:                              │  │  │
│  │  │     new_messages = [                              │  │  │
│  │  │       synthetic_summary_message(history_summary), │  │  │
│  │  │       *recent_window                              │  │  │
│  │  │     ]                                             │  │  │
│  │  │                                                   │  │  │
│  │  │  4. Store on AgentContext for response envelope   │  │  │
│  │  └──────────────────┬────────────────────────────────┘  │  │
│  │                     │                                    │  │
│  │                     ▼                                    │  │
│  │  reasoning-action loop  (UNCHANGED)                      │  │
│  │    sees: [summary, *recent_window] as task_messages      │  │
│  │                                                          │  │
│  │  result envelope assembly:                               │  │
│  │    attach rolling_memory: {                              │  │
│  │      summary,                                            │  │
│  │      recent_window_tokens,                               │  │
│  │      summarized_message_count,                           │  │
│  │      ...                                                 │  │
│  │    }                                                     │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────┘
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
         REST SSE envelope       MCP ask response
         (metadata event         (rolling_memory field
          + JSON top-level        in result payload)
          field)
```

No new process, no new dependency, no external storage. The summarizer uses the existing `AsyncOpenAI`-compatible client pool the agent already uses for reasoning, parameterised by the model name in config.

### 3.3 Why In-Agent (vs. Middleware or Microservice)

| Concern | Microservice (ADR-004 pattern) | Server Middleware | **In-Agent Buffer (this proposal)** |
|---------|--------------------------------|-------------------|--------------------------------------|
| External dependencies | Redis + service | None | **None** |
| Op complexity | High (extra deploy unit) | Low | **Lowest** |
| Per-agent configurability | Global only | Global only | **Per-agent definition** ✓ |
| Access to `AgentContext` | No | No | **Yes** ✓ |
| Can rewrite prompt the agent sees | Yes (via `task_messages` substitution) | Yes | **Yes** ✓ |
| Applies uniformly to REST and MCP | Only if wired into both handlers | Only if wired into both handlers | **Yes, automatically** ✓ |
| Suitable for stateless single-turn calls | Overkill | Overkill | **Yes** ✓ |

The last row is decisive. Because the memory is applied *inside* `BaseAgent.execute()`, it runs regardless of which endpoint invoked the agent — REST, MCP `ask`, or any future entrypoint. A middleware placed at the REST handler would have to be duplicated for the MCP handler; the in-agent approach solves both with one implementation.

---

## 4. `RollingBufferMemory` Design

### 4.1 Responsibilities

1. **Split** incoming `task_messages` into `history_tail` and `recent_window` by token budget.
2. **Summarize** `history_tail` via a configurable LLM (fail-open).
3. **Rewrite** the task-message list to `[summary_message, *recent_window]`.
4. **Publish** the structured `RollingMemoryInfo` to `AgentContext` for envelope assembly.

It owns no persistent state. Each agent invocation creates a fresh instance.

### 4.2 Window Split Algorithm

```
Inputs:
  messages:   list[Message]   # incoming task_messages, excluding system prompt
  max_tokens: int             # RollingBufferConfig.max_tokens_to_summarize
  tokenizer:  Tokenizer       # tiktoken cl100k_base (model-agnostic default)

Algorithm:
  recent_window = []
  total_tokens  = 0
  split_index   = len(messages)   # default: everything is recent

  for i in range(len(messages) - 1, -1, -1):
      msg_tokens = tokenizer.count(messages[i]) + ROLE_OVERHEAD
      if total_tokens + msg_tokens > max_tokens and recent_window:
          split_index = i + 1
          break
      recent_window.insert(0, messages[i])
      total_tokens += msg_tokens
  else:
      split_index = 0   # every message fit in the window

  history_tail = messages[:split_index]
  return history_tail, recent_window
```

Notes:

- The system prompt (agent configuration) is excluded from the split — it is infrastructure, not dialog.
- At least one message is always kept in `recent_window` (the latest user message), even if it alone exceeds `max_tokens`. In that degenerate case `history_tail` is empty and the oversized message is passed through unchanged.
- Splits **never break a user/assistant pair in the middle of a tool-call chain.** If the tentative split would land between an assistant tool-call message and its matching tool-result message, the boundary is moved earlier (toward history) so the whole tool-call chain stays together in `recent_window`. This preserves OpenAI function-calling protocol requirements.

### 4.3 Summarization LLM Call

A single chat completion request, no tools, no streaming, low temperature:

```
System prompt (fixed, version-pinned for cache reuse):
  "You produce concise summaries of the earlier portion of an
   in-progress conversation between a user and an AI agent.
   Given the turns below, write a summary that captures:
     - The user's overall goal(s)
     - Key facts and context established so far
     - Decisions, recommendations, or commitments the agent has made
     - Any unresolved sub-questions or open threads
   Constraints:
     - 4–8 sentences
     - Plain prose, no bullets, no headings
     - Past tense, third person ('The user asked...', 'The agent
       explained...')
     - Do not invent details not present in the turns
     - This summary will be given to the agent as context for the
       rest of the conversation, so prioritise information the
       agent will need to continue coherently.
   Respond with ONLY the summary text."

User prompt:
  <serialised history_tail: each message rendered as
   "USER: ...\n\nASSISTANT: ...\n\n">

Model parameters:
  model:       config.summarization_model  (default: agent's main model)
  temperature: 0.2
  max_tokens:  400
  stream:      false
```

The call uses the same `AsyncOpenAI` client pool the agent uses, inheriting credentials, retries, and base URL from existing infrastructure.

### 4.4 Injecting the Summary into the Agent's Context

The summary is inserted at the front of the rewritten `task_messages` as a synthetic message with a clear framing tag so the agent cannot confuse it with real dialog:

```python
summary_message = {
    "role": "system",
    "content": (
        "<conversation_history_summary>\n"
        "The following is a summary of earlier turns in this "
        "conversation that have been condensed to save context. "
        "Treat it as established background, not as recent dialog.\n\n"
        f"{history_summary}\n"
        "</conversation_history_summary>"
    ),
}
new_task_messages = [summary_message, *recent_window]
```

Role choice: `system` is preferred because most frontier models treat additional system-role messages as instructions/context rather than dialog turns, which is the correct semantic. For providers that reject multiple system messages, the implementation falls back to role `user` with the same framing tag and an opening `[SYSTEM NOTE]` prefix. The fallback is selected per-provider in the `ModelAdapter` layer, not hard-coded here.

Crucially, the summary replaces `history_tail` only in the prompt the agent sees. The *original* `task_messages` are preserved on `AgentContext.original_task_messages` for observability, debugging, and any downstream consumer that needs the unmodified history.

### 4.5 Failure Mode: Fail-Open

Summarisation must never break the agent. If the summarizer call fails (timeout, rate limit, malformed response, network error):

1. Log a `WARNING` with the failure reason.
2. **Do not rewrite `task_messages`.** The agent proceeds with the original unmodified history. This is the critical failure contract: a failed summarization degrades to "feature off for this turn", not to a broken or truncated prompt.
3. Attach `rolling_memory: { status: "failed", error: "<reason>", summary: null }` to the response envelope so the client can surface the degradation.

This mirrors ADR-004's fail-open posture and platform principle P5 (existing behaviour is preserved on any failure path).

### 4.6 Interaction with Prompt Caching (ADR-006)

This feature **does** affect the prompt prefix the agent sends to the LLM, and therefore interacts with ADR-006's cache-friendly structuring. The relevant observations:

- The system prompt (ADR-006's cache anchor) is **untouched** and remains the first block in the final prompt sequence. Prompt cache hits on the system prompt are preserved.
- The `history_summary` block changes every turn, so the cache boundary effectively sits between the system prompt and the summary. This is **strictly better** than the current state, where the boundary sits between the system prompt and the full message history (which also changes every turn, but is much larger).
- Net effect on ADR-006: neutral-to-positive. No change to the system-prompt cache prefix; smaller post-prefix tail means fewer tokens re-processed per request.

A fuller analysis and any follow-up to ADR-006 is called out in §13 as an open decision.

### 4.7 Performance

| Quantity | Typical value |
|----------|---------------|
| Split algorithm overhead (no LLM) | < 5 ms |
| Summarization latency (gpt-4.1-nano, 2k token tail) | 200–500 ms |
| Summarization latency (gpt-4o-mini, 2k token tail) | 400–800 ms |
| Summarization latency (agent's main model) | 600–1500 ms |
| Added cost per turn (gpt-4.1-nano, 2k tail → 300 summary) | ~$0.0002 |
| Tokens saved per subsequent reasoning iteration | `size(history_tail) − size(summary)` (typically 1500–10000 tokens) |

Unlike the earlier draft of this feature, the summarization call **cannot** run in parallel with the first reasoning iteration: the reasoning iteration needs the summary to be already substituted into its prompt. The summary is therefore on the critical path for turn-1 latency. However, on multi-iteration agents (2+ reasoning steps), the per-iteration token savings typically outweigh the one-time summarization cost after the second iteration. The break-even analysis is documented in §13.

---

## 5. Configuration

### 5.1 Pydantic Models

```python
class RollingBufferConfig(BaseModel):
    enabled: bool = False
    max_tokens_to_summarize: int = Field(default=2000, ge=100, le=32000)
    summarization_model: str | None = None        # None → agent's main model
    summarization_timeout_s: float = Field(default=10.0, gt=0.0, le=120.0)
    summarization_max_output_tokens: int = Field(default=400, ge=100, le=2000)

class MemoryConfig(BaseModel):
    rolling_buffer: RollingBufferConfig = RollingBufferConfig()
    # (future) topic_aware: TopicAwareMemoryConfig = ...
```

### 5.2 `config.yaml` Example

```yaml
memory:
  rolling_buffer:
    enabled: false                    # Master switch — default OFF
    max_tokens_to_summarize: 2000     # Tokens kept verbatim as recent window
    summarization_model: null         # null → use agent's main model
    summarization_timeout_s: 10.0
    summarization_max_output_tokens: 400
```

Semantics clarification: `max_tokens_to_summarize` is the **recent window budget** — the number of tokens kept verbatim. Everything *older* than that budget is what gets summarized. The name is kept for alignment with the original spec phrasing; an alias `recent_window_tokens` is accepted at config load time.

### 5.3 Per-Agent Override

Because the buffer lives inside `BaseAgent`, individual agent definitions may override the global config in their YAML:

```yaml
# agent definition
name: research-agent
model: gpt-4.1
memory:
  rolling_buffer:
    enabled: true
    max_tokens_to_summarize: 4000
    summarization_model: gpt-4.1-nano    # cheaper than the agent's own model
```

`MemoryConfig` is added as a field on both `GlobalConfig` and the per-agent definition; the agent definition wins when both are present.

---

## 6. Integration with `BaseAgent`

### 6.1 New `BaseAgent` Hook

A single new call added at the start of `execute()`:

```python
class BaseAgent:
    async def execute(self) -> AgentResult:
        await self._maybe_apply_rolling_buffer()   # NEW
        result = await self._reasoning_action_loop()  # UNCHANGED
        self._attach_rolling_memory_info(result)   # NEW
        return result

    async def _maybe_apply_rolling_buffer(self) -> None:
        cfg = self._resolve_memory_config().rolling_buffer
        if not cfg.enabled:
            return
        buffer = RollingBufferMemory(
            config=cfg,
            llm_client_pool=self._llm_client_pool,
            default_model=self.model,
            tokenizer=self._tokenizer,
        )
        try:
            result = await buffer.apply(self.context.task_messages)
        except Exception as exc:
            logger.warning("rolling buffer failed, proceeding with raw history: %s", exc)
            self.context.rolling_memory_info = RollingMemoryInfo.failed(str(exc))
            return

        self.context.original_task_messages = self.context.task_messages
        self.context.task_messages = result.rewritten_messages
        self.context.rolling_memory_info = result.info

    def _attach_rolling_memory_info(self, result: AgentResult) -> None:
        result.rolling_memory = self.context.rolling_memory_info
```

The reasoning-action loop is **not modified**. Both new methods are no-ops when `enabled=false`.

### 6.2 `RollingBufferMemory.apply()` Return Shape

```python
class RollingMemoryInfo(BaseModel):
    status: Literal["applied", "bypassed", "failed"]
    summary: str | None
    summarized_message_count: int
    recent_window_message_count: int
    recent_window_tokens: int
    summarization_model: str | None
    summarization_latency_ms: float | None
    error: str | None = None

class RollingBufferResult(BaseModel):
    rewritten_messages: list[Message]
    info: RollingMemoryInfo
```

`status="bypassed"` is returned when the entire history fits in the recent window (no summarization needed); in that case `rewritten_messages == original task_messages` and `summary=None`.

---

## 7. Response Envelope Augmentation

The `rolling_memory` field is attached to the response using the same pattern ADR-004 uses for `topicId`. Both REST and MCP endpoints are updated to forward `AgentResult.rolling_memory`.

### 7.1 REST — Non-Streaming JSON

```json
{
  "id": "chatcmpl-...",
  "model": "research-agent",
  "choices": [...],
  "rolling_memory": {
    "status": "applied",
    "summary": "The user asked the agent to compare three vector databases for a RAG pipeline. The agent established that latency under 50 ms, hybrid search, and self-hostable deployment were hard requirements. It evaluated Qdrant, Weaviate, and Milvus on these axes and recommended Qdrant. The user then asked about Qdrant's clustering story, which the agent answered in detail.",
    "summarized_message_count": 12,
    "recent_window_message_count": 4,
    "recent_window_tokens": 1847,
    "summarization_model": "gpt-4.1-nano",
    "summarization_latency_ms": 342.1
  }
}
```

### 7.2 REST — Streaming SSE

An additional metadata event is emitted before the terminal `[DONE]`:

```
event: metadata
data: {"rolling_memory": {"status": "applied", "summary": "...", ...}}

data: [DONE]
```

This matches the placement used by ADR-004 for its `topicId` metadata event.

### 7.3 MCP `ask` Endpoint (Feature 001)

The MCP response envelope gains a top-level `rolling_memory` field mirroring the REST JSON shape. Because the MCP endpoint internally calls the same `BaseAgent.execute()`, no separate integration is needed beyond reading `AgentResult.rolling_memory` in the MCP response serializer.

```json
{
  "agent_id": "...",
  "response": "...",
  "rolling_memory": { "status": "applied", "summary": "...", ... }
}
```

### 7.4 Client Backward Compatibility

Any client that ignores unknown fields (the default for `pydantic.BaseModel(extra="ignore")` and OpenAI SDK DTOs) is unaffected. The `choices` / primary response payload is unchanged.

---

## 8. AgentContext and AgentResult Changes

```python
class AgentContext:
    # ... existing fields ...
    task_messages: list[Message]
    original_task_messages: list[Message] | None = None   # NEW
    rolling_memory_info: RollingMemoryInfo | None = None  # NEW

class AgentResult:
    # ... existing fields ...
    rolling_memory: RollingMemoryInfo | None = None       # NEW
```

All new fields default to `None`; any consumer that does not opt in is unaffected.

---

## 9. Phasing

### Phase 1 — Basic Realization (this ADR)

- `RollingBufferMemory` component
- Token-budgeted split of `task_messages`
- Single summarization LLM call with fail-open
- Prompt rewrite: `[summary_message, *recent_window]`
- Per-agent and global config
- REST (streaming + non-streaming) and MCP response envelope augmentation
- Tool-call chain preservation in the split

### Phase 2 — Cumulative / Hierarchical Summary

- Persist the previous summary across `agent_id`-scoped continuations (in-memory keyed by `agent_id`)
- Each new summary takes `(previous_summary, newly_evicted_turns)` as input instead of re-summarizing from scratch
- Bounds summarization cost for long conversations and produces more coherent narratives

### Phase 3 — Convergence with Feature 004

- When both `memory.rolling_buffer.enabled` and `memory.topic_aware.enabled` are true, the rolling buffer scopes its split to messages of the *current topic* only, so cross-topic context is not summarized together
- The summary becomes the topic's authoritative description and is stored in the topic record

### Phase 4 — Semantic Recall via MCP Tool

- Expose prior summaries via an MCP `memory_search` tool so the agent can explicitly fetch archived summaries from earlier topics / sessions on demand
- Complements (does not replace) the automatic rolling buffer

---

## 10. Alignment with Platform Principles

| Principle | Compliance |
|-----------|-----------|
| **P1: Deterministic-First** | ⚠️ Partial. The reasoning-action loop is unchanged and deterministic. However, the prompt it sees is now the product of an LLM judgement (summarization). This is an explicit, scoped concession: the summarization is bounded (one call, fixed prompt, low temperature, fail-open to raw history) and gated behind an opt-in flag. When `enabled=false`, full determinism is preserved. |
| **P2: Schema-Guided Reasoning** | ✅ Neutral. The summarizer uses a fixed system prompt; the output is a single string field with no structural ambiguity. `RollingMemoryInfo` is a Pydantic model. |
| **P3: Composable Tools / Adapters** | ✅ Reuses the existing `AsyncOpenAI` client pool and `ModelAdapter` layer. No new transport, no new credential surface. |
| **P4: Gateway as Single Control Point** | ✅ Neutral. Operates inside the agent, not at the gateway. |
| **P5: Evolution, Not Revolution** | ✅ Off by default. Zero changes to the reasoning loop. New `BaseAgent` hooks are no-ops when disabled. New config field is additive. `enabled=false` produces behaviour identical to current SGR. |
| **P6: Self-Improving Loop** | ✅ Emitted `RollingMemoryInfo` is structured data the platform can later score, store, or attach to Feature 003 Langfuse spans. |

The P1 partial compliance is the most important entry and is explicitly acknowledged: injecting an LLM-generated summary into the agent's prompt is a deliberate tradeoff between determinism and context-window economy. §13 captures the open question of whether future work should formalise this as a "distillation layer" with its own principle-level treatment.

---

## 11. Relationship to Other Features and ADRs

| Feature / ADR | Relationship |
|---------------|--------------|
| **001 — MCP Ask Endpoint** | Directly integrated. The MCP response envelope is extended with `rolling_memory` identically to the REST envelope. Because both endpoints share `BaseAgent`, the integration is a single serializer change. |
| **004 — Topic-Aware Memory** | Complementary and composable. ADR-004 handles *which* messages enter the agent (topic filtering); this ADR handles *how* those messages are compacted (rolling buffer + summary). Phase 3 above describes the convergence point. |
| **003 — Langfuse Observability** | The summarization LLM call is wrapped in a `provider.start_span("rolling-buffer-summarize")` span when observability is enabled. `RollingMemoryInfo` is attached as span attributes (`summarized_message_count`, `recent_window_tokens`, `summarization_latency_ms`), making token-savings analysis queryable in Langfuse. |
| **006 — Cache-Friendly Prompt Structure** | Neutral-to-positive interaction (see §4.6). The system-prompt cache prefix is untouched; the post-prefix tail becomes smaller and more stable. A short follow-up note to ADR-006 is captured as an open decision. |
| **008 — Tool Result Truncation** | Composes naturally. By the time the buffer reads `task_messages`, any historical tool-result blocks have already been compacted by `ToolResultProcessor`, so the buffer's split budget is spent on substantive content. |
| **010 — Reasoning-Augmented Function Calling** | Independent. The buffer runs before the reasoning loop; ADR-010 governs what happens *inside* it. The synthetic summary message introduced in §4.4 uses role `system` or `user` — never role `tool` or `function_call` — so it never interacts with ADR-010's function-call protocol. |
| **011 — Hopfield Memory Accelerator** | Future synergy. The rolling summary is a candidate "key" for Hopfield retrieval in Phase 4; out of scope here. |

---

## 12. Component Breakdown

### 12.1 New Files

| File | Purpose |
|------|---------|
| `sgr_agent_core/memory/__init__.py` | Package init; exports `RollingBufferMemory`, `RollingBufferConfig`, `MemoryConfig`, `RollingMemoryInfo` |
| `sgr_agent_core/memory/config.py` | `RollingBufferConfig` and `MemoryConfig` Pydantic models |
| `sgr_agent_core/memory/rolling_buffer.py` | `RollingBufferMemory` class (split, summarize, rewrite, fail-open) |
| `sgr_agent_core/memory/models.py` | `RollingMemoryInfo`, `RollingBufferResult` Pydantic models |
| `sgr_agent_core/memory/prompts.py` | Version-pinned summarizer system prompt constant |
| `sgr_agent_core/memory/tokenizer.py` | Tokenizer abstraction with `tiktoken` (cl100k_base) default implementation |
| `tests/memory/test_rolling_buffer.py` | Unit + integration tests |

### 12.2 Modified Files

| File | Change |
|------|--------|
| `sgr_agent_core/agents/base.py` | Add `_maybe_apply_rolling_buffer()` and `_attach_rolling_memory_info()` hooks; wire into `execute()` |
| `sgr_agent_core/agents/context.py` | Add `original_task_messages` and `rolling_memory_info` fields |
| `sgr_agent_core/agents/result.py` | Add `rolling_memory` field |
| `sgr_agent_core/config.py` | Add `memory: MemoryConfig` field on `GlobalConfig`; support per-agent override loading |
| `sgr_agent_core/server/rest.py` | Forward `rolling_memory` into SSE metadata event and non-streaming JSON envelope |
| `sgr_agent_core/server/mcp.py` (or equivalent MCP handler) | Forward `rolling_memory` into MCP `ask` response envelope |
| `config.yaml.example` | Add commented `memory.rolling_buffer:` section |

---

## 13. Implementation Tasks

| # | Task | Scope | Dependencies | Parallel |
|---|------|-------|-------------|----------|
| 1 | Create `RollingBufferConfig`, `MemoryConfig`, `RollingMemoryInfo`, `RollingBufferResult` Pydantic models | Config | — | [P] |
| 2 | Add `memory: MemoryConfig` field to `GlobalConfig` | Config | Task 1 | — |
| 3 | Add per-agent `memory` override loading in agent definition parser | Config | Task 1 | [P] |
| 4 | Define version-pinned summarizer system prompt in `memory/prompts.py` | Buffer | — | [P] |
| 5 | Implement `Tokenizer` abstraction with `tiktoken` default | Buffer | — | [P] |
| 6 | Implement window split algorithm with tool-call-chain preservation | Buffer | Task 5 | — |
| 7 | Implement `RollingBufferMemory.apply()` (split + summarize + rewrite + fail-open) | Buffer | Tasks 4, 6 | — |
| 8 | Add `original_task_messages` and `rolling_memory_info` fields to `AgentContext` | Agent | — | [P] |
| 9 | Add `rolling_memory` field to `AgentResult` | Agent | — | [P] |
| 10 | Add `_maybe_apply_rolling_buffer()` + `_attach_rolling_memory_info()` hooks to `BaseAgent` | Agent | Tasks 7, 8, 9 | — |
| 11 | Forward `rolling_memory` into REST non-streaming JSON envelope | Server | Task 9 | [P] |
| 12 | Forward `rolling_memory` into REST SSE metadata event | Server | Task 9 | [P] |
| 13 | Forward `rolling_memory` into MCP `ask` response envelope | Server | Task 9 | [P] |
| 14 | Update `config.yaml.example` with `memory.rolling_buffer:` section | Config | Task 1 | [P] |
| 15 | Unit tests: `MemoryConfig` defaults, validation bounds, per-agent override resolution | Tests | Tasks 1, 3 | [P] |
| 16 | Unit tests: split algorithm (under budget, over budget, single oversized msg, empty list, tool-call chain straddling the boundary) | Tests | Task 6 | [P] |
| 17 | Unit tests: `RollingBufferMemory.apply()` happy path with mocked LLM | Tests | Task 7 | [P] |
| 18 | Unit tests: fail-open behavior (timeout, network error, malformed response) — verify original `task_messages` are used and `status="failed"` is emitted | Tests | Task 7 | [P] |
| 19 | Integration test: REST endpoint with `enabled=true` — verify summary in response and rewritten prompt sent to LLM | Tests | Tasks 10, 11, 12 | — |
| 20 | Integration test: MCP `ask` endpoint with `enabled=true` — verify `rolling_memory` in MCP envelope | Tests | Tasks 10, 13 | — |
| 21 | Integration test: per-agent override of `summarization_model` | Tests | Tasks 3, 19 | — |
| 22 | Integration test: `enabled=false` produces byte-identical response to baseline (both REST and MCP) | Tests | Task 10 | [P] |
| 23 | Integration test: history shorter than budget → `status="bypassed"`, no LLM call made | Tests | Task 7 | [P] |

### Task Dependency Graph

```
[1] Pydantic models ──┬──[2] GlobalConfig field
                      ├──[3] Per-agent override
                      ├──[14] config.yaml.example
                      └──[15] Config tests

[4] Summarizer prompt ──┐
[5] Tokenizer ──────────┴──[6] Split algorithm ──[7] apply() ──[16] Split tests
                                                  │           └──[17] Happy-path test
                                                  │           └──[18] Fail-open test
                                                  │           └──[23] Bypass test
                                                  │
[8] Context fields ─────┐                         │
[9] Result field ───────┴──[10] BaseAgent hooks ◄─┘
                            │
                            ├──[11] REST JSON ──[19] REST integration test
                            ├──[12] REST SSE   ─┘
                            ├──[13] MCP envelope ──[20] MCP integration test
                            ├──[21] Per-agent override test
                            └──[22] Baseline parity test
```

---

## 14. Open Design Decisions

1. **Tokenizer choice.** Use `tiktoken` `cl100k_base` as a model-agnostic default for all split computations. Per-model-accurate tokenizers are deferred. Ship the abstraction (`memory/tokenizer.py`) so the swap is a drop-in when needed.

2. **Synthetic summary message role.** Default to `system`. Fall back to `user` with a `[SYSTEM NOTE]` prefix only for providers that reject multiple system messages. The fallback selection is owned by `ModelAdapter`, not `RollingBufferMemory`.

3. **Break-even vs. one-turn agents.** On a single-iteration agent (no tool use), the summarization LLM call is pure overhead — the saved tokens never pay for it. Should the feature auto-disable for single-turn agents, or leave it as the operator's responsibility? Recommendation: leave it to the operator; document the tradeoff in `config.yaml.example`.

4. **Cumulative summary across `agent_id` continuations.** Out of scope for Phase 1 (the buffer is stateless per `execute()` call, so each continuation re-summarizes from scratch). Phase 2 addresses it. Confirm this is acceptable for the initial rollout.

5. **ADR-006 follow-up note.** A short addendum to ADR-006 should document that the post-system-prompt cache boundary is unchanged in shape but now smaller in size. Should this be a patch to ADR-006 or a standalone ADR-013? Recommendation: inline patch to ADR-006 with a cross-reference to this ADR.

6. **Summary message placement relative to system prompt.** The synthetic summary message is placed *after* the agent's system prompt (so the agent reads: agent instructions → history summary → recent turns). Confirm this ordering, as an alternative is to prepend it to the system prompt itself (which would change ADR-006's cache prefix and is therefore rejected by default).

---

## 15. Summary

The rolling buffer memory feature adds a single new component, `RollingBufferMemory`, owned by `BaseAgent` and activated by config. On each agent invocation (when enabled), it splits the incoming `task_messages` by token budget into a *recent window* (newest turns up to `max_tokens_to_summarize`, preserved verbatim) and a *history tail* (everything older). The history tail is condensed by a configurable summarization LLM — defaulting to the agent's own model — into a 4–8 sentence narrative. Both the summary (as a synthetic system message) and the recent window are **injected into the agent's context**, replacing the original `task_messages` the reasoning-action loop sees. Both are **returned to the client** in a `rolling_memory` field on REST (streaming and non-streaming) and MCP `ask` responses, using the same envelope-augmentation pattern Feature 004 established for `topicId`.

The feature is **off by default**, runs **entirely in-process** with no external service, requires **zero changes** to the reasoning-action loop, supports **per-agent overrides** of every parameter including the summarization model, **preserves tool-call chains** in the split boundary, and **fails open** on every error path (a failed summarization degrades to "feature off for this turn", never to a broken prompt). When `enabled=false`, the system behaves byte-identically to current SGR.

The basic realization deliberately defers cumulative cross-turn summarization (Phase 2) and convergence with Feature 004's topic scoping (Phase 3); both are designed so that the Phase 1 interfaces need not change to accommodate them. The feature composes cleanly with Features 001 (MCP endpoint integration is a single serializer change), 003 (Langfuse trace attributes), 004 (topic scoping in Phase 3), ADR-006 (neutral-to-positive cache interaction), ADR-008 (tool result truncation runs first), and ADR-010 (independent — the buffer operates outside the reasoning loop).
