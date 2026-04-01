# Architecture Proposal: Topic-Aware Conversational Memory

> **Feature:** 004-topic-aware-memory  
> **Status:** Draft  
> **Created:** 2026-03-31  
> **Depends on:** None (standalone; future integration with 002-mcp-payload-processor for cross-agent trace/session propagation is out of scope)

---

## 1. Problem Statement

SGR Agent Core handles multi-turn conversations using the OpenAI-compatible pattern: the client sends the full message history in the `messages` array of each `POST /v1/chat/completions` request. The agent is created fresh per request, with no server-side memory of previous exchanges. This has three consequences:

1. **No topic awareness.** When a user shifts between domains (e.g., "AI market trends" → "climate policy" → back to "AI regulation"), the entire flat message history is sent to the LLM. There is no mechanism to detect that a topic shift occurred or to filter the context to only topic-relevant exchanges. The LLM wastes tokens processing irrelevant history and may confuse cross-domain context.

2. **No structured dialog history.** Conversation state lives entirely on the client side. If the client loses state, the conversation is lost. There is no server-side record of what was discussed, when topics changed, or what the agent's responses were. This makes it impossible to build features like session resumption, conversation analytics, or quality scoring across turns.

3. **Context window waste.** As conversations grow, the full message history consumes an increasing share of the LLM's context window. For a 30-turn research conversation spanning 3 topics, the agent receives all 30 turns even when only the 8 turns from the current topic are relevant. This degrades reasoning quality (the "Lost in the Middle" effect) and increases cost.

The proposed solution introduces a **topic-aware memory layer** that stores dialog history with topic tags, detects coarse domain shifts, and injects only topic-relevant history into the agent's context. The memory layer is implemented as a modular external microservice with a lightweight integration middleware in SGR, requiring no changes to the agent execution loop itself.

---

## 2. How SGR Currently Handles Multi-Turn Conversations — Analysis

### 2.1 The Client-Side History Pattern

SGR follows the OpenAI convention: the client is responsible for accumulating conversation history and sending it with each request:

```json
POST /v1/chat/completions
{
  "model": "sgr-tools-agent",
  "messages": [
    {"role": "user", "content": "Research AI market trends in 2025"},
    {"role": "assistant", "content": "## AI Market Trends 2025\n..."},
    {"role": "user", "content": "Now tell me about climate policy"},
    {"role": "assistant", "content": "## Climate Policy Overview\n..."},
    {"role": "user", "content": "Going back to AI — what about regulation?"}
  ]
}
```

The server treats `messages` as an opaque list. It does not parse, filter, or enrich it. The full array is passed directly to `AgentFactory.create()` as `task_messages`.

### 2.2 Agent Creation Flow

The `/v1/chat/completions` endpoint handler (in `sgr_agent_core/server/`) follows this flow:

```
1. Parse request → extract model, messages, stream, temperature, etc.
2. Resolve agent definition from model name (or agent_id for continuations)
3. AgentFactory.create(agent_def, task_messages=messages, ...)
4. agent.execute() → reasoning loop → result
5. Stream result to client via SSE
```

The key observation: **step 2–3 is the natural intervention point**. Between parsing the request and creating the agent, we can intercept `messages`, query a memory service, and substitute the raw history with topic-filtered context — all without touching `AgentFactory`, `BaseAgent`, or any agent subclass.

### 2.3 Context Assembly Inside the Agent

`BaseAgent._prepare_context()` assembles the LLM context from:

1. System prompt (from config or file)
2. Task messages (the `messages` array from the request)
3. Tool results from previous iterations (appended during the reasoning loop)

The task messages are used verbatim. There is no filtering, summarization, or restructuring. This means whatever we pass as `task_messages` at agent creation time is exactly what the LLM sees as conversation history.

### 2.4 Session Continuity Mechanism

SGR does support multi-turn via the `agent_id` pattern: the first response includes an `agent_id` in the `model` field of the SSE stream. The client can send subsequent messages with this `agent_id` as the `model` value, and the server routes to the existing agent instance (which is kept in memory). However:

- The agent is stateful only while in memory — there is no persistence.
- The conversation history accumulates as a flat list inside `AgentContext`.
- There is no topic segmentation, no filtering, and no external storage.

### 2.5 What Does Not Exist Today

| Capability | Current State |
|-----------|---------------|
| Server-side dialog storage | ❌ No persistence beyond in-memory agent state |
| Topic detection | ❌ No awareness of domain shifts |
| Topic-tagged message storage | ❌ Messages are unstructured |
| Context filtering by topic | ❌ Full history always sent |
| Session resumption from storage | ❌ Lost when agent is garbage-collected |
| External memory service integration | ❌ No hooks or abstractions |

