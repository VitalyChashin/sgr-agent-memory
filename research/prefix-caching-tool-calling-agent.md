---
title: Prompt prefix caching in ToolCallingAgent — what already works, what breaks it
status: active
created: 2026-08-11
updated: 2026-08-11
owner: Vitaly Chashin
related:
  - research/toolcalling-context-growth.md
  - research/agent-context-processors.md
  - notes/prepare-tools-injection.md
  - sgr_agent_core/base_agent.py
  - sgr_agent_core/agents/tool_calling_agent.py
  - sgr_agent_core/services/prompt_loader.py
  - sgr_agent_core/context_processors/repeated_tool_call_guard.py
tags: [agent-loop, prefix-caching, kv-cache, cost, tool-calling, observability]
---

# Research: prefix caching in `ToolCallingAgent`

## 0. Question

A `ToolCallingAgent` run can span many iterations, and every iteration re-sends the
whole transcript. Do earlier steps actually land in the provider's prompt cache, or
does something in the current setup invalidate the prefix each turn?

Scope: **provider-side automatic prefix caching** (OpenAI, DeepSeek, Qwen/vLLM, and
other OpenAI-compatible endpoints), which keys on an exact byte-prefix match of the
serialized request. Anthropic's explicit `cache_control` breakpoints are out of scope
(see §6).

## 1. Verdict

The loop is **already prefix-cache-friendly by construction**. One real defect
(`set`-ordered tool definitions), one inherent trade-off (tool dropping), and one
accidental save that must not be "fixed" naively.

## 2. What already works — the append-only invariant

`_prepare_context` (`base_agent.py:270`) rebuilds the message list each turn as:

```python
[
    {"role": "system", ...},   # system prompt, built from self.toolkit (full, unfiltered)
    *self.task_messages,
    {"role": "user", ...},     # initial_user_request
    *self.conversation,
]
```

Everything before `self.conversation` is constant for the run, and `self.conversation`
is only ever **appended to** during the loop:

| Where | Appends |
|-------|---------|
| `base_agent.py:623` | `Agent {id} started` (once, before the loop) |
| `tool_calling_agent.py:97` | assistant message carrying the tool call |
| `tool_calling_agent.py:119` | `{"role": "tool", "content": result, ...}` |
| `base_agent.py:314` | context-processor `inject_messages` (prepare-tools seam) |
| `base_agent.py:700` | context-processor `inject_messages` (before-finish veto) |

Nothing rewrites or reorders earlier entries. The prompt grows strictly monotonically,
so iteration *N* shares an exact prefix with iteration *N−1* and caching hits with no
code change. Request-level knobs are constant too: `temperature`, `max_tokens`
(`LLMConfig`), `tool_choice="required"` (`tool_calling_agent.py:35`).

Only two things reset the prefix, both legitimately:

- `provide_clarification(replace_conversation=True)` (`base_agent.py:189`) clears the
  conversation — used for stateless clients that re-send full history each turn.
- The rolling-summary compaction (`base_agent.py:626-669`) rewrites `task_messages`,
  but runs **once, before** the loop.

### Measurement is already wired

`_extract_usage` (`base_agent.py:140-146`) reads `prompt_tokens_details.cached_tokens`
into the `cached` key, and `include_stream_usage` defaults to `True`
(`agent_definition.py:65`), so cache hit rate is visible in Langfuse generation spans
today. **Cross-check any change here against `usage.cached` rather than reasoning
about it.**

## 3. Defect — tool definitions came from a `set`

`_prepare_tools` (`base_agent.py:287`) built its tool list from `set(self.toolkit)`,
and the context-processor drop path rebuilt it as a set comprehension.

Tool schemas are part of the cacheable prefix, so their order is part of the cache
key — measured, see §9.2 for how much it actually costs (it is a bounded penalty, not
the whole prefix; an earlier draft of this section overstated it). Set iteration order
for class objects derives from `id()`. Measured across three interpreter runs of the
same five classes:

