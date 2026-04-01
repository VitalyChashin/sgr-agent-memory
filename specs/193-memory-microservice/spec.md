# Feature Specification: Memory Microservice

**Feature Branch**: `193-memory-microservice`
**Created**: 2026-03-31
**Status**: Draft
**Input**: Architecture proposal `004-topic-aware-memory-architecture.md` — memory microservice implementation

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Store and Retrieve Topic-Filtered Conversation Context (Priority: P1)

The SGR Agent Core middleware sends a user's message to the memory microservice along with a session identifier. The service stores the message, determines whether it continues the current topic or starts a new one, and returns only the messages relevant to the current topic. This enables the agent to receive focused context instead of the full unfiltered conversation history.

**Why this priority**: This is the core capability — without it, the memory layer has no value. The combined store-and-retrieve endpoint (`POST /context`) is the primary interface consumed by the SGR middleware on every user turn.

**Independent Test**: Send a sequence of messages across 2-3 topics within a session. Verify that after a topic shift, only messages from the new topic are returned. Verify that within a topic, all relevant messages accumulate and are returned.

**Acceptance Scenarios**:

1. **Given** no session state exists for a sessionId, **When** the first message is sent, **Then** the service creates a new session, assigns a topic (e.g., "topic-001"), generates a topic label, stores the message, and returns it as the sole message in the response.
2. **Given** a session has 5 messages in the current topic, **When** a follow-up message about the same subject is sent, **Then** the service classifies it as the same topic, stores it, and returns all 6 messages in the topic.
3. **Given** a session has 5 messages about "AI market trends", **When** a message about "climate policy" is sent, **Then** the service detects a topic shift, creates a new topic (e.g., "topic-002") with the label "climate policy", stores the message under the new topic, and returns only the new message.
4. **Given** a topic has accumulated 100+ messages, **When** context is requested, **Then** the service returns at most the configured limit (default: 50 messages), prioritizing the most recent ones.

---

### User Story 2 - Store Assistant Response After Agent Execution (Priority: P1)

After the agent produces a response, the SGR middleware sends the assistant's message to the memory service for storage. The service associates the response with the session, the current topic, and the originating user message.

**Why this priority**: Without storing assistant responses, the conversation history is incomplete — future topic-filtered context would only contain user messages, degrading the quality of context provided to the agent.

**Independent Test**: Store a user message via `POST /context`, then store the assistant response via `POST /messages`. Query the topic's messages and verify both user and assistant messages are present in chronological order.

**Acceptance Scenarios**:

1. **Given** a user message was stored with topic "topic-001", **When** the assistant response is stored via `POST /messages`, **Then** the response is associated with the same topic and linked to the originating user message.
2. **Given** a stored assistant response, **When** the next context retrieval occurs for the same topic, **Then** the assistant message appears in the returned message list at the correct chronological position.

---

### User Story 3 - Topic Detection via Lightweight Classification (Priority: P1)

The service uses a lightweight language model to determine whether a new message continues the current topic or introduces a new domain. The classification must be fast (under 200ms), cheap (under $0.0001 per call), and fail-safe (if the model is unavailable, assume the same topic continues).

**Why this priority**: Topic detection is the intelligence behind the memory layer. Without it, messages would either all be in one topic (useless) or require manual topic tagging (impractical).

**Independent Test**: Send a series of messages — some clearly on-topic follow-ups (refinements, drill-downs) and some clearly shifting domains (completely different subject). Verify detection accuracy of 90%+ for major shifts and 95%+ for same-topic follow-ups.

**Acceptance Scenarios**:

1. **Given** the current topic is "AI market trends", **When** a message asks "What about the enterprise segment?", **Then** the classifier identifies this as the same topic.
2. **Given** the current topic is "AI market trends", **When** a message asks "What is the EU's climate policy?", **Then** the classifier identifies this as a different topic and provides a label like "EU climate policy".
3. **Given** the classification model is unreachable or returns an error, **When** a message is processed, **Then** the service assumes the same topic continues (fail-open), logs a warning, and returns the current topic's messages.
4. **Given** the classification model returns malformed output, **When** the response is parsed, **Then** the service treats it as same-topic and logs a warning.

---

### User Story 4 - Automatic Session Cleanup (Priority: P2)

Session data (messages, topic state) is automatically cleaned up after a configurable retention period (default: 24 hours). No manual intervention is required.

**Why this priority**: Without cleanup, storage grows unbounded. However, this is lower priority than core functionality because storage can be manually managed during initial deployment.

**Independent Test**: Create a session with messages, configure a short retention period (e.g., 5 seconds for testing), wait for expiration, and verify the session data is no longer accessible.

**Acceptance Scenarios**:

1. **Given** a session was created 24 hours ago (default retention), **When** the retention period expires, **Then** all messages and topic state for that session are automatically removed.
2. **Given** a session has active messages, **When** a new message is stored, **Then** the session's retention timer is reset (sliding expiration).

---

### User Story 5 - Health Check and Operational Readiness (Priority: P2)

The service exposes a health check endpoint that reports whether the service and its dependencies (storage, classification model) are operational.

**Why this priority**: Required for production deployment (container orchestration, load balancers) but not needed for core functionality testing.

**Independent Test**: Call `GET /health` and verify it returns a healthy status when all dependencies are available, and an appropriate status when storage is unavailable.

**Acceptance Scenarios**:

