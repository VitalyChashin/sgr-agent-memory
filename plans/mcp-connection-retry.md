---
title: Implementation plan — MCP connection retry (build + call)
status: archived
created: 2026-06-18
updated: 2026-06-18  # implemented: services/retry.py + ExecutionConfig.mcp_retry + both wrap sites + tests
owner: Vitaly Chashin
supersedes: []
related:
  - sgr_agent_core/services/retry.py
  - sgr_agent_core/services/mcp_service.py
  - sgr_agent_core/base_tool.py
  - sgr_agent_core/agent_definition.py
  - tests/test_mcp_retry.py
  - notes/mcp-retry.md
tags: [mcp, resilience, retry, plan]
---

> **Status: implemented (2026-06-18).** All steps landed as planned: hand-rolled
> `services/retry.py` (no new dependency), `MCPRetryConfig` on
> `ExecutionConfig.mcp_retry`, build- and call-time wrap sites, 20 tests in
> `tests/test_mcp_retry.py` (incl. call-time integration), docs in `CLAUDE.md` /
> `config.yaml.example` / `notes/mcp-retry.md`. Build default stayed fatal
> (`degrade_on_build_failure: false`).

# Plan: MCP connection retry

Today there is **no retry** anywhere on the MCP path. Two failure surfaces:

| Surface | Location | Current behavior |
|---------|----------|------------------|
| **Build** | `MCP2ToolConverter.build_tools_from_mcp` (`services/mcp_service.py:80-82`) — `Client(config)` + `async with client: list_tools()` | Unguarded. Connection failure propagates through `agent_factory.py:147` → **agent creation aborts (fatal)**. |
| **Call** | `MCPBaseTool.__call__` (`base_tool.py:87-108`) — `async with self._client: call_tool(...)` | Wrapped in try/except → error **swallowed** into `"Error: {e}"` result string, loop continues. No retry. |

Goal: add a small, dependency-free, bounded retry-with-backoff around both
connect points, retrying **only transient connection/transport errors** and
never genuine tool-logic errors.

---

## Key design decisions

### 1. No new dependency — hand-rolled async retry helper
`tenacity` is **not** installed and the project is deliberately lean. A ~25-line
`async` retry helper covers exactly our needs (fixed count, exponential backoff,
an explicit retryable-exception predicate). Tenacity is the alternative if we
later want jitter/decorator ergonomics, but it's overkill here.

### 2. Retry only transient errors — never `ToolError`
fastmcp's exception taxonomy (verified in `.venv/.../fastmcp/exceptions.py`):

- `FastMCPError` → `ToolError`, `ValidationError`, `ResourceError`, `PromptError`,
  `AuthorizationError` — these mean *the call reached the server and failed on
  logic/auth/args*. **Do not retry** (retrying a deterministically-failing tool
  just wastes wall-clock and can double side-effects).
- Transient transport/connection failures surface as: `mcp.McpError`,
  `httpx.HTTPError` (ConnectError/ReadTimeout/etc.), builtin `ConnectionError`,
  `TimeoutError`, `OSError`, and fastmcp's `RuntimeError("Failed to initialize
  server session")` / `RuntimeError("Server session was closed unexpectedly")`.

**Retryable set (explicit allow-list):**
```python
RETRYABLE_MCP_EXC = (ConnectionError, TimeoutError, OSError, McpError, httpx.HTTPError)
```
`RuntimeError` is the awkward case: fastmcp wraps init/session failures in bare
`RuntimeError`. We retry `RuntimeError` **only** when its message matches a known
transient signature (`"Failed to initialize server session"`,
`"Server session was closed"`) — a small substring check in the predicate, so we
don't blanket-retry every `RuntimeError`. `ToolError`/`FastMCPError` are never in
the allow-list, so they fall straight through.

### 3. Build-time stays fatal after retries exhausted (recommended)
Fail-fast at startup is the right default: if the MCP server is down when the
agent is built, silently starting an agent that's missing half its tools is more
confusing than a clear startup error. So: **retry, then re-raise.** A
`degrade_on_build_failure` flag (default `False`) is included as a documented
escape hatch — when `True`, build logs a WARNING and returns the non-MCP tools
only instead of raising. (Open question for review — see Gaps.)

### 4. Config lives in `ExecutionConfig`
`agent_definition.py:166` `mcp: MCPConfig` is **fastmcp's own** model — we don't
own it and shouldn't add fields there. Retry knobs go in app-owned
`ExecutionConfig` (`agent_definition.py:135`), right next to `mcp_context_limit`,
under an `mcp_retry` sub-model so they're grouped.