```
proc 1: ['D','B','E','C','A'] | after dropping C: ['B','E','A','D']
proc 2: ['C','D','A','B','E'] | after dropping C: ['B','E','A','D']
proc 3: ['A','C','E','D','B'] | after dropping C: ['E','A','B','D']
```

Two distinct consequences:

- **Cross-process.** Order is stable *within* one interpreter, so a single run was
  never self-invalidating. But each uvicorn worker gets its own ordering, so workers
  cannot share a warm cache entry, and every restart/deploy pays a cold tail. This is
  the consequence the fix actually buys back, and §9.1 measures it: 98% cache on the
  first call of a second process, vs 85% with a different order.
- **After a drop.** Rebuilding the set reshuffles the *survivors* — the prefix changes
  by more than the removed tool, for no reason.

**Fix applied** (`base_agent.py:296`, `:309`):

```python
tools = list(dict.fromkeys(self.toolkit))          # ordered + deduped, was set(...)
...
    tools = [t for t in tools if t.tool_name not in result.drop]
```

`dict.fromkeys` preserves the dedupe the `set` provided, in toolkit order. Regression
test: `tests/test_base_agent.py::TestBaseAgentAbstractMethods::test_prepare_tools_preserves_toolkit_order`
(asserts order both with and without a drop).

Note the system prompt was never affected — `PromptLoader.get_system_prompt` renders
from `self.toolkit`, the ordered list, and always the **full** toolkit regardless of
drops.

## 4. Trade-off — dropping tools invalidates from token 0

`RepeatedToolCallGuard` removes a tool at the `on_prepare_tools` seam
(`repeated_tool_call_guard.py:120`). Any change to the tool block invalidates the
prefix from the tool block onward, so **every remaining iteration of that run re-pays
that tail uncached**. Measured cost of a tool-block change on gpt-4.1-mini: ~1.3k
tokens for a ~250-token block (§9.2) — the penalty exceeds the block's own size but is
far from the full prompt. This is inherent to dropping, not a bug.

Worth knowing: the guard also injects a one-shot "tool disabled" directive
(`repeated_tool_call_guard.py:105`), appended at the end of the conversation — that
half is cache-neutral and often sufficient on its own to steer the model. On a long run
with a large toolkit, `announce: true` with a generous `max_repeats` costs less than an
early drop. Same consideration applies to any future processor that filters tools.

## 5. The accidental save — don't naively fix the stale date

`PromptLoader.get_initial_user_request` (`prompt_loader.py:26-36`) takes
`current_datetime=datetime.now()` as a **default argument**, evaluated once at import
time. So `{current_date}` in `prompts/initial_user_request.txt` is frozen at process
start.

That is a genuine staleness bug for long-lived servers (a week-old worker reports a
week-old date, and `system_prompt.txt` has a whole `<DATE_GUIDELINES>` block telling
the model to trust it). But it is also the only reason the prefix survives: this
message sits **before** `self.conversation`, so moving `datetime.now()` inside the
function body would invalidate the prefix on every request of every iteration —
turning a cheap staleness bug into a total cache loss.

**If the staleness is fixed, pin the timestamp per agent run** (stamp once in
`__init__` / at trace start and reuse it for the whole loop), never per call.

## 6. Optional wins

- **`prompt_cache_key` needs no code.** `LLMConfig` is `extra="allow"` and
  `to_openai_client_kwargs()` dumps extras (`agent_definition.py:67-71`), so a YAML
  `llm: { prompt_cache_key: "tool-calling-agent-v1" }` already reaches the request.
  Set it per agent definition — runs of one agent share the system prompt + tool block,
  which is exactly the routing hint OpenAI wants.
- **Agent-id in the transcript.** `init_message = f"Agent {self.id} started\n"`
  (`base_agent.py:622`) puts a fresh UUID at `conversation[0]`. Harmless within a run;
  it only blocks cross-run reuse of a tail that already differs by task. Not worth
  changing on its own.
- **Anthropic** would need explicit `cache_control` breakpoints on the system block and
  a rolling breakpoint near the transcript tail. There is no seam for that today, and
  an OpenAI-compatible gateway generally cannot carry the field. Only worth building if
  a direct Anthropic path is added.

