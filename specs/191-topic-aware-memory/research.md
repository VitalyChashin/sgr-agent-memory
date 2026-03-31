# Research: Topic-Aware Conversational Memory

**Feature Branch**: `191-topic-aware-memory`
**Date**: 2026-03-31

## R1: Memory Middleware Integration Point

**Decision**: Integrate at the endpoint level in `endpoints.py`, immediately before `AgentFactory.create()`, as a pre-processing step on `request.messages`. Post-processing (storing assistant response) uses `asyncio.create_task()` fire-and-forget after the agent completes.

**Rationale**: The endpoint handler (`create_chat_completion`) is the single point where request messages are accessible before agent creation. The existing architecture already follows this pattern — `request.messages.root` is passed directly to `AgentFactory.create()`. Inserting a memory middleware call between parsing and agent creation:
- Requires no changes to `BaseAgent`, `AgentFactory`, or any agent subclass (FR-009)
- Follows the same request-scoped pattern as `request_metadata` injection
- Keeps memory concerns entirely within the server layer

**Alternatives considered**:
- **FastAPI middleware**: Too broad — would intercept all routes, not just chat completions. Would need to parse request bodies manually. Rejected.
- **AgentFactory parameter**: Would couple the factory to memory concerns. The factory should remain agnostic to message preprocessing. Rejected.
- **MCPPayloadProcessor**: These operate on MCP tool call payloads, not on chat completion request messages. Wrong abstraction layer. Rejected.

## R2: Capturing Assistant Response for Post-Processing

**Decision**: Use a callback/hook pattern. After `agent.execute()` completes, fire-and-forget the assistant's final response to the memory service. The streaming generator already accumulates the full response. The endpoint will schedule a background task that awaits agent completion and then stores the response.

**Rationale**: The agent runs asynchronously via `asyncio.create_task(agent.execute())`. The response is streamed to the client via SSE. To capture the final response without adding latency (FR-006), we need a separate task that:
1. Awaits the agent's execution task completion
2. Extracts the final assistant message from `agent.conversation`
3. Sends it to the memory service asynchronously (fire-and-forget)

**Alternatives considered**:
- **Modify streaming generator**: Would couple streaming to memory concerns. Rejected.
- **Agent post-execution hook**: Would require modifying `BaseAgent`. Violates FR-009. Rejected.
- **Response middleware**: FastAPI response middleware can't access the full streamed body. Rejected.

## R3: Memory Service Client Design

**Decision**: Use `httpx.AsyncClient` with connection pooling, configured timeouts, and retry-free fail-fast behavior. The client is a thin async wrapper around the memory service HTTP API.

**Rationale**: The project already depends on `httpx[socks]` for the OpenAI client proxy support. Using `httpx.AsyncClient`:
- Connection pooling amortizes TCP/TLS overhead across requests
- Native async/await integration with the FastAPI event loop
- Configurable timeouts per-request (matching the memory service timeout config)
- No retries — fail-fast with fallback to raw messages (FR-004)

**Alternatives considered**:
- **aiohttp**: Would add another HTTP client dependency. The project already uses httpx. Rejected.
- **MCP client**: Memory service is not an MCP server — it's a purpose-built microservice. MCP protocol overhead is unnecessary. Rejected.
- **gRPC**: Adds significant complexity (protobuf schemas, code generation). HTTP/JSON is simpler and sufficient for the expected latency (1-5ms local network). Rejected.

## R4: Session ID and User ID Pass-Through

**Decision**: Accept `sessionId` and `userId` as optional top-level fields on `ChatCompletionRequest`. These are additive fields that existing clients will simply not send. When absent, memory is skipped entirely (FR-011).

**Rationale**: The OpenAI chat completions API schema allows additional fields — clients can send extra JSON fields and the server can choose to use or ignore them. Pydantic's model with these optional fields will:
- Accept requests from clients that include `sessionId`/`userId`
- Ignore the fields silently for clients that don't send them
- Require zero changes to existing client integrations

The `request_metadata` dict on `AgentContext` is the natural place to propagate these values to downstream processors if needed.

**Alternatives considered**:
- **Custom HTTP headers**: Less discoverable, harder to document, not part of the request body schema. Rejected.
- **Nested in a `memory` object**: Over-engineering for two fields. Flat optional fields are simpler. Rejected.

## R5: Topic Metadata in Response

**Decision**: Include topic metadata (`topicId`, `topicLabel`, `topicShift`) as additional fields in the final SSE chunk's response object. This is additive and does not break OpenAI compatibility (extra fields are ignored by standard clients).

**Rationale**: The `OpenAIStreamingGenerator` constructs SSE chunks as JSON dicts. Adding optional fields to the final chunk (or as a separate metadata event) allows topic-aware clients to consume this information without affecting standard OpenAI clients.

**Alternatives considered**:
- **Custom SSE event type**: Would require clients to handle a non-standard event type. Standard `data:` events with extra fields are simpler. Rejected.
- **Response headers**: SSE headers are sent before streaming begins — topic metadata isn't known until after processing. Rejected.

## R6: Topic Classification Architecture

**Decision**: Topic classification is the responsibility of the external memory microservice, not SGR Agent Core. The core framework sends the current user message (and optionally the previous topic context) to the memory service, which returns topic-classified context.

**Rationale**: Per the spec assumptions, the memory microservice is a separate process with its own lifecycle. The topic classification model (lightweight LLM or embedding-based) runs within the memory service. SGR Agent Core's responsibility is limited to:
1. Sending user messages to the memory service for storage and classification
2. Receiving topic-filtered context back
3. Falling back gracefully if the service is unavailable

This separation keeps SGR Agent Core focused on agent orchestration, not NLP/topic-modeling concerns.

**Alternatives considered**:
- **In-process topic detection**: Would add an LLM dependency to the core framework, increase memory footprint, and violate the microservice boundary defined in the spec. Rejected.
- **Client-side topic tagging**: Would require all clients to implement topic detection. Defeats the purpose of server-side intelligence. Rejected.

## R7: Configuration Integration Pattern

**Decision**: Add a `MemoryConfig` Pydantic model as a new field on `GlobalConfig`, following the exact pattern of `MCPServerConfig` and `ObservabilityConfig`.

**Rationale**: The existing configuration system uses nested Pydantic models within the `GlobalConfig` singleton. `MCPServerConfig` and `ObservabilityConfig` demonstrate the pattern:
- Disabled by default (`enabled: bool = False`)
- Self-contained configuration section in `config.yaml`
- Accessible via `GlobalConfig().memory`
- Supports environment variable override via `SGR__MEMORY__*`

**Alternatives considered**:
- **Separate config file**: Would break the single-config-file pattern. Rejected.
- **Agent-level config**: Memory operates at the server level, not per-agent. Agent-level configuration would be wrong. Rejected.
