import asyncio
import json
import logging
import os
import traceback
import uuid
from datetime import datetime
from typing import Any, Type

from openai import AsyncOpenAI, pydantic_function_tool
from openai.types.chat import ChatCompletionFunctionToolParam, ChatCompletionMessageParam
from pydantic import BaseModel

from sgr_agent_core.agent_definition import AgentConfig, ToolDefinition
from sgr_agent_core.models import AgentContext, AgentStatesEnum
from sgr_agent_core.observability.context import current_tool_span, mcp_call_errored
from sgr_agent_core.services.prompt_loader import PromptLoader
from sgr_agent_core.services.registry import AgentRegistry
from sgr_agent_core.stream import BaseStreamingGenerator, OpenAIStreamingGenerator
from sgr_agent_core.tools import (
    BaseTool,
    ClarificationTool,
    ReasoningTool,
)


def _classify_error_tags(exc: BaseException) -> set[str]:
    """Map a fatal loop exception to Langfuse-filterable error tags.

    ``RuntimeError("Max iterations reached")`` is the max-steps crash; everything
    else that bubbles to the loop's handler is an LLM/tool failure.
    """
    if isinstance(exc, RuntimeError) and str(exc).startswith("Max iterations reached"):
        return {"error", "error:max_steps"}
    return {"error", "error:llm"}


class AgentRegistryMixin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__name__ not in ("BaseAgent",):
            AgentRegistry.register(cls, name=cls.name)


