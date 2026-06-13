---
title: Implementation plan — Agent Context Processor plugin system
status: active
created: 2026-06-13
updated: 2026-06-13
owner: Vitaly Chashin
supersedes: []
related:
  - research/agent-context-processors.md
  - plans/gaps/agent-context-processors.md
  - notes/processor-plugin-pattern.md
tags: [architecture, processors, agent-loop, plan]
---

# Plan: Agent Context Processor plugin system

Implements the recommendation in `research/agent-context-processors.md`: a third processor
family that runs **read-write at agent-loop seams**, configured **per-agent**, mirroring the
existing MCP-payload and metrics processor skeletons. Ships the two reported failures as
reference built-ins.

## 1. Goal & scope

**In scope**

- A new `AgentContextProcessor` plugin family (ABC + registry + `*Definition` + `*Chain`),
  reusing the established skeleton (`notes/processor-plugin-pattern.md`).
- Three loop seams wired into `BaseAgent`: `on_prepare_tools`, `on_tool_end`,
  `on_before_finish`.
- Per-agent config field `context_processors` with registry/import-string resolution.
- Two built-ins:
  - `RepeatedToolCallGuard` — Issue 1 (leaf-agent retry loop).
  - `MandatoryToolCallProcessor` — Issue 2 (router answers itself).
- Tests (unit + loop integration) and a `config.yaml.example` snippet.

**Out of scope (deferred)**

