# Feature Specification: MCP Ask Endpoint

**Feature Branch**: `185-mcp-ask-endpoint`
**Created**: 2026-03-25
**Status**: Draft
**Input**: User description: "Add an MCP endpoint to SGR Agent Core that exposes a single tool called `ask`, wrapping existing chat completions functionality with extended payload for tracing and user context."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Send Research Query with Trace Context (Priority: P1)

As an MCP client (another agent, orchestrator, or IDE integration), I want to call the SGR Agent's `ask` tool via the MCP protocol with a query, trace identifier, and user identifier, so that I receive a structured response with the agent's answer and the trace identifier echoed back for correlation.

**Why this priority**: This is the core value of the feature — enabling MCP clients to interact with SGR agents. Without this, the feature delivers no functionality.

**Independent Test**: Can be fully tested by calling the `ask` tool with a query string and verifying the response contains the agent's answer and the echoed trace identifier.

**Acceptance Scenarios**:

1. **Given** the MCP server is running with a configured agent, **When** a client calls the `ask` tool with a query, traceId, and userId, **Then** the response contains the agent's text answer and the same traceId echoed back.
2. **Given** the MCP server is running, **When** a client calls the `ask` tool with only a query (no traceId or userId), **Then** the response uses default values for traceId (`"trace-default-001"`) and userId (`"user-default-001"`), and the default traceId is echoed in the response.
3. **Given** the MCP server is running, **When** a client calls the `ask` tool with an empty query string, **Then** the system returns an error indicating the query must not be empty.
4. **Given** the MCP server is running, **When** the agent encounters an error during execution (e.g., LLM timeout), **Then** the system returns an error message to the client without crashing the server.

---

### User Story 2 - MCP Server Co-Starts with REST API (Priority: P2)

As a system operator, I want the MCP server to be configurable and start alongside the existing REST API server, so that I don't need separate deployment infrastructure for MCP access.

**Why this priority**: Deployment integration is essential for production use but is secondary to the core tool functionality. The `ask` tool can be developed and tested independently of co-hosting.

**Independent Test**: Can be tested by starting the system with MCP enabled in configuration and verifying both the REST API and MCP server accept connections on their respective ports.

**Acceptance Scenarios**:

1. **Given** the MCP server is enabled in configuration, **When** the system starts, **Then** both the REST API and MCP server start concurrently in the same process.
2. **Given** the MCP server is disabled in configuration (default), **When** the system starts, **Then** only the REST API starts; no MCP listener is active.
3. **Given** both servers are running, **When** the system shuts down, **Then** both servers stop cleanly.
4. **Given** the MCP server is enabled, **When** the system starts, **Then** logs indicate the MCP server status including host and port.

---

### User Story 3 - Extensible Payload Schema (Priority: P3)

As a developer extending the platform, I want the request and response payloads to use extensible data models, so that new fields can be added in the future without breaking existing clients.

**Why this priority**: Extensibility is a design quality that supports long-term evolution. It doesn't deliver direct user value on its own but prevents future breaking changes.

**Independent Test**: Can be tested by sending a request with additional unknown fields and verifying they are accepted and preserved in the data model without validation errors.

**Acceptance Scenarios**:

1. **Given** a request payload with the required query field plus additional unknown fields, **When** the system processes the request, **Then** the unknown fields are accepted without validation errors.
2. **Given** a response payload model, **When** new fields are added to it, **Then** existing clients that don't read those fields continue to work without changes.

---

### Edge Cases

- What happens when the query is an empty string or whitespace only? The system returns an error: "Query must not be empty."
- What happens when the agent execution fails due to an LLM error or timeout? The system returns an error with the exception message to the client; the server remains operational.
- What happens when the MCP server is disabled in configuration? The system starts only the REST API with no MCP listener, identical to current behavior.
- What happens when a very long query is submitted? The query is passed through to the agent; the agent's own iteration and token limits apply.
- What happens when traceId or userId are omitted? Default values are used: `"trace-default-001"` for traceId and `"user-default-001"` for userId.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose an MCP server that registers exactly one tool called `ask`
- **FR-002**: The `ask` tool MUST accept a required query parameter (text string) and optional traceId and userId parameters with default values `"trace-default-001"` and `"user-default-001"` respectively
- **FR-003**: The `ask` tool MUST create an agent instance, execute the query, and return the result as a structured response containing the agent's text answer and the echoed traceId
- **FR-004**: The agent used by the `ask` tool MUST be configurable, defaulting to the first available agent definition
- **FR-005**: The response MUST be a structured object containing a `response` field (the agent's text answer) and a `traceId` field (echoed from the request or the default)
- **FR-006**: MCP server configuration MUST be part of the system's global configuration with settings for enabled/disabled, host, port, and transport type
- **FR-007**: The MCP server MUST start alongside the existing REST API when enabled in configuration, running concurrently in the same process
- **FR-008**: Errors during agent execution MUST be caught and returned as tool-level errors to the client without crashing the server
- **FR-009**: The `ask` tool MUST reject empty or whitespace-only queries with a clear error message
- **FR-010**: Request and response data models MUST support additional unknown fields without validation errors, enabling future extensibility

### Key Entities

- **Ask Request**: Represents an incoming query to the MCP ask tool. Contains the query text, a trace identifier for request correlation, and a user identifier for context. Supports additional fields for future extensibility.
- **Ask Response**: Represents the structured response from the ask tool. Contains the agent's text answer and the echoed trace identifier. Supports additional fields for future extensibility.
- **MCP Server Configuration**: Represents the operational settings for the MCP server. Contains enabled flag, network host, port, transport type, and optional default agent reference.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: MCP clients can send a query and receive a structured response with the agent's answer within the same time frame as the equivalent REST API call
- **SC-002**: The MCP server starts and accepts connections without impacting the existing REST API's availability or response times
- **SC-003**: 100% of requests with trace identifiers have the same identifier echoed back in the response
- **SC-004**: Agent execution errors are returned as client-facing error messages in 100% of failure cases, with zero server crashes
- **SC-005**: Requests containing unknown additional fields are processed successfully without validation errors

## Assumptions

- The existing MCP library dependency (fastmcp >= 2.12.4) is sufficient for the MCP server implementation
- The initial implementation is non-streaming; streaming MCP support is out of scope
- Default values for traceId and userId are hardcoded constants; dynamic population is a future enhancement
- The MCP server runs in the same process as the REST API server using asynchronous co-hosting
- Each `ask` call creates a fresh, stateless agent instance — no session or conversation continuity between calls
- The MCP transport defaults to SSE (Server-Sent Events) with a default port of 8011
