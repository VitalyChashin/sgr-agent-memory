# SGR Agent Core — Project Constitution

> **Version:** 1.0.0  
> **Last updated:** 2026-03-25

## Purpose

This constitution defines the non-negotiable principles governing how specifications become code in the SGR Agent Core project. All specs, plans, and implementations must comply with these principles.

---

## Architectural Principles

### P1: Two-Phase Agent Architecture

Every agent implementation must follow the `BaseAgent` two-phase execution cycle: Reasoning Phase → Action Phase (Select + Call). New agent types must implement `_reasoning_phase()`, `_select_action_phase()`, and `_action_phase()`. No agent may bypass this contract.

### P2: Tool-Centric Capability Model

All agent capabilities are expressed as tools inheriting from `BaseTool`. A tool is a Pydantic model with `tool_name`, `description`, and `__call__()`. Tools auto-register in `ToolRegistry`. There are no side-channel capability mechanisms — if an agent can do something, there is a tool for it.

### P3: OpenAI API Compatibility

The REST API must remain a drop-in replacement for the OpenAI `/v1/chat/completions` format. Any new endpoints may extend the API surface but must never break existing OpenAI-compatible clients.

### P4: Configuration Cascades

All configuration follows the `GlobalConfig → AgentDefinition → AgentConfig` hierarchy. Agent-level settings override global settings. New configurable features must integrate into this cascade, not introduce parallel config mechanisms.

### P5: MCP as the Extension Protocol

External tool integrations use MCP (Model Context Protocol) via `MCPBaseTool` and `fastmcp`. New external integrations should be implemented as MCP servers/tools, not as bespoke integrations, unless MCP is technically infeasible for the use case.

---

## Code Quality Principles

### P6: Python ≥ 3.11, Target 3.12

All code targets Python 3.12 with 3.11 as the minimum supported version. Use modern Python features (type hints, `match` statements, `asyncio`).

### P7: Pydantic for All Data Contracts

Request/response models, tool schemas, configuration objects, and agent state must be Pydantic models. No raw dicts for structured data at API boundaries.

### P8: Async-First

All agent execution, tool calls, and API handlers are async. Synchronous blocking calls are prohibited in the hot path.

### P9: Test Coverage for New Features

Every new feature must include pytest tests. Use `asyncio_mode = "auto"`. Integration tests for API endpoints, unit tests for tools and agent logic.

### P10: Ruff for Linting and Formatting

Code style is enforced by Ruff with `line-length = 120`. No exceptions. Run `ruff check .` and `ruff format .` before committing.

---

## API & Protocol Principles

### P11: SSE Streaming by Default

All long-running agent operations must support Server-Sent Events streaming. Clients should receive incremental progress, not just a final response.

### P12: Stateful Agents, Stateless API

Agents are stateful (they maintain conversation history, state machine, sources). The API is stateless — agent state is keyed by `agent_id` and can be retrieved or resumed. No server-side session state beyond the agent registry.

### P13: Backward-Compatible Versioning

New API endpoints and tool fields are additive. Existing fields must not change semantics. Breaking changes require a new API version prefix.

---

## Development Process Principles

### P14: Spec-Driven Feature Development

Non-trivial features (new agent types, new endpoints, tool system changes) follow the Spec Kit workflow: constitution → specify → plan → tasks → implement. Bug fixes and minor changes may skip the full workflow.

### P15: Registry Auto-Discovery

Agents and tools must be auto-discoverable via their respective registries (`AgentRegistry`, `ToolRegistry`). If a class is defined and imported, it is available. No manual registration steps.

---

## Sync Impact Report

All templates (`spec-template.md`, `plan-template.md`, `tasks-template.md`) are aligned with this constitution as of v1.0.0. Changes to principles P1–P5 (MAJOR) require re-validation of all active specs.
