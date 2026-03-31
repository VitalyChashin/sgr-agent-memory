# Feature Specification: Topic-Aware Conversational Memory

**Feature Branch**: `191-topic-aware-memory`
**Created**: 2026-03-31
**Status**: Draft
**Input**: Architecture proposal for topic-aware conversational memory with middleware integration and external memory microservice

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Topic-Filtered Context in Multi-Topic Conversations (Priority: P1)

A user conducts a multi-turn research conversation spanning multiple domains (e.g., "AI market trends" then "climate policy" then back to "AI regulation"). When the user shifts topics, the system automatically detects the domain change and provides only topic-relevant conversation history to the agent, reducing context noise and improving response quality.

**Why this priority**: This is the core value proposition — without topic-aware context filtering, the agent processes irrelevant history that wastes tokens, increases cost, and degrades reasoning quality ("Lost in the Middle" effect). This single capability justifies the entire feature.

**Independent Test**: Can be fully tested by sending a sequence of messages across 2-3 distinct topics and verifying that the agent receives only the messages relevant to the current topic. Delivers immediate value through improved response quality and reduced token usage.

**Acceptance Scenarios**:

1. **Given** a user has discussed "AI market trends" for 5 turns, **When** they send a message about "climate policy", **Then** the agent receives only the new climate policy message as context (not the AI market trends history).
2. **Given** a user shifts to a new topic, **When** the agent generates a response, **Then** the response does not reference or confuse context from the previous unrelated topic.
3. **Given** a user asks a follow-up question within the same topic, **When** the system evaluates the message, **Then** it is classified as the same topic and all messages within that topic are provided as context.
4. **Given** a conversation has 30 turns across 3 topics with 8 turns in the current topic, **When** the agent receives context, **Then** it receives approximately 8 topic-relevant turns instead of all 30.

---

### User Story 2 - Transparent Fallback When Memory Is Unavailable (Priority: P1)

When the memory layer is disabled, unavailable, or encounters errors, the system falls back gracefully to the current behavior — passing the full client-provided message history unchanged. No user action is required, and the agent continues to function identically to how it works today.

**Why this priority**: Equal to P1 because fail-safe behavior is a non-negotiable requirement. The memory layer must never degrade the existing user experience. Without this guarantee, the feature cannot be shipped.

**Independent Test**: Can be tested by disabling the memory service (or not providing session identifiers) and verifying that agent behavior is identical to the current system with no errors or degradation.

**Acceptance Scenarios**:

1. **Given** memory is disabled in configuration, **When** a user sends a chat request, **Then** the system processes the request identically to current behavior with zero additional overhead.
2. **Given** the memory service is unreachable, **When** a user sends a chat request, **Then** the system logs a warning and processes the request using the raw client-provided messages.
3. **Given** the memory service times out (exceeds configured threshold), **When** a user sends a chat request, **Then** the system falls back to raw messages within the timeout window.
4. **Given** a request does not include a session identifier, **When** the system processes the request, **Then** memory is skipped entirely and behavior is unchanged.

---

### User Story 3 - Server-Side Dialog Storage with Topic Tags (Priority: P2)

The system stores all conversation messages on the server side, tagged with topic identifiers and session metadata. This enables future capabilities like session resumption, conversation analytics, and quality scoring across turns.

**Why this priority**: While not directly user-facing in Phase 1, server-side storage is the foundation that enables topic-filtered retrieval (P1) and all future phases. It provides structured data for analytics and debugging.

**Independent Test**: Can be tested by sending messages through the system and querying the storage to verify messages are persisted with correct topic tags, session IDs, timestamps, and user/assistant role attribution.

**Acceptance Scenarios**:

1. **Given** a user sends a message in a session, **When** the message is processed, **Then** it is stored with a unique message ID, session ID, topic ID, role, content, and timestamp.
2. **Given** the agent responds to a user message, **When** the response is complete, **Then** the assistant's response is stored asynchronously and linked to the originating user message.
3. **Given** a session has been inactive beyond the configured retention period, **When** the retention period expires, **Then** all messages and topic state for that session are automatically cleaned up.

---

### User Story 4 - Configuration-Driven Memory Activation (Priority: P2)

An operator can enable or disable the memory layer through configuration without code changes. When disabled (the default), the system behaves identically to the current version with zero overhead.

**Why this priority**: Operators need control over whether memory is active, where the memory service runs, and what timeout thresholds to use. This is essential for staged rollout and troubleshooting.

**Independent Test**: Can be tested by toggling the memory configuration on/off and verifying that the system behavior changes accordingly, with no code deployments needed.

**Acceptance Scenarios**:

1. **Given** no memory configuration is present, **When** the system starts, **Then** memory is disabled by default and no memory service connections are attempted.
2. **Given** an operator sets memory to enabled and provides a service URL, **When** the system starts, **Then** the memory middleware initializes and begins intercepting requests.
3. **Given** an operator changes the timeout threshold, **When** the memory service is slow, **Then** the system respects the configured timeout before falling back.

---

### Edge Cases

