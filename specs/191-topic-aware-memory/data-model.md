# Data Model: Topic-Aware Conversational Memory

**Feature Branch**: `191-topic-aware-memory`
**Date**: 2026-03-31

## Entities

### 1. MemoryConfig

Configuration for the memory subsystem. Nested within `GlobalConfig`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `False` | Whether memory layer is active |
| `service_url` | `str` | `"http://localhost:9100"` | Memory microservice base URL |
| `timeout` | `float` | `0.3` | HTTP timeout in seconds (300ms to meet SC-002) |
| `max_messages` | `int` | `50` | Max messages returned per topic (edge case bound) |

**Validation Rules**:
- `timeout` must be > 0
- `service_url` must be a valid URL
- `max_messages` must be > 0

**Relationships**: Owned by `GlobalConfig` as `memory: MemoryConfig`

---

### 2. StoreMessageRequest

Request DTO sent to the memory service to store a message.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | `str` | Yes | Session identifier from client |
| `user_id` | `str \| None` | No | Optional user identifier |
| `role` | `str` | Yes | Message role: `"user"` or `"assistant"` |
| `content` | `str` | Yes | Message text content |
| `parent_message_id` | `str \| None` | No | For assistant messages: the user message ID it responds to |

**Validation Rules**:
- `role` must be one of `"user"`, `"assistant"`
- `content` must not be empty

---

### 3. StoreMessageResponse

Response DTO from the memory service after storing a message.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | `str` | Unique ID assigned by the memory service |
| `topic_id` | `str` | Topic the message was classified into (e.g., `"topic-001"`) |
| `topic_label` | `str` | Human-readable topic label (e.g., `"AI market trends"`) |
| `topic_shift` | `bool` | Whether a topic shift was detected |

---

### 4. GetContextRequest

Request DTO sent to the memory service to retrieve topic-filtered context.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | `str` | Yes | Session identifier |
| `user_id` | `str \| None` | No | Optional user identifier |
| `content` | `str` | Yes | Current user message content (for topic classification) |
| `max_messages` | `int \| None` | No | Override max messages limit |

---

### 5. ContextMessage

A single message returned as part of topic-filtered context.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | `str` | Unique message identifier |
| `role` | `str` | `"user"` or `"assistant"` |
| `content` | `str` | Message text content |
| `topic_id` | `str` | Topic identifier |
| `timestamp` | `str` | ISO 8601 timestamp |

---

### 6. GetContextResponse

Response DTO from the memory service with topic-filtered conversation context.

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list[ContextMessage]` | Topic-relevant messages in chronological order |
| `current_topic_id` | `str` | Current active topic identifier |
| `current_topic_label` | `str` | Human-readable label for current topic |
| `topic_shift` | `bool` | Whether the current message triggered a topic shift |
| `user_message_id` | `str` | ID assigned to the stored user message |

---

### 7. TopicMetadata

Topic metadata included in the chat completion response (additive fields).

| Field | Type | Description |
|-------|------|-------------|
| `topic_id` | `str` | Current topic identifier |
| `topic_label` | `str` | Human-readable topic label |
| `topic_shift` | `bool` | Whether a topic shift occurred on this request |

---

## State Transitions

### Memory Middleware Request Flow

```
Request Received
    │
    ├─ sessionId absent? ──► SKIP (pass-through raw messages)
    │
    ├─ memory disabled? ──► SKIP (pass-through raw messages)
    │
    ├─ Call memory service GET_CONTEXT
    │   │
    │   ├─ Success ──► Use topic-filtered messages
    │   │               Store topic metadata for response
    │   │
    │   ├─ Timeout ──► FALLBACK (log warning, use raw messages)
    │   │
    │   └─ Error ──► FALLBACK (log error, use raw messages)
    │
    └─ Agent executes with (filtered or raw) messages
        │
        └─ Agent completes
            │
            └─ Fire-and-forget: STORE assistant response to memory service
                │
                ├─ Success ──► (silent)
                │
                └─ Error ──► Log warning (no user impact)
```

## Relationship Diagram

```
GlobalConfig
  └── MemoryConfig (1:1)

ChatCompletionRequest
  ├── messages: MessagesList
  ├── sessionId: str | None (NEW, optional)
  └── userId: str | None (NEW, optional)

MemoryMiddleware
  ├── uses MemoryServiceClient (1:1)
  ├── reads MemoryConfig
  ├── transforms ChatCompletionRequest.messages
  └── produces TopicMetadata

MemoryServiceClient
  ├── sends StoreMessageRequest → receives StoreMessageResponse
  └── sends GetContextRequest → receives GetContextResponse

GetContextResponse
  └── contains list[ContextMessage]
```
