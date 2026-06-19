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

**Connection retry.** Both MCP connect points retry transient transport errors (connection refused/reset, timeouts, broken sessions) per `execution.mcp_retry` (bounded exponential backoff; `services/retry.py`). Genuine tool errors (`ToolError`/`FastMCPError`) are **never** retried. **Build** (`build_tools_from_mcp`) re-raises after retries (fatal startup) unless `mcp_retry.degrade_on_build_failure: true`, which skips MCP tools instead. **Call** (`MCPBaseTool.__call__`) retries then, on final failure, swallows the error into the result string so the loop continues. The payload processor's `pre_call`/`post_call` run **once**, outside the retry loop. See `notes/mcp-retry.md`.

## Processor Plugins

Three processor-plugin families share one skeleton (auto-registering ABC + `*Definition` + `*Chain` + registry-then-import-string resolution; see `notes/processor-plugin-pattern.md`):

| Family | When it runs | Can mutate? |
|--------|--------------|-------------|
| `MCPPayloadProcessor` (`mcp_payload_processor.py`) | per MCP tool call (`pre_call`/`post_call`) | yes |
| `MetricsProcessor` (`observability/metrics/`) | at loop seams | no (observe only, fail-silent) |
| `AgentContextProcessor` (`context_processors/`) | at loop seams | yes (read-write) |

**Agent Context Processors** are configured **per-agent** via the `context_processors` list on `AgentConfig` (per-agent value replaces the global default). They run at three seams: `on_prepare_tools` (drop tools before selection), `on_tool_end`, and `on_before_finish` (veto a premature finish + inject messages). Hooks are fail-safe-but-visible (logged at WARNING; failures never loop the agent forever). Each hook is offered the active observability `provider`/`parent_span` for **optional** per-processor span emission (`emit_event_span`, a no-op under `NoOpProvider`). Built-ins: `RepeatedToolCallGuard`, `MandatoryToolCallProcessor`. Config example: `agents.yaml.example`.

## Local Langfuse (observability testing)

A local Langfuse instance is available for manually inspecting traces at
`http://localhost:3000`. Install the optional SDK with `uv pip install
"langfuse>=2.0.0,<3.0.0"` (the `observability` extra). Local-only dev keys:

```python
from langfuse import Langfuse

langfuse = Langfuse(
    secret_key="sk-lf-fa49d4be-6763-400d-9542-76736c056e65",
    public_key="pk-lf-519ae97e-7f15-42fa-ab0f-42e7be77fd64",
    host="http://localhost:3000",
)
```

Equivalent framework config (enables the `LangfuseProvider` for an agent run):

```yaml
observability:
  enabled: true
  provider: langfuse
  capture_tool_definitions: true   # embed tool defs in generation input (Available-tools section)
  langfuse:
    public_key: "pk-lf-519ae97e-7f15-42fa-ab0f-42e7be77fd64"
    secret_key: "sk-lf-fa49d4be-6763-400d-9542-76736c056e65"
    base_url: "http://localhost:3000"
```

Demo runner that emits a real `ToolCallingAgent` trace: `scripts/langfuse_tool_trace_demo.py`
(needs `OPENAI_API_KEY`). These are throwaway local-instance keys — fine to keep
in-repo, do **not** reuse the pattern for any hosted Langfuse.

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

## Workflow

```
research (if required) → plan → implementation
         ↓                ↓           ↓
                    gaps/ + notes/ (filed at any phase)
```

> **`main` is locked — never commit to it.** Always create a feature branch
> (`NNN-feature-name`, matching the spec number) and land work there. `main`
> tracks the remote; changes reach it only through a reviewed PR/merge.

1. **Research** — investigate unknowns before committing to an approach. Output: `research/<topic>.md`.
2. **Plan** — write an implementation plan before touching code. Output: `plans/<feature>.md`.
3. **Implementation** — execute the plan, following the session start protocol in the affected repo's `CLAUDE.md`.
4. **Gaps** — whenever a gap is noticed (missing spec, unresolved ambiguity, out-of-scope dependency, deferred decision), add a short note to `plans/gaps/<topic>.md`. File gaps at any phase, not just at the end.
5. **Notes** — whenever you hit a durable caveat, gotcha, or non-obvious decision that is **resolved** but worth remembering (a sharp edge, a deliberate choice and its reason), file it to `notes/<topic>.md`. This is a standing rule: keep documenting these as they come up. Gaps are *unresolved/missing*; notes are *settled but worth remembering* — if it's a gap, it goes to `plans/gaps/`, not here. See `notes/README.md`.
6. **Completion** — express lifecycle via the doc's `status:` frontmatter, **not** by moving files (GitMark model; see `notes/gitmark-ontology.md`):
   - Research acted on → set `status: archived` (+ bump `updated:`).
   - Plan fully implemented → set `status: archived`.
   - Gap resolved → set `status: archived` (keep the node so its links survive) rather than deleting.
   - A replacement doc declares `supersedes: [old.md]` (target must already be `deprecated|archived`).
   - **Do not create new `completed/` folders.** The existing `research/completed/` and `plans/completed/` are grandfathered (already carry `status: archived`) — leave them in place, but file new work at the folder root.
