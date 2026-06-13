# Contract: Response Fields for Rolling Memory

**Feature Branch**: `195-rolling-summary-memory`  
**Date**: 2026-04-07

## REST API (SSE Metadata Event)

When rolling memory is enabled and summarization occurs, the SSE stream emits a metadata event before `[DONE]`:

```
event: metadata
data: {"conversationSummary": "<summary text>", "recentMessages": [<message objects>]}

data: [DONE]
```

When the conversation fits within budget (no summarization):

```
event: metadata
data: {"conversationSummary": null, "recentMessages": [<all non-system message objects>]}

data: [DONE]
```

When disabled or on failure: no metadata event emitted.

### Fields

| Field | Type | Present When | Description |
|-------|------|-------------|-------------|
| `conversationSummary` | `string \| null` | Always in metadata event | Narrative summary of older history; null if no summarization needed |
| `recentMessages` | `list[object]` | Always in metadata event | Message objects from the recent window (or all messages if no split) |

## MCP Tool Response

The `AskResponse` JSON includes:

```json
{
  "response": "Agent final answer...",
  "traceId": "trace-xyz",
  "conversationSummary": "The user asked about...",
  "recentMessages": [{"role": "user", "content": "..."}]
}
```

### AskResponse Fields (new)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `conversationSummary` | `string \| null` | No | Summary of older history; omitted when disabled or no summarization |
| `recentMessages` | `list[object] \| null` | No | Recent window messages; omitted when disabled |

## Configuration Contract

### YAML (Global)

```yaml
memory:
  rolling_summary:
    enabled: false                        # Default: off
    max_tokens_to_summarize: 2000         # Token budget for recent window (100-32000)
    summarization_model: null             # null = agent's main model
    summarization_timeout_s: 10.0         # Hard timeout (0.01-120.0)
```

### YAML (Per-Agent Override)

```yaml
agents:
  research_agent:
    name: research_agent
    base_class: SGRToolCallingAgent
    memory:
      rolling_summary:
        enabled: true
        max_tokens_to_summarize: 4000
        summarization_model: gpt-4.1-nano
```

## Backward Compatibility

- Feature off by default. No changes when disabled.
- SSE metadata event uses named `event: metadata` — ignored by OpenAI-compatible clients.
- MCP response fields are optional (`exclude_none=True`).
- No existing fields modified.