- Global-baseline + per-agent-extend merge semantics (gap #5) — start with per-agent replace.
- Refactoring the two existing processor systems onto a shared base (research §4.5) — note it,
  don't do it here.
- Anything driven by gaps #2–#4 beyond exposing them as config toggles.

## 2. Architecture overview

```
BaseAgent._execute()
  └─ build_context_processor_chain(self.config) → self._context_chain   (per run, fresh state)
       loop:
         _select_action_phase
            └─ _prepare_tools()            ──► chain.run_prepare_tools(toolkit) → drop names
         _action_phase(tool)               ──► chain.run_on_tool_end(tool, result)
       after step, if state ∈ FINISH_STATES ──► chain.run_before_finish() → FinishDecision
                                                 (veto: reset state + inject messages)
```

The chain is built **once per run** (like `build_metrics_chain`, `base_agent.py:442`), so
processor instances hold per-request state (call-count maps, retry counters) with no
cross-request leakage. Stored on `self._context_chain` so the seam methods can reach it.

## 3. Module layout

New package `sgr_agent_core/context_processors/` mirroring `observability/metrics/`:

| File | Contents |
|---|---|
| `context_processors/__init__.py` | exports; `build_context_processor_chain()`; imports builtins to self-register |
| `context_processors/base.py` | `AgentContextProcessor` ABC, `AgentContextProcessorRegistry`, `ContextProcessorDefinition`, `AgentContextProcessorChain`, directive dataclasses |
| `context_processors/repeated_tool_call_guard.py` | `RepeatedToolCallGuard` (Issue 1) |
| `context_processors/mandatory_tool_call.py` | `MandatoryToolCallProcessor` (Issue 2) |

## 4. Data model & interfaces (`base.py`)

### 4.1 Directives (return-a-directive contract)

```python
@dataclass
class FinishDecision:
    force_continue: bool = False
    inject_messages: list[dict[str, Any]] = field(default_factory=list)  # OpenAI message dicts
    reason: str | None = None
```

`on_prepare_tools` returns a `set[str]` of tool names to drop (simple, no dataclass needed).

### 4.2 ABC

```python
class AgentContextProcessor(ABC):
    def __init__(self, processor_config: dict[str, Any] | None = None):
        self.processor_config = processor_config or {}

    def __init_subclass__(cls, **kw):                      # auto-register (skeleton)
        super().__init_subclass__(**kw)
        if not getattr(cls, "__abstractmethods__", set()):
            AgentContextProcessorRegistry.register(cls, name=cls.__name__)

    # all hooks optional, default no-op
    async def on_tool_end(self, *, tool: BaseTool, result: str,
                          context: AgentContext, config: AgentConfig, **kw) -> None: ...
    async def on_prepare_tools(self, *, toolkit: list[type[BaseTool]],
                          context: AgentContext, config: AgentConfig, **kw) -> set[str]:
        return set()
    async def on_before_finish(self, *, context: AgentContext,
                          config: AgentConfig, **kw) -> FinishDecision | None:
        return None
```

> Note: `on_tool_end` receives the **tool instance** (not just the name as metrics does), so
> processors can read `tool.isSystemTool` and `tool.model_dump(mode="json")` for counting and
> hashing.

### 4.3 Config model & registry

```python
class ContextProcessorDefinition(BaseModel, extra="allow"):
    class_name: str = Field(alias="class")
    config: dict[str, Any] = Field(default_factory=dict)

class AgentContextProcessorRegistry(Registry["AgentContextProcessor"]): ...
```

### 4.4 Chain

```python
class AgentContextProcessorChain:
    def __init__(self, processors: list[AgentContextProcessor]):
        self.processors = processors

    async def run_on_tool_end(self, tool, result, context, config) -> None:
        for p in self.processors:
            try: await p.on_tool_end(tool=tool, result=result, context=context, config=config)
            except Exception as e: logger.warning("ctx-proc %s.on_tool_end failed: %s", type(p).__name__, e)

    async def run_prepare_tools(self, toolkit, context, config) -> set[str]:
        drop: set[str] = set()
        for p in self.processors:
            try: drop |= await p.on_prepare_tools(toolkit=toolkit, context=context, config=config)
            except Exception as e: logger.warning(...)  # fail-safe: this processor drops nothing
        # NEVER drop system/terminal tools — agent must be able to finish
        system_names = {t.tool_name for t in toolkit if getattr(t, "isSystemTool", False)}
        return drop - system_names

    async def run_before_finish(self, context, config) -> FinishDecision:
        merged = FinishDecision()
        for p in self.processors:
            try: d = await p.on_before_finish(context=context, config=config)
            except Exception as e: logger.warning(...); continue   # fail-safe: allow finish
            if d and d.force_continue:
                merged.force_continue = True
                merged.inject_messages.extend(d.inject_messages)
        return merged
```

### 4.5 Builder

```python
def build_context_processor_chain(config: AgentConfig) -> AgentContextProcessorChain | None:
    defs = getattr(config, "context_processors", None) or []
    if not defs: return None                      # zero-cost path
    processors = []
    for raw in defs:
        d = ContextProcessorDefinition.model_validate(raw)
        cls = AgentContextProcessorRegistry.get(d.class_name) or _import_dotted(d.class_name)
        if cls is None: logger.warning("ctx processor '%s' not found, skipping", d.class_name); continue
        processors.append(cls(d.config))
    return AgentContextProcessorChain(processors) if processors else None
```

(`_import_dotted` = the registry-then-import-string fallback already used in
`services/mcp_service.py:19` and `observability/metrics/__init__.py:34` — factor a tiny shared
helper or copy.)

## 5. Config schema & wiring

### 5.1 Field placement

Add to **`AgentConfig`** (`agent_definition.py:154`) so it is inheritable and per-agent
overridable like `execution`/`llm`:

```python
context_processors: list[ContextProcessorDefinition] = Field(default_factory=list)
```

The override validator (`agent_definition.py:280-297`) merges only `BaseModel` global fields
and `continue`s on others; a `list` field therefore passes through untouched → **per-agent
value replaces global** (acceptable for v1; gap #5 covers extend semantics).

### 5.2 YAML

```yaml
agents:
  router_agent:
    base_class: "sgr_agent_core.agents.ToolCallingAgent"
    tools: ["search_specialist_tool", "final_answer_tool"]
    context_processors:
      - class: "MandatoryToolCallProcessor"
        config: { min_tool_calls: 1, max_retries: 2 }

  db_leaf_agent:
    base_class: "sgr_agent_core.agents.ToolCallingAgent"
    tools: ["db_search", "final_answer_tool"]
    context_processors:
      - class: "RepeatedToolCallGuard"
        config: { max_repeats: 3, scope: "exact_args" }
```

## 6. BaseAgent wiring (three diffs)

Add `self._context_chain: AgentContextProcessorChain | None = None` in `__init__`
(`base_agent.py:~53`).

**Diff A — build the chain (in `_execute`, near `base_agent.py:442`):**
```python
from sgr_agent_core.context_processors import build_context_processor_chain
self._context_chain = build_context_processor_chain(self.config)
```

**Diff B — filter tools (`_prepare_tools`, `base_agent.py:214-226`):**
```python
tools = set(self.toolkit)
if self._context.iteration >= self.config.execution.max_iterations:
    raise RuntimeError("Max iterations reached")
if self._context_chain:
    drop = await self._context_chain.run_prepare_tools(list(self.toolkit), self._context, self.config)
    tools = {t for t in tools if t.tool_name not in drop}
return [pydantic_function_tool(tool, name=tool.tool_name) for tool in tools]
```

**Diff C — tool-end + before-finish (in `_execution_step` / `_execute`):**
- After the tool executes (`base_agent.py:331`, alongside the metrics `on_tool_end` at `:337`):
  ```python
  if self._context_chain:
      await self._context_chain.run_on_tool_end(action_tool, tool_result, self._context, self.config)
  ```
- The new finish seam in the loop body, after `_execution_step` returns and before the
  `while` re-check (`base_agent.py:517`):
  ```python
  if self._context_chain and self._context.state in AgentStatesEnum.FINISH_STATES.value:
      decision = await self._context_chain.run_before_finish(self._context, self.config)
      if decision.force_continue:
          for m in decision.inject_messages:
              self.conversation.append(m)
          self._context.state = AgentStatesEnum.RESEARCHING   # resume (models.py:37)
  ```

> The veto deliberately leaves `execution_result` as-set; the next iteration overwrites it when
> the agent re-finishes. Infinite-loop protection lives in the processor (`max_retries`), not
> the loop.

## 7. Built-in processors

### 7.1 `RepeatedToolCallGuard` (Issue 1)

State: `self._counts: dict[str, int]`. Config: `max_repeats: int = 3`,
`scope: "exact_args" | "tool_name" = "exact_args"`, `failed_only: bool = False` (gap #2).

- `on_tool_end`: skip system tools. Compute key:
  - `exact_args`: `sha1(tool.tool_name + json.dumps(tool.model_dump(mode="json"), sort_keys=True))`
  - `tool_name`: `tool.tool_name`
  If `failed_only`, only count when `result` starts with `"Error:"` (`base_tool.py:90-92`).
  Increment `self._counts[key]`.
- `on_prepare_tools`: return `{tool_name for any key ≥ max_repeats}` → resolves to the set of
  tool names whose (name|name+args) count crossed the threshold. (For `exact_args`, map the
  crossed key back to its `tool_name`, stored alongside the count.)

> Decision (gap #3): v1 = **drop the tool** (deterministic). Optionally also inject a one-line
> system note "tool X disabled after N repeats" so the model understands the shrink — gate with
> `announce: bool = True`.

### 7.2 `MandatoryToolCallProcessor` (Issue 2)

State: `self._work_calls: int = 0`, `self._retries: int = 0`. Config:
`min_tool_calls: int = 1`, `max_retries: int = 2`,
`message: str = "On the previous step you did not call any tool. You must call a tool (e.g. delegate to a sub-agent) before finishing."`

- `on_tool_end`: if `not tool.isSystemTool`: `self._work_calls += 1`.
- `on_before_finish`: if `self._work_calls < min_tool_calls and self._retries < max_retries`:
  `self._retries += 1`; return
  `FinishDecision(force_continue=True, inject_messages=[{"role": "user", "content": self.message}], reason="router finished with no work calls")`.
  Else return `None` (allow finish; safety valve avoids infinite veto).

## 8. Fail policy (gap #6, decided)

- `on_prepare_tools` throws → log + that processor contributes no drops (agent keeps full
  toolkit). Never fatal.
- `on_tool_end` throws → log + skip (counter just misses one).
- `on_before_finish` throws → log + treat as no veto (**let the agent finish** — fail toward
  termination, never toward an infinite loop).

All three are fail-safe-but-visible (logged at WARNING), unlike metrics' fully-silent policy.

## 9. Testing

Mirror `tests/test_payload_processor.py` / `tests/test_builtin_processors.py`.

- `tests/context_processors/test_base.py` — registry resolution (name + dotted import),
  `build_context_processor_chain` (empty → None), chain fail-safety (a throwing processor does
  not break the chain), system-tool drop exclusion.
- `tests/context_processors/test_repeated_tool_call_guard.py` — count by `exact_args` vs
  `tool_name`; threshold drop; `failed_only`; system tools never counted/dropped.
- `tests/context_processors/test_mandatory_tool_call.py` — veto when 0 work calls; allow after
  `max_retries`; system tools don't satisfy `min_tool_calls`.
- `tests/context_processors/test_loop_integration.py` — drive a `ToolCallingAgent` with a fake
  client: (1) leaf repeatedly calling a stub tool that returns `Error:` → tool dropped → agent
  finishes instead of looping to `max_iterations`; (2) router that calls `FinalAnswerTool`
  immediately → vetoed once → forced to call the sub-agent tool → finishes.

## 10. Task breakdown

1. `context_processors/base.py` — ABC, registry, definition, chain, directives, builder. [P]
2. `context_processors/__init__.py` — exports + builtin imports. (after 1)
3. `RepeatedToolCallGuard`. [P after 1]
4. `MandatoryToolCallProcessor`. [P after 1]
5. `AgentConfig.context_processors` field + validate it threads through per-agent override. [P]
6. BaseAgent diffs A/B/C + `_context_chain` attribute. (after 1)
7. Unit tests for base + both builtins. (after 3,4)
8. Loop integration test. (after 6)
9. `config.yaml.example` snippet + short docs in CLAUDE.md MCP/processors area.

## 11. Open items carried into implementation

- Resume-state choice = `RESEARCHING` (decided). Re-confirm nothing keys off `INITED`
  vs `RESEARCHING` for first-vs-subsequent behaviour.
- Gaps #2 (failed-only), #3 (drop vs instruct), #4 (gateway tool-list caching when a tool
  disappears mid-run) exposed as config toggles; #4 still needs a real check against Context
  Forge before relying on drop-mid-run in production.
- Gap #5 (global+per-agent extend merge) deferred; per-agent replace for v1.

When this plan is fully implemented → set `status: archived` here and on the research doc.
