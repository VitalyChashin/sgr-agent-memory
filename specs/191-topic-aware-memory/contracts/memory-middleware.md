# Contract: Memory Middleware (Internal)

**Owner**: `sgr_agent_core.memory.middleware.MemoryMiddleware`
**Consumers**: `sgr_agent_core.server.endpoints` (chat completion handler)

## Interface

### MemoryMiddleware

```python
class MemoryMiddleware:
    """Pre/post-processing layer for topic-aware memory.

    Initialized once during server lifespan. Called per-request
    from the chat completion endpoint.
    """

    def __init__(self, config: MemoryConfig, client: MemoryServiceClient):
        ...

    async def preprocess(
        self,
        messages: list[ChatCompletionMessageParam],
        session_id: str,
        user_id: str | None = None,
    ) -> PreprocessResult:
        """Store user message and retrieve topic-filtered context.

        Args:
            messages: Raw client-provided messages
            session_id: Client session identifier
            user_id: Optional user identifier

        Returns:
            PreprocessResult with filtered messages and topic metadata.
            On any error/timeout, returns original messages with no metadata.
        """
        ...

    async def postprocess(
        self,
        session_id: str,
        user_message_id: str,
        assistant_content: str,
        user_id: str | None = None,
    ) -> None:
        """Store assistant response (fire-and-forget).

        Args:
            session_id: Client session identifier
            user_message_id: ID of the user message being responded to
            assistant_content: Full assistant response text
            user_id: Optional user identifier

        Returns:
            None. Errors are logged but never raised.
        """
        ...
```

### PreprocessResult

```python
class PreprocessResult(BaseModel):
    """Result of memory preprocessing."""

    messages: list[ChatCompletionMessageParam]
    """Topic-filtered messages (or original messages on fallback)."""

    topic_metadata: TopicMetadata | None = None
    """Topic metadata for the response. None if memory was skipped/failed."""

    user_message_id: str | None = None
    """ID of the stored user message. Needed for postprocess linkage."""

    used_memory: bool = False
    """Whether memory was actually used (vs fallback)."""
```

## Behavioral Contract

### Preprocess

1. **Skip conditions** (return raw messages immediately):
   - `MemoryConfig.enabled` is `False`
   - `session_id` is not provided

2. **Success path**:
   - Extract last user message content from `messages`
   - Call `MemoryServiceClient.get_context(session_id, content, user_id, max_messages)`
   - Convert `GetContextResponse.messages` to `ChatCompletionMessageParam` format
   - Return `PreprocessResult(messages=filtered, topic_metadata=..., used_memory=True)`

3. **Failure path** (timeout, connection error, HTTP error, malformed response):
   - Log warning with error details
   - Return `PreprocessResult(messages=original_messages, used_memory=False)`

### Postprocess

1. **Skip conditions**:
   - `user_message_id` is `None` (memory wasn't used in preprocessing)
   - `assistant_content` is empty

2. **Execution**:
   - Call `MemoryServiceClient.store_message(session_id, "assistant", content, parent_message_id)`
   - On any error: log warning, return silently

## Integration Point

In `endpoints.py`, the chat completion handler:

```python
@router.post("/v1/chat/completions")
async def create_chat_completion(request: ChatCompletionRequest):
    # ... existing validation ...

    # Memory preprocessing (new)
    memory_result = await memory_middleware.preprocess(
        messages=request.messages.root,
        session_id=request.session_id,  # new optional field
        user_id=request.user_id,        # new optional field
    )

    # Create agent with (possibly filtered) messages
    agent = await AgentFactory.create(agent_def, memory_result.messages)

    # ... existing agent execution and streaming ...

    # Memory postprocessing (new, fire-and-forget)
    if memory_result.used_memory:
        asyncio.create_task(_store_assistant_response(
            agent=agent,
            session_id=request.session_id,
            user_message_id=memory_result.user_message_id,
            user_id=request.user_id,
        ))
```

## ChatCompletionRequest Extensions

```python
class ChatCompletionRequest(MessagesRequest):
    # ... existing fields ...
    session_id: str | None = Field(default=None, alias="sessionId")
    user_id: str | None = Field(default=None, alias="userId")
```

These fields use `alias` to accept camelCase JSON keys (`sessionId`, `userId`) while keeping Python snake_case internally. Both are optional — when absent, memory is skipped entirely.
