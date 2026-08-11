---
title: CoT via a tool argument — BaseTool.reasoning
status: current
created: 2026-08-05
updated: 2026-08-05
related:
  - notes/action-selection-reasoning-tracing.md
  - sgr_agent_core/base_tool.py
  - sgr_agent_core/services/mcp_service.py
  - sgr_agent_core/agents/tool_calling_agent.py
---

# Note: `reasoning` as a tool argument (fallback CoT channel)

`BaseTool` declares `reasoning: str = ""`, so **every** tool — built-in and MCP —
advertises it in its schema. It is a prompting device, not tool input: the model
writes its thinking into the tool call, and the framework reads it back out.

## Why it exists
Native reasoning is only available when the whole chain cooperates. Production runs
Qwen on vLLM behind LiteLLM, where the model has no thinking mode enabled, so
`_extract_reasoning` correctly returns `None` and traces show no CoT (see
[[action-selection-reasoning-tracing]] for the extraction paths). A tool argument
works on any model that can call tools at all — no server flags, no gateway support.

## Three things that make it work
1. **Declared on the base class, so it is first in the schema.** Pydantic emits
   base-class fields before subclass fields, and every model generates JSON in
   property order — the model writes its reasoning *before* the arguments that
   reasoning is meant to justify. Declaring it per-tool would put it last and make
   it a post-hoc rationalisation. Verified for MCP tools too: they are built with
   `create_model(__base__=(PdModel, MCPBaseTool))`, and reverse-MRO field collection
   still puts `reasoning` ahead of the server's own parameters.
2. **Default `""`, yet always emitted.** The default keeps code-constructed tools
   (`SomeTool(city="Rome")`, fixtures, tests) valid. It costs nothing at inference
   because `pydantic_function_tool` sets `strict: True` and OpenAI's
   `to_strict_json_schema` marks *every* property required regardless of default.
3. **Stripped from MCP payloads.** `MCPBaseTool.__call__` excludes it from
   `model_dump`, because an MCP server rejects unexpected arguments. The exception
   is a server whose own input schema declares a `reasoning` parameter — then it is
   a real argument, and `_declares_reasoning` (set in `build_tools_from_mcp` from the
   server's `inputSchema`) keeps it in the payload. Without that guard we would
   silently drop a legitimate argument.

Built-in tools already followed this convention by hand (`FinalAnswerTool`,
`WebSearchTool`, … each declared their own `reasoning` first); those subclass
declarations still win, keeping their tailored descriptions.

## Tracing
`ToolCallingAgent._select_action_phase` falls back to `tool.reasoning` when the
provider supplied none, so the Langfuse generation output has one `reasoning` field
whatever the provider does. Native reasoning wins when both are present — it is the
model's actual thinking rather than a self-report.

## Scope / cost
Always on. Every tool call now spends output tokens on the field even where native
reasoning already exists, and the CoT is duplicated into the conversation history
(deliberate: it is context for later turns). If a deployment needs it off, the cheap
lever is popping the property in `BaseAgent._prepare_tools` after
`pydantic_function_tool` — not a per-tool field change.