1. **Given** the service is running and storage is available, **When** `GET /health` is called, **Then** it returns a healthy status.
2. **Given** storage is unavailable, **When** `GET /health` is called, **Then** it returns an unhealthy status with details.

---

### Edge Cases

- What happens when a stale or expired sessionId is provided? The service treats it as a new session and initializes fresh state.
- What happens when an empty or whitespace-only message content is sent? The service returns an error (400) indicating content must not be empty.
- What happens when the user returns to a previously discussed domain? The service creates a new topic segment (does not merge with the earlier topic), ensuring clean context boundaries.
- What happens when storage is completely unavailable? The service returns HTTP 503 to the caller; the SGR middleware falls back to using raw client messages.
- What happens when two concurrent requests arrive for the same session? The service processes them sequentially per session (storage operations for a session are serialized) to avoid race conditions in topic state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The service MUST expose a `POST /context` endpoint that accepts a session identifier, optional user identifier, message content, and optional max-messages limit; and returns topic-filtered messages, current topic metadata, and whether a topic shift occurred.
- **FR-002**: The service MUST expose a `POST /messages` endpoint that accepts a session identifier, message role, content, and optional parent message identifier; and stores the message under the current topic.
- **FR-003**: The service MUST expose a `GET /health` endpoint that reports service health including storage availability.
- **FR-004**: The service MUST detect coarse domain shifts between consecutive user messages using a lightweight classification step, distinguishing major topic changes from follow-up questions, refinements, and drill-downs within the same domain.
- **FR-005**: The service MUST skip the classification step for the first message in a new session, immediately assigning it to a new topic with a generated label.
- **FR-006**: The service MUST fail open when the classification model is unavailable, returns an error, or returns malformed output — defaulting to "same topic continues" with a logged warning.
- **FR-007**: The service MUST store all messages (user and assistant) with unique identifiers, session associations, topic assignments, role attribution, timestamps, and optional parent-message links.
- **FR-008**: The service MUST return at most a configurable number of messages per topic (default: 50) to prevent unbounded context growth, prioritizing the most recent messages.
- **FR-009**: The service MUST automatically clean up session data after a configurable retention period (default: 24 hours) with sliding expiration reset on new activity.
- **FR-010**: The service MUST generate human-readable topic labels (2-5 words) for new topics, either via the classification model or as part of the topic-shift detection response.
- **FR-011**: The service MUST respond to `POST /context` requests within 300ms for 95% of calls (including classification latency), and within 10ms for the first message in a session (no classification needed).
- **FR-012**: The service MUST maintain topic state per session including the current topic identifier, a monotonically incrementing topic counter, and a mapping of topic identifiers to labels.
- **FR-013**: The service MUST be deployable as a standalone process, independent of SGR Agent Core, with its own configuration, lifecycle, and health monitoring.

### Key Entities

- **Message**: A single conversational exchange — unique identifier, session association, role (user/assistant), topic assignment, content, timestamp, optional link to originating user message (for assistant responses).
- **Session Topic State**: Per-session tracking of the active topic — current topic identifier, topic counter (monotonically incrementing), and a mapping of topic identifiers to human-readable labels.
- **Topic**: A segment of conversation about a coherent domain — identified by an auto-generated identifier (e.g., "topic-001") and labeled with a short description (e.g., "AI market trends").

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The combined store-and-retrieve operation (`POST /context`) completes within 300ms for 95% of requests when a classification call is needed, and within 10ms when no classification is needed (first message in session).
- **SC-002**: Topic detection correctly identifies major domain shifts (e.g., "AI" to "climate") in at least 90% of cases.
- **SC-003**: Topic detection correctly identifies same-topic follow-ups (refinements, drill-downs, clarifications) in at least 95% of cases.
- **SC-004**: The per-call cost for topic classification is under $0.0001 (using a lightweight model with ~200-400 input tokens and ~15-30 output tokens).
- **SC-005**: Session data is automatically cleaned up within the configured retention window (default: 24 hours) with no manual intervention.
- **SC-006**: When the classification model is unavailable, the service continues operating (fail-open) with zero errors visible to the caller — only degraded topic detection (everything stays in current topic).
- **SC-007**: The service handles at least 100 concurrent sessions with no performance degradation.

## Assumptions

- A lightweight, low-cost language model is available for topic classification (e.g., GPT-4.1-nano at ~$0.10/1M input tokens, ~50ms latency; or GPT-4o-mini at ~$0.15/1M input, ~100ms latency).
- An in-memory data store is available as the storage backend for within-session data. Data durability is not required — session data is ephemeral and can be lost on storage restart without user impact.
- The service and SGR Agent Core run on the same machine or within the same local network, ensuring low-latency communication (1-5ms round-trip for HTTP calls).
- Topic detection operates at a coarse domain level (major subject changes), not fine-grained subtopic detection. Follow-up questions, refinements, and drill-downs within the same domain are treated as the same topic.
- Cross-session persistence, topic summaries, and semantic search are out of scope for this initial implementation and will be addressed in future phases.
- The SGR middleware (features 191/192) is the primary consumer. The service's API contract follows what was defined in `specs/191-topic-aware-memory/contracts/memory-service-api.md`.
- Session ownership validation (verifying a userId matches the session creator) is out of scope. The service trusts caller-provided identifiers.
- The service is deployed and managed separately from SGR Agent Core (separate process, configuration, and lifecycle).