class BaseAgent(AgentRegistryMixin):
    """Base class for agents."""

    name: str = "base_agent"

    # Above this many tool defs, traced input degrades to compact name+description
    # (see _build_gen_input) to keep Langfuse traces from bloating.
    _MAX_TRACED_TOOL_DEFS: int = 40

    # Cap for the reasoning/CoT text copied into action-selection generation traces.
    # Larger than the default _truncate cap (this is the diagnostic field) but bounded
    # so a long internal CoT can't bloat the trace. The raw text never leaves the
    # client stream — only this truncated copy reaches the observability provider.
    _REASONING_TRACE_MAX_LEN: int = 8000

    def __init__(
        self,
        task_messages: list[ChatCompletionMessageParam],
        openai_client: AsyncOpenAI,
        agent_config: AgentConfig,
        toolkit: list[Type[BaseTool]],
        def_name: str | None = None,
        streaming_generator: type[BaseStreamingGenerator] = OpenAIStreamingGenerator,
        tool_configs: dict[str, ToolDefinition] | None = None,
        **kwargs: dict,
    ):
        self.id = f"{def_name or self.name}_{uuid.uuid4()}"
        self.streaming_generator = streaming_generator(agent_id=self.id)

        self.openai_client = openai_client
        self.config = agent_config
        self.creation_time = datetime.now()
        self.task_messages = task_messages
        self.toolkit = toolkit
        self.tool_configs = tool_configs or {}

        self._context = AgentContext()
        self.conversation = []
        self.logger = logging.getLogger(f"sgr_agent_core.agents.{self.id}")
        self.log = []

        self._execute_task: asyncio.Task | None = None
        self._last_llm_call: dict | None = None

        # Agent Context Processor chain (built per run in _execute; None = zero-cost path).
        # _current_iter_span lets _prepare_tools (deeper in the call stack, without iter_span
        # in scope) parent any emitted span under the active iteration.
        self._context_chain: Any = None
        self._current_iter_span: Any = None

    @staticmethod
    def _truncate(value: Any, max_len: int = 2000) -> str:
        """Safely truncate any value to a string with max length."""
        if value is None:
            return ""
        s = str(value) if not isinstance(value, str) else value
        return s[:max_len] if len(s) > max_len else s

    @staticmethod
    def _extract_reasoning(obj: Any) -> str | None:
        """Pull reasoning text from a message or a stream delta, defensively.

        Providers differ: OpenAI omits raw reasoning text (only token counts);
        DeepSeek-style providers expose ``reasoning_content``; some use ``reasoning``.
        These are non-standard fields, so on the OpenAI SDK they arrive via the
        pydantic model's ``model_extra`` rather than as typed attributes. Returns
        ``None`` when nothing is present and never raises — tracing is fail-silent.
        """
        if obj is None:
            return None
        for attr in ("reasoning_content", "reasoning"):
            val = getattr(obj, attr, None)
            if val is None:
                extra = getattr(obj, "model_extra", None)
                if extra:
                    val = extra.get(attr)
            if val:
                return str(val)
        return None

    @staticmethod
    def _extract_usage(usage: Any) -> dict[str, Any] | None:
        """Flat input/output plus a best-effort token breakdown. Never raises.

        Keeps ``input``/``output`` (TokenEfficiencyProcessor depends on them) and
        adds ``total``, ``reasoning``, and ``cached`` when the provider returns the
        detail objects. Missing details are simply omitted.
        """
        if usage is None:
            return None
        out: dict[str, Any] = {
            "input": getattr(usage, "prompt_tokens", None),
            "output": getattr(usage, "completion_tokens", None),
            "total": getattr(usage, "total_tokens", None),
        }
        ctd = getattr(usage, "completion_tokens_details", None)
        if ctd is not None:
            out["reasoning"] = getattr(ctd, "reasoning_tokens", None)
        ptd = getattr(usage, "prompt_tokens_details", None)
        if ptd is not None:
            out["cached"] = getattr(ptd, "cached_tokens", None)
        return {k: v for k, v in out.items() if v is not None}

    def _flush_provider_async(self, provider: Any) -> None:
        """Fire-and-forget flush of the observability provider in a thread executor."""

        def _do_flush() -> None:
            try:
                provider.flush()
            except Exception as e:
                self.logger.warning("Observability flush failed: %s", e)

        try:
            asyncio.get_running_loop().run_in_executor(None, _do_flush)
        except RuntimeError:
            pass  # No running loop (e.g., during testing)

    def get_tool_config(self, tool_class: Type[BaseTool]) -> BaseModel | dict[str, Any]:
        """Return resolved config for a tool as a Pydantic model or raw dict.

        If the tool defines config_model, builds and returns a validated
        instance from tool_configs. Otherwise returns the raw dict.
        """
        raw = self.tool_configs.get(tool_class.tool_name, {})
        config_model = getattr(tool_class, "config_model", None)
        if config_model is None:
            return raw
        return config_model(**raw)

    async def provide_clarification(
        self,
        messages: list[ChatCompletionMessageParam],
        replace_conversation: bool = False,
    ) -> None:
        """Receive clarification from an external source in OpenAI messages
        format.

        Args:
            messages: Clarification messages in OpenAI ChatCompletionMessageParam format.
            replace_conversation: When True, clear the conversation
                before applying messages (continuing stateful conversation / stateless mode).
                Use this for stateless clients that re-send the full history on every turn.
        """
        if replace_conversation:
            self.conversation = []
        self.conversation.extend(messages)
        self.conversation.append(
            {"role": "user", "content": PromptLoader.get_clarification_template(messages, self.config.prompts)}
        )

        self._context.clarifications_used += 1
        self._context.clarification_received.set()
        self._context.state = AgentStatesEnum.RESEARCHING
        self.logger.info(f"✅ Clarification received: {len(messages)} messages")

    def _log_reasoning(self, result: ReasoningTool) -> None:
        next_step = result.remaining_steps[0] if result.remaining_steps else "Completing"
        self.logger.info(
            f"""
    ###############################################
    🤖 LLM RESPONSE DEBUG:
       🧠 Reasoning Steps: {result.reasoning_steps}
       📊 Current Situation: '{result.current_situation[:400]}...'
       📋 Plan Status: '{result.plan_status[:400]}...'
       🔍 Searches Done: {self._context.searches_used}
       🔍 Clarifications Done: {self._context.clarifications_used}
       ✅ Enough Data: {result.enough_data}
       📝 Remaining Steps: {result.remaining_steps}
       🏁 Task Completed: {result.task_completed}
       ➡️ Next Step: {next_step}
    ###############################################"""
        )
        self.log.append(
            {
                "step_number": self._context.iteration,
                "timestamp": datetime.now().isoformat(),
                "step_type": "reasoning",
                "agent_reasoning": result.model_dump(mode="json"),
            }
        )

    def _log_tool_execution(self, tool: BaseTool, result: str):
        self.logger.info(
            f"""
###############################################
🛠️ TOOL EXECUTION DEBUG:
    🔧 Tool Name: {tool.tool_name}
    📋 Tool Model: {tool.model_dump_json(indent=2)}
    🔍 Result: '{result[:400]}...'
###############################################"""
        )
        self.log.append(
            {
                "step_number": self._context.iteration,
                "timestamp": datetime.now().isoformat(),
                "step_type": "tool_execution",
                "tool_name": tool.tool_name,
                "agent_tool_context": tool.model_dump(mode="json"),
                "agent_tool_execution_result": result,
            }
        )

    def _save_agent_log(self):
        from sgr_agent_core.agent_config import GlobalConfig

        logs_dir = GlobalConfig().execution.logs_dir
        # Skip saving if logs_dir is None or empty string
        if not logs_dir:
            self.logger.debug("Skipping agent log save: logs_dir is not configured")
            return

        os.makedirs(logs_dir, exist_ok=True)
        filepath = os.path.join(logs_dir, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{self.id}-log.json")
        agent_log = {
            "id": self.id,
            "model_config": self.config.llm.model_dump(
                exclude={"api_key", "proxy"}, mode="json"
            ),  # Sensitive data excluded by default
            "task_messages": self.task_messages,
            "toolkit": [tool.tool_name for tool in self.toolkit],
            "log": self.log,
        }

        json.dump(agent_log, open(filepath, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    async def _prepare_context(self) -> list[dict]:
        """Prepare a conversation context with system prompt, task data and any
        other context.

        Note: Override this method to change the context setup for the agent.

        Returns a list of dictionaries OpenAI like format, each
        containing a role and content key by default.
        """

        return [
            {"role": "system", "content": PromptLoader.get_system_prompt(self.toolkit, self.config.prompts)},
            *self.task_messages,
            {"role": "user", "content": PromptLoader.get_initial_user_request(self.task_messages, self.config.prompts)},
            *self.conversation,
        ]

    async def _prepare_tools(self) -> list[ChatCompletionFunctionToolParam]:
        """Prepare available tools for the current agent state and progress.

        Note: Override this method to change the tool setup or conditions for tool
        usage.

        Returns a list of ChatCompletionFunctionToolParam based
        available tools.
        """
        tools = set(self.toolkit)
        if self._context.iteration >= self.config.execution.max_iterations:
            raise RuntimeError("Max iterations reached")
        if self._context_chain:
            from sgr_agent_core.observability import get_provider

            result = await self._context_chain.run_prepare_tools(
                list(self.toolkit),
                self._context,
                self.config,
                provider=get_provider(),
                parent_span=self._current_iter_span,
            )
            tools = {t for t in tools if t.tool_name not in result.drop}
            # Inject any directives (e.g. "tool X disabled") into the conversation so
            # they reach this same iteration's selection call — agents prepare tools
            # before context, so the snapshot taken next includes these messages.
            for msg in result.inject_messages:
                self.conversation.append(msg)
        return [pydantic_function_tool(tool, name=tool.tool_name) for tool in tools]

    def _build_gen_input(
        self,
        messages: list,
        tool_defs: list[ChatCompletionFunctionToolParam] | None,
        tool_choice: Any = None,
    ) -> Any:
        """Shape the generation ``input`` so Langfuse renders an Available-tools section.

        Returns the OpenAI-request object ``{messages, tools, tool_choice}`` when
        tool capture is enabled and tools exist; otherwise the bare ``messages``
        list (unchanged behaviour, e.g. NoOp / structured-output paths).

        The tool defs are kept in the full ``{"type": "function", "function": {...}}``
        shape — that is what Langfuse's tool renderer keys on — except when the
        toolset is large enough to bloat the trace, in which case they degrade to
        a compact ``[{name, description}]`` list flagged with ``_tools_truncated``.
        """
        from sgr_agent_core.agent_config import GlobalConfig

        if not tool_defs or not GlobalConfig().observability.capture_tool_definitions:
            return messages

        payload: dict[str, Any] = {"messages": messages}
        # Degrade gracefully for pathologically large toolsets: keep names +
        # descriptions but drop the full schemas, and flag it so the capping is
        # visible in the trace rather than silent.
        if len(tool_defs) > self._MAX_TRACED_TOOL_DEFS:
            payload["tools"] = [
                {
                    "name": (fn := td.get("function", td)).get("name"),
                    "description": fn.get("description", ""),
                }
                for td in tool_defs
            ]
            payload["_tools_truncated"] = True
        else:
            payload["tools"] = tool_defs

        tc = tool_choice if tool_choice is not None else getattr(self, "tool_choice", None)
        if tc is not None:
            payload["tool_choice"] = tc
        return payload

    async def _reasoning_phase(self) -> ReasoningTool:
        """Call LLM to decide next action based on current context."""
        raise NotImplementedError("_reasoning_phase must be implemented by subclass")

    async def _select_action_phase(self, reasoning: ReasoningTool) -> BaseTool:
        """Select the most suitable tool for the action decided in the
        reasoning phase.

        Returns the tool suitable for the action.
        """
        raise NotImplementedError("_select_action_phase must be implemented by subclass")

    async def _action_phase(self, tool: BaseTool) -> str:
        """Call Tool for the action decided in the select_action phase.

        Returns string or dumped JSON result of the tool execution.
        """
        raise NotImplementedError("_action_phase must be implemented by subclass")

    async def _execution_step(self, iter_span=None, metrics_chain=None, trace=None):
        """Execute a single step of the agent workflow.

        Note: Override this method to change the agent workflow for each step.

        Args:
            iter_span: Parent span handle for nesting tool spans under the iteration.
            metrics_chain: Optional metrics processor chain for hook callbacks.
            trace: Root trace handle for passing to metrics hooks.
        """
        from sgr_agent_core.observability import get_provider

        provider = get_provider()

        # Graph step counter: 3 phases per iteration (reasoning, action-selection, tool)
        graph_step_base = (self._context.iteration - 1) * 3

        reasoning = await self._reasoning_phase()
        self._context.current_step_reasoning = reasoning

        # Create generation span for reasoning phase LLM call (if subclass populated _last_llm_call)
        if self._last_llm_call is not None:
            llm_info = self._last_llm_call
            self._last_llm_call = None
            gen = provider.start_generation(
                name=llm_info.get("name", "reasoning"),
                model=llm_info.get("model"),
                model_parameters=llm_info.get("model_parameters"),
                input=llm_info.get("input"),
                metadata={"langgraph_node": "reasoning", "langgraph_step": graph_step_base + 1},
                _parent=iter_span,
            )
            provider.end_generation(gen, output=llm_info.get("output"), usage=llm_info.get("usage"))
            if metrics_chain:
                await metrics_chain.run_hook(
                    "on_generation_end",
                    gen_handle=gen,
                    llm_info=llm_info,
                    context=self._context,
                    config=self.config,
                    provider=provider,
                    trace_handle=trace,
                )

        action_tool = await self._select_action_phase(reasoning)

        # Create generation span for action-selection phase LLM call
        if self._last_llm_call is not None:
            llm_info = self._last_llm_call
            self._last_llm_call = None
            # Surface the reasoning-token count in metadata so token-heavy
            # action-selection spikes are filterable in the Langfuse UI even when
            # the SDK's typed usage only carries flat input/output.
            action_meta: dict[str, Any] = {
                "langgraph_node": "action-selection",
                "langgraph_step": graph_step_base + 2,
            }
            reasoning_tokens = (llm_info.get("usage") or {}).get("reasoning")
            if reasoning_tokens is not None:
                action_meta["reasoning_tokens"] = reasoning_tokens
            gen = provider.start_generation(
                name=llm_info.get("name", "action-selection"),
                model=llm_info.get("model"),
                model_parameters=llm_info.get("model_parameters"),
                input=llm_info.get("input"),
                metadata=action_meta,
                _parent=iter_span,
            )
            provider.end_generation(gen, output=llm_info.get("output"), usage=llm_info.get("usage"))
            if metrics_chain:
                await metrics_chain.run_hook(
                    "on_generation_end",
                    gen_handle=gen,
                    llm_info=llm_info,
                    context=self._context,
                    config=self.config,
                    provider=provider,
                    trace_handle=trace,
                )

        # Trace tool execution with full arguments
        tool_name = getattr(action_tool, "tool_name", str(action_tool))
        try:
            tool_args = action_tool.model_dump(mode="json")
        except Exception:
            tool_args = {}
        tool_span = provider.start_span(
            name=f"tool-{tool_name}",
            span_type="tool",
            input={"tool": tool_name, "arguments": tool_args},
            metadata={"langgraph_node": "tools", "langgraph_step": graph_step_base + 3},
            _parent=iter_span,
        )
        try:
            # Expose the tool span so MCP payload-processor spans (inside the tool's
            # __call__) can nest under it. Reset exactly once, success or error.
            _span_token = current_tool_span.set(tool_span)
            try:
                tool_result = await self._action_phase(action_tool)
            finally:
                current_tool_span.reset(_span_token)
            # An MCP tool error is swallowed into the result string (the loop continues),
            # but the tool layer flags it so we still mark the span ERROR for filtering.
            mcp_errored = mcp_call_errored.get()
            mcp_call_errored.set(False)
            provider.end_span(
                tool_span,
                output={"result": self._truncate(tool_result)},
                level="ERROR" if mcp_errored else "DEFAULT",
                status="MCP tool returned an error" if mcp_errored else None,
            )
            if metrics_chain:
                await metrics_chain.run_hook(
                    "on_tool_end",
                    tool_span_handle=tool_span,
                    tool_name=tool_name,
                    tool_result=tool_result,
                    context=self._context,
                    config=self.config,
                    provider=provider,
                    trace_handle=trace,
                )
            if self._context_chain:
                await self._context_chain.run_on_tool_end(
                    action_tool,
                    tool_result,
                    self._context,
                    self.config,
                    provider=provider,
                    parent_span=tool_span,
                )
        except Exception as tool_err:
            provider.end_span(
                tool_span,
                output={"error": self._truncate(str(tool_err))},
                level="ERROR",
                status=f"Tool execution failed: {tool_err}",
            )
            raise

        if isinstance(action_tool, ClarificationTool):
            self.logger.info("\n⏸️  Research paused - please answer questions")
            self.streaming_generator.finish(
                phase_id=f"{self._context.iteration}-final", content=self._context.execution_result
            )
            self._context.clarification_received.clear()
            await self._context.clarification_received.wait()

    async def cancel(self) -> None:
        """Cancel the agent execution.

        Cancels the running execute task if it exists and sets the agent
        state to CANCELLED.
        """
        if self._execute_task and not self._execute_task.done():
            self._execute_task.cancel()
            try:
                await self._execute_task
            except asyncio.CancelledError:
                pass

    async def execute(self) -> str | None:
        """Start agent execution and return the result.

        Creates an asyncio task for the agent execution, stores it
        in _execute_task for later cancellation, and awaits completion.

        Returns:
            The execution result (final answer) or None.
        """
        self._execute_task = asyncio.create_task(self._execute())
        return await self._execute_task

    async def _execute(self):
        """Internal execution loop for the agent.

        This method contains the main agent execution logic. It is
        called by execute() which wraps it in an asyncio task.
        """
        from sgr_agent_core.observability import get_provider

        provider = get_provider()

        # Start root trace for this agent execution
        def_name = self.id.rsplit("_", 1)[0] if "_" in self.id else self.id

        # Build trace input — include full MCP request payload if available
        trace_input: dict[str, Any] = {
            "task": self._truncate(self.task_messages[-1].get("content", "") if self.task_messages else ""),
            "agent_type": self.__class__.__name__,
            "messages_count": len(self.task_messages),
        }
        mcp_payload = self._context.request_metadata.get("_mcp_request_payload")
        if mcp_payload:
            trace_input["request_payload"] = mcp_payload

        # Build trace metadata — include extra fields from request_metadata
        trace_metadata: dict[str, Any] = {
            "model": self.config.llm.model,
            "max_iterations": self.config.execution.max_iterations,
        }
        # Merge all request_metadata into trace metadata (excluding internal keys)
        for key, value in self._context.request_metadata.items():
            if not key.startswith("_") and key not in ("userId", "sessionId"):
                trace_metadata[key] = value

        # Build tags — agent class name + any configured tags from request_metadata
        trace_tags = [self.__class__.__name__]
        extra_tags = self._context.request_metadata.get("_tags")
        if extra_tags:
            trace_tags.extend(extra_tags)

        trace = provider.start_trace(
            name=def_name,
            agent_id=self.id,
            input=trace_input,
            user_id=self._context.request_metadata.get("userId"),
            session_id=self._context.request_metadata.get("sessionId"),
            tags=trace_tags,
            metadata=trace_metadata,
        )

        # Build metrics processor chain (None if no processors configured = zero overhead)
        from sgr_agent_core.agent_config import GlobalConfig as _GlobalConfig
        from sgr_agent_core.observability.metrics import build_metrics_chain

        metrics_chain = build_metrics_chain(_GlobalConfig._instance or _GlobalConfig())

        # Build per-agent context processor chain (None if none configured = zero overhead).
        from sgr_agent_core.context_processors import build_context_processor_chain

        self._context_chain = build_context_processor_chain(self.config)

        if metrics_chain:
            await metrics_chain.run_hook(
                "on_trace_start",
                trace_handle=trace,
                context=self._context,
                config=self.config,
                provider=provider,
            )

        self.logger.info(f"🚀 User provided {len(self.task_messages)} messages.")
        init_message = f"Agent {self.id} started\n"
        self.conversation.append({"role": "system", "content": init_message})
        self.streaming_generator.add_content_delta(init_message, "0-start")

        # Rolling memory — compact context before reasoning loop
        _original_task_messages = self.task_messages
        try:
            from sgr_agent_core.memory.config import RollingSummaryConfig

            memory_cfg = getattr(self.config, "memory", None)
            if isinstance(memory_cfg, dict):
                rs_raw = memory_cfg.get("rolling_summary")
            else:
                rs_raw = getattr(memory_cfg, "rolling_summary", None)
            rs_cfg = RollingSummaryConfig.model_validate(rs_raw) if isinstance(rs_raw, dict) else rs_raw

            if rs_cfg is not None and rs_cfg.enabled:
                from sgr_agent_core.memory.rolling_summary import RollingSummaryBuffer

                _buf = RollingSummaryBuffer(rs_cfg, self.openai_client, self.config.llm.model)
                system_msgs, older_history, recent_window = _buf.split_conversation(self.task_messages)

                # Store recent window on context (for response fields)
                self._context.recent_messages = recent_window

                if older_history:
                    summary_text = await _buf.summarize(older_history)
                    if summary_text:
                        self.task_messages = _buf.compact_messages(system_msgs, summary_text, recent_window)
                        self._context.conversation_summary = summary_text
                        self.logger.info(
                            "Rolling memory: compacted %d messages → summary + %d recent",
                            len(_original_task_messages),
                            len(recent_window),
                        )
                    else:
                        # Summarization failed — fall back to full messages
                        self.task_messages = _original_task_messages
                        self._context.recent_messages = None
                        self.logger.warning("Rolling memory: summarization failed, using full messages")
                else:
                    # All messages fit in budget — no summarization needed
                    self._context.conversation_summary = None
        except Exception as _rs_err:
            self.logger.warning("Rolling memory init failed: %s", _rs_err)
            self.task_messages = _original_task_messages
            self._context.conversation_summary = None
            self._context.recent_messages = None

        try:
            while self._context.state not in AgentStatesEnum.FINISH_STATES.value:
                self._context.iteration += 1
                self.logger.info(f"Step {self._context.iteration} started")

                # Start iteration span
                iter_span = provider.start_span(
                    name=f"iteration-{self._context.iteration}",
                    span_type="span",
                    input={"iteration": self._context.iteration, "state": self._context.state.value},
                    metadata={"searches_used": self._context.searches_used},
                    _parent=trace,
                )
                # Expose the active iteration span so _prepare_tools (deeper in the
                # call stack) can parent any context-processor span under it.
                self._current_iter_span = iter_span

                try:
                    await self._execution_step(iter_span=iter_span, metrics_chain=metrics_chain, trace=trace)

                    # Agent Context Processor: before-finish seam. Runs right after a terminal
                    # tool drove a finish state, before the loop re-checks. A veto resets the
                    # state to a non-finish resume state and injects corrective messages.
                    if self._context_chain and self._context.state in AgentStatesEnum.FINISH_STATES.value:
                        decision = await self._context_chain.run_before_finish(
                            self._context, self.config, provider=provider, parent_span=iter_span
                        )
                        if decision.force_continue:
                            for m in decision.inject_messages:
                                self.conversation.append(m)
                            self._context.state = AgentStatesEnum.RESEARCHING

                    # Build iteration output with reasoning data if available
                    iter_output: dict[str, Any] = {"state_after": self._context.state.value}
                    reasoning = self._context.current_step_reasoning
                    if reasoning is not None:
                        try:
                            iter_output["reasoning"] = {
                                "current_situation": self._truncate(getattr(reasoning, "current_situation", None), 500),
                                "plan_status": self._truncate(getattr(reasoning, "plan_status", None), 500),
                                "remaining_steps": getattr(reasoning, "remaining_steps", []),
                                "enough_data": getattr(reasoning, "enough_data", None),
                                "task_completed": getattr(reasoning, "task_completed", None),
                            }
                        except Exception:
                            pass  # Reasoning data enrichment is best-effort
                    provider.end_span(
                        iter_span,
                        output=iter_output,
                    )
                    if metrics_chain:
                        await metrics_chain.run_hook(
                            "on_iteration_end",
                            iter_span_handle=iter_span,
                            context=self._context,
                            config=self.config,
                            provider=provider,
                            trace_handle=trace,
                        )
                except Exception as iter_err:
                    provider.end_span(
                        iter_span,
                        output={"error": str(iter_err)},
                        level="ERROR",
                    )
                    raise

            # Run metrics on_trace_end before closing trace
            if metrics_chain:
                await metrics_chain.run_hook(
                    "on_trace_end",
                    trace_handle=trace,
                    context=self._context,
                    config=self.config,
                    provider=provider,
                )

            # End trace on success — capture full structured output
            is_completed = self._context.state == AgentStatesEnum.COMPLETED
            final_status = "completed" if is_completed else self._context.state.value
            trace_output: dict[str, Any] = {
                "answer": self._truncate(self._context.execution_result),
                "status": final_status,
                "iterations": self._context.iteration,
            }
            # Include last tool execution details from log (typically FinalAnswerTool)
            if self.log:
                last_tool_log = next(
                    (entry for entry in reversed(self.log) if entry.get("step_type") == "tool_execution"), None
                )
                if last_tool_log:
                    trace_output["final_tool"] = last_tool_log.get("tool_name")
                    tool_context = last_tool_log.get("agent_tool_context")
                    if tool_context:
                        trace_output["final_tool_context"] = {
                            k: self._truncate(v, 500) if isinstance(v, str) else v
                            for k, v in tool_context.items()
                            if k in ("reasoning", "completed_steps", "answer", "status")
                        }
            provider.end_trace(
                trace,
                output=trace_output,
                status=final_status,
                # Merge any swallowed-error tags (e.g. an MCP 500) so a run that ends
                # "completed" is still filterable as error:mcp_tool.
                tags=trace_tags + sorted(self._context.error_tags),
            )
            self._flush_provider_async(provider)
            return self._context.execution_result

        except asyncio.CancelledError:
            self.logger.info("⏹️ Agent execution cancelled")
            self._context.state = AgentStatesEnum.CANCELLED
            provider.end_trace(trace, output={"error": "cancelled"}, status="cancelled")
            self._flush_provider_async(provider)
            raise

        except Exception as e:
            self.logger.error(f"❌ Agent execution error: {str(e)}")
            self._context.state = AgentStatesEnum.FAILED
            err_tags = self._context.error_tags | _classify_error_tags(e)
            provider.end_trace(
                trace,
                output={"error": str(e)},
                status=f"failed: {e}",
                tags=trace_tags + sorted(err_tags),
            )
            self._flush_provider_async(provider)
            traceback.print_exc()
        finally:
            # Restore original task_messages so agent log and post-execution code see full history
            self.task_messages = _original_task_messages

            # Emit rolling memory metadata before finishing stream
            if self.streaming_generator is not None:
                metadata: dict[str, Any] = {}
                if self._context.conversation_summary is not None:
                    metadata["conversationSummary"] = self._context.conversation_summary
                if self._context.recent_messages is not None:
                    metadata["recentMessages"] = self._context.recent_messages
                if metadata:
                    self.streaming_generator.add_metadata_event(metadata)

            if self.streaming_generator is not None:
                self.streaming_generator.finish(
                    phase_id=f"{self._context.iteration}-final", content=self._context.execution_result
                )
            self._save_agent_log()
