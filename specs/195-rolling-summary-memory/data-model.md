# Data Model: Rolling Summary Memory (v2 — Context Injection)

**Feature Branch**: `195-rolling-summary-memory`  
**Date**: 2026-04-07

## Entities

### RollingSummaryConfig

Configuration for the rolling memory buffer. Nested under `MemoryConfig`.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `enabled` | `bool` | `False` | — | Master switch |
| `max_tokens_to_summarize` | `int` | `2000` | `ge=100, le=32000` | Token budget for the recent window |
| `summarization_model` | `str \| None` | `None` | — | Model for summarization; `None` = agent's main model |
| `summarization_timeout_s` | `float` | `10.0` | `gt=0.0, le=120.0` | Hard timeout for summarizer call |

Note: `parallel_with_first_iteration` is removed in v2 — summarization always runs before the reasoning loop since context injection requires the summary to be available before the first iteration.

### MemoryConfig (extended)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Topic-aware memory toggle (existing) |
| `service_url` | `str` | `"http://localhost:9100"` | Memory service URL (existing) |
| `timeout` | `float` | `0.3` | HTTP timeout (existing) |
| `max_messages` | `int` | `50` | Max messages per topic (existing) |
| `rolling_summary` | `RollingSummaryConfig` | `RollingSummaryConfig()` | Rolling memory buffer config |

### AgentContext (extended)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `conversation_summary` | `str \| None` | `None` | Generated summary of older history |
| `recent_messages` | `list[dict] \| None` | `None` | Recent window message objects |

### RollingSummaryBuffer

Runtime component. Created per `_execute()` invocation when enabled.

| Attribute | Type | Description |
|-----------|------|-------------|
| `config` | `RollingSummaryConfig` | Buffer configuration |
| `client` | `AsyncOpenAI` | LLM client for summarization |
| `model` | `str` | Model name for summarization |

**Methods**:
- `split_conversation(messages) → (older_history, recent_window)` — splits non-system messages by token budget
- `summarize(older_history) → str | None` — generates narrative summary
- `compact_messages(system_msgs, summary, recent_window) → list[dict]` — builds rewritten message list

## Relationships

```
GlobalConfig
  └── memory: MemoryConfig
        ├── (existing topic-aware fields)
        └── rolling_summary: RollingSummaryConfig

BaseAgent._execute()
  └── creates RollingSummaryBuffer (if enabled)
        ├── reads self.task_messages
        ├── splits into older_history + recent_window
        ├── summarizes older_history → summary text
        ├── rewrites self.task_messages to compacted form
        ├── stores summary on AgentContext.conversation_summary
        ├── stores recent window on AgentContext.recent_messages
        └── on failure: restores original task_messages

Response assembly:
  ├── REST SSE: metadata event with conversationSummary + recentMessages
  └── MCP JSON: fields conversationSummary + recentMessages in AskResponse
```

## State Transitions

1. **Before buffer**: `task_messages` = full raw conversation from client
2. **After split**: older_history (to summarize) + recent_window (to keep)
3. **After summarize**: summary text available
4. **After compact**: `task_messages` = `[system msgs] + [summary msg] + [recent window]`
5. **On failure**: `task_messages` restored to original (step 1)