---

## 3. Proposed Architecture: Memory Microservice + SGR MemoryMiddleware

### 3.1 Core Concept

Introduce two components:

1. **Memory Microservice** — a standalone FastAPI service (separate process) that owns dialog storage, topic detection, and topic-filtered retrieval. It exposes a REST API consumed by SGR.

2. **MemoryMiddleware** — a lightweight integration layer in SGR's server endpoint that intercepts the request/response flow to query and update the memory service. It sits between the HTTP handler and `AgentFactory.create()`, requiring no changes to the agent execution loop.

The separation is deliberate: the memory service is an independent, reusable component that can be consumed by any agent framework or application, while the SGR middleware is a thin adapter. This follows Principle 5 (Evolution, Not Revolution) — the existing agent loop, `BaseAgent`, `AgentFactory`, and all agent subclasses remain untouched.

### 3.2 Architecture Overview

```
Client
  │
  │  POST /v1/chat/completions
  │  { messages: [...], userId: "user-42", sessionId: "sess-abc" }
  │
  ▼
┌──────────────────────────────────────────────────────────────┐
│  SGR Agent Core Server (FastAPI, port 8010)                  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  MemoryMiddleware (pre-processing)                     │  │
│  │                                                        │  │
│  │  1. Extract latest user message from request           │  │
│  │  2. POST to Memory Service /memory/check-and-retrieve  │  │
│  │     → sends: sessionId, userId, newMessage             │  │
│  │     ← receives: topicId, topicChanged, messages[]      │  │
│  │  3. Replace task_messages with enriched messages[]      │  │
│  └──────────────────────┬─────────────────────────────────┘  │
│                         │                                    │
│                         ▼                                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  AgentFactory.create(task_messages=enriched_messages)   │  │
│  │  agent.execute() → reasoning loop (UNCHANGED)          │  │
│  └──────────────────────┬─────────────────────────────────┘  │
│                         │                                    │
│  ┌──────────────────────▼─────────────────────────────────┐  │
│  │  MemoryMiddleware (post-processing, fire-and-forget)   │  │
│  │                                                        │  │
│  │  4. POST to Memory Service /memory/store               │  │
│  │     → sends: sessionId, userId, topicId,               │  │
│  │       userMessage, assistantResponse                   │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────┘
                            │  async HTTP (httpx)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Memory Microservice (FastAPI, port 8012)                    │
│                                                              │
│  Endpoints:                                                  │
│    POST /memory/check-and-retrieve                           │
│    POST /memory/store                                        │
│    GET  /memory/sessions/{sessionId}/topics (future)         │
│    POST /memory/search (future: semantic summary search)     │
│                                                              │
│  ┌──────────────┐  ┌─────────────────────────────────────┐   │
│  │  Redis       │  │  Lightweight LLM Client             │   │
│  │  (storage)   │  │  (topic detection via gpt-4.1-nano  │   │
│  │              │  │   or gpt-4o-mini)                    │   │
│  └──────────────┘  └─────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 Why a Separate Microservice (Not an MCP Tool)

SGR already has MCP infrastructure (feature 001), making it tempting to expose memory as an MCP tool. However, this is the wrong abstraction for the primary use case:

| Concern | MCP Tool (Pattern C) | Middleware + Service (Pattern A) |
|---------|---------------------|----------------------------------|
| **Invocation** | LLM decides when to call | Automatic, every turn |
| **Determinism** | Non-deterministic (LLM may skip it) | Deterministic (always runs) |
| **Latency** | Adds a reasoning step for the LLM to decide | Runs before reasoning starts |
| **Context quality** | Agent sees full unfiltered history *then* retrieves memory | Agent sees only filtered history from the start |
| **Agent code changes** | None (tool system handles it) | None (middleware handles it) |

The memory service's primary mode is **pre-reasoning infrastructure** — it runs before the LLM ever sees the context. This is a preprocessing step, not a tool call.

**Future exception:** When cross-topic semantic search is added (Phase 3), a `MemorySearchTool` exposed via MCP becomes valuable — the agent can explicitly search past topic summaries when its reasoning determines it needs historical context. At that point, the memory service gains a second interface: its REST API for the middleware (automatic) and an MCP interface for agent-initiated search (on-demand). Both consume the same underlying service.

### 3.4 Why Not a Hook in BaseAgent

An alternative to the middleware approach is subclassing `BaseAgent` and overriding `_prepare_context()` to inject memory-retrieved history. This was considered and rejected for the initial implementation because:

1. It requires every agent definition to use a memory-aware base class — either all six agent types must be subclassed, or `BaseAgent._prepare_context()` itself must be modified.
2. It couples memory logic to the agent lifecycle, making it harder to test and evolve independently.
3. The middleware approach achieves the same result (filtered `task_messages`) without any agent code changes.

If future requirements demand per-iteration memory queries (e.g., the agent needs to recall past context mid-reasoning), the `_prepare_context()` hook becomes the right extension point. The memory service's REST API is designed to support both integration patterns.

---

## 4. Memory Microservice Design

### 4.1 Data Model

```
Message:
  messageId:  str        # UUID, primary identifier
  sessionId:  str        # Groups messages into a conversation session
  role:       str        # "user" | "assistant"
  requestId:  str | null # For role="assistant": messageId of the user
                         #   message this responds to. Null for user messages.
  userId:     str | null # Metadata: who sent the message
  topicId:    str        # Topic segment identifier (e.g., "topic-001")
  content:    str        # Message text content
  timestamp:  float      # Unix timestamp (time.time())
