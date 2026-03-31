# Feature Specification: Langfuse Observability Integration

**Feature Branch**: `188-langfuse-observability`
**Created**: 2026-03-26
**Status**: Draft
**Input**: User description: "Add structured observability tracing to SGR Agent Core using Langfuse, enabling full agent execution tracing, LLM token/cost tracking, tool invocation spans, and quality scoring — with a provider abstraction that defaults to zero-cost no-op when disabled."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trace a Complete Agent Execution (Priority: P1)

As a platform operator, I want to see a structured trace tree for every agent execution so that I can understand the full reasoning loop — which iterations occurred, what LLM calls were made, which tools were selected, and how long each step took.

**Why this priority**: Without execution tracing, operators cannot debug agent behavior, identify performance bottlenecks, or understand why an agent chose a particular tool. This is the foundational capability all other observability features depend on.

**Independent Test**: Can be fully tested by running a single agent request and verifying that a structured trace appears in the observability backend with the correct hierarchy (trace → iteration spans → generation + tool spans).

**Acceptance Scenarios**:

1. **Given** an agent receives a user request and observability is enabled, **When** the agent completes its execution loop, **Then** a structured trace is recorded containing: a root trace with agent metadata, one span per iteration, and nested spans for each tool invocation.
2. **Given** an agent execution fails mid-iteration, **When** an error occurs during tool execution, **Then** the trace records the error with the appropriate error level on the failed span, and all parent spans are properly closed.
3. **Given** observability is disabled in configuration, **When** an agent executes, **Then** no tracing overhead is incurred and no trace data is recorded — agent behavior is identical to the current baseline.

---

### User Story 2 - Track LLM Token Usage and Cost (Priority: P1)

As a platform operator, I want every LLM call to automatically capture token usage (input tokens, output tokens) and cost so that I can monitor spending per agent, per user, and per session.

**Why this priority**: Agents make 8–14+ LLM calls per request across multiple iterations. Without token and cost tracking, operators have no visibility into resource consumption and cannot set budgets or identify expensive agent patterns.

**Independent Test**: Can be fully tested by running an agent and verifying that each LLM call in the trace includes token usage counts and that cost is computed at the trace level.

**Acceptance Scenarios**:

1. **Given** an agent makes LLM calls during reasoning and action selection phases, **When** each call completes (including streamed responses), **Then** token usage (input and output tokens), model name, latency, and model parameters are automatically captured without any additional instrumentation code.
2. **Given** LLM calls use streaming responses, **When** the stream is fully consumed, **Then** accurate token usage is recorded (not estimated) and time-to-first-token is captured.

---

### User Story 3 - Correlate Traces to Users and Sessions (Priority: P2)

As a platform operator, I want traces to be tagged with user ID and session ID so that I can filter and analyze agent behavior for a specific user or conversation session.

**Why this priority**: Multi-tenant environments with concurrent agent executions need request-level correlation. Without it, traces from different users are mixed together, making debugging specific issues impractical.

**Independent Test**: Can be fully tested by sending requests with different user/session metadata and verifying that traces are filterable by these attributes in the observability backend.

**Acceptance Scenarios**:

1. **Given** a request includes user ID and session ID in its metadata, **When** the agent executes, **Then** the root trace is tagged with those identifiers and they are searchable/filterable in the observability backend.
2. **Given** a request arrives via the MCP `ask` endpoint with userId and traceId fields, **When** the agent executes, **Then** the trace carries those identifiers through to the observability backend.

---

### User Story 4 - Zero-Impact Default (Priority: P2)

As a framework maintainer, I want the observability system to have zero impact on existing deployments that don't opt in, so that no current behavior, performance, or dependency changes occur unless the operator explicitly enables observability.

**Why this priority**: The framework has existing users who must not be affected. Observability is an additive capability — it must degrade gracefully in every failure mode and never crash or slow down agent execution.

**Independent Test**: Can be fully tested by running the full test suite without the observability dependency installed and with observability disabled in config, verifying zero behavioral difference.

**Acceptance Scenarios**:

1. **Given** the observability configuration section is absent from the config file, **When** the server starts, **Then** a no-op provider is used and all tracing calls are zero-cost no-ops.
2. **Given** the observability provider package is not installed, **When** the server starts with observability enabled, **Then** an import error is caught, a warning is logged, and the system falls back to the no-op provider without crashing.
3. **Given** the observability backend becomes unreachable during operation, **When** the agent is executing, **Then** agent execution continues unaffected — tracing events are queued or silently dropped.

---

### User Story 5 - Attach Quality Scores to Traces (Priority: P3)

As a platform operator, I want to be able to attach quality scores (human or automated) to agent execution traces so that I can measure and track agent output quality over time.

**Why this priority**: Quality scoring is the foundation for the platform's self-improving loop. While the initial use may be manual scoring, the API enables future automated evaluation pipelines.

