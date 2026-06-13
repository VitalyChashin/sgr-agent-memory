# Architecture Proposal: In-Agent Rolling Summary Memory

> **Feature:** 012-rolling-summary-memory  
> **Status:** Draft  
> **Created:** 2026-04-07  
> **Depends on:** None (standalone; complements 004-topic-aware-memory but does not require it)  
> **Related:** ADR-004 (response-augmentation pattern for `topicId`), ADR-008 (tool result truncation), ADR-006 (cache-friendly prompt structure)

---

## 1. Problem Statement

SGR Agent Core is stateless across requests: each `POST /v1/chat/completions` call rebuilds the agent from `task_messages` and the conversation grows by accumulating raw turns on the client. As dialogs lengthen, three problems compound:

1. **Linear context growth.** Every new turn adds the full prior turn (user message + assistant response, often containing extensive tool output) to the next request. By turn 15–20 the prefix dominates the context window, degrades reasoning ("Lost in the Middle"), and inflates cost on every iteration of the reasoning-action loop — not just on the final answer.

2. **No compact recall surface.** A client that wants to reason about, log, or display "what happened so far" has only the raw message list. There is no condensed, model-generated narrative of the conversation that can be persisted, indexed, or shown in a UI without re-reading every turn.

3. **Topic-aware memory (Feature 004) is heavyweight for the common case.** Many deployments do not need a separate Redis-backed microservice, topic detection, or session persistence. They need a single, in-process knob: *"keep a rolling summary of recent context and give it back to me with the response."*

The proposed solution is an **in-agent rolling summary buffer**: the agent maintains a token-bounded view of recent conversation, generates a short summary of it once per turn using a (configurable) LLM, and returns the summary to the client as a structured field on the response — analogous to how Feature 004 returns `topicId`. The feature is fully optional (off by default), runs entirely inside the agent process (no external service, no Redis), and adds zero changes to the reasoning-action loop.

---

## 2. How SGR Currently Handles Conversation State — Analysis

### 2.1 Stateless Per-Request Model

SGR follows the OpenAI convention: the client owns history and replays it on every call. The server-side flow is:

```
1. Parse request → extract model, messages, stream, temperature, etc.
2. Resolve agent definition from model name (or agent_id for continuations)
3. AgentFactory.create(agent_def, task_messages=messages, ...)
4. agent.execute() → reasoning loop → result
5. Stream result to client via SSE
```

`task_messages` is passed verbatim to `BaseAgent._prepare_context()` and used as the conversation prefix on every iteration of the reasoning-action loop.

### 2.2 No Server-Side Compaction

`BaseAgent` has no awareness of "recent vs. old" turns. The full message list flows through every iteration. ADR-008 (`ToolResultProcessor`) truncates *tool result blocks* to bound observation size, but the user/assistant message pairs themselves are untouched. There is no mechanism that:

- Summarizes the conversation so far.
- Distinguishes "recent context window" from "older context".
- Emits a structured summary back to the caller.

### 2.3 Existing Response-Augmentation Pattern (ADR-004)

Feature 004 establishes the pattern this proposal reuses: when memory is enabled, the server attaches a structured field (`topicId`, `topicLabel`, `topicChanged`) to the streaming response. The client receives the augmented response without breaking OpenAI compatibility — extra fields are returned alongside the standard `choices[]` payload via SSE metadata events.

### 2.4 What Does Not Exist Today

| Capability | Current State |
|-----------|---------------|
| In-agent rolling buffer of recent turns | ❌ |
| Token-bounded view of conversation history | ❌ |
| Per-turn summary generation | ❌ |
| Summary returned on response | ❌ |
| Configurable summarization LLM (separate from main agent LLM) | ❌ |
| Summary feature toggle | ❌ |

---

## 3. Proposed Architecture: `RollingSummaryBuffer` Inside the Agent

### 3.1 Core Concept

Introduce a single new component, `RollingSummaryBuffer`, owned by `BaseAgent` and activated by config. On each agent invocation:

1. **Pre-execution:** the buffer ingests the incoming `task_messages`, walks them from newest to oldest, and selects the suffix whose cumulative token count is `≤ max_tokens_to_summarize`. This is the *rolling window*.
2. **Summarization:** the buffer issues a single LLM call (using the configured summarization model — by default the agent's own model) that condenses the window into a short narrative summary.
3. **Post-execution:** the summary is attached to the response as a structured field (`conversationSummary`), in the same envelope position used by ADR-004's `topicId`.

The summary is **not** injected back into the agent's context — this proposal is about *emitting* a recall surface, not rewriting the prompt. (A future phase may add prompt-side injection; see §8.) This keeps the feature orthogonal to the reasoning-action loop and preserves OpenAI prompt-cache hits established by ADR-006.

### 3.2 Architecture Overview

```
Client
  │
  │  POST /v1/chat/completions
  │  { messages: [...] }
  │
  ▼
┌──────────────────────────────────────────────────────────────┐
│  SGR Agent Core Server (FastAPI)                             │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  AgentFactory.create(task_messages=messages, ...)       │  │
│  │                                                        │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │  BaseAgent                                       │  │  │
│  │  │                                                  │  │  │
│  │  │  ┌────────────────────────────────────────────┐  │  │  │
│  │  │  │  RollingSummaryBuffer  (if enabled)        │  │  │  │
│  │  │  │                                            │  │  │  │
│  │  │  │  pre:  select suffix ≤ max_tokens          │  │  │  │
│  │  │  │        → call summarizer LLM               │  │  │  │
│  │  │  │        → store summary on AgentContext     │  │  │  │
│  │  │  └────────────────────────────────────────────┘  │  │  │
│  │  │                                                  │  │  │
│  │  │  reasoning-action loop  (UNCHANGED)              │  │  │
│  │  │                                                  │  │  │
│  │  │  ┌────────────────────────────────────────────┐  │  │  │
│  │  │  │  result envelope assembly                  │  │  │  │
│  │  │  │  → attach context.conversationSummary      │  │  │  │
│  │  │  └────────────────────────────────────────────┘  │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────┘
                            │  SSE stream
                            ▼
                    Client receives:
                      - normal chat.completion chunks
                      - metadata event: { conversationSummary: "..." }
```

No new process. No new dependency. No external storage. The summarizer LLM call uses the existing `AsyncOpenAI`-compatible client pool (the same one the agent already uses for reasoning), parameterised by the model name in config.

### 3.3 Why In-Agent (vs. Middleware or Microservice)

| Concern | Microservice (ADR-004 pattern) | Server Middleware | **In-Agent Buffer (this proposal)** |
|---------|------|-------------------|------------------|
| External dependencies | Redis + service | None | **None** |
| Op complexity | High (extra deploy unit) | Low | **Lowest** |
| Per-agent configurability | Global only | Global only | **Per-agent definition** ✓ |
| Access to `AgentContext` | No | No | **Yes** (direct) ✓ |
| Reusable across agents | Yes | Yes | **Yes** (via `BaseAgent`) ✓ |
| Suitable for stateless single-turn calls | Overkill | Overkill | **Yes** ✓ |

The defining characteristic of this feature is that the summary depends on what *this specific agent* saw, including any task-message preprocessing, and must travel back on *this specific response*. The natural owner is `BaseAgent` itself. Per platform principle P5 (Evolution, Not Revolution), this is the smallest possible extension: a new optional component, no changes to the reasoning loop, default off.

### 3.4 Why Not Inject the Summary Back Into the Prompt

A natural extension is to *replace* the rolling-window suffix with the summary, shrinking the context the LLM sees. This is intentionally **out of scope for the basic realization** because:

1. **Prompt cache invalidation.** ADR-006 carefully structures the prompt prefix to maximise OpenAI/Anthropic cache hits. Rewriting the message list with a summary every turn would invalidate the cache on every request, neutralising the savings ADR-006 was designed to deliver.
2. **Determinism.** Summarisation is LLM judgement; replacing the prompt with it introduces a non-deterministic transformation in front of every reasoning step. Per platform principle P1 (Deterministic-First), determinism-impacting changes need explicit justification and a separate ADR.
3. **Separation of concerns.** This proposal answers *"give the client a compact recall surface"*, not *"reduce the agent's input tokens"*. The latter is the territory of ADR-006 (cache-friendly structuring) and a future context-distillation ADR.

A Phase 2 follow-up may add an opt-in `inject_into_context: true` mode; the buffer is designed so that flag becomes a one-line addition.

---

## 4. `RollingSummaryBuffer` Design

### 4.1 Responsibilities

The buffer has exactly three responsibilities:

1. **Window selection** — given the incoming `task_messages`, return the newest contiguous suffix whose token count does not exceed `max_tokens_to_summarize`.
2. **Summary generation** — call the configured summarizer LLM with the window and a fixed system prompt, return a short narrative string.
3. **Result publication** — store the summary on `AgentContext` so the response-assembly stage can attach it to the outgoing envelope.

It owns no persistent state. Each agent invocation creates a fresh buffer instance.

### 4.2 Window Selection Algorithm

```
Inputs:
  messages:           list[Message]   # incoming task_messages
  max_tokens:         int             # MemoryConfig.max_tokens_to_summarize
  tokenizer:          Tokenizer       # tiktoken or model-appropriate

Algorithm:
  window      = []
  total_tokens = 0
  for msg in reversed(messages):
      msg_tokens = tokenizer.count(msg.content) + ROLE_OVERHEAD
      if total_tokens + msg_tokens > max_tokens and window:
          break
      window.insert(0, msg)
      total_tokens += msg_tokens
  return window
```

Notes:

- Always include at least one message, even if it alone exceeds `max_tokens` (in which case the message itself is truncated to fit, marked with a `[truncated]` suffix). This guarantees the summarizer always has something to work with.
- The system message is excluded from the window — it is configuration, not conversation.
- Tool result blocks inside assistant messages are included but participate in the same token budget; ADR-008's `ToolResultProcessor` will already have shrunk them by the time the buffer sees them.

### 4.3 Summarization LLM Call

A single chat completion request, no tools, no streaming, low temperature:

```
System prompt (fixed, version-pinned for cache):
  "You produce concise rolling summaries of in-progress conversations
   between a user and an AI agent. Given the recent turns below,
   write a summary that captures:
     - The user's current goal or question
     - Key facts established so far
     - Decisions or commitments the agent has made
     - Any unresolved sub-questions
   Constraints:
     - 4–8 sentences
     - Plain prose, no bullets, no headings
     - Past tense, third person
     - Do not invent details not present in the turns
   Respond with ONLY the summary text."

User prompt:
  <serialised window: each message rendered as
   "USER: ...\n\nASSISTANT: ...\n\n">

Model parameters:
  model:       config.summarization_model  (default: agent's main model)
  temperature: 0.2
  max_tokens:  300
  stream:      false
```

The call is made via the same `AsyncOpenAI` client pool the agent uses, so credentials, retries, and base URLs are inherited from existing infrastructure. No new client wiring.

### 4.4 Failure Mode: Fail-Open

Summarisation must never break the agent. If the summarizer call fails (timeout, rate limit, malformed response, network error):

1. Log a `WARNING` with the failure reason.
2. Set `context.conversation_summary = None`.
3. Proceed with the normal agent execution.

The response envelope will simply omit the `conversationSummary` field. This mirrors ADR-004's fail-open posture and platform principle P5 (existing behavior is preserved on any failure path).

### 4.5 Performance

| Quantity | Typical value |
|----------|---------------|
| Pre-execution overhead (window selection, no LLM) | < 5 ms |
| Summarization LLM latency (gpt-4.1-nano, 2k token window) | 200–500 ms |
| Summarization LLM latency (gpt-4o-mini, 2k token window) | 400–800 ms |
| Summarization LLM latency (agent's main model) | 600–1500 ms |
| Added cost per turn (gpt-4.1-nano, 2k window) | ~$0.0002 |

The summarization call can run **in parallel** with the first reasoning-action iteration (`asyncio.gather`), so its latency is masked entirely on any turn that requires more than one LLM call. This optimisation is included in the basic realization (see §6.2).

---

## 5. Configuration

### 5.1 `MemoryConfig` (new)

```yaml
# config.yaml
memory:
  rolling_summary:
    enabled: false                    # Master switch — default OFF
    max_tokens_to_summarize: 2000     # Token budget for the rolling window
    summarization_model: null         # null → use the agent's main model
    summarization_timeout_s: 10.0     # Hard timeout on the summarizer call
    parallel_with_first_iteration: true  # Run summary call in parallel
                                          # with the first reasoning step
```

### 5.2 Pydantic Model

```python
class RollingSummaryConfig(BaseModel):
    enabled: bool = False
    max_tokens_to_summarize: int = Field(default=2000, ge=100, le=32000)
    summarization_model: str | None = None
    summarization_timeout_s: float = Field(default=10.0, gt=0.0, le=120.0)
    parallel_with_first_iteration: bool = True

class MemoryConfig(BaseModel):
    rolling_summary: RollingSummaryConfig = RollingSummaryConfig()
    # (future) topic_aware: TopicAwareMemoryConfig = ...
```

`MemoryConfig` is added as a field on `GlobalConfig`. The nesting under `memory.rolling_summary` is intentional so that Feature 004 (`memory.topic_aware`) and any future memory subsystem land in the same namespace without breaking changes.

### 5.3 Per-Agent Override

Because the buffer lives inside `BaseAgent`, individual agent definitions may override the global config in their YAML:

```yaml
# agent definition
name: research-agent
model: gpt-4.1
memory:
  rolling_summary:
    enabled: true
    max_tokens_to_summarize: 4000
    summarization_model: gpt-4.1-nano    # cheaper model than the agent
```

This is impossible with the middleware pattern of ADR-004 and is one of the principal reasons the buffer is owned by the agent.

---

## 6. Integration with `BaseAgent`

### 6.1 New `BaseAgent` Hooks

Two minimal additions to `BaseAgent`:

```python
class BaseAgent:
    async def execute(self) -> AgentResult:
        await self._maybe_run_rolling_summary()   # NEW
        result = await self._reasoning_action_loop()  # UNCHANGED
        self._attach_context_summary(result)      # NEW
        return result

    async def _maybe_run_rolling_summary(self) -> None:
        cfg = self.config.memory.rolling_summary
        if not cfg.enabled:
            return
        buffer = RollingSummaryBuffer(cfg, self._llm_client_pool, self.model)
        if cfg.parallel_with_first_iteration:
            # Schedule, don't await — see §6.2
            self._summary_task = asyncio.create_task(
                buffer.summarize(self.context.task_messages)
            )
        else:
            self.context.conversation_summary = await buffer.summarize(
                self.context.task_messages
            )

    def _attach_context_summary(self, result: AgentResult) -> None:
        if getattr(self, "_summary_task", None) is not None:
            try:
                result.conversation_summary = self._summary_task.result()
            except Exception as exc:
                logger.warning("rolling summary failed: %s", exc)
                result.conversation_summary = None
        else:
            result.conversation_summary = self.context.conversation_summary
```

The reasoning-action loop is **not modified**. Both new methods are no-ops when `enabled=false`.

### 6.2 Parallel Execution Pattern

When `parallel_with_first_iteration=true`, the summarization call is launched as an `asyncio.Task` *before* the reasoning loop begins. The first reasoning iteration proceeds immediately. After the loop terminates (whether on iteration 1 or iteration N), the result-assembly step awaits the summary task — by which point it has almost always completed. Net latency added on the critical path:

- Single-iteration agent: `max(0, summary_latency − iteration_1_latency)` — typically 0–200 ms.
- Multi-iteration agent: 0 ms (summary completes during iteration 1 or 2).

### 6.3 Response Envelope Augmentation

The response is augmented exactly as ADR-004 augments it with `topicId`. The SSE stream emits an additional metadata event before the terminal `[DONE]`:

```
event: metadata
data: {"conversationSummary": "The user asked the agent to compare three vector databases for a RAG pipeline. The agent established that latency under 50 ms, hybrid search, and self-hostable deployment were hard requirements. It evaluated Qdrant, Weaviate, and Milvus on these axes and recommended Qdrant. The user has not yet confirmed the recommendation."}

data: [DONE]
```

For non-streaming responses, the field is added at the top level of the JSON envelope, alongside `choices`:

```json
{
  "id": "chatcmpl-...",
  "model": "research-agent",
  "choices": [...],
  "conversationSummary": "The user asked the agent to compare..."
}
```

This is identical in shape to ADR-004's `topicId` augmentation, so client libraries that already handle one will trivially handle the other.

---

## 7. AgentContext Changes

A single optional field is added:

```python
class AgentContext:
    # ... existing fields ...
    conversation_summary: str | None = None
```

`AgentResult` mirrors the same field. Both default to `None`, so any consumer that does not opt in is unaffected.

---

## 8. Phasing

### Phase 1 — Basic Realization (this ADR)

- `RollingSummaryBuffer` component
- Window selection by token budget
- Single summarization LLM call
- Per-agent and global config
- Parallel execution with first iteration
- Response envelope augmentation
- Fail-open on every failure mode

### Phase 2 — Optional Prompt Injection

- `inject_into_context: bool` flag
- When true, replace the windowed suffix with `<prior conversation summary>: ...` pseudo-message
- Requires explicit follow-up ADR documenting prompt-cache impact (likely interacts with ADR-006)

### Phase 3 — Cumulative / Hierarchical Summary

- Persist the previous summary across turns (in `agent_id`-keyed in-memory state)
- Each new summary takes (previous_summary + new_window) as input — classic "memory of memory" pattern
- Bounds total summary growth, enables long-running conversations beyond the rolling window

### Phase 4 — Convergence with Feature 004

- When both `memory.rolling_summary.enabled` and `memory.topic_aware.enabled` are true, the buffer scopes its window to the *current topic* instead of the full message list
- The summary is stored on the topic record in Redis, becoming the topic's official summary
- Feature 004's response includes `conversationSummary` alongside `topicId`

---

## 9. Alignment with Platform Principles

| Principle | Compliance |
|-----------|-----------|
| **P1: Deterministic-First** | ✅ The reasoning-action loop is unchanged and remains deterministic. The summary is a side-channel output, not a prompt-rewriting transform. The one LLM judgement introduced (summarisation) is isolated from the agent's decision path. |
| **P2: Schema-Guided Reasoning** | ✅ Neutral. The summarizer call uses a fixed system prompt; the output is a single string field with no structural ambiguity. |
| **P3: Composable Tools / Adapters** | ✅ The buffer reuses the existing `AsyncOpenAI` client pool. No new transport, no new credential surface. |
| **P4: Gateway as Single Control Point** | ✅ Neutral. Operates inside the agent, not at the gateway. |
| **P5: Evolution, Not Revolution** | ✅ Off by default. Zero changes to the reasoning loop. New `BaseAgent` hooks are no-ops when disabled. New config field is additive. Identical behaviour to current SGR when `enabled=false`. |
| **P6: Self-Improving Loop** | ✅ The emitted summary is structured data the platform can later score, store, or feed into observability (Feature 003 — `conversationSummary` as a Langfuse trace attribute). |

---

## 10. Relationship to Other Features and ADRs

| Feature / ADR | Relationship |
|---------------|--------------|
| **004 — Topic-Aware Memory** | Complementary. Uses the same response-augmentation envelope. Phase 4 above describes convergence: when both are on, the buffer scopes to the current topic and the summary becomes the topic's authoritative description. |
| **003 — Langfuse Observability** | The summarizer LLM call is wrapped in a `provider.start_span("rolling-summary")` when observability is enabled. The resulting summary is attached as a span attribute, making it queryable in Langfuse. |
| **006 — Cache-Friendly Prompt Structure** | Compatible. Because the summary is *not* injected into the agent's prompt, ADR-006's cache prefix is unaffected. This is the explicit reason §3.4 keeps prompt injection out of scope. |
| **008 — Tool Result Truncation** | Composes naturally. By the time the buffer reads `task_messages`, any historical tool results have already been compacted by `ToolResultProcessor`, so the buffer's token budget is spent on substantive content. |
| **010 — Reasoning-Augmented Function Calling** | Independent. The buffer runs before/around the reasoning loop; ADR-010 governs what happens *inside* it. |
| **011 — Hopfield Memory Accelerator** | Future synergy. The rolling summary is a strong candidate "key" for Hopfield retrieval — Phase 3's cumulative summary could serve as the recall query into a Hebbian-indexed past-conversation store. Out of scope for this ADR. |

---

## 11. Component Breakdown

### 11.1 New Files

| File | Purpose |
|------|---------|
| `sgr_agent_core/memory/__init__.py` | Package init; exports `RollingSummaryBuffer`, `RollingSummaryConfig`, `MemoryConfig` |
| `sgr_agent_core/memory/config.py` | `RollingSummaryConfig` and `MemoryConfig` Pydantic models |
| `sgr_agent_core/memory/rolling_summary.py` | `RollingSummaryBuffer` class (window selection, summarization, fail-open) |
| `sgr_agent_core/memory/prompts.py` | Version-pinned summarizer system prompt constant |
| `tests/memory/test_rolling_summary.py` | Unit + integration tests |

### 11.2 Modified Files

| File | Change |
|------|--------|
| `sgr_agent_core/agents/base.py` | Add `_maybe_run_rolling_summary()` and `_attach_context_summary()` hooks; wire into `execute()` |
| `sgr_agent_core/agents/context.py` | Add `conversation_summary: str \| None = None` field |
| `sgr_agent_core/agents/result.py` | Add `conversation_summary: str \| None = None` field |
| `sgr_agent_core/server/server.py` | Forward `conversation_summary` from `AgentResult` into the SSE metadata event and the non-streaming JSON envelope |
| `sgr_agent_core/config.py` | Add `memory: MemoryConfig` field on `GlobalConfig` |
| `config.yaml.example` | Add commented `memory.rolling_summary:` section |

---

## 12. Implementation Tasks

| # | Task | Scope | Dependencies | Parallel |
|---|------|-------|-------------|----------|
| 1 | Create `RollingSummaryConfig` and `MemoryConfig` Pydantic models | Config | — | [P] |
| 2 | Add `memory: MemoryConfig` field to `GlobalConfig` | Config | Task 1 | — |
| 3 | Add per-agent `memory` override loading in agent definition parser | Config | Task 1 | [P] |
| 4 | Define version-pinned summarizer system prompt in `memory/prompts.py` | Buffer | — | [P] |
| 5 | Implement window selection algorithm (token-bounded suffix) | Buffer | — | [P] |
| 6 | Implement `RollingSummaryBuffer.summarize()` (LLM call, fail-open) | Buffer | Tasks 4, 5 | — |
| 7 | Add `conversation_summary` field to `AgentContext` and `AgentResult` | Agent | — | [P] |
| 8 | Add `_maybe_run_rolling_summary()` and `_attach_context_summary()` hooks to `BaseAgent` | Agent | Tasks 6, 7 | — |
| 9 | Implement parallel execution path (`asyncio.create_task` + result await) | Agent | Task 8 | — |
| 10 | Forward `conversation_summary` into SSE metadata event in server | Server | Task 7 | [P] |
| 11 | Forward `conversation_summary` into non-streaming JSON envelope | Server | Task 7 | [P] |
| 12 | Update `config.yaml.example` with `memory.rolling_summary:` section | Config | Task 1 | [P] |
| 13 | Unit tests: `MemoryConfig` defaults, validation bounds, per-agent override | Tests | Tasks 1, 3 | [P] |
| 14 | Unit tests: window selection (under budget, over budget, single oversized message, empty list) | Tests | Task 5 | [P] |
| 15 | Unit tests: `RollingSummaryBuffer.summarize()` happy path with mocked LLM | Tests | Task 6 | [P] |
| 16 | Unit tests: fail-open behavior (timeout, malformed response, network error) | Tests | Task 6 | [P] |
| 17 | Integration test: agent with `enabled=true`, verify summary on response | Tests | Tasks 9, 10 | — |
| 18 | Integration test: parallel execution — verify summary latency masked by reasoning loop | Tests | Task 9 | — |
| 19 | Integration test: per-agent override of `summarization_model` | Tests | Tasks 3, 17 | — |
| 20 | Integration test: `enabled=false` produces byte-identical response to baseline | Tests | Task 9 | [P] |

### Task Dependency Graph

```
[1] Pydantic models ──┬──[2] GlobalConfig field
                      ├──[3] Per-agent override
                      ├──[12] config.yaml.example
                      └──[13] Config tests

[4] Summarizer prompt ──┐
[5] Window algorithm ───┴──[6] Buffer.summarize ──[15] Happy-path test
                                                  └──[16] Fail-open test
                                                  └──[14] Window tests

[7] Context/Result fields ──[8] BaseAgent hooks ──[9] Parallel execution
                            │                     └──[17] Integration test
                            ├──[10] SSE metadata        └──[18] Latency test
                            └──[11] JSON envelope       └──[19] Override test
                                                        └──[20] Baseline test
```

---

## 13. Open Design Decisions

1. **Tokenizer choice for window selection.** Option A: bundle `tiktoken` and use `cl100k_base` as a model-agnostic approximation. Option B: select tokenizer from the summarization model name (more accurate, more dependencies). Recommendation: Option A for the basic realization; revisit if accuracy complaints arise.

2. **Should the system message count toward the token budget?** Current proposal: no (it is configuration, not conversation). Alternative: yes, to enforce a true ceiling on summarizer input. Recommendation: exclude in Phase 1, document the choice in code comments.

3. **What happens on `agent_id` continuation (in-memory multi-turn)?** The buffer is currently stateless per `execute()` call, so each continuation regenerates the summary from scratch. This is correct for Phase 1 but wasteful — Phase 3 (cumulative summary) directly addresses it. No action needed now.

---

## 14. Summary

The in-agent rolling summary memory feature adds a single new component, `RollingSummaryBuffer`, owned by `BaseAgent` and activated by config. On each agent invocation, when enabled, it selects the newest contiguous suffix of `task_messages` that fits within `max_tokens_to_summarize`, calls a configurable summarization LLM (default: the agent's own model) to produce a 4–8 sentence narrative, and attaches the result to the response envelope as `conversationSummary` — using the same augmentation pattern Feature 004 uses for `topicId`.

The feature is **off by default**, runs **entirely in-process** with no Redis or external service, requires **zero changes** to the reasoning-action loop, supports **per-agent overrides** of every parameter including the summarization model, and **fails open** on every error path so that summarization can never break agent execution. When the parallel execution mode is on (the default), the summarizer LLM call runs concurrently with the first reasoning iteration, masking its latency entirely on any multi-iteration agent. The basic realization deliberately does *not* inject the summary back into the agent's prompt, preserving ADR-006's prompt-cache prefix and the determinism guarantees of the reasoning loop; a future Phase 2 ADR may add opt-in prompt injection. The feature composes cleanly with Features 003 (Langfuse traces) and 004 (topic-scoped summaries) without coupling to either.