## 7. Interaction with in-loop context eviction

[`research/toolcalling-context-growth.md`](toolcalling-context-growth.md) proposes an
`AgentContextProcessor` that stubs out old tool-result bodies to bound context growth.
That directly conflicts with the append-only invariant in §2: **every eviction
invalidates the prefix from the earliest edited message onward.** If both land, evict
in infrequent large batches rather than every turn, and watch `usage.cached` to confirm
the token savings from eviction exceed the re-ingest cost of the lost cache.

## 8. Live validation against NeuralDeep Hub (2026-08-11)

Endpoint `https://api.neuraldeep.ru/v1`, model `qwen3.6-35b-a3b`, key from
`$NEURALDEEP_API_KEY`. A LiteLLM router in front of a heterogeneous upstream pool
(`/v1/models` lists 18 ids; the error body exposes fallback groups such as
`qwen3.6-fp8 → [qwen3.6-35b-a3b, gpt-oss-20b]`).

### 8.1 Bug found and fixed: mid-conversation `system` message → HTTP 400

The first live run died on iteration 3 with:

> `System message must be at the beginning.`

Cause: `base_agent.py:623` appended the "Agent … started" notice as
`{"role": "system", ...}`, but it lands **after** `task_messages` and the initial user
request in `_prepare_context`. Isolated with a 3-message probe:

| model | mid-list `system` | mid-list `assistant` |
|-------|-------------------|----------------------|
| `qwen3.6-35b-a3b` | **400** | 200 |
| `qwen3.6-fp8` | 200 | 200 |
| `gpt-oss-120b` | 200 | 200 |

Only one upstream in the pool enforces it, which is why the failure looked
intermittent — round-robin decided whether a given iteration crashed. **Fixed**:
role changed to `assistant` (`base_agent.py:625`), test updated to assert no
`system` message survives in `conversation`. This is a latent crash for any strict
chat template, unrelated to caching — found only by going live.

### 8.2 Blocker: the Hub does not report cache stats for streaming requests

`_select_action_phase` always uses `chat.completions.stream`. Identical messages,
streaming vs not:

| mode | `prompt_tokens` | `prompt_tokens_details` |
|------|-----------------|-------------------------|
| non-stream ×3 | 9401 | `null`, `null`, `cached=8448` |
| stream ×3 | **7618** | `null`, `null`, `null` |

15/15 streaming calls (3 probes + 12 across three full agent runs) returned
`prompt_tokens_details: null`. The differing `prompt_tokens` for the *same* payload
shows LiteLLM **synthesizes** stream usage by local counting rather than passing the
upstream's through — which also drops the cache detail.

Consequence: **`usage.cached` (§2) is always absent on this Hub**, so the
already-wired measurement path is blind here. It is not evidence that caching is off,
only that it is unobservable through the framework's normal path.

### 8.3 There is no cache floor — retracted

**This section originally claimed a ~4k-token floor. That was wrong.** The evidence was
a handful of non-streaming calls per size (877 → never cached, 2 197 → never cached),
which conflated §8.4's routing scatter with a size threshold. Re-run with 6 calls per
size via `scripts/prefix_cache_probe.py --sweep`:

| prompt tokens | best cached | hit |
|---------------|-------------|-----|
| 1 098 | 1 056 | **96%** |
| 1 388 | 1 056 | 76% |
| 2 288 | 2 112 | 92% |
| 3 938 | 3 168 | 80% |

The Hub caches happily at ~1.1k tokens. A default-configured agent (~900 prompt tokens)
is **not** excluded from caching — you just have to sample enough calls to see past the
scatter. Cached counts land on multiples of 1 056/32, i.e. vLLM's block granularity.

Lesson worth keeping: on a round-robin router, *n=2 cannot distinguish "no caching" from
"routed to a cold upstream."* Any single miss proves nothing here.

### 8.4 Routing scatter dominates everything