```

**Topic tracking per session:**

```
SessionTopicState:
  sessionId:       str   # Session identifier
  currentTopicId:  str   # Active topic being discussed
  topicCounter:    int   # Monotonically incrementing topic number
  topicLabels:     dict  # { topicId: "AI market trends", ... }
```

### 4.2 Redis Storage Schema

Redis is chosen for within-session memory because it is ephemeral by nature (matching the within-session scope), fast (sub-millisecond reads), and simple to operate. The data naturally expires when sessions end.

```
# Session topic state
session:{sessionId}:topic:current        → string (current topicId)
session:{sessionId}:topic:counter        → int (topic counter)
session:{sessionId}:topic:labels         → hash { topicId → label string }

# Messages indexed by topic
session:{sessionId}:topic:{topicId}:msgs → list of messageIds (chronological)

# Individual messages
msg:{messageId}                          → hash {
                                              sessionId, role, requestId,
                                              userId, topicId, content,
                                              timestamp
                                           }

# Session-level TTL for automatic cleanup
# All keys under session:{sessionId}:* expire together
# Default TTL: 24 hours (configurable)
```

### 4.3 API Endpoints

#### POST /memory/check-and-retrieve

The primary endpoint called by SGR's MemoryMiddleware on every user turn.

**Request:**

```json
{
  "sessionId": "sess-abc",
  "userId": "user-42",
  "newMessage": {
    "role": "user",
    "content": "What about AI regulation?"
  }
}
```

**Internal flow:**

```
1. Look up current topicId for session
   → Redis: GET session:sess-abc:topic:current
   → If no session state exists: initialize with topicId="topic-001",
     store the new message, return immediately with messages=[newMessage]

2. Retrieve messages with current topicId
   → Redis: LRANGE session:sess-abc:topic:topic-001:msgs 0 -1
   → Resolve each messageId → build topic_history (list of messages)

