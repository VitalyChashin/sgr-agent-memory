# Feature Specification: MCP Payload Processor

**Feature Branch**: `187-mcp-payload-processor`
**Created**: 2026-03-25
**Status**: Draft
**Input**: Architecture proposal from `002-mcp-payload-processor-architecture.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inject Trace Context into MCP Calls (Priority: P1)

As a platform operator running multi-agent workflows where Agent A calls Agent B via MCP, I want trace identifiers to be automatically injected into outgoing MCP call payloads from application context rather than relying on the LLM to fill them, so that I can correlate requests across agent chains for debugging and observability.

**Why this priority**: Trace propagation is the foundational use case that proves the processor pattern works. Without it, multi-agent workflows are opaque and undebuggable. Every other processor use case (auth, billing, redaction) follows the same pattern.

**Independent Test**: Configure a trace context processor on an MCP server connection, trigger an agent that calls an MCP tool, and verify the outgoing payload contains the injected trace identifier rather than the LLM-generated default.

**Acceptance Scenarios**:

1. **Given** a processor is configured on an MCP server connection that injects a trace identifier, **When** the agent calls an MCP tool on that server, **Then** the outgoing payload contains the processor-injected trace identifier, overriding any LLM-generated value.
2. **Given** no processors are configured on an MCP server connection, **When** the agent calls an MCP tool, **Then** the behavior is identical to the current system (backward compatible).
3. **Given** multiple processors are configured in a chain, **When** the agent calls an MCP tool, **Then** each processor runs in order, and each can see and modify the payload from the previous processor.

---

### User Story 2 - Carry Request-Scoped Metadata Through Agent Execution (Priority: P2)

As a platform developer, I want incoming request metadata (trace IDs, user identity, session context) to be available to processors during MCP tool calls, so that request-scoped values flow from the original caller through the entire agent execution without the LLM's involvement.

**Why this priority**: Processors need access to request-scoped data to be useful. Without a metadata propagation mechanism, processors can only inject static/configured values, not dynamic per-request values.

**Independent Test**: Create an agent with request metadata (e.g., a trace ID passed at creation time), configure a processor that reads from that metadata, trigger an MCP tool call, and verify the metadata value appears in the outgoing payload.

**Acceptance Scenarios**:

1. **Given** an agent is created with request metadata containing a trace identifier, **When** a processor accesses that metadata during an MCP call, **Then** the processor can read the trace identifier and inject it into the payload.
2. **Given** an agent is created without request metadata, **When** a processor attempts to read metadata, **Then** the processor receives an empty collection and can fall back to defaults without errors.
3. **Given** an agent receives a request via the MCP endpoint (agent-to-agent call), **When** the MCP handler creates the agent, **Then** the incoming trace and user identifiers are automatically placed into the agent's request metadata.

---

### User Story 3 - Configure Processors via YAML (Priority: P3)

As a system operator, I want to configure payload processors per MCP server connection in YAML configuration files, so that I can control which transformations apply to which MCP connections without code changes.

**Why this priority**: Configuration-driven processor attachment makes the feature usable in production without custom code for common cases. It enables per-deployment customization.

**Independent Test**: Define processor configurations in the YAML config for an MCP server, start the system, and verify the processors are instantiated and attached to the correct MCP tools.

**Acceptance Scenarios**:

1. **Given** a YAML configuration listing processors under an MCP server entry, **When** the system starts and discovers MCP tools, **Then** the processor chain is built and attached to all tools from that server.
2. **Given** processor configuration includes custom parameters, **When** the processor is instantiated, **Then** the parameters are available to the processor for its logic.
3. **Given** a processor class name that cannot be resolved, **When** the system starts, **Then** a clear error is raised indicating the processor was not found.

---

### User Story 4 - Hide Processor-Managed Fields from LLM (Priority: P4)

As a platform developer, I want fields that are always set by processors (e.g., trace ID, user ID) to be hidden from the tool schema shown to the LLM, so that the LLM does not waste tokens reasoning about system metadata fields it cannot meaningfully fill.

**Why this priority**: This is an optimization that reduces token usage and improves LLM reasoning quality. It is valuable but not required for the core processor functionality to work.

**Independent Test**: Configure a processor with managed fields, verify the LLM-facing tool schema does not include those fields, and verify the outgoing MCP payload still contains them after processor execution.

**Acceptance Scenarios**:

1. **Given** a processor declares certain fields as "managed", **When** the system prepares the tool schema for the LLM, **Then** those fields are excluded from the schema.
2. **Given** managed fields are hidden from the LLM, **When** the MCP call is made, **Then** the processor still populates those fields in the outgoing payload.

---

### Edge Cases

- What happens when a processor raises an error during pre_call? The error is propagated to the agent as a tool execution failure; the MCP call is not made.
- What happens when a processor raises an error during post_call? The error is propagated to the agent; the raw MCP result is lost.
- What happens when the processor chain is empty (configured but no processors listed)? The system behaves as if no processors are configured — pass-through.
- What happens when two processors modify the same payload field? The last processor in the chain wins (ordered execution).
- What happens when request metadata is not set but a processor expects it? The processor receives an empty dictionary and must handle the absence gracefully (use defaults).
- What happens when a processor modifies a field that the MCP server does not expect? The modified payload is sent as-is; the MCP server's schema validation applies on the receiving end.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide an extensible processor mechanism that can transform outgoing MCP tool call payloads before they are sent
- **FR-002**: Processors MUST execute as an ordered chain: each processor receives the payload from the previous one
- **FR-003**: Processors MUST have access to the current agent's execution context and configuration at call time
- **FR-004**: The system MUST support an optional post-call processing step that can transform the MCP response after it is received
- **FR-005**: Post-call processors MUST execute in reverse order of the chain (last-in-first-out)
- **FR-006**: The agent's execution context MUST support carrying arbitrary request-scoped metadata (key-value pairs) that processors can read
- **FR-007**: Request metadata MUST be populatable at agent creation time from external sources (incoming request headers, MCP call arguments, orchestrator-provided values)
- **FR-008**: Processors MUST be configurable per MCP server connection via the system's YAML configuration
- **FR-009**: Each processor configuration MUST support a class reference (for resolution) and an arbitrary parameters map
- **FR-010**: The system MUST provide built-in processors for trace context injection and user identity injection
- **FR-011**: Custom processors MUST be discoverable via the same auto-registration pattern used by other extensible components in the system
- **FR-012**: Processors MUST be able to declare "managed fields" that are hidden from the LLM-facing tool schema but present in the outgoing MCP payload
- **FR-013**: When no processors are configured, the system MUST behave identically to the current implementation (full backward compatibility)
- **FR-014**: Processor errors during pre_call MUST prevent the MCP call from being made and surface the error to the agent
- **FR-015**: The agent creation entry points (REST API handler, MCP endpoint handler) MUST populate request metadata from incoming request context

### Key Entities

- **Payload Processor**: An extensible component that transforms MCP call payloads before sending and optionally transforms responses after receiving. Has a configuration parameters map and a list of managed fields.
- **Processor Chain**: An ordered collection of processors attached to an MCP server connection. Executes pre_call top-down and post_call bottom-up.
- **Processor Configuration**: A definition in YAML specifying the processor class, its parameters, and its managed fields. Configured per MCP server.
- **Request Metadata**: An arbitrary key-value store on the agent's execution context, populated at agent creation time and readable by processors during MCP calls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Trace identifiers injected by processors appear in 100% of outgoing MCP call payloads when a trace processor is configured
- **SC-002**: Request-scoped metadata set at agent creation is accessible to processors in 100% of MCP tool calls during that agent's execution
- **SC-003**: Existing deployments without processor configuration experience zero behavioral changes (full backward compatibility)
- **SC-004**: Custom processors can be added and configured without modifying any core system files — only YAML configuration and the custom processor code
- **SC-005**: Fields declared as "managed" by processors are absent from the LLM-facing tool schema in 100% of cases, reducing token usage for those fields to zero

## Assumptions

- The processor pattern applies only to outgoing MCP tool calls made by the agent, not to incoming MCP requests received by the MCP server endpoint
- Processors run in the same async execution context as the agent — no separate threads or processes
- The processor chain is built once at tool discovery time (server startup) and reused for all calls to tools from that MCP server
- Request metadata is set once at agent creation and is immutable during the agent's execution (processors read but do not write to it)
- The initial implementation includes two built-in processors (trace context, auth/user context) as reference implementations; additional processors are user-provided
- Processor configuration follows the existing configuration cascade: global config can be overridden at the agent level
- The `post_call` processing step is optional — processors that only need pre-call transformation can skip it
- This feature depends on the MCP ask endpoint (feature 001) being implemented for the MCP-to-MCP metadata propagation path
