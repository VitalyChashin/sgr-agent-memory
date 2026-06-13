# Feature Specification: Rolling Summary Memory

**Feature Branch**: `195-rolling-summary-memory`  
**Created**: 2026-04-07  
**Status**: Draft  
**Input**: User description: "In-agent rolling summary memory buffer based on ADR-012"

## Clarifications

### Session 2026-04-07

- Q: Where should the summary be injected relative to system prompt and recent messages? → A: Between system prompt and recent window: `[system] → [summary] → [recent turns]`
- Q: Should the agent receive compacted messages or the full original list? → A: Replace task_messages — agent sees `[system] → [summary message] → [recent window]`, older turns removed
- Q: Should the system re-summarize from scratch each request or persist summaries? → A: Stateless — re-summarize older turns from scratch on every request
- Q: What response fields should be returned? → A: Return `conversationSummary` (text) + `recentMessages` (actual recent message objects)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent Reasons with Compacted Context (Priority: P1)

An operator enables rolling memory on an agent. When a long multi-turn conversation is sent, the system splits the conversation into two parts: a **recent window** (the newest turns fitting within the configured token budget) and **older history** (everything before the window). The older history is summarized into a concise narrative by an LLM call. The agent then receives a compacted message list — `[system prompt] → [summary of older turns] → [recent window turns]` — instead of the full raw conversation. This allows the agent to reason with full conversation knowledge while staying within practical context limits.

**Why this priority**: This is the core mechanic — context distillation that enables long conversations without degrading reasoning quality or inflating cost.

**Independent Test**: Send a 20-turn conversation to an agent with rolling memory enabled (token budget covering ~5 recent turns); verify the agent's actual LLM input contains the system prompt, a summary message, and only the recent turns — not the full 20 turns.

**Acceptance Scenarios**:

1. **Given** rolling memory is enabled with a token budget of N, **When** a conversation with more turns than fit in N tokens is sent, **Then** the agent's context contains: system prompt, one summary message covering older turns, and the recent turns within budget.
2. **Given** a conversation that fits entirely within the token budget, **When** the agent processes it, **Then** no summarization occurs — all turns are kept as-is (no summary message injected).
3. **Given** rolling memory is enabled, **When** the agent reasons and acts, **Then** its responses demonstrate awareness of facts from both the summary and the recent window.

---

### User Story 2 - Summary and Recent Messages Returned on Response (Priority: P1)

After the agent completes, the response includes two additional fields: `conversationSummary` (the narrative text summarizing older history) and `recentMessages` (the actual message objects from the recent window). These fields are returned on both REST API responses (SSE metadata event) and MCP tool responses (JSON fields). Clients can use these for display, logging, or state management.

**Why this priority**: Returning the compacted view gives clients visibility into how the conversation was split and what the agent actually saw.

**Independent Test**: Send a long conversation; verify the response contains both `conversationSummary` and `recentMessages` fields with correct content.

**Acceptance Scenarios**:

1. **Given** rolling memory is enabled and summarization occurs, **When** the response is returned via REST SSE, **Then** a metadata event contains both `conversationSummary` (string) and `recentMessages` (list of message objects).
2. **Given** rolling memory is enabled and summarization occurs, **When** the response is returned via MCP, **Then** the JSON contains both `conversationSummary` and `recentMessages` fields.
3. **Given** the conversation fits within the token budget (no summarization needed), **When** the response is returned, **Then** `conversationSummary` is null/omitted and `recentMessages` contains all turns.

---

### User Story 3 - Feature Off by Default, Zero Impact When Disabled (Priority: P1)

An operator who has not opted in expects identical behavior to the current baseline. No context rewriting, no extra fields, no latency.

**Why this priority**: Backward compatibility is foundational.

**Independent Test**: Run with default config; verify no summarization, no response metadata, no context changes.

**Acceptance Scenarios**:

1. **Given** rolling memory is not enabled (default), **When** the agent processes any request, **Then** the full raw task_messages are passed to the agent unchanged.
2. **Given** rolling memory is disabled, **When** responses are returned, **Then** no `conversationSummary` or `recentMessages` fields appear.
3. **Given** rolling memory is disabled, **When** the agent executes, **Then** no summarization LLM calls are made.

---

### User Story 4 - Configure Rolling Memory Per Agent (Priority: P2)

An operator configures different rolling memory settings per agent. A research agent with long conversations uses a larger token window and cheaper summarization model; a quick-answer agent has it disabled.

**Why this priority**: Per-agent configurability enables cost optimization across agent types.

**Independent Test**: Define two agents with different configs; verify each respects its own settings.

**Acceptance Scenarios**:

1. **Given** an agent overrides the global token budget, **When** it processes a request, **Then** window selection uses the per-agent budget.
2. **Given** an agent specifies a different summarization model, **When** the summary is generated, **Then** the specified model is used.
3. **Given** the global config enables rolling memory but a specific agent disables it, **When** that agent runs, **Then** no summarization occurs and full messages are passed.

---

### User Story 5 - Graceful Degradation on Summarization Failure (Priority: P2)

The summarization call fails. The agent falls back to passing the full raw messages (as if rolling memory were disabled), completes normally, and logs a warning. The response omits the summary fields.

**Why this priority**: Fail-open behavior ensures summarization failures never break agent execution.