3. Call lightweight LLM for topic continuity check:
   System prompt:
     "You classify whether a new message continues the current
      conversation topic or shifts to a different domain.
      Respond ONLY with JSON:
      {\"same_topic\": true|false, \"new_topic_label\": \"...\"|null}
      If same_topic is false, provide a 2-5 word label for the new topic."

   User prompt:
     "Current topic: {current_topic_label}
      Recent messages in this topic:
      {last 3-5 messages from topic_history, condensed}

      New message: {newMessage.content}

      Is this the same topic or a different one?"

   → Model: gpt-4.1-nano or gpt-4o-mini (target: <150ms, <$0.0001/call)

4a. If same_topic=true:
   → Store the new message with current topicId
   → Return all messages in current topic (including the new one)

4b. If same_topic=false:
   → Increment topic counter → new topicId (e.g., "topic-002")
   → Update current topicId in session state
   → Store topic label: HSET session:sess-abc:topic:labels topic-002 "AI regulation"
   → Store the new message with the new topicId
   → Return ONLY the new message (clean context for new topic)
```

**Response:**

```json
{
  "topicId": "topic-002",
  "topicLabel": "AI regulation",
  "topicChanged": true,
  "previousTopicId": "topic-001",
  "previousTopicLabel": "AI market trends",
  "messages": [
    {"role": "user", "content": "What about AI regulation?"}
  ]
}
```

Or, when topic continues:

```json
{
  "topicId": "topic-001",
  "topicLabel": "AI market trends",
  "topicChanged": false,
  "previousTopicId": null,
  "previousTopicLabel": null,
  "messages": [
    {"role": "user", "content": "Tell me about AI market trends in 2025"},
    {"role": "assistant", "content": "## AI Market Trends 2025\n..."},
    {"role": "user", "content": "What about the enterprise segment specifically?"}
  ]
}
```

#### POST /memory/store

Stores a completed exchange (user message + agent response) after the agent finishes execution. Called by SGR's MemoryMiddleware as a fire-and-forget async task.

**Request:**

```json
{
  "sessionId": "sess-abc",
  "userId": "user-42",
  "topicId": "topic-001",
  "userMessage": {
    "messageId": "msg-123",
    "content": "Tell me about AI market trends"
  },
  "assistantMessage": {
    "messageId": "msg-124",
    "content": "## AI Market Trends 2025\n..."
  }
}
```

**Note:** The `check-and-retrieve` endpoint already stores the user message (step 4a/4b). The `store` endpoint is for the assistant response, which is only available after agent execution completes. The `requestId` field on the assistant message is automatically set to the user message's `messageId`.

#### GET /memory/sessions/{sessionId}/topics (future)

Returns all topics for a session with metadata. Useful for UI display and analytics.

#### POST /memory/search (future, Phase 3)

Semantic search across topic summaries. Requires vector index.

### 4.4 Topic Detection Prompt Design

The topic detection prompt is optimized for speed and cost. It sends minimal context (the topic label + last 3–5 messages, not the full topic history) to keep input tokens low:

```
System: You classify whether a new message continues the current
conversation topic or shifts to a different domain.

Rules:
- A topic shift means a MAJOR domain change (e.g., "AI market" → "climate policy")
- Asking follow-up questions, requesting details, or refining the same
  subject is NOT a topic shift
- Returning to a previously discussed domain IS a topic shift
  (creates a new topic segment, even if related to an earlier one)

Respond ONLY with JSON, no other text:
{"same_topic": true, "new_topic_label": null}
or
{"same_topic": false, "new_topic_label": "2-5 word label"}
```

The model choice should be the cheapest and fastest available. As of March 2026, `gpt-4.1-nano` (~$0.10/1M input tokens, ~50ms latency) or `gpt-4o-mini` (~$0.15/1M input, ~100ms latency) are suitable. The prompt consumes approximately 200–400 input tokens and produces 15–30 output tokens per call, putting the per-call cost at **under $0.0001**.

### 4.5 First-Turn and Edge Case Handling

| Scenario | Behavior |
|----------|----------|
| First message in a new session | Create session state, assign `topic-001`, generate topic label via LLM, store message, return `[newMessage]` |
| Empty `content` in new message | Return error 400: "Message content must not be empty" |
| Session not found (stale sessionId) | Treat as new session (initialize fresh) |
| LLM topic detection fails (timeout, error) | **Fail open:** assume `same_topic=true`, log warning, return full current topic history |
| LLM returns malformed JSON | Same as above — fail open with `same_topic=true` |
| Very long topic history (100+ messages) | Return last N messages within a configurable token budget (default: 50 messages or ~8000 tokens) |
| Redis unavailable | Return error 503 with `retry-after` header; SGR middleware falls back to raw client messages |

---

## 5. SGR MemoryMiddleware Design

### 5.1 Middleware Integration Point

The middleware wraps the existing `/v1/chat/completions` handler without modifying it. It operates at the server endpoint level:

```python
# sgr_agent_core/server/memory_middleware.py — conceptual structure

class MemoryMiddleware:
    """
    Pre/post-processing layer for topic-aware memory.
    
    Pre-processing: intercepts incoming messages, queries the memory
    service for topic-filtered history, and substitutes task_messages.
    
    Post-processing: stores the agent's response in the memory service
    as a fire-and-forget async task.
    """

    def __init__(self, config: MemoryConfig):
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.service_url,
            timeout=config.timeout,
        )

    async def pre_process(
        self,
        messages: list[dict],
        session_id: str | None,
        user_id: str | None,
    ) -> MemoryPreProcessResult:
        """
        Query memory service and return topic-filtered messages.
        
        If memory is disabled, session_id is missing, or the service
        is unreachable, returns the original messages unchanged.
        """
        ...

    async def post_process(
        self,
        session_id: str,
        user_id: str | None,
        topic_id: str,
        user_message: dict,
        assistant_response: str,
    ) -> None:
        """
        Store the completed exchange in the memory service.
        Fire-and-forget — errors are logged but never raised.
        """
        ...
