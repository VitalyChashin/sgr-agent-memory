# Research: MCP Payload Processor

**Date**: 2026-03-25

## R1: Intervention point in MCPBaseTool.__call__()

**Decision**: Insert processor chain between `self.model_dump(mode="json")` (line 68 of base_tool.py) and `self._client.call_tool(self.tool_name, payload)` (line 71).

**Rationale**: This is the only point where the payload exists as a dict and both `context` and `config` are available. The pattern mirrors middleware in web frameworks — transform input before handler, transform output after.

**Alternatives considered**: Overriding `model_dump()` — rejected because it doesn't have access to `context` or `config`. Modifying the agent's select-action phase — rejected because it would couple processor logic to agent types.

## R2: ProcessorRegistry auto-registration

**Decision**: Use `__init_subclass__` pattern identical to `ToolRegistryMixin` (base_tool.py lines 20-24).

**Rationale**: Existing pattern proven in codebase. Processors auto-register when their module is imported. Custom processors use import strings resolved the same way as tools and agents.

## R3: MCPConfig extension for payload_processors

**Decision**: Use the existing `extra="allow"` on `fastmcp.MCPConfig` to add `payload_processors` key per server entry in YAML.

**Rationale**: The `fastmcp` library's `MCPConfig` model allows extra fields. This avoids forking or wrapping the external model. During `build_tools_from_mcp()`, we extract `payload_processors` from the raw config dict before passing to the fastmcp Client.

**Alternatives considered**: Wrapping MCPConfig with our own model — rejected as unnecessary complexity. Adding a separate config section — rejected as it would break the config cascade (P4).

## R4: Request metadata propagation

**Decision**: Add `request_metadata: dict[str, Any]` field to `AgentContext` and a `request_metadata` parameter to `AgentFactory.create()`.

**Rationale**: `AgentContext` already has `custom_context: dict | BaseModel | None` for project-specific data, but that field is user-facing. `request_metadata` is system-level and should be separate. The factory method is the single creation point for agents.

## R5: Managed fields schema filtering

**Decision**: Override `model_json_schema()` on MCPBaseTool subclasses to exclude fields listed in `_managed_fields` ClassVar.

**Rationale**: The LLM sees tool schemas via `pydantic_function_tool()` or SGR structured output which calls `model_json_schema()`. Overriding this method at the class level (set during `build_tools_from_mcp()`) transparently hides managed fields from the LLM while keeping them on the model for processor population.
