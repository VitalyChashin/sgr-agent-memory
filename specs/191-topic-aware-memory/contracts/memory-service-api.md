# Contract: Memory Service HTTP API

**Owner**: External memory microservice (separate process)
**Consumer**: SGR Agent Core (`MemoryServiceClient`)
**Protocol**: HTTP/JSON
**Base URL**: Configured via `MemoryConfig.service_url`

## Endpoints

### POST /context

Store the current user message and retrieve topic-filtered conversation context in a single call.

**Request**:
```json
{
  "session_id": "sess-abc123",
  "user_id": "user-001",
  "content": "What are the latest AI market trends?",
  "max_messages": 50
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | Yes | Client-provided session identifier |
| `user_id` | string | No | Optional user identifier |
| `content` | string | Yes | Current user message content |
| `max_messages` | int | No | Max messages to return (server default if omitted) |

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
    },
    {
      "message_id": "msg-002",
      "role": "assistant",
      "content": "AI investment has seen significant growth...",
      "topic_id": "topic-001",
      "timestamp": "2026-03-31T10:00:05Z"
    }
  ],
  "current_topic_id": "topic-001",
  "current_topic_label": "AI market trends",
  "topic_shift": false,
  "user_message_id": "msg-003"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `messages` | array | Topic-relevant messages in chronological order |
| `messages[].message_id` | string | Unique message identifier |
| `messages[].role` | string | `"user"` or `"assistant"` |
| `messages[].content` | string | Message text |
| `messages[].topic_id` | string | Topic identifier |
| `messages[].timestamp` | string | ISO 8601 timestamp |
| `current_topic_id` | string | Active topic for this request |
| `current_topic_label` | string | Human-readable topic label |
| `topic_shift` | bool | Whether topic changed on this message |
| `user_message_id` | string | ID assigned to the stored user message |

**Error Responses**:
- `400`: Invalid request (missing session_id, empty content)
- `500`: Internal server error

---

### POST /messages

Store an assistant response message (fire-and-forget from SGR Core's perspective).

**Request**:
```json
{
  "session_id": "sess-abc123",
  "user_id": "user-001",
  "role": "assistant",
  "content": "Based on current market data, AI investment trends show...",
  "parent_message_id": "msg-003"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | Yes | Session identifier |
| `user_id` | string | No | Optional user identifier |
| `role` | string | Yes | Must be `"assistant"` |
| `content` | string | Yes | Assistant response content |
| `parent_message_id` | string | No | ID of the user message this responds to |

**Response (200)**:
```json
{
  "message_id": "msg-004",
  "topic_id": "topic-001",
  "topic_label": "AI market trends",
  "topic_shift": false
}
```

**Error Responses**:
- `400`: Invalid request
- `500`: Internal server error

---

### GET /health

Health check endpoint.

**Response (200)**:
```json
{
  "status": "healthy"
}
```

## Error Handling Contract

SGR Agent Core treats ALL non-200 responses and connection errors identically:
1. Log warning/error with response details
2. Fall back to raw client-provided messages
3. Continue normal agent execution

The memory service is **non-critical infrastructure** — its unavailability must never block or degrade agent execution.

## Timeout Contract

- SGR Core enforces a client-side timeout (default: 300ms, configurable)
- If the memory service does not respond within the timeout, SGR Core falls back immediately
- The memory service should target < 200ms p95 response time for `/context`