```

### 5.2 Pre-Processing Flow

```python
# Pseudocode for the modified /v1/chat/completions handler

async def chat_completions(request: ChatCompletionRequest):
    memory = get_memory_middleware()  # singleton, initialized at startup
    
    # --- PRE-PROCESSING ---
    if memory and request.session_id:
        result = await memory.pre_process(
            messages=request.messages,
            session_id=request.session_id,
            user_id=request.user_id,
        )
        task_messages = result.messages      # topic-filtered
        topic_id = result.topic_id           # for post-processing
        memory_active = True
    else:
        task_messages = request.messages      # raw client messages
        topic_id = None
        memory_active = False
    
    # --- AGENT EXECUTION (unchanged) ---
    agent = await AgentFactory.create(
        agent_def=resolve_agent_def(request.model),
        task_messages=task_messages,
        request_metadata={
            "userId": request.user_id,
            "sessionId": request.session_id,
            "topicId": topic_id,
        },
    )
    result = await agent.execute()
    
    # --- POST-PROCESSING (fire-and-forget) ---
    if memory_active and topic_id:
        asyncio.create_task(
            memory.post_process(
                session_id=request.session_id,
                user_id=request.user_id,
                topic_id=topic_id,
                user_message=request.messages[-1],
                assistant_response=str(result),
            )
        )
    
    return result
```

### 5.3 Fail-Open Behavior

The middleware is designed to **never block or crash** agent execution:

| Failure | Behavior |
|---------|----------|
| Memory service unreachable | Log warning, use raw client `messages` unchanged |
| Memory service returns error | Log warning, use raw client `messages` unchanged |
| Memory service timeout (default: 2s) | Log warning, use raw client `messages` unchanged |
| `session_id` not provided in request | Skip memory entirely, use raw `messages` |
| `memory.enabled: false` | Middleware not instantiated; zero overhead |
| Post-processing (store) fails | Log warning, discard silently (fire-and-forget) |

This guarantees that the memory layer is purely additive — removing it or having it fail produces identical behavior to the current system.

### 5.4 Request Schema Extension

The `/v1/chat/completions` request needs two optional fields that are not part of the standard OpenAI schema. These are passed as extra fields (SGR already uses Pydantic v2 models with `ConfigDict(extra="allow")`):

```json
{
  "model": "sgr-tools-agent",
  "messages": [...],
  "stream": true,
  "sessionId": "sess-abc",
  "userId": "user-42"
}
```

Both fields are optional. When absent, memory is skipped and behavior is identical to current.

---

## 6. Configuration Design

### 6.1 SGR Config (config.yaml)

```yaml
# config.yaml — new section
memory:
  enabled: false                        # Master switch
  service_url: "http://localhost:8012"   # Memory microservice URL
  timeout: 2.0                          # Request timeout in seconds
  fail_open: true                       # If service is down, use raw messages
```

### 6.2 Pydantic Config Model

```python
# sgr_agent_core/memory/config.py

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """Configuration for the memory middleware."""
    enabled: bool = Field(
        default=False,
        description="Enable topic-aware memory. When false, no memory "
                    "service calls are made.",
    )
    service_url: str = Field(
        default="http://localhost:8012",
        description="Base URL of the memory microservice.",
    )
    timeout: float = Field(
        default=2.0,
        description="HTTP timeout for memory service calls in seconds.",
    )
    fail_open: bool = Field(
        default=True,
        description="If true, memory service failures are non-fatal — "
                    "the agent uses raw client messages. If false, "
                    "memory service errors are propagated.",
    )
```

### 6.3 GlobalConfig Integration

```python
# Modification to GlobalConfig
class GlobalConfig(BaseModel):
    # ... existing fields ...
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
```

Missing `memory:` section in YAML → defaults (`enabled=False`) → zero behavior change.

### 6.4 Memory Microservice Config (separate file)

The memory microservice has its own configuration, independent of SGR:

```yaml
# memory-service-config.yaml
server:
  host: "0.0.0.0"
  port: 8012

redis:
  url: "redis://localhost:6379"
  db: 0
  session_ttl: 86400              # 24 hours in seconds

topic_detection:
  llm:
    base_url: "https://api.openai.com/v1"
    api_key: "sk-..."             # or MEMORY_LLM_API_KEY env var
    model: "gpt-4.1-nano"         # cheapest/fastest available
    temperature: 0.0
    max_tokens: 50
  max_history_messages: 5         # messages sent to LLM for topic check
  fail_open: true                 # on LLM failure, assume same_topic

