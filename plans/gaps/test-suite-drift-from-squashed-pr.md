---
title: Test-suite drift from squashed PR #188/#189/#190
status: open
created: 2026-06-18
updated: 2026-06-18
owner: Vitaly Chashin
related:
  - plans/langfuse-error-trace-filtering.md
  - tests/test_langfuse_provider.py
  - tests/test_trace_correlation.py
tags: [tests, observability, tech-debt, gap]
---

# Gap: test-suite drift introduced by squashed PR `1f5d23a`

`git log` shows the entire observability + metrics test surface landed in a single
squashed commit `1f5d23a` ("Add Langfuse observability, trace enrichment, and metrics
processor plugin system (#188, #189, #190)"). Several test modules were written against
APIs that the *implementation* in that same commit does not provide — so they have
**never passed** on this branch. Baseline (before the Langfuse error-filtering work):
**26 failed, 13 errors**.

## Resolved here (Deliverable A reconciliation)

- `tests/test_langfuse_provider.py` (was 13 errors) — **rewritten to the shipped v2
  provider API.** Now 16 passing.
- `tests/test_trace_correlation.py` (was 3 failed) — **rewritten to v2.** Now 3 passing.

Both were written for a **Langfuse v3 (OTEL) provider** that was never merged:
`client.start_as_current_observation()`, a module-level `propagate_attributes`, and
handles with `.observation` / `.context_manager` / `.propagate_ctx`. The shipped
provider is v2 (`client.trace()`, `trace.span()`, `span.end()`, handle `.trace`), and
`pyproject.toml` pins `langfuse>=2.0.0,<3.0.0`. Reconciliation chose **tests → v2**
(match shipped, pinned, used code) rather than a v3 migration.

## Still open (out of scope for the Langfuse error-filtering feature)

Independent drift categories, each unrelated to error-trace filtering:

| Test(s) | Symptom | Likely cause |
|---|---|---|
| `test_observability_config::test_custom_values` | `assert SecretStr('***') == 'sk-lf-test'` | test asserts plain `secret_key`; impl wraps in `SecretStr` |
| `test_mcp_server.py` (many), `test_mcp_models.py` (2) | `'FastMCP' object has no attribute 'list_tools'` | fastmcp API drift (method renamed/removed) |
| `test_base_agent::TestBaseAgentCancellation` (2) | mock `slow_execution_step()` rejects `iter_span`/`metrics_chain`/`trace` kwargs | `_execution_step` signature evolved; test mock didn't |
| `test_llm_instrumentation::TestAgentFactoryClientCreation` (2) | patches `agent_factory.get_provider` (absent) | `get_provider` imported in a different module |
| `test_tools::test_run_command_tool_uses_workspace_path_as_cwd` | cwd assertion | environment/path drift (separate) |

## Recommended next step

Treat as a dedicated test-hygiene pass (own branch). For each row: decide test-vs-impl
source of truth (mostly **test → match impl**, as with the v2 reconciliation), or open
a real bug if the impl is wrong (e.g. confirm `SecretStr` is intended for `secret_key`).
A future Langfuse **v2 → v3 migration** is the only case warranting impl changes — track
that separately if/when the dependency is bumped.

## A future re-squash / rebase caveat

Because the drift originates from one squashed commit, `git blame` won't separate
test from impl authorship — don't expect history to explain the mismatch.
