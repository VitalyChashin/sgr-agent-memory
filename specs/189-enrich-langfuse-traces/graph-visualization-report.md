# Report: Langfuse Graph Visualization for SGR Agent Core

**Date**: 2026-03-26
**Branch**: 189-enrich-langfuse-traces
**Status**: Research complete, implementation feasible

## What the Graph Shows

The LangGraph screenshot shows a visual call graph at the bottom of the Langfuse trace view with nodes (`__start__`, `model`, `tools`, `__end__`) connected by directed edges. This represents the agent's execution flow as a DAG (directed acyclic graph).

## How It Works

### The graph is a built-in Langfuse feature, not LangGraph-specific

Langfuse renders the graph visualization for **any trace** that has the right metadata fields on its observations. The field names are unfortunately hardcoded to LangGraph's naming convention in the Langfuse backend SQL:

```sql
SELECT id, parent_observation_id, type, name, start_time, end_time,
       metadata['langgraph_node'] AS node,
       metadata['langgraph_step'] AS step
FROM observations
WHERE project_id = ... AND trace_id = ...
```

### Required metadata fields

Two fields must be present in span/generation `metadata`:

| Field | Type | Description |
|-------|------|-------------|
| `langgraph_node` | string | Node name in the graph (e.g., `"reasoning"`, `"tools"`, `"model"`) |
| `langgraph_step` | integer | Execution step number (0, 1, 2, ...). At least one observation must have a non-zero step |

### Graph view availability conditions

The graph tab appears in Langfuse when ALL of these are true:

1. The trace has at least 1 observation with graph data
2. The trace has fewer than 5,000 observations
3. **At least one observation has `langgraph_step` that is non-null and non-zero**

### Graph construction rules

- `__start__` and `__end__` nodes are auto-injected if not present
- All nodes in step N connect to all nodes in step N+1 (supports parallel branches)
- The final step connects to `__end__`
- Multiple observations with the same `langgraph_node` name are grouped into one graph node
- The node label in the graph is the `langgraph_node` value

## How to Add This to SGR

### Approach: Add `langgraph_node` and `langgraph_step` to existing span metadata

Since SGR already creates spans for iterations, tool calls, and generations, we just need to add the two metadata fields. No new spans or API changes required.

### Mapping SGR execution to graph nodes

SGR's execution loop maps naturally to a graph:

```
__start__ → reasoning → action-selection → tool → reasoning → action-selection → tool → __end__
```

Or simplified (grouping by role):

```
__start__ → model → tools → model → tools → __end__
```

### Concrete implementation

For each observation created in `BaseAgent._execution_step()` and `_execute()`, add metadata:

```python
# Step counter (incremented for each phase in the iteration)
step = (self._context.iteration - 1) * 3  # 3 phases per iteration

# Reasoning generation span
provider.start_generation(
    name="reasoning",
    metadata={"langgraph_node": "reasoning", "langgraph_step": step + 1},
    ...
)

# Action-selection generation span
provider.start_generation(
    name="action-selection",
    metadata={"langgraph_node": "action-selection", "langgraph_step": step + 2},
    ...
)

# Tool execution span
provider.start_span(
    name=f"tool-{tool_name}",
    metadata={"langgraph_node": "tools", "langgraph_step": step + 3},
    ...
)
```

### Expected result

For a 3-iteration agent execution, the graph would show:

```
__start__ → reasoning → action-selection → tools → reasoning → action-selection → tools → reasoning → action-selection → tools → __end__
```

Or with simplified node names (grouping repeated nodes):

```
__start__
    ↓
reasoning (3/3)
    ↓
action-selection (3/3)
    ↓
tools (3/3)
    ↓
__end__
```

The `(3/3)` notation means the node appeared in 3 of 3 steps — Langfuse groups observations with the same `langgraph_node` name into one graph node.

## Complexity Assessment

| Aspect | Assessment |
|--------|-----------|
| **Effort** | Low — only need to add 2 metadata fields to existing span/generation creation calls |
| **Risk** | Zero — metadata fields are additive and ignored if Langfuse version doesn't support graphs |
| **Files to change** | 1 file: `sgr_agent_core/base_agent.py` (add metadata to existing `start_span` and `start_generation` calls) |
| **Breaking changes** | None — purely additive metadata |
| **Dependency** | Requires Langfuse server version that supports the graph view (appears to be a recent feature). Self-hosted Langfuse instances may need to be updated |

## Limitations

1. **Field names are hardcoded to `langgraph_*`**: The Langfuse backend SQL uses `metadata['langgraph_node']` and `metadata['langgraph_step']`. There's no way to use SGR-specific field names. This is a Langfuse limitation — the feature was originally built for LangGraph and the naming wasn't generalized.

2. **Step numbering must be sequential**: The graph connects step N to step N+1. If step numbers are non-contiguous, there will be gaps in the graph.

3. **No conditional edges**: The graph shows all transitions that occurred. It cannot show edges that were possible but not taken (e.g., "if enough_data, skip to __end__"). LangGraph shows this because it has a static graph definition; SGR's execution is dynamic.

4. **Iteration spans won't show as nodes**: Only observations with `langgraph_node` metadata appear as graph nodes. The iteration wrapper spans would not appear in the graph unless explicitly given the metadata, but this would clutter the visualization. Better to only tag the leaf-level observations (reasoning, action-selection, tool).

5. **Self-hosted Langfuse version**: The graph view feature may not be available in older self-hosted Langfuse versions. The user would need to check their Langfuse version supports it.

## Recommendation

**Implement it.** The effort is minimal (adding 2 metadata fields to 3 existing calls in `base_agent.py`), the risk is zero (additive metadata), and it provides a valuable visual overview of agent execution flow. The `langgraph_*` naming is unfortunate but harmless — it's an implementation detail of Langfuse's backend.