retrieval:
  max_messages: 50                # max messages returned per topic
  max_tokens: 8000                # approximate token budget for history
```

---

## 7. Latency Analysis

The memory layer adds latency to the critical path (pre-processing) and zero latency to the post-processing path (fire-and-forget).

### 7.1 Pre-Processing Latency Budget

```
Component                          Latency       Notes
─────────────────────────────────  ────────────  ──────────────────────
HTTP round-trip to memory service   1–5ms         localhost, same machine
Redis lookups (session + messages)  0.5–2ms       in-memory, pipelined
LLM topic detection call           50–200ms       gpt-4.1-nano / gpt-4o-mini
JSON serialization/parsing          <1ms          —
─────────────────────────────────  ────────────  ──────────────────────
Total pre-processing               ~55–210ms
```

**Comparison:** A single LLM reasoning call in SGR takes 500–3000ms. The memory pre-processing adds **3–15% overhead** relative to one reasoning iteration, and **1–4%** relative to a full 5-iteration agent execution.

### 7.2 Optimization: Skip LLM on First Message

For the first message in a session (no previous topic exists), the memory service skips the LLM call entirely, reducing pre-processing to ~5ms.

### 7.3 Post-Processing Latency

Zero on the critical path. `asyncio.create_task()` returns immediately. The background task completes in ~5–10ms (HTTP call + Redis write).

---

## 8. Future Evolution Path

The basic realization establishes a foundation that supports incremental capability additions without architectural changes:

### Phase 1 (This Proposal): Topic-Filtered History

- Redis storage with topic tags
- LLM-based coarse topic detection
- Topic-filtered history injection via MemoryMiddleware
- No SGR core changes

### Phase 2: Topic Summaries

When a topic shift is detected, the memory service generates a summary of the completed topic segment and stores it alongside the raw messages:

```
SessionTopicState (extended):
  topicSummaries: dict  # { topicId: "User researched AI market trends,
                        #   focusing on enterprise segment and regulation
                        #   outlook. Key finding: ...", ... }
```

The `check-and-retrieve` response gains an optional `previousTopicSummaries` field — a list of summary strings for past topics in the session, injected as a system message prefix. This gives the agent awareness of what was discussed before, even when the full history is not in context.

**Intervention point:** Add a `TopicSummarizationWorker` to the memory service that triggers asynchronously when a topic closes. Uses the same lightweight LLM with a summarization prompt.

### Phase 3: Semantic Search Across Topics (MCP Tool)

Add a vector index (Qdrant, in-memory or persistent) to the memory service. Topic summaries are embedded and stored. A `MemorySearchTool` is exposed via MCP, allowing the agent to explicitly search past topics:

```python
class MemorySearchTool(MCPBaseTool):
    """Search past conversation topics for relevant context."""
    tool_name: str = "memory_search"
    query: str  # What to search for in past topics

    async def __call__(self, context, config, **kwargs):
        # Calls memory service POST /memory/search
        # Returns ranked topic summaries + key messages
        ...
```

This is the point where the MCP interface on the memory service becomes valuable — Pattern C (agent-initiated retrieval) complements Pattern A (automatic injection).

**Intervention point:** The memory service gains `POST /memory/search` endpoint + Qdrant collection. SGR gains `MemorySearchTool` registered in `ToolRegistry`.

### Phase 4: Cross-Session Persistence

Swap Redis for PostgreSQL (or add PG as a persistent tier behind Redis). The `messages` table uses your exact schema with indexes on `(sessionId, topicId)` and `(userId, timestamp)`. The MemoryMiddleware interface is unchanged — the memory service handles storage migration internally.

### Phase Dependency Graph

```
Phase 1: Topic-Filtered History (this proposal)
    │
    ├──▶ Phase 2: Topic Summaries
    │        │
    │        └──▶ Phase 3: Semantic Search (MCP Tool)
    │
    └──▶ Phase 4: Cross-Session Persistence (independent)
