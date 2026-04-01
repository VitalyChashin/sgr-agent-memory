# Data Model: Memory Microservice

**Feature Branch**: `193-memory-microservice`
**Date**: 2026-03-31

## Entities

### 1. Message

A single conversational exchange stored in the memory service.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_id` | `str` | Yes | UUID, auto-generated on storage |
| `session_id` | `str` | Yes | Groups messages into a session |
| `role` | `str` | Yes | `"user"` or `"assistant"` |
| `topic_id` | `str` | Yes | Topic segment this message belongs to |
| `content` | `str` | Yes | Message text content |
| `timestamp` | `float` | Yes | Unix timestamp (auto-set on creation) |
| `parent_message_id` | `str \| None` | No | For assistant messages: the user message this responds to |
| `user_id` | `str \| None` | No | Optional user identifier |

**Identity**: `message_id` is globally unique (UUID).
**Validation**: `content` must not be empty. `role` must be `"user"` or `"assistant"`.

---

### 2. SessionTopicState

Per-session tracking of the active topic and topic history.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | Session identifier |
| `current_topic_id` | `str` | Active topic being discussed |
| `topic_counter` | `int` | Monotonically incrementing counter |
| `topic_labels` | `dict[str, str]` | Mapping of topic ID to human-readable label |

**Identity**: One per `session_id`.
**Lifecycle**: Created on first message. Updated on topic shift. Expired after retention period.

---

### 3. TopicClassificationResult

Output from the lightweight LLM classification step.

| Field | Type | Description |
|-------|------|-------------|
| `same_topic` | `bool` | Whether the message continues the current topic |
| `new_topic_label` | `str \| None` | 2-5 word label for the new topic (only when `same_topic=False`) |

**Not persisted** — this is a transient result used during the `POST /context` flow.

---

## Storage Schema (Redis Keys)

```
# Session topic state
session:{sessionId}:topic:current        → string (current topicId)
session:{sessionId}:topic:counter        → int (topic counter)
session:{sessionId}:topic:labels         → hash { topicId → label }

# Messages indexed by topic (chronological order)
session:{sessionId}:topic:{topicId}:msgs → list of messageIds

# Individual message data
msg:{messageId}                          → hash {
                                              session_id, role, topic_id,
                                              content, timestamp,
                                              parent_message_id, user_id
                                           }

# TTL: All session:* keys expire together (default: 24 hours)
# Sliding expiration: reset on each new message
```

---

## State Transitions

### Session Lifecycle

```
[No Session] ──(first message)──► [Active Session]
    topic_counter = 1
    current_topic = "topic-001"
    topic_labels = {"topic-001": "<generated label>"}

[Active Session] ──(same-topic message)──► [Active Session]
    message stored under current topic
    TTL reset

[Active Session] ──(topic-shift message)──► [Active Session]
    topic_counter += 1
    current_topic = "topic-{counter}"
    topic_labels += {new_topic: "<generated label>"}
    message stored under new topic
    TTL reset

[Active Session] ──(TTL expires)──► [No Session]
    all keys deleted
```

### Topic Detection Flow

```
Message Received
    │
    ├─ First message in session? ──► Skip LLM, create topic-001
    │
    └─ Existing session
        │
        ├─ Retrieve current topic label + last 3-5 messages
        │
        ├─ Call LLM classifier
        │   │
        │   ├─ same_topic=true ──► Store under current topic
        │   │
        │   ├─ same_topic=false ──► Create new topic, store under it
        │   │
        │   ├─ LLM timeout/error ──► Assume same_topic (fail-open)
        │   │
        │   └─ Malformed response ──► Assume same_topic (fail-open)
        │
        └─ Return topic-filtered messages
```

## Relationship Diagram

```
Session (identified by session_id)
  └── SessionTopicState (1:1)
        ├── current_topic_id
        ├── topic_counter
        └── topic_labels
              ├── "topic-001" → "AI market trends"
              ├── "topic-002" → "EU climate policy"
              └── ...

  └── Messages (1:many, grouped by topic)
        ├── Topic "topic-001"
        │     ├── Message (user) ─────────────────┐
        │     ├── Message (assistant, parent_id) ──┘
        │     └── ...
        └── Topic "topic-002"
              ├── Message (user)
              └── ...
```
