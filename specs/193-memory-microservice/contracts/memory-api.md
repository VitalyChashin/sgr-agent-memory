# Contract: Memory Microservice REST API

**Owner**: Memory microservice (`memory_service`)
**Consumers**: SGR Agent Core `MemoryServiceClient` (features 191/192)
**Protocol**: HTTP/JSON
**Base URL**: Configurable (default `http://localhost:9100`)

This contract MUST match the consumer contract defined in `specs/191-topic-aware-memory/contracts/memory-service-api.md`.

## POST /context

Store the current user message and retrieve topic-filtered conversation context.

**Internal flow**: Lookup session → retrieve current topic messages → classify topic (LLM) → store message → return filtered context.

**Request**:
```json
{
  "session_id": "sess-abc123",
  "user_id": "user-001",
  "content": "What are the latest AI market trends?",
  "max_messages": 50
}
```

**Response (200)**:
```json
{
  "messages": [
    {
      "message_id": "msg-001",
      "role": "user",
      "content": "Tell me about AI investment trends",
      "topic_id": "topic-001",
      "timestamp": "2026-03-31T10:00:00Z"
    }
  ],
  "current_topic_id": "topic-001",
  "current_topic_label": "AI market trends",
  "topic_shift": false,
  "user_message_id": "msg-003"
}
```

**Error Responses**: `400` (invalid request), `503` (storage unavailable)

## POST /messages

Store an assistant response message.

**Request**:
```json
{
  "session_id": "sess-abc123",
  "user_id": "user-001",
  "role": "assistant",
  "content": "Based on current data...",
  "parent_message_id": "msg-003"
}
```

**Response (200)**:
```json
{
  "message_id": "msg-004",
  "topic_id": "topic-001",
  "topic_label": "AI market trends",
  "topic_shift": false
}
```

## GET /health

**Response (200)**:
```json
{
  "status": "healthy"
}
```

**Response (503)** (storage unavailable):
```json
{
  "status": "unhealthy",
  "details": "Storage unavailable"
}
```
