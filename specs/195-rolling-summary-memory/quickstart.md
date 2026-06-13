# Quickstart: Rolling Summary Memory (v2)

**Feature Branch**: `195-rolling-summary-memory`

## Enable Rolling Memory

Add to `config.yaml`:

```yaml
memory:
  rolling_summary:
    enabled: true
```

No other `memory` settings needed — rolling memory is independent of the topic-aware microservice.

### Per Agent Override

```yaml
agents:
  my_research_agent:
    name: my_research_agent
    base_class: SGRToolCallingAgent
    memory:
      rolling_summary:
        enabled: true
        max_tokens_to_summarize: 4000
        summarization_model: gpt-4.1-nano
```

## How It Works

When a long conversation exceeds the token budget:

1. **Split**: Messages are divided into older history + recent window (newest turns within budget)
2. **Summarize**: Older history is condensed into a 4-8 sentence narrative by an LLM call
3. **Inject**: The agent receives `[system prompt] → [summary message] → [recent turns]` instead of the full conversation
4. **Return**: The response includes `conversationSummary` and `recentMessages` fields

When the conversation fits within the budget, no summarization occurs — all messages pass through unchanged.

## Read Response Fields

### SSE (REST API)

```
event: metadata
data: {"conversationSummary": "The user asked about...", "recentMessages": [...]}

data: [DONE]
```

### MCP Tool Response

```json
{
  "response": "Agent answer...",
  "conversationSummary": "The user asked about...",
  "recentMessages": [{"role": "user", "content": "latest message"}]
}
```

## Configuration Reference

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `enabled` | `false` | — | Turn on/off |
| `max_tokens_to_summarize` | `2000` | 100-32000 | Token budget for the recent window |
| `summarization_model` | `null` | — | Model name; null = agent's model |
| `summarization_timeout_s` | `10.0` | 0.01-120.0 | Hard timeout for summarization call |

## Failure Behavior

If summarization fails, the agent receives the full original messages (no compaction) and completes normally. The `conversationSummary` and `recentMessages` fields are omitted from the response. A warning is logged.
