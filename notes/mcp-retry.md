---
title: MCP connection retry — settled design caveats
status: current
created: 2026-06-18
updated: 2026-06-18
related:
  - plans/mcp-connection-retry.md
  - sgr_agent_core/services/retry.py
  - sgr_agent_core/base_tool.py
  - sgr_agent_core/services/mcp_service.py
---

# Note: MCP connection retry (`services/retry.py`)

**2026-06-18 —** Retry was added around both MCP connect points (build +
call). A few deliberate choices that aren't obvious from the code:

- **Only transport errors are retried, never `ToolError`.** The retryable
  allow-list is `(ConnectionError, TimeoutError, OSError, McpError,
  httpx.HTTPError)` plus `RuntimeError` *only* when its message matches a known
  fastmcp session-init/teardown signature. fastmcp's `ToolError`/`FastMCPError`
  family means the call reached the server and failed deterministically — retrying
  wastes wall-clock and risks double side-effects, so it falls straight through on
  the first attempt (and at call time is then swallowed into `"Error: {e}"`,
  exactly as before retry existed).

- **The `RuntimeError` message-match is the brittle part.** fastmcp wraps session
  failures in bare `RuntimeError("Failed to initialize server session")` /
  `"Server session was closed"`. We substring-match those rather than blanket-retry
  every `RuntimeError`. If fastmcp rewords them on upgrade, those failures simply
  stop being retried (degrades to pre-retry behavior, never worse). Re-check
  `_RETRYABLE_RUNTIME_SIGNATURES` after fastmcp bumps.

- **Payload processors run once, outside the retry loop.** In
  `MCPBaseTool.__call__`, only `connect → call_tool` is wrapped; `run_pre_call`
  (before) and `run_post_call` (after success) each run once. Pulling them into the
  retried unit would re-mutate the payload per attempt.

- **Build is fatal by default, call is not.** A failed build re-raises after
  retries (fail-fast at startup beats silently launching an agent missing half its
  tools). `mcp_retry.degrade_on_build_failure: true` flips it to "warn + return
  non-MCP tools". A failed call always swallows after retries so the agent loop
  keeps going — unchanged contract, retry just sits inside the existing try/except.

- **Idempotency caveat for call retries.** We only retry transport-class errors,
  which almost always mean the call never landed. The one theoretical hole: a
  read-timeout *after* the server already acted could double-execute a
  side-effecting tool. Accepted as low-risk for the transient set.

- **Config lives in `ExecutionConfig.mcp_retry`, not `mcp:`.** The `mcp` field is
  fastmcp's own `MCPConfig` model — we don't own it, so the knob sits in app-owned
  `ExecutionConfig` next to `mcp_context_limit`.
