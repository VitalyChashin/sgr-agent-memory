# Research: Custom MCP Tool Name and Description

**Date**: 2026-03-25

## R1: FastMCP @mcp.tool() decorator parameters

**Decision**: Use `name` and `description` parameters of `@mcp.tool()` to set custom tool identity.

**Rationale**: FastMCP's `@mcp.tool(name="custom", description="Custom desc")` directly supports overriding both name and description. No workaround needed.

**Alternatives considered**: Renaming the Python function — rejected because it would break internal references and the function name is irrelevant to MCP clients.