```

---

## 9. Alignment with Platform Principles

| Principle | Alignment | Notes |
|-----------|-----------|-------|
| **P1: Deterministic First, Semantic Second** | ✅ | Topic detection uses a deterministic check first (is this the first message? is sessionId present?) before invoking the semantic LLM call. Redis lookups are deterministic. Only topic classification requires LLM reasoning. |
| **P2: Prefer Agent-as-Tool** | ✅ Neutral | Memory is infrastructure, not an agent coordination pattern. The middleware approach avoids adding agent-to-agent complexity. Phase 3's `MemorySearchTool` follows the agent-as-tool-consumer pattern. |
| **P3: Workspace Isolation** | ✅ | Sessions are isolated by `sessionId`. No cross-session data leakage. Redis key namespace enforces isolation. |
| **P4: Gateway as Single Control Point** | ✅ Neutral | Memory middleware operates at the server endpoint level, not as a gateway enforcement layer. It preprocesses data, it does not enforce constraints. |
| **P5: Evolution, Not Revolution** | ✅ | No existing code modified. Middleware wraps the endpoint. Memory service is a new process. Agent loop, `BaseAgent`, `AgentFactory`, all agent subclasses — unchanged. `memory.enabled: false` (default) = identical behavior to current system. |
| **P6: Self-Improving Loop** | ✅ | Stored dialog history with topic tags provides structured data for future quality scoring, failure attribution, and entropy detection. Topic-level analytics (average turns per topic, topic shift patterns, resolution rates) become possible. |

### Layer Positioning

The memory layer spans two architectural layers:

- **Layer 4: Context Management** — topic detection and context filtering (the S-MMU concern of managing what is in the agent's active attention)
- **Layer 1: Foundation Services** — dialog storage in Redis (persistence infrastructure)

The MemoryMiddleware sits at **Layer 7: Presentation & Access** — it is an API-level preprocessing step.

---

## 10. Relationship to Other Features

| Feature | Relationship |
|---------|-------------|
| **001 — MCP Ask Endpoint** | The MCP `ask` handler can pass `sessionId` and `userId` from its MCP call arguments into `request_metadata`, enabling memory for MCP-initiated conversations. No changes to feature 001 required — just populate `sessionId` in the MCP call payload. |
| **002 — MCP Payload Processor** | In multi-agent chains, the payload processor can propagate `sessionId` from Agent A to Agent B's MCP call, creating a shared memory scope across composed agents. This is a Phase 4+ concern. |
| **003 — Langfuse Observability** | Memory operations (topic detection latency, topic shift frequency, context token savings) can be traced as Langfuse spans. The `topicId` can be added to trace metadata for correlation. Integration is additive — the `MemoryMiddleware` can call `provider.start_span("memory-check")` if observability is enabled. |

---

## 11. Component Breakdown

### 11.1 New Files in SGR Agent Core

| File | Purpose |
|------|---------|
| `sgr_agent_core/memory/__init__.py` | Package init, exports `MemoryMiddleware`, `MemoryConfig` |
| `sgr_agent_core/memory/config.py` | `MemoryConfig` Pydantic model |
| `sgr_agent_core/memory/middleware.py` | `MemoryMiddleware` class (pre/post processing) |
| `sgr_agent_core/memory/client.py` | `MemoryServiceClient` — async httpx wrapper for the memory service API |
| `sgr_agent_core/memory/models.py` | Request/response Pydantic models for the memory service API (`CheckAndRetrieveRequest`, `CheckAndRetrieveResponse`, `StoreRequest`) |

### 11.2 Modified Files in SGR Agent Core

| File | Change |
|------|--------|
| `sgr_agent_core/server/server.py` (or equivalent) | Initialize `MemoryMiddleware` at startup; wrap chat completions handler |
| `config.yaml.example` | Add `memory:` section with commented defaults |
| `GlobalConfig` class | Add `memory: MemoryConfig` field |

### 11.3 Memory Microservice (Separate Repository / Package)

| File | Purpose |
|------|---------|
| `memory_service/__init__.py` | Package init |
| `memory_service/main.py` | FastAPI app, endpoint definitions |
| `memory_service/config.py` | Service configuration (Pydantic) |
| `memory_service/models.py` | API request/response models, `Message` data model |
| `memory_service/storage.py` | Redis storage layer (session state, message CRUD) |
| `memory_service/topic_detector.py` | LLM-based topic classification |
| `memory_service/retriever.py` | Topic-filtered message retrieval logic |
| `Dockerfile` | Container build |
| `docker-compose.yaml` | Service + Redis |
| `config.yaml.example` | Service config template |
| `tests/` | Test suite |

---

## 12. Implementation Tasks

| # | Task | Scope | Dependencies | Parallel |
|---|------|-------|-------------|----------|
| **Memory Microservice** | | | | |
| 1 | Create memory service project skeleton (FastAPI, config, Dockerfile) | Service | — | — |
| 2 | Implement `Message` data model and Redis storage layer (`storage.py`) | Service | Task 1 | — |
| 3 | Implement `TopicDetector` — LLM-based topic classification with fail-open | Service | Task 1 | [P] |
| 4 | Implement `POST /memory/check-and-retrieve` endpoint with full flow (lookup → detect → store → return) | Service | Tasks 2, 3 | — |
| 5 | Implement `POST /memory/store` endpoint (store assistant response) | Service | Task 2 | [P] with Task 4 |
| 6 | Add session TTL and Redis key expiration | Service | Task 2 | [P] |
| 7 | Unit tests: Redis storage layer (CRUD, TTL, key structure) | Service | Task 2 | [P] |
| 8 | Unit tests: TopicDetector (same topic, different topic, LLM failure → fail-open, malformed JSON) | Service | Task 3 | [P] |
| 9 | Integration test: full check-and-retrieve flow with mocked LLM | Service | Task 4 | — |
| 10 | Integration test: multi-turn conversation with topic shifts | Service | Task 4, 5 | — |
| **SGR Integration** | | | | |
| 11 | Create `MemoryConfig` Pydantic model and integrate into `GlobalConfig` | SGR | — | [P] |
| 12 | Create `MemoryServiceClient` — async httpx wrapper for memory service API | SGR | — | [P] |
| 13 | Create API request/response models (`CheckAndRetrieveRequest`, `CheckAndRetrieveResponse`, `StoreRequest`) | SGR | — | [P] |
| 14 | Implement `MemoryMiddleware` (pre-process + post-process with fail-open) | SGR | Tasks 11, 12, 13 | — |
| 15 | Integrate `MemoryMiddleware` into `/v1/chat/completions` handler (startup init + request wrapping) | SGR | Task 14 | — |
| 16 | Update `config.yaml.example` with `memory:` section | SGR | Task 11 | [P] |
| 17 | Unit tests: `MemoryConfig` defaults and validation | SGR | Task 11 | [P] |
| 18 | Unit tests: `MemoryMiddleware` fail-open behavior (service down, timeout, missing sessionId) | SGR | Task 14 | — |
| 19 | Integration test: SGR server + memory service end-to-end (multi-turn with topic shifts) | SGR + Service | Tasks 10, 15 | — |
| 20 | Integration test: verify no behavior change with `memory.enabled: false` | SGR | Task 15 | [P] |

### Task Dependency Graph

```
MEMORY MICROSERVICE                          SGR INTEGRATION
                                            