---

## Implementation

### Step 1 — Retry helper + config model
New file `sgr_agent_core/services/retry.py`:

```python
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import httpx
from mcp.shared.exceptions import McpError

logger = logging.getLogger(__name__)
T = TypeVar("T")

RETRYABLE_MCP_EXC: tuple[type[BaseException], ...] = (
    ConnectionError, TimeoutError, OSError, McpError, httpx.HTTPError,
)
_RETRYABLE_RUNTIME_SIGNATURES = ("Failed to initialize server session",
                                 "Server session was closed")


def is_retryable_mcp_error(exc: BaseException) -> bool:
    if isinstance(exc, RETRYABLE_MCP_EXC):
        return True
    if isinstance(exc, RuntimeError):
        return any(sig in str(exc) for sig in _RETRYABLE_RUNTIME_SIGNATURES)
    return False


@dataclass
class RetryPolicy:
    attempts: int = 3          # total tries (1 = no retry)
    base_delay: float = 0.5    # seconds, first backoff
    max_delay: float = 8.0
    backoff_factor: float = 2.0


async def with_mcp_retry(
    fn: Callable[[], Awaitable[T]], policy: RetryPolicy, *, what: str,
) -> T:
    delay = policy.base_delay
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below unless retryable
            if attempt >= policy.attempts or not is_retryable_mcp_error(exc):
                raise
            logger.warning(
                "MCP %s failed (attempt %d/%d): %s — retrying in %.1fs",
                what, attempt, policy.attempts, exc, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * policy.backoff_factor, policy.max_delay)
    raise AssertionError("unreachable")  # loop either returns or raises
```

Add the config sub-model in `agent_definition.py` (above `ExecutionConfig`):
```python
class MCPRetryConfig(BaseModel):
    attempts: int = Field(default=3, ge=1, description="Total MCP connect tries (1 disables retry)")
    base_delay: float = Field(default=0.5, gt=0, description="First backoff delay (seconds)")
    max_delay: float = Field(default=8.0, gt=0, description="Backoff ceiling (seconds)")
    backoff_factor: float = Field(default=2.0, ge=1.0, description="Exponential backoff multiplier")
    degrade_on_build_failure: bool = Field(
        default=False,
        description="If True, a failed MCP build logs a warning and returns non-MCP tools instead of raising",
    )
```
And in `ExecutionConfig`:
```python
mcp_retry: MCPRetryConfig = Field(default_factory=MCPRetryConfig, description="MCP connection retry policy")
```
`extra="allow"` on `ExecutionConfig` means existing YAML without this key keeps
working (defaults apply). Add a `RetryPolicy.from_config()` adapter or build the
`RetryPolicy` inline from these fields.

### Step 2 — Build-time retry (`services/mcp_service.py`)
Wrap the connect+`list_tools()` block. Note the current code does work *inside*
the `async with client:` (it iterates `mcp_tools` and stores `ToolCls._client = client`
**while the context is open**). Retrying must re-open a fresh `Client` each
attempt, so the retried unit is "open client → list_tools → snapshot the tool
metadata", returning the tool list out of the `async with`.

Minimal-diff approach: extract the existing body into an inner
`async def _connect_and_build()` and call it via `with_mcp_retry`. Pull the
`mcp_retry` policy from `GlobalConfig().execution.mcp_retry`.

```python
policy = RetryPolicy(**GlobalConfig().execution.mcp_retry.model_dump(exclude={"degrade_on_build_failure"}))
try:
    return await with_mcp_retry(_connect_and_build, policy, what=f"build_tools({server_names})")
except Exception:
    if GlobalConfig().execution.mcp_retry.degrade_on_build_failure:
        logger.warning("MCP tool build failed after retries; continuing without MCP tools", exc_info=True)
        return []
    raise
```

> ⚠️ **Client lifetime caveat** (see `notes/`): `ToolCls._client` is the `Client`
> built here. The tool layer re-enters `async with self._client` per call, so the
> stored client just needs to be re-openable — which fastmcp's `Client` supports.
> Retry must not leave a half-open client: `with_mcp_retry` only returns the tool
> list (plain data), and each attempt's `async with` fully closes on exception, so
> a failed attempt leaves nothing dangling. Verify no `ToolCls._client` escapes
> pointing at a client whose context exited — it shouldn't, since assignment
> happens inside the successful attempt's `async with`.

### Step 3 — Call-time retry (`base_tool.py`)
Wrap **only** the `async with self._client: call_tool(...)` connect+invoke in the
retry, keeping the outer try/except that swallows the *final* failure into
`"Error: {e}"` so loop semantics are unchanged.

