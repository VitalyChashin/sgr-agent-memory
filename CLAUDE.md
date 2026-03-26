# CLAUDE.md — SGR Agent Core

## Project Overview

SGR Agent Core is an open-source agentic framework for building intelligent research agents using Schema-Guided Reasoning (SGR). It provides a `BaseAgent` interface with a two-phase architecture (Reasoning → Action), multiple agent implementations, an extensible Pydantic-based tool system, and an OpenAI-compatible REST API with SSE streaming.

- **Repository:** `vamplabAI/sgr-agent-core`
- **Version:** 0.6.0
- **License:** MIT
- **Python:** ≥ 3.11
- **Package manager:** `uv` (preferred) or pip

## Architecture

### Two-Phase Execution Cycle

Every agent follows: `while not finished → reasoning_phase() → select_action_phase() → action_phase()`. The loop runs until a terminal state (`COMPLETED`, max iterations, or error).

### Agent Types

| Agent | Strategy |
|-------|----------|
| `SGRAgent` | Pure Structured Output — LLM returns reasoning + tool selection in a single JSON response |
| `ToolCallingAgent` | Pure native Function Calling — no explicit reasoning phase |
| `SGRToolCallingAgent` | Hybrid (recommended) — explicit SGR reasoning via `ReasoningTool`, then FC for tool selection |
| `Research*` variants | Same strategies with pre-configured research tool sets |

### Tool System

All tools inherit from `BaseTool` (Pydantic model). Tools auto-register in `ToolRegistry` on class creation. MCP tools use `MCPBaseTool`. Tool filtering uses BM25 ranking.

### Configuration Hierarchy

`GlobalConfig` → `AgentDefinition` → `AgentConfig`. YAML-based: `config.yaml` (global), `agents.yaml` (per-agent overrides), `logging_config.yaml`.

## Code Layout

```
sgr_agent_core/           # Core Python package
├── base_agent.py         # BaseAgent — parent of all agents
├── agents/               # Agent implementations (SGRAgent, ToolCallingAgent, etc.)
├── server/               # FastAPI server, routes, SSE streaming
├── tools/                # Built-in tools (BaseTool, WebSearchTool, etc.)
├── prompts/              # Default prompt templates
└── cli/                  # sgrsh interactive CLI
```

## REST API (FastAPI, port 8010)

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Health check |
| `GET /v1/models` | List agent models |
| `POST /v1/chat/completions` | Main chat endpoint (OpenAI-compatible) |
| `GET /agents` | List active agents |
| `GET /agents/{id}` | Agent state |
| `POST /agents/{id}/provide_clarification` | Resume waiting agent |

## MCP Integration

MCP servers are configured in `config.yaml` under `mcp.mcpServers`. MCP tools use `MCPBaseTool` which handles calls through the MCP client and converts them to the framework's tool format. The framework uses `fastmcp` ≥ 2.12.4 for MCP server integration.

## Development Commands

```bash
uv sync                    # Install dependencies
pytest                     # Run tests
pytest --cov               # Tests with coverage
ruff check .               # Lint
ruff format .              # Format
sgr --config-file <path>   # Start server
sgrsh                      # Interactive CLI
```

## Key Dependencies

`pydantic` ≥ 2.0, `openai` ≥ 1.0, `fastapi` ≥ 0.116.1, `uvicorn` ≥ 0.35.0, `fastmcp` ≥ 2.12.4, `tavily-python` ≥ 0.3.0, `rank-bm25` ≥ 0.2.2, `httpx[socks]` ≥ 0.25.0.

## Conventions

- **Target Python:** 3.12 (`target-version = "py312"`)
- **Line length:** 120
- **Linter/formatter:** Ruff
- **Test runner:** pytest with `asyncio_mode = "auto"`
- **Type checking:** mypy
- **Tool naming:** `snake_case` for `tool_name`, `PascalCase` for class names. Auto-conversion supported.
- **Imports:** Tools and agents must be imported within project scope to appear in registries.

## Spec-Driven Development

This project uses GitHub Spec Kit for feature development. All new features follow the workflow:

1. Read the constitution at `.specify/memory/constitution.md`
2. Specs live in `specs/NNN-feature-name/spec.md`
3. Plans live alongside specs as `plan.md`
4. Tasks live as `tasks.md`
5. Use `/speckit.*` slash commands to drive the workflow

When implementing tasks from `tasks.md`, work through them sequentially unless marked `[P]` (parallelizable). Each task is scoped to be implementable and testable in isolation.