[1] Project skeleton                        [11] MemoryConfig ─────────┐
 ├──[2] Redis storage ──[7] Unit tests       [12] ServiceClient ───────┤
 │   └──[6] Session TTL                      [13] API models ─────────┤
 │                                                                     │
 ├──[3] TopicDetector ──[8] Unit tests      [14] MemoryMiddleware ◄────┘
 │                                            │
 ├──[4] check-and-retrieve ◄─ 2, 3          [15] Endpoint integration ◄── 14
 │   └──[9] Integration test                  │
 │                                           [18] Fail-open tests
 ├──[5] store endpoint ◄── 2                 [16] config.yaml.example
 │   └──[10] Multi-turn test ◄─ 4, 5        [17] Config unit tests
 │                                           [20] No-change test
 └─────────────────────────────────┐
                                   │
                            [19] End-to-end test ◄── 10, 15
```

---

## 13. Summary

The topic-aware conversational memory architecture introduces a modular, fail-safe memory layer to SGR Agent Core through two components: a standalone Memory Microservice (FastAPI + Redis + lightweight LLM) that owns dialog storage and topic detection, and a thin MemoryMiddleware in SGR's server endpoint that intercepts request/response flow to query the service. The design requires **zero changes** to `BaseAgent`, `AgentFactory`, agent subclasses, or the reasoning-action loop — memory is purely a server-level preprocessing concern.

The basic realization covers within-session dialog storage with topic tags, coarse domain shift detection via a cheap LLM call, and topic-filtered context injection. It adds **55–210ms** of pre-processing latency (3–15% of a single LLM call) and **zero** post-processing latency on the critical path. The architecture is designed for incremental evolution: Phase 2 adds topic summaries, Phase 3 adds semantic search via an MCP tool, and Phase 4 adds cross-session persistence — each building on the established interfaces without architectural changes.

The key design decisions are: (1) middleware over agent subclass (no agent code changes), (2) separate microservice over in-process library (independent evolution, reusable by other consumers), (3) Redis over PostgreSQL for the initial scope (ephemeral sessions, sub-millisecond reads, natural TTL), and (4) fail-open at every layer (memory never degrades agent execution). When `memory.enabled: false` (the default), the system behaves identically to current SGR — the memory layer is invisible.