```python
policy = RetryPolicy(**global_config.execution.mcp_retry.model_dump(exclude={"degrade_on_build_failure"}))

async def _invoke():
    async with self._client:
        result = await self._client.call_tool(self.tool_name, payload)
        return json.dumps([m.model_dump_json() for m in result.content], ensure_ascii=False)[
            : global_config.execution.mcp_context_limit
        ]

try:
    result_str = await with_mcp_retry(_invoke, policy, what=f"call_tool({self.tool_name})")
    if self._processor_chain:
        result_str = await self._processor_chain.run_post_call(...)
    return result_str
except Exception as e:
    logger.error(...)  # unchanged: tag trace, mcp_call_errored.set(True), return f"Error: {e}"
```
Because `ToolError`/`FastMCPError` are not retryable, a tool that legitimately
errors still fails on the **first** attempt and is swallowed exactly as today —
only transport blips get the retry budget.

> Note: `run_pre_call` runs **once** before the retry loop (payload mutation is
> idempotent-by-design and shouldn't repeat per attempt). `run_post_call` runs
> once after success. Keep them outside `_invoke`.

### Step 4 — Tests (`tests/test_mcp_retry.py`)
Unit-test the helper directly (no live MCP needed):
- `is_retryable_mcp_error`: `McpError`/`ConnectionError`/`httpx.ConnectError`/
  transient-`RuntimeError` → True; `ToolError`/`ValueError`/generic `RuntimeError`
  → False.
- `with_mcp_retry`: succeeds-on-2nd-attempt (fn raises `McpError` once then
  returns); exhausts and re-raises after `attempts`; **does not retry** a
  `ToolError` (fn called exactly once); respects `attempts=1` (no retry).
- Backoff: monkeypatch `asyncio.sleep` to record delays, assert exponential
  growth capped at `max_delay`.

Integration (mock the `Client`):
- Build: `Client` whose `list_tools` raises `McpError` N-1 times then succeeds →
  tools built; raises every time → `build_tools_from_mcp` raises (default) /
  returns `[]` (when `degrade_on_build_failure=True`).
- Call: `call_tool` raises `ConnectionError` once then succeeds → real result
  returned, not `"Error:"`; raises every time → `"Error: ..."` and
  `mcp_call_errored` set.

### Step 5 — Docs
- `agents.yaml.example` / `config.yaml.example`: show an `execution.mcp_retry:`
  block with the defaults and a one-line comment.
- `CLAUDE.md` "MCP Integration" section: one sentence that MCP connects retry
  transient transport errors per `execution.mcp_retry` (build re-raises by
  default; calls swallow after retries).
- `notes/mcp-retry.md`: record the two settled caveats — (a) `ToolError` is
  deliberately non-retryable; (b) `run_pre_call` runs once, not per attempt.

---

## File-by-file change list

| File | Change |
|------|--------|
| `sgr_agent_core/services/retry.py` | **new** — `RetryPolicy`, `is_retryable_mcp_error`, `with_mcp_retry` |
| `sgr_agent_core/agent_definition.py` | **new** `MCPRetryConfig`; add `mcp_retry` field to `ExecutionConfig` |
| `sgr_agent_core/services/mcp_service.py` | wrap connect+`list_tools` in retry; optional degrade path |
| `sgr_agent_core/base_tool.py` | wrap `async with client: call_tool` in retry; keep outer swallow |
| `tests/test_mcp_retry.py` | **new** — helper unit tests + mocked build/call integration |
| `*.yaml.example`, `CLAUDE.md`, `notes/mcp-retry.md` | docs |

## Estimated size
~120 LOC source (helper + config + 2 wrap sites) + ~150 LOC tests. No new
runtime dependency.

## Risks / gaps
- **`RuntimeError` message-matching is brittle** — fastmcp could reword its init
  error strings on upgrade. Mitigation: keep the signature list small and
  documented; worst case a transient init error simply isn't retried (degrades to
  today's behavior, not worse). → file to `plans/gaps/` if we want a sturdier signal.
- **Build fatal-vs-degrade default** — recommending fatal (re-raise). Confirm
  this matches operational expectations before implementing; flip the default if
  startup resilience is valued over fail-fast.
- **Idempotency of `call_tool` retries** — retrying a tool with side effects could
  double-execute *if* the failure happened server-side after the effect. We only
  retry transport-class errors (connection refused/reset/timeout-on-connect),
  which almost always mean the call didn't land — but a read-timeout *after* the
  server acted is theoretically double-executable. Acceptable for the transient
  set; documented in `notes/`.
