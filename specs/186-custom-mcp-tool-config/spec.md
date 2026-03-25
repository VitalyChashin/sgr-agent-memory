# Feature Specification: Custom MCP Tool Name and Description

**Feature Branch**: `186-custom-mcp-tool-config`
**Created**: 2026-03-25
**Status**: Draft
**Input**: User description: "Expand the MCP config. I need to add a custom tool name (instead of default 'ask'), and custom tool description in agent config."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure Custom Tool Name (Priority: P1)

As a system operator deploying an SGR agent as an MCP server, I want to configure a custom name for the exposed MCP tool instead of the hardcoded default "ask", so that the tool name is meaningful and descriptive within my MCP ecosystem (e.g., "research", "analyze", "summarize").

**Why this priority**: The tool name is how MCP clients discover and call the tool. A hardcoded "ask" name is generic and cannot be tailored to the agent's specific purpose. This is the core value of the feature.

**Independent Test**: Configure a custom tool name in the configuration, start the MCP server, and verify the tool is listed with the custom name when an MCP client queries available tools.

**Acceptance Scenarios**:

1. **Given** a configuration with `tool_name: "research"` in the MCP server section, **When** the MCP server starts, **Then** the server exposes exactly one tool named "research" (not "ask").
2. **Given** no `tool_name` is configured (omitted from config), **When** the MCP server starts, **Then** the server exposes the tool with the default name "ask" (backward compatible).
3. **Given** a configuration with an empty `tool_name`, **When** the system validates configuration, **Then** the system falls back to the default name "ask".

---

### User Story 2 - Configure Custom Tool Description (Priority: P2)

As a system operator, I want to configure a custom description for the MCP tool, so that MCP clients (and their users) understand what the tool does in the context of the specific agent deployment.

**Why this priority**: The tool description helps MCP clients and LLMs understand the tool's purpose and when to use it. It is secondary to the tool name but enhances discoverability and correct usage.

**Independent Test**: Configure a custom description, start the MCP server, and verify the tool's description matches the configured value when listed by an MCP client.

**Acceptance Scenarios**:

1. **Given** a configuration with `tool_description: "Perform deep technical research on a topic"`, **When** the MCP server starts, **Then** the tool's description field contains the configured text.
2. **Given** no `tool_description` is configured, **When** the MCP server starts, **Then** the tool uses a sensible default description.

---

### Edge Cases

- What happens when `tool_name` contains spaces or special characters? The system accepts any valid string; MCP protocol compliance is the only constraint.
- What happens when `tool_name` is set to an empty string? The system falls back to the default name "ask".
- What happens when only `tool_name` is configured but not `tool_description`? Each setting is independent; the description uses its default value.
- What happens when the configuration is changed and the server restarts? The new tool name and description take effect on the next startup.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The MCP server configuration MUST support an optional `tool_name` setting that overrides the default tool name "ask"
- **FR-002**: The MCP server configuration MUST support an optional `tool_description` setting that overrides the default tool description
- **FR-003**: When `tool_name` is not configured or is empty, the system MUST use "ask" as the default tool name
- **FR-004**: When `tool_description` is not configured or is empty, the system MUST use a sensible default description
- **FR-005**: The configured tool name MUST be used when registering the tool with the MCP server, so that MCP clients see the custom name when listing tools
- **FR-006**: The configured tool description MUST be included in the tool's metadata so MCP clients can display it
- **FR-007**: Existing deployments without `tool_name` or `tool_description` in their configuration MUST continue to work without changes (backward compatibility)

### Key Entities

- **MCP Server Configuration**: Extended with two new optional fields: `tool_name` (string, default "ask") and `tool_description` (string, default provided by the system).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: MCP clients listing tools see the configured custom name instead of "ask" in 100% of cases when configured
- **SC-002**: MCP clients listing tools see the configured description in the tool metadata in 100% of cases when configured
- **SC-003**: Existing configurations without the new fields continue to work identically (zero regressions)

## Assumptions

- The tool name and description are static configuration — they do not change at runtime without a server restart
- The MCP protocol does not impose strict constraints on tool name format beyond being a non-empty string
- Only one tool is exposed by the MCP server (regardless of name); multi-tool support is out of scope
- The configuration fields are added to the existing MCP server configuration section, not a new section