**Independent Test**: Can be fully tested by running an agent, then calling the scoring API to attach a score to the resulting trace, and verifying it appears in the observability backend.

**Acceptance Scenarios**:

1. **Given** an agent execution has completed and a trace exists, **When** a quality score is submitted via the provider API, **Then** the score is attached to the trace with a name, value, and optional comment.

---

### Edge Cases

- What happens when the observability provider is initialized but the backend credentials are invalid? The system logs a warning and continues with tracing silently disabled.
- What happens when multiple agents execute concurrently in the same process? Each agent execution maintains its own independent trace context without cross-contamination.
- What happens when an agent reaches its maximum iteration limit? The trace is properly closed with a status reflecting the iteration limit, not left in an open/orphaned state.
- What happens when a tool execution hangs or times out? The tool span is closed with an error status, and the parent iteration and trace spans are also properly closed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a provider abstraction for observability that supports multiple backend implementations.
- **FR-002**: System MUST include a no-op provider as the default, ensuring zero overhead when observability is not enabled.
- **FR-003**: System MUST include a Langfuse-backed provider implementation that traces agent executions as structured trace trees.
- **FR-004**: System MUST automatically capture LLM call details (model, messages, response, token usage, latency, model parameters) for every LLM call made during agent execution without requiring changes to agent logic.
- **FR-005**: System MUST capture streaming response metrics including time-to-first-token and accurate token counts (not estimates).
- **FR-006**: System MUST create a root trace per agent execution containing agent metadata (agent ID, agent type, model, max iterations).
- **FR-007**: System MUST create a child span for each iteration of the agent reasoning loop.
- **FR-008**: System MUST create a child span for each tool invocation within an iteration.
- **FR-009**: System MUST propagate user ID and session ID from request metadata to the trace.
- **FR-010**: System MUST support attaching quality scores (numeric, string, or boolean) to traces.
- **FR-011**: System MUST properly close all spans and traces in both success and error paths, including when agents hit iteration limits.
- **FR-012**: System MUST flush pending trace events at the end of each request and during graceful server shutdown.
- **FR-013**: System MUST never crash or measurably slow down agent execution due to observability — all tracing operations must be fail-silent.
- **FR-014**: System MUST fall back to the no-op provider when the observability package is not installed (import error) or when the backend is unreachable.
- **FR-015**: System MUST support configuration via YAML config file with sensible defaults and via environment variables as a fallback.
- **FR-016**: System MUST treat the observability provider package as an optional dependency — the framework must function fully without it installed.
- **FR-017**: System MUST maintain independent trace contexts for concurrent agent executions in the same process.

### Key Entities

- **ObservabilityProvider**: The abstraction representing a tracing backend. Defines operations for starting/ending traces and spans, scoring, flushing, and creating instrumented LLM clients.
- **Trace**: A root-level observability record representing a complete agent execution, tagged with agent metadata and user/session context.
- **Span**: A child record within a trace representing a discrete operation — an iteration cycle, a tool invocation, or an LLM generation.
- **TraceHandle / SpanHandle**: Opaque references returned when a trace or span is started, used to end or annotate the corresponding record.
- **ObservabilityConfig**: The configuration model controlling whether observability is enabled, which provider to use, and provider-specific settings (keys, URLs, sampling rates, flush intervals).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can view a complete structured trace tree for any agent execution within 30 seconds of the execution completing.
- **SC-002**: Every LLM call within an agent execution is captured with accurate token counts (within 1% of actual usage as reported by the LLM provider).
- **SC-003**: Per-agent-run cost is visible, enabling operators to identify the most expensive agent patterns.
- **SC-004**: Traces are filterable by user ID and session ID, enabling operators to isolate a specific user's agent interactions.
- **SC-005**: Enabling observability adds less than 5% latency overhead to agent execution time (tracing is non-blocking with background export).
- **SC-006**: Existing deployments that do not enable observability experience zero behavioral change — identical test results, no new dependencies loaded, no additional resource consumption.
- **SC-007**: Concurrent agent executions produce independent, non-overlapping traces with correct parent-child span hierarchies.

## Assumptions

- The observability backend (self-hosted or cloud) is available and reachable from the SGR Agent Core deployment environment when observability is enabled. If not, the system degrades gracefully.
- The LLM provider (OpenAI-compatible API) returns token usage information in its responses. If not, the observability system captures what it can and notes missing fields.
- The existing async/await execution model in SGR Agent Core correctly propagates context variables across await boundaries (standard Python asyncio behavior).
- Trace propagation across MCP agent-to-agent calls (multi-agent tracing) is out of scope for this feature and will be addressed in a future feature building on the MCP payload processor.
- The observability provider package is compatible with the project's existing dependency versions (Pydantic v2, OpenAI SDK v1+, Python 3.11+).
- Automated quality scoring pipelines are out of scope — this feature provides the score-attachment API only; scoring logic will be a future feature.
