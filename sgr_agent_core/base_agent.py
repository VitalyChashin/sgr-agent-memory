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
from sgr_agent_core.services.prompt_loader import PromptLoader
from sgr_agent_core.services.registry import AgentRegistry
from sgr_agent_core.stream import BaseStreamingGenerator, OpenAIStreamingGenerator
from sgr_agent_core.tools import (
    BaseTool,
    ClarificationTool,
    ReasoningTool,
)


class AgentRegistryMixin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__name__ not in ("BaseAgent",):
            AgentRegistry.register(cls, name=cls.name)


class BaseAgent(AgentRegistryMixin):
    """Base class for agents."""

    name: str = "base_agent"

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

    @staticmethod
    def _truncate(value: Any, max_len: int = 2000) -> str:
        """Safely truncate any value to a string with max length."""
        if value is None:
            return ""
        s = str(value) if not isinstance(value, str) else value
        return s[:max_len] if len(s) > max_len else s

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
        return [pydantic_function_tool(tool, name=tool.tool_name) for tool in tools]

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
                    "on_generation_end", gen_handle=gen, llm_info=llm_info,
                    context=self._context, config=self.config, provider=provider, trace_handle=trace,
                )

        action_tool = await self._select_action_phase(reasoning)

        # Create generation span for action-selection phase LLM call
        if self._last_llm_call is not None:
            llm_info = self._last_llm_call
            self._last_llm_call = None
            gen = provider.start_generation(
                name=llm_info.get("name", "action-selection"),
                model=llm_info.get("model"),
                model_parameters=llm_info.get("model_parameters"),
                input=llm_info.get("input"),
                metadata={"langgraph_node": "action-selection", "langgraph_step": graph_step_base + 2},
                _parent=iter_span,
            )
            provider.end_generation(gen, output=llm_info.get("output"), usage=llm_info.get("usage"))
            if metrics_chain:
                await metrics_chain.run_hook(
                    "on_generation_end", gen_handle=gen, llm_info=llm_info,
                    context=self._context, config=self.config, provider=provider, trace_handle=trace,
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
            tool_result = await self._action_phase(action_tool)
            provider.end_span(
                tool_span,
                output={"result": self._truncate(tool_result)},
            )
            if metrics_chain:
                await metrics_chain.run_hook(
                    "on_tool_end", tool_span_handle=tool_span, tool_name=tool_name,
                    tool_result=tool_result, context=self._context, config=self.config,
                    provider=provider, trace_handle=trace,
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
            "task": self._truncate(
                self.task_messages[-1].get("content", "") if self.task_messages else ""
            ),
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
        if metrics_chain:
            await metrics_chain.run_hook(
                "on_trace_start", trace_handle=trace, context=self._context,
                config=self.config, provider=provider,
            )

        self.logger.info(f"🚀 User provided {len(self.task_messages)} messages.")
        init_message = f"Agent {self.id} started\n"
        self.conversation.append({"role": "system", "content": init_message})
        self.streaming_generator.add_content_delta(init_message, "0-start")
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

                try:
                    await self._execution_step(iter_span=iter_span, metrics_chain=metrics_chain, trace=trace)
                    # Build iteration output with reasoning data if available
                    iter_output: dict[str, Any] = {"state_after": self._context.state.value}
                    reasoning = self._context.current_step_reasoning
                    if reasoning is not None:
                        try:
                            iter_output["reasoning"] = {
                                "current_situation": self._truncate(
                                    getattr(reasoning, "current_situation", None), 500
                                ),
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
                            "on_iteration_end", iter_span_handle=iter_span, context=self._context,
                            config=self.config, provider=provider, trace_handle=trace,
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
                    "on_trace_end", trace_handle=trace, context=self._context,
                    config=self.config, provider=provider,
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
            provider.end_trace(trace, output={"error": str(e)}, status=f"failed: {e}")
            self._flush_provider_async(provider)
            traceback.print_exc()
        finally:
            if self.streaming_generator is not None:
                self.streaming_generator.finish(
                    phase_id=f"{self._context.iteration}-final", content=self._context.execution_result
                )
            self._save_agent_log()