Even at 9.4k tokens with an identical prefix, hits were erratic (`null, null, 8448`),
and `user: <session_id>` — the Hub's documented session-stickiness knob — did not
produce reliable stickiness in testing (hit/miss stayed erratic with it set). It does
pass through from config with no code change: `LLMConfig(extra="allow")` +
`to_openai_client_kwargs()` carried `user` to the wire, confirming §6's claim.

### 8.5 The ordering fix could not be proven here — only shown consistent

A/B with the framework's exact payload (`_prepare_tools()` + `_prepare_context()`),
sent non-streaming, comparing a repeatedly-sent canonical tool order against a
permutation never sent before:

```
canonical (warm order) : 2/5 hit
fresh permutation      : 0/5 hit
```

Directionally consistent with tool order being part of the cache key, but **not
statistically meaningful** at n=5 (Fisher exact p ≈ 0.44), and the canonical order's
own 3/5 miss rate shows upstream scatter dominating. An earlier A/B was discarded as
invalid: it re-sent the same "different" order every rep, so that order warmed itself
and both arms hit 9504.

**Status of the §3 fix on the Hub: correct by construction, unproven in this
environment.** Deterministic ordering cannot be worse than hash ordering, but the Hub
is too noisy to demonstrate the delta. Verified separately on an endpoint that reports
`cached_tokens` on streams — §9.

## 9. Live validation against OpenAI `gpt-4.1-mini`

Same harness, `ND_BASE_URL=https://api.openai.com/v1 ND_MODEL=gpt-4.1-mini`. OpenAI
was chosen purely as a *measurable* control: unlike the Hub it reports
`prompt_tokens_details` on streams, so the framework's own `usage.cached` path works.

| mode | prompt | cached ×3 |
|------|--------|-----------|
| non-stream | 7 615 | 0, 7 424, 7 424 |
| **stream** | 7 615 | 7 424, 7 424, 7 424 |

### 9.1 Cross-process reuse — the fix, demonstrated

Three separate interpreter runs of the real `ToolCallingAgent` loop (4 action-selection
calls each). Processes 1 and 2 use the same toolkit order; process 3 reverses it,
emulating what pre-fix `set(self.toolkit)` produced in a second worker:

| run | iter 1 | iter 2 | iter 3 | iter 4 |
|-----|--------|--------|--------|--------|
| proc 1, order A (cold) | 8105 / 0 — **0%** | 97% | 98% | 99% |
| proc 2, order A | 8099 / 7936 — **98%** | 97% | 98% | 97% |
| proc 3, order B | 8103 / 6912 — **85%** | 97% | 98% | 99% |

Two readings:

- **A fresh process reusing a warm order hits 98% on its very first call.** That is the
  cross-worker/cross-restart reuse §3 predicted, observed end-to-end through the
  framework's own streaming path.
- **A different order costs ~1 024 tokens on that first call (98% → 85%), and nothing
  after.** Within a run the order never changes, so iterations 2+ are identical in both
  arms.

So the fix is real but **modest, and one-shot per process**: it is worth roughly
(tool-block size) × (number of worker starts), not a per-iteration saving. For this
5-tool toolkit that is ~1k tokens per worker start; for a production MCP toolset with
tens of tools it scales with the block.

### 9.2 Isolating tool order — how big is the blast radius?

Because proc 3 got 85% rather than 0%, the "tools sit at the very front" rationale in
the first draft of §3 was wrong. Isolated it: a fixed 7 015-token system prompt with
**no** tool listing in the text, 8 synthetic tools, varying only tool order.

| request | prompt | cached | uncached |
|---------|--------|--------|----------|
| order X (cold) | 7 259 | 5 888 | 1 371 |
| order X (warm) | 7 259 | **7 168** | **91** |
| reversed (fresh order) | 7 259 | 5 888 | 1 371 |
| rotated (fresh order) | 7 259 | 5 888 | 1 371 |
| order X again | 7 259 | **7 168** | **91** |

Reproducible to the token. Conclusions:

- **Tool order is part of the cache key** — every never-before-sent ordering falls back
  to the same 5 888 shared prefix, every repeat of a seen ordering reaches 7 168.