- What happens when a user sends an empty message? The system returns an error (400) indicating that message content must not be empty.
- What happens when the topic detection model returns malformed or unparseable output? The system fails open — assumes the message continues the current topic, logs a warning, and returns the current topic's full history.
- What happens when a very long topic accumulates (100+ messages)? The system returns a bounded set of messages within a configurable limit (default: 50 messages or approximately 8000 tokens worth of history).
- What happens when a stale or expired session ID is provided? The system treats it as a new session and initializes fresh topic state.
- What happens when the user returns to a previously discussed domain? The system creates a new topic segment (not merging with the earlier topic), ensuring clean context boundaries.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST detect coarse domain shifts between consecutive user messages within a session using a lightweight classification step.
- **FR-002**: System MUST store all conversation messages (user and assistant) with topic identifiers, session identifiers, role attribution, and timestamps.
- **FR-003**: System MUST provide only topic-relevant messages as context to the agent when memory is active, replacing the full client-provided message history.
- **FR-004**: System MUST fall back to using the raw client-provided messages when the memory layer is disabled, unreachable, returns an error, or times out.
- **FR-005**: System MUST support enabling/disabling memory through configuration without code changes, with memory disabled by default.
- **FR-006**: System MUST store the assistant's response asynchronously (fire-and-forget) after agent execution completes, without adding latency to the response delivery.
- **FR-007**: System MUST automatically clean up session data after a configurable retention period (default: 24 hours).
- **FR-008**: System MUST skip the topic classification step for the first message in a new session, immediately assigning it to a new topic.
- **FR-009**: System MUST operate as a preprocessing/postprocessing layer without modifying the agent execution loop, agent factory, or any agent subclasses.
- **FR-010**: System MUST support configurable limits on the number of messages or token budget returned per topic to prevent unbounded context growth.
- **FR-011**: System MUST accept optional `sessionId` and `userId` fields in the chat completion request; when absent, memory is skipped entirely.
- **FR-012**: System MUST include topic metadata (current topic ID, topic label, and a topic-shift indicator) as optional extra fields in the chat completion response when memory is active.
- **FR-013**: System MUST emit structured log entries for memory operations: warnings on fallback events, errors on failures, and informational entries on topic shifts.

### Key Entities

- **Message**: A single conversational exchange unit — contains a unique identifier, session association, role (user or assistant), topic assignment, content, timestamp, and an optional link to the originating user message (for assistant responses).
- **Session Topic State**: Tracks the active topic for a session — contains the current topic identifier, a monotonically incrementing topic counter, and a mapping of topic identifiers to human-readable labels.
- **Topic**: A segment of conversation within a session about a coherent domain — identified by an auto-generated identifier (e.g., "topic-001") and labeled with a short human-readable description (e.g., "AI market trends").

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In multi-topic conversations, the agent receives at least 50% fewer irrelevant context messages compared to the full-history approach.
- **SC-002**: The memory preprocessing step completes within 300ms for 95% of requests (excluding the first message in a session, which should complete within 10ms).
- **SC-003**: Post-processing (storing the assistant response) adds zero latency to the user-visible response time.
- **SC-004**: When the memory layer is disabled or unavailable, system behavior is identical to the system without the memory feature installed.
- **SC-005**: Topic detection correctly identifies major domain shifts (e.g., "AI market trends" to "climate policy") in at least 90% of cases.
- **SC-006**: Topic detection correctly identifies same-topic follow-ups (refinements, drill-downs, clarifications) in at least 95% of cases.
- **SC-007**: Session data is automatically cleaned up within the configured retention window with no manual intervention.

## Clarifications

### Session 2026-03-31

- Q: Should the system validate that a `userId` is authorized to access a given `sessionId`? → A: No ownership validation in Phase 1. The system trusts the caller. Session access control is a future consideration.
- Q: Should topic metadata (topic ID, label, shift indicator) be surfaced to the client in the chat completion response? → A: Yes, included as optional extra fields in the response.
- Q: What observability should memory operations have? → A: Structured logging only (warnings on fallback, errors on failure, info on topic shifts). Full tracing integration deferred to post-Phase 1.

## Assumptions

- The memory microservice and the SGR server run on the same machine or within the same local network, ensuring low-latency communication (1-5ms round-trip).
- A lightweight, low-cost language model is available for topic classification (target: under $0.0001 per classification call, under 200ms latency).
- An in-memory data store is available as the storage backend for within-session data.
- Clients that wish to use memory will provide a `sessionId` in their requests; existing clients that do not provide this field will experience no behavior change.
- The memory microservice is deployed and managed separately from SGR Agent Core (separate process, separate configuration, separate lifecycle).
- Topic detection operates at a coarse domain level (major subject changes), not fine-grained subtopic detection. Follow-up questions, refinements, and drill-downs within the same domain are treated as the same topic.
- Cross-session persistence, topic summaries, and semantic search are out of scope for Phase 1 and will be addressed in future phases.
- Session ownership validation (verifying `userId` matches the session creator) is out of scope for Phase 1. The system trusts the caller-provided identifiers.
- Full tracing integration (e.g., Langfuse spans for memory operations) is out of scope for Phase 1. Structured logging provides sufficient observability for initial deployment.
- The existing SGR Agent Core request handling already supports extra fields in the request body, so no schema-breaking changes are required.
