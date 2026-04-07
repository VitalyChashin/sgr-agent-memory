# Research: Rolling Summary Memory (v2 — Context Injection)

**Feature Branch**: `195-rolling-summary-memory`  
**Date**: 2026-04-07

## R1: Context Rewriting Strategy

**Decision**: Rewrite `self.task_messages` on `BaseAgent` before the reasoning loop starts, replacing older turns with a summary message. The compacted list is: `[system messages] → [summary as a system message] → [recent window messages]`.

**Rationale**: `BaseAgent._prepare_context()` (line 197, `base_agent.py`) builds the LLM input as `[system prompt] + task_messages + [initial request] + conversation`. By rewriting `task_messages` before the loop, all downstream calls — `_reasoning_phase()`, `_select_action_phase()` — automatically use the compacted context. No changes needed in any agent subclass. The original `task_messages` is preserved in a separate variable for response field assembly.

**Alternatives considered**:
- Override `_prepare_context()` — rejected because it would require changes in every agent subclass or a fragile super() chain.
- Middleware in endpoints.py — rejected because the buffer needs access to agent config (token budget, model) which is only available on the agent instance.

## R2: Summary Message Role

**Decision**: Inject the summary as a `system` role message with a clear prefix: `"[Conversation history summary]: ..."`.

**Rationale**: Using `system` role positions the summary as context/instruction rather than a conversational turn, which matches its semantic purpose. A `user` role message would confuse the LLM into thinking the user said those words. The prefix clearly delineates it from the actual system prompt. This follows the pattern used by ChatGPT's own memory feature.

**Alternatives considered**:
- `user` role — rejected as it misrepresents who authored the content.
- `assistant` role — rejected as it implies the agent previously said those words.
- Custom role — not supported by OpenAI API.

## R3: Preserving Original Messages for Response Fields

**Decision**: Before rewriting `task_messages`, store the original list in `self._original_task_messages`. After execution, use this plus the buffer's split point to assemble the `recentMessages` response field.

**Rationale**: The client needs `recentMessages` (the actual message objects from the recent window) on the response. After rewriting, the original messages are gone from `task_messages`. Keeping a reference costs no extra memory (it's a list reference, not a copy).

## R4: When Summarization Is Not Needed

**Decision**: If the full conversation (excluding system messages) fits within the token budget, skip summarization entirely. Do not rewrite `task_messages`. Set `conversationSummary = None` and `recentMessages = all non-system messages`.

**Rationale**: Unnecessary summarization wastes an LLM call and adds latency. The token budget check is cheap (walk messages, count tokens). When all messages fit, the agent should see the original conversation verbatim.

## R5: Fail-Open Fallback

**Decision**: If summarization fails (timeout, error, malformed response), restore `self.task_messages` to the original full list and proceed. Log a warning. Omit summary fields from response.

**Rationale**: The v1 implementation had this pattern (summary omitted from response), but now we also need to un-rewrite the context since the agent would otherwise have no messages to reason with. Keeping a reference to the original list makes this a simple assignment.

## R6: Parallel vs Sequential Summarization

**Decision**: When `parallel_with_first_iteration=true`, the summarization call is launched via `asyncio.create_task` but the context rewriting **must** complete before the reasoning loop starts (since the loop reads `task_messages`). Therefore parallel mode cannot work the same way as in v1 — the summary must be available before the first iteration. 

**Revised approach**: Always summarize before the loop (sequential). The `parallel_with_first_iteration` config option is removed for v2 since it is incompatible with context injection. Summarization latency is accepted as the cost of context compaction.

**Rationale**: In v1, the summary was a side-channel output that could arrive after the loop. In v2, the summary IS the context — the agent cannot reason without it. Launching it in parallel would mean the first iteration uses un-compacted messages (defeating the purpose) or blocks waiting for the task (equivalent to sequential).

**Alternatives considered**:
- Parallel launch + await before loop — this is just sequential with extra async overhead. Simpler to await directly.

## R7: Config Cascade Dict Handling

**Decision**: Reuse the pattern from the v1 fix — check if `memory` config is a dict and coerce `rolling_summary` to `RollingSummaryConfig` if needed.

**Rationale**: The `agent_level_config_override_validator` calls `model_dump()` which produces raw dicts for extra fields on `AgentConfig`. This was the root cause of the v1 MCP activation bug. The same coercion is needed in v2.

## R8: Token Counting

**Decision**: Reuse `tiktoken` with `cl100k_base` encoding (same as v1). The `_extract_text_content()` helper from v1 handles multi-part content.

**Rationale**: Already proven in v1. Approximate counting is sufficient for window selection.
