# Feature Specification: Enrich Langfuse Observability Traces

**Feature Branch**: `189-enrich-langfuse-traces`
**Created**: 2026-03-26
**Status**: Draft
**Input**: Trace enrichment report from `specs/188-langfuse-observability/trace-enrichment-report.md`
**Depends on**: 188-langfuse-observability (observability foundation must be deployed)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See Tool Invocation Details (Priority: P1)

As a platform operator debugging an agent execution, I want each tool span in the trace to show the full tool arguments (what was requested) and the tool execution result (what was returned) so that I can understand what each tool did and whether it produced the expected output.

**Why this priority**: Without tool arguments and results, the trace tree is a skeleton — operators can see *which* tools ran but not *what* they did. This is the most basic debugging need and the simplest fix.

**Independent Test**: Run an agent that calls a tool with parameters. Open the trace in the observability dashboard. The tool span should show the full tool arguments under "Input" and the tool result text under "Output".

**Acceptance Scenarios**:

1. **Given** an agent invokes a tool with specific arguments, **When** the tool span is viewed in the dashboard, **Then** the Input section shows the tool name and all tool arguments (e.g., query, timezone, format).
2. **Given** a non-terminal tool completes successfully, **When** the tool span is viewed, **Then** the Output section shows the tool's return value (truncated to a reasonable length), not `null`.
3. **Given** a tool execution fails with an error, **When** the tool span is viewed, **Then** the Output section shows the error message and the span is marked with error level.

---

### User Story 2 - See What the User Asked (Priority: P1)

As a platform operator reviewing traces, I want the root trace to display the user's original request text so that I can immediately understand what triggered the agent execution without having to cross-reference logs or databases.

**Why this priority**: The root trace is the entry point for every debugging session. Without the task text, operators must look elsewhere to understand what the agent was asked to do, breaking the observability flow.

**Independent Test**: Send a chat request to the agent. Open the resulting trace. The root trace's Input section should display the user's message text.

**Acceptance Scenarios**:

1. **Given** a user sends a message to the agent, **When** the root trace is viewed, **Then** the Input section shows the user's message content along with the agent type and message count.
2. **Given** a user sends multiple messages, **When** the root trace is viewed, **Then** the Input section shows the last user message as the primary task description.
3. **Given** the agent completes execution, **When** the root trace is viewed, **Then** the Output section shows the agent's final answer (truncated if very long).

---

### User Story 3 - Track LLM Token Usage and Cost (Priority: P1)

As a platform operator monitoring costs, I want each LLM call within an agent execution to appear as a dedicated generation span with model name, token usage (input and output tokens), and latency so that I can track per-request cost and identify expensive patterns.

**Why this priority**: Token usage and cost visibility is the primary motivation for many operators to adopt observability. Without generation spans, the system provides no insight into the most expensive part of agent execution — the LLM calls.

**Independent Test**: Run an agent that makes at least one LLM call. Open the trace. Each LLM call should appear as a "Generation" span nested under its iteration, showing model name, token counts, and call duration.

**Acceptance Scenarios**:

1. **Given** an agent makes an LLM call during the reasoning phase, **When** the trace is viewed, **Then** a generation span named "reasoning" appears under the iteration span with the model name and token usage.
2. **Given** an agent makes an LLM call during the action selection phase, **When** the trace is viewed, **Then** a generation span named "action-selection" appears under the iteration span with the model name and token usage.
3. **Given** an LLM call uses streaming, **When** the generation span is viewed, **Then** it shows accurate token counts (not estimates) and total call latency.

---

### User Story 4 - See Agent Reasoning in Trace (Priority: P2)

As a platform operator debugging why an agent chose a particular tool, I want each iteration span to include the agent's reasoning output (current situation assessment, plan status, remaining steps) so that I can understand the agent's decision-making process without reconstructing it from logs.

**Why this priority**: Reasoning data explains *why* the agent made each decision. While tool data (US1) shows *what* happened, reasoning data shows the *thought process*. This is essential for debugging incorrect tool selections but is secondary to having basic tool and LLM data.

**Independent Test**: Run an agent that goes through at least 2 iterations. Open the trace. Each iteration span should include reasoning metadata showing the agent's assessment.

**Acceptance Scenarios**:

1. **Given** an agent completes a reasoning phase, **When** the iteration span is viewed, **Then** the span's output or metadata includes the reasoning summary (current situation, plan status, remaining steps, enough data flag).
2. **Given** an agent completes a reasoning phase that results in no explicit reasoning object (e.g., ToolCallingAgent), **When** the iteration span is viewed, **Then** the span still shows the iteration completed without errors — missing reasoning data is acceptable for agents without explicit reasoning.

---

### Edge Cases

- What happens when tool arguments contain very large data (e.g., a tool receiving a full document)? Tool arguments should be truncated to a configurable maximum length to avoid excessive data transfer to the observability backend.
- What happens when a tool returns a very large result (e.g., a web page content extraction)? Tool results should be truncated to a configurable maximum length.
- What happens when the LLM response doesn't include token usage data (e.g., some OpenAI-compatible providers omit usage in streaming)? The generation span should still be created with available data; missing fields should be omitted rather than set to zero.
- What happens when the reasoning phase returns None (e.g., ToolCallingAgent has no explicit reasoning)? The system should handle None gracefully and skip reasoning data enrichment for that iteration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST include full tool arguments (serialized from the tool's data model) in the tool span's Input field.
- **FR-002**: System MUST capture the actual tool execution return value and include it (truncated) in the tool span's Output field.
- **FR-003**: System MUST include the user's task message text in the root trace's Input field.
- **FR-004**: System MUST create a dedicated generation observation for each LLM call, capturing at minimum: model name, call duration, and token usage (when available from the provider response).
- **FR-005**: System MUST include the agent's reasoning output (when available) in the iteration span's metadata or output.
- **FR-006**: System MUST truncate all captured text data (tool arguments, tool results, task messages, LLM messages) to prevent excessive data transfer to the observability backend. Default maximum should be 2000 characters per field.
- **FR-007**: System MUST handle missing or None values gracefully in all enrichment points — missing data must never cause agent execution to fail.
- **FR-008**: System MUST include the agent's final answer in the root trace's Output field.
- **FR-009**: Generation spans MUST include model parameters (temperature, max tokens) when available.
- **FR-010**: The enrichment MUST NOT add measurable latency overhead beyond the existing observability instrumentation (all data is already available in memory; enrichment is serialization only).

### Key Entities

- **Generation Span**: A new observation type (in addition to existing Trace and Span) representing a single LLM API call. Contains model name, parameters, input messages, output response, token usage, and timing.
- **Tool Arguments**: The serialized parameters of a tool invocation (from the tool's Pydantic model), captured as the tool span's input.
- **Tool Result**: The string returned by a tool's execution, captured as the tool span's output.
- **Reasoning Summary**: A subset of the reasoning phase output (current situation, plan status, remaining steps, enough data flag), captured as iteration span metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every tool span in a trace shows the tool's invocation arguments (not just the tool name) — 100% of tool spans have populated Input fields.
- **SC-002**: Every non-terminal tool span shows a non-null Output with the tool's execution result.
- **SC-003**: Every root trace shows the user's original request text in the Input field.
- **SC-004**: Every LLM call within an agent execution appears as a generation observation with model name and token usage (when the provider returns usage data).
- **SC-005**: Operators can calculate per-agent-run cost by summing token usage across generation spans within a trace.
- **SC-006**: Iteration spans for agents with explicit reasoning (SGRToolCallingAgent, SGRAgent) include reasoning metadata.
- **SC-007**: No increase in agent execution failure rate after enrichment — all enrichment is fail-silent.

## Assumptions

- The existing observability foundation (feature 188) is deployed and working — traces, iteration spans, and tool spans are already being created with correct hierarchy.
- Tool arguments are available as Pydantic model instances before `_action_phase()` is called — this is true for all current agent implementations.
- Tool execution results are returned by `_action_phase()` — this is true for all current agent subclasses (SGRToolCallingAgent, ToolCallingAgent, SGRAgent, IronAgent).
- LLM streaming responses include token usage when `stream_options={"include_usage": True}` is set (already added in feature 188). Providers that don't support this parameter will have generation spans without token data.
- The observability provider's `start_span` API supports creating generation-type observations, or a new `start_generation` method will be added to the provider interface.
- Truncation at 2000 characters per field is sufficient for debugging while keeping data transfer reasonable. This can be adjusted via configuration if needed.
