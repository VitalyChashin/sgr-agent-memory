# Quickstart: Topic-Aware Conversational Memory

## Prerequisites

- SGR Agent Core running (v0.6.0+)
- External memory microservice running (see memory service docs)

## 1. Enable Memory in Configuration

Add to your `config.yaml`:

```yaml
memory:
  enabled: true
  service_url: "http://localhost:9100"
  timeout: 0.3          # seconds (default: 0.3)
  max_messages: 50       # per-topic message limit (default: 50)
```

Or via environment variables:

```bash
export SGR__MEMORY__ENABLED=true
export SGR__MEMORY__SERVICE_URL=http://localhost:9100
```

## 2. Send Requests with Session Context

Include `sessionId` in your chat completion requests:

```bash
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sgr_tool_calling_agent",
    "stream": true,
    "sessionId": "sess-abc123",
    "userId": "user-001",
    "messages": [
      {"role": "user", "content": "What are the latest AI market trends?"}
    ]
  }'
```

## 3. Topic-Filtered Context (Automatic)

On subsequent messages, the system automatically filters context by topic:

```bash
# Same session, same topic — full context provided
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sgr_tool_calling_agent",
    "stream": true,
    "sessionId": "sess-abc123",
    "messages": [
      {"role": "user", "content": "How does this compare to last year?"}
    ]
  }'

# Same session, new topic — only new topic context provided
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sgr_tool_calling_agent",
    "stream": true,
    "sessionId": "sess-abc123",
    "messages": [
      {"role": "user", "content": "What is the current climate policy in the EU?"}
    ]
  }'
```

## 4. Backward Compatibility

Requests without `sessionId` work exactly as before — memory is skipped entirely:

```bash
# No sessionId — memory is not used, identical to current behavior
curl -X POST http://localhost:8010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sgr_tool_calling_agent",
    "stream": true,
    "messages": [
      {"role": "user", "content": "Hello world"}
    ]
  }'
```

## 5. Disabling Memory

Set `enabled: false` in config or omit the `memory` section entirely. The system defaults to disabled with zero overhead.

## Observability

When memory is active, structured log entries are emitted:

- **INFO**: Topic shift detected (`topic_id`, `topic_label`, `session_id`)
- **WARNING**: Memory service fallback (timeout, connection error)
- **ERROR**: Memory service failure (unexpected errors)

## Topic Metadata in Response

When memory is active and the SSE stream includes the final chunk, topic metadata is included:

```json
{
  "id": "...",
  "object": "chat.completion.chunk",
  "choices": [...],
  "memory": {
    "topic_id": "topic-002",
    "topic_label": "EU climate policy",
    "topic_shift": true
  }
}
```

Standard OpenAI clients will ignore the extra `memory` field.