**Independent Test**: Simulate failures; verify agent runs with full messages and produces correct results.

**Acceptance Scenarios**:

1. **Given** summarization times out, **When** the agent executes, **Then** it receives the original full task_messages, completes normally, and a warning is logged.
2. **Given** summarization returns an error, **When** the agent executes, **Then** it falls back to full messages with no summary fields on the response.
3. **Given** summarization fails, **When** the reasoning loop runs, **Then** the result is correct (using full context as fallback).

---

### User Story 6 - Rolling Memory Independent of Topic-Aware Memory (Priority: P2)

Rolling memory works without the topic-aware memory microservice. The two are independent subsystems.

**Why this priority**: No external service dependency for basic context management.

**Independent Test**: Enable only rolling memory (topic-aware disabled); verify it works.

**Acceptance Scenarios**:

1. **Given** `rolling_summary.enabled` is true and `memory.enabled` is false, **When** the agent runs, **Then** context compaction and summarization work normally.
2. **Given** both are enabled, **When** the agent runs, **Then** both operate independently.

---

### Edge Cases

- Conversation fits entirely within token budget: no summarization occurs; all turns passed as-is; `conversationSummary` is null/omitted; `recentMessages` contains all turns.
- Single user message: no older history to summarize; message passed directly.
- Single message exceeds token budget: message is truncated with a marker to fit the recent window; no summary generated (nothing older exists).
- Empty conversation: no processing; fields omitted.
- Only system messages: no conversation content to summarize; system prompt passed through unchanged.
- Config arrives as raw dict from YAML cascade: feature activates correctly.
- Summarization fails: agent falls back to full raw messages (not compacted).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST split incoming conversation messages into two segments: a **recent window** (newest turns within the configured token budget) and **older history** (all turns before the window). System messages are excluded from both segments and passed through separately.
- **FR-002**: System MUST generate a concise narrative summary (4-8 sentences) of the older history segment using a configurable summarization model.
- **FR-003**: System MUST rewrite the agent's task_messages before the reasoning loop to: `[system prompt messages] → [summary as a message] → [recent window messages]`. Older turns are removed from the context.
- **FR-004**: When the full conversation fits within the token budget, system MUST NOT summarize — all turns are kept as-is with no summary message injected.
- **FR-005**: System MUST return `conversationSummary` (narrative text) and `recentMessages` (list of recent message objects) on responses via both REST API (SSE metadata event) and MCP tool response (JSON fields).
- **FR-006**: System MUST be off by default — no context rewriting, no summarization, no response fields when not enabled.
- **FR-007**: System MUST support global configuration of parameters: enabled, token budget for recent window, summarization model, timeout, parallel mode.
- **FR-008**: System MUST support per-agent override of all configuration parameters.
- **FR-009**: System MUST fail open on summarization failure — fall back to passing full raw messages, log a warning, omit summary response fields.
- **FR-010**: System MUST enforce a configurable hard timeout on the summarization call.
- **FR-011**: System MUST activate correctly regardless of whether config arrives as a validated model or raw dictionary from the cascade.
- **FR-012**: Rolling memory MUST be independently activatable — no dependency on the topic-aware memory microservice.
- **FR-013**: System MUST re-summarize older history from scratch on every request (stateless — no cross-request summary persistence).
- **FR-014**: System MUST always include at least one message in the recent window, truncating with a marker if it alone exceeds the budget.

### Key Entities

- **Rolling Memory Buffer**: Component that splits conversation into recent window and older history, generates summary, and rewrites context. Stateless per invocation.
- **Rolling Memory Configuration**: Settings governing the buffer (enabled, token budget for recent window, summarization model, timeout, parallel mode).
- **Conversation Summary**: Narrative text (4-8 sentences) summarizing the older history segment — injected into context and returned on response.
- **Recent Window**: The newest contiguous turns from the conversation that fit within the token budget — preserved verbatim in context and returned on response.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When enabled, the agent's LLM input contains compacted context (summary + recent window) instead of the full conversation, reducing token count for long conversations.
- **SC-002**: When enabled, every successful response includes `conversationSummary` and `recentMessages` on both REST and MCP paths.
- **SC-003**: When disabled (default), behavior is identical to baseline — no context changes, no extra fields, no latency difference.
- **SC-004**: Summarization failures never cause agent execution failures — 100% of calls complete using full-message fallback.
- **SC-005**: Per-agent overrides are respected — agents with different settings produce behavior consistent with their configurations.
- **SC-006**: The summary is concise (4-8 sentences), factually grounded, and does not fabricate details.
- **SC-007**: Rolling memory works when topic-aware memory is disabled — no external service dependency.
- **SC-008**: Agent responses demonstrate awareness of facts from both summarized older history and recent window content.

## Assumptions

- The existing asynchronous OpenAI-compatible client pool is reusable for summarization calls.
- Token counting uses an approximate, model-agnostic tokenizer sufficient for window selection.
- The summarization prompt is fixed and version-pinned; operator customization is out of scope.
- Cumulative/hierarchical summarization (building on previous summaries across turns) is out of scope for this version.
- The feature runs entirely in-process with no external dependencies.
- The configuration cascade may produce raw dicts for extra fields; the feature must handle both dict and model types.
- The summary message role (e.g., "system" or "user") for the injected summary is a planning-phase decision.