- **The penalty is bounded, and larger than the tool block itself.** The tool block is
  only ~244 tokens (7 259 − 7 015), yet changing it re-processes ~1 280. Tool schemas
  are therefore *not* at the absolute head of the serialized prompt, but they are not
  at the tail either. Don't reason about the exact position — measure it per provider.
  (vLLM caches on fixed token blocks and will have its own granularity.)

## 10. What this means for production (vLLM behind LiteLLM)

Production runs the same shape as the Hub — vLLM upstreams behind a LiteLLM router —
and the platform team reports caching is enabled. Taking that at face value, the Hub
findings transfer, and the practical consequences are:

1. **Do not use `usage.cached` to verify it.** §8.2 is a property of LiteLLM's
   stream-usage synthesis, not of NeuralDeep. If prod runs a similar LiteLLM version,
   `prompt_tokens_details` will be absent on every streaming call — and
   `_select_action_phase` always streams. Caching can be fully working and still report
   zero. **Verify with `scripts/prefix_cache_probe.py`**, or read vLLM's own
   `gpu_prefix_cache_hit_rate` metric on the upstreams; the framework's observability
   cannot answer this question.

   ```bash
   uv run python scripts/prefix_cache_probe.py \
       --base-url https://<litellm-host>/v1 --model <model> --key-var <ENV_VAR> --repeat 6
   ```

   It builds the payload from the framework's own `_prepare_tools()` /
   `_prepare_context()`, sends it non-streaming N times plus once streaming, and prints
   a verdict distinguishing *caching off* from *caching invisible*. `--sweep a,b,c`
   probes several prompt sizes.
2. **Sample enough calls before concluding anything.** The "~4k floor" this doc
   originally reported did not exist (§8.3) — it was two-sample noise on a scattering
   router. Use `--repeat 6` or more; a miss is not evidence.
3. **Routing scatter is the dominant term, not prompt construction.** A round-robin
   router scatters consecutive iterations of one agent across upstreams, each with its
   own KV cache. `user: <session_id>` passes through from config today
   (`LLMConfig(extra="allow")`, §6) at zero code cost, but only helps if the router is
   actually configured for session-affinity routing — worth confirming with the
   platform team, since it is the single highest-leverage change available and it lives
   in their config, not ours.
4. **The §3 ordering fix still applies**, and matters *more* in prod than in testing:
   multiple uvicorn workers × frequent deploys is exactly the cross-process case §9.1
   measures, and prod toolkits are larger than the 5 tools tested.
5. **Heterogeneous pools break the append-only assumption at a different layer.** §8.1
   showed one upstream in the pool rejecting a payload the others accept. Fallback
   model groups mean the same conversation can land on a different model mid-run, which
   is a guaranteed cache miss regardless of anything the framework does.

## 11. Bottom line

Nothing structural needed changing. The `set` → ordered-list fix is applied; it buys
cross-worker and cross-restart cache reuse and stops drops from reshuffling survivors.
`prompt_cache_key` / `user` are config-only adds (passthrough confirmed live). The two
things to *not* do: move `datetime.now()` into the request path, and add per-turn
context eviction without measuring the cache cost.

Live testing changed the priority order. On NeuralDeep Hub, caching **is** on and works
down to ~1.1k tokens (§8.3, after retracting this doc's own bogus "4k floor"), but cache
stats are **unobservable through the streaming path the agent uses** (§8.2) and
routing scatter dominates any framework-side effect (§8.4). The genuinely valuable
outcome of going live was unrelated to caching — the mid-conversation `system` message
(§8.1) was crashing runs against a strict upstream.

On OpenAI the loop caches ~97-99% from iteration 2 onward with no changes at all,
confirming the append-only design (§2) is doing its job. The ordering fix is worth one
cold call per worker start, not more — real, cheap, permanently applied, but not the
lever anyone should optimise next. For the vLLM-behind-LiteLLM production stack the
first move is not a code change: **establish whether caching is actually happening**
via a non-streaming probe or vLLM's own metrics (§10.1), because the framework's
`usage.cached` cannot see it there.
