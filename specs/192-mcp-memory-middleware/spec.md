# Feature Specification: Memory Middleware for MCP Endpoint

**Feature Branch**: `192-mcp-memory-middleware`
**Created**: 2026-03-31
**Status**: Draft
**Input**: User description: "Implement memory middleware for MCP endpoint"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Topic-Filtered Context for MCP Tool Calls (Priority: P1)

An MCP client (e.g., Claude Desktop, Cursor, or another AI agent) sends a series of research queries through the `ask` tool across different topics in the same session. When the user shifts topics, the system provides only topic-relevant conversation history to the agent, reducing irrelevant context and improving response quality — just as it does for the REST chat completion endpoint.

**Why this priority**: This is the core value proposition. Without topic-aware context in the MCP endpoint, agents processing multi-topic MCP sessions experience the same "Lost in the Middle" effect that the REST endpoint already solves. Parity between the two interfaces is essential for a consistent user experience.

**Independent Test**: Send a sequence of `ask` tool calls with a `sessionId` parameter across 2-3 distinct topics. Verify the agent receives only the messages relevant to the current topic (not the full conversation history).

**Acceptance Scenarios**:

1. **Given** an MCP client has sent 5 queries about "AI market trends" in a session, **When** the client sends a query about "climate policy", **Then** the agent receives only the new climate policy query as context (not the AI market trends history).
2. **Given** an MCP client sends a follow-up query within the same topic, **When** the system evaluates the message, **Then** all messages within that topic are provided as context.
3. **Given** an MCP `ask` call includes a `sessionId` parameter, **When** the memory service returns topic-filtered context, **Then** the agent receives the filtered messages instead of a single-message context.
4. **Given** an MCP `ask` call includes a `sessionId`, **When** the agent produces a response, **Then** both the user query and the assistant response are stored in the memory service with the correct topic tags.

---

### User Story 2 - Transparent Fallback for MCP When Memory Is Unavailable (Priority: P1)

When the memory layer is disabled, unavailable, or the MCP client does not provide a `sessionId`, the `ask` tool behaves identically to its current behavior — the query is processed as a single user message with no memory involvement.

**Why this priority**: Equal to P1 because the MCP endpoint must never degrade due to the memory feature. Existing MCP clients that do not send `sessionId` must experience zero behavior change.

**Independent Test**: Send `ask` tool calls without a `sessionId` parameter and verify behavior is identical to the system without the memory feature installed.

**Acceptance Scenarios**:

1. **Given** memory is disabled in configuration, **When** an MCP client sends an `ask` call, **Then** the system processes the query identically to current behavior with zero additional overhead.
2. **Given** the memory service is unreachable, **When** an MCP client sends an `ask` call with `sessionId`, **Then** the system logs a warning and processes the query using the single-message format (current behavior).
3. **Given** an MCP `ask` call does not include a `sessionId` parameter, **When** the system processes the query, **Then** memory is skipped entirely and behavior is unchanged.
4. **Given** the memory service times out, **When** the system falls back, **Then** the fallback happens within the configured timeout window.

---

### User Story 3 - Topic Metadata in MCP Response (Priority: P2)

When memory is active and a topic shift is detected, the MCP response includes topic metadata (topic ID, topic label, topic shift indicator) alongside the agent's answer. This allows MCP clients to surface topic context to users or use it for their own session management.

**Why this priority**: Valuable for advanced MCP clients but not required for core functionality. Most MCP clients will initially ignore extra fields.

**Independent Test**: Send an `ask` call with `sessionId` that triggers a topic shift. Verify the response JSON includes topic metadata fields.

**Acceptance Scenarios**:

1. **Given** an MCP `ask` call triggers a topic shift, **When** the response is returned, **Then** it includes `topicId`, `topicLabel`, and `topicShift` fields alongside the `response` and `traceId`.
2. **Given** an MCP `ask` call does not use memory, **When** the response is returned, **Then** no topic metadata fields are present (standard response format).

---

### Edge Cases

- What happens when an MCP client sends an empty `sessionId`? The system treats it as absent and skips memory entirely.
- What happens when the MCP `ask` tool receives a very long query that exceeds the memory service's content limit? The system sends the query as-is to the memory service; if the memory service rejects it, the system falls back to single-message processing.
- What happens when the `sessionId` is shared between MCP and REST endpoints? The memory service treats sessions uniformly — both endpoints contribute to and read from the same session state. This is by design.
- What happens when the MCP agent takes a long time to execute? The assistant response is stored after execution completes, regardless of duration, since MCP execution is synchronous (no fire-and-forget pattern is needed).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The MCP `ask` tool MUST accept an optional `sessionId` parameter for memory-enabled sessions.
- **FR-002**: When `sessionId` is provided and memory is enabled, the system MUST query the memory service for topic-filtered context and provide it to the agent instead of the single-query message.
- **FR-003**: When `sessionId` is provided and memory is enabled, the system MUST store the assistant's response in the memory service after agent execution completes.
- **FR-004**: The system MUST fall back to current single-message behavior when memory is disabled, the memory service is unreachable, or `sessionId` is absent.
- **FR-005**: The system MUST reuse the existing `MemoryMiddleware` and `MemoryServiceClient` from the REST endpoint implementation — no duplication of memory logic.
- **FR-006**: The MCP response MUST include topic metadata (`topicId`, `topicLabel`, `topicShift`) when memory is active and a preprocess result is available.
- **FR-007**: The system MUST not modify the MCP server startup, configuration, or transport layer. Memory integration happens within the `ask` tool handler only.
- **FR-008**: The system MUST emit the same structured log entries for memory operations in the MCP path as in the REST path (warnings on fallback, errors on failure, info on topic shifts).
- **FR-009**: The system MUST extract `sessionId` from the MCP request payload using the existing `McpFieldMapping` configuration, or accept it as a direct tool parameter.

### Key Entities

- **AskRequest**: Extended to include an optional `sessionId` field (additive, backward-compatible).
- **AskResponse**: Extended to include optional `topicId`, `topicLabel`, and `topicShift` fields (additive, backward-compatible).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In multi-topic MCP sessions, the agent receives at least 50% fewer irrelevant context messages compared to single-message processing without memory.
- **SC-002**: The memory preprocessing step in the MCP path completes within 300ms for 95% of requests (matching the REST endpoint's performance target).
- **SC-003**: The assistant response is stored in the memory service before the MCP tool returns its result (synchronous storage, since MCP execution is already synchronous).
- **SC-004**: When memory is disabled or unavailable, MCP behavior is identical to the system without the memory feature installed.
- **SC-005**: Existing MCP clients that do not send `sessionId` experience zero behavior change.

## Assumptions

- The existing `MemoryMiddleware` and `MemoryServiceClient` (from feature 191-topic-aware-memory) are available and fully functional. This feature depends on that implementation being merged first.
- The `sessionId` parameter can be added to the MCP `ask` tool signature without breaking existing MCP clients, because MCP clients ignore unknown parameters and extra parameters are already supported via `extra="allow"` on the models.
- The MCP execution model is synchronous (await agent.execute()), so assistant response storage can happen inline before returning the response — no fire-and-forget pattern is needed.
- The same `MemoryConfig` (enabled, service_url, timeout, max_messages) used by the REST endpoint applies to the MCP endpoint. No separate MCP-specific memory configuration is needed.
- Session state in the memory service is shared across MCP and REST endpoints — a session started via REST can be continued via MCP and vice versa.
- The memory microservice is already deployed and accessible when memory is enabled (same assumption as the REST implementation).
