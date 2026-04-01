# Quickstart: Memory Microservice

## Prerequisites

- Redis instance running (default: `localhost:6379`)
- OpenAI API key (for topic classification model)

## 1. Configure the Service

Create `memory-config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 9100

redis:
  url: "redis://localhost:6379"
  db: 0
  session_ttl: 86400  # 24 hours

topic_detection:
  llm:
    base_url: "https://api.openai.com/v1"
    api_key: "sk-..."  # or set MEMORY_LLM_API_KEY env var
    model: "gpt-4.1-nano"
    temperature: 0.0
    max_tokens: 50
  max_history_messages: 5
  fail_open: true

retrieval:
  max_messages: 50
```

## 2. Start the Service

```bash
# Direct
python -m memory_service --config memory-config.yaml

# Docker
docker compose up memory-service
```

## 3. Test Basic Flow

### First message (new session)

```bash
curl -X POST http://localhost:9100/context \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess-test-001",
    "content": "What are the latest AI market trends?"
  }'
```

Response:
```json
{
  "messages": [
    {
      "message_id": "msg-...",
      "role": "user",
      "content": "What are the latest AI market trends?",
      "topic_id": "topic-001",
      "timestamp": "2026-03-31T10:00:00Z"
    }
  ],
  "current_topic_id": "topic-001",
  "current_topic_label": "AI market trends",
  "topic_shift": false,
  "user_message_id": "msg-..."
}
```

### Store assistant response

```bash
curl -X POST http://localhost:9100/messages \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess-test-001",
    "role": "assistant",
    "content": "AI market trends show significant growth...",
    "parent_message_id": "<msg-id-from-above>"
  }'
```

### Follow-up (same topic)

```bash
curl -X POST http://localhost:9100/context \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess-test-001",
    "content": "How does this compare to last year?"
  }'
```

Returns all messages in topic-001 (user + assistant + new follow-up).

### Topic shift

```bash
curl -X POST http://localhost:9100/context \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess-test-001",
    "content": "What is the current EU climate policy?"
  }'
```

Returns only the new message under topic-002, with `"topic_shift": true`.

## 4. Health Check

```bash
curl http://localhost:9100/health
```

## 5. Integration with SGR Agent Core

In SGR's `config.yaml`:

```yaml
memory:
  enabled: true
  service_url: "http://localhost:9100"
  timeout: 0.3
  max_messages: 50
```
