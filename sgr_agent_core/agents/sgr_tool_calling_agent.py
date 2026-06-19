from typing import Literal, Type

from openai import AsyncOpenAI, pydantic_function_tool

from sgr_agent_core.agent_config import AgentConfig
from sgr_agent_core.base_agent import BaseAgent
from sgr_agent_core.models import AgentStatesEnum
from sgr_agent_core.tools import (
    BaseTool,
    FinalAnswerTool,
    ReasoningTool,
    SystemBaseTool,
)


class SGRToolCallingAgent(BaseAgent):
    """Agent that uses OpenAI native function calling to select and execute
    tools based on SGR like a reasoning scheme."""

    name: str = "sgr_tool_calling_agent"

    def __init__(
        self,
        task_messages: list,
        openai_client: AsyncOpenAI,
        agent_config: AgentConfig,
        toolkit: list[Type[BaseTool]],
        *,
        def_name: str | None = None,
        reasoning_tool_cls: type[SystemBaseTool] = ReasoningTool,
        **kwargs: dict,
    ):
        super().__init__(
            task_messages=task_messages,
            openai_client=openai_client,
            agent_config=agent_config,
            toolkit=toolkit,
            def_name=def_name,
            **kwargs,
        )
        self.tool_choice: Literal["required"] = "required"
        self.ReasoningTool: type[SystemBaseTool] = reasoning_tool_cls

    async def _reasoning_phase(self) -> ReasoningTool:
        phase_id = f"{self._context.iteration}-reasoning"
        messages = await self._prepare_context()
        tool_defs = [pydantic_function_tool(self.ReasoningTool, name=self.ReasoningTool.tool_name)]
        async with self.openai_client.chat.completions.stream(
            messages=messages,
            tools=tool_defs,
            tool_choice=self.tool_choice,
            **self.config.llm.to_openai_client_kwargs(),
        ) as stream:
            async for event in stream:
                if event.type == "chunk":
                    self.streaming_generator.add_chunk(event.chunk, phase_id)
            final_completion = await stream.get_final_completion()
        reasoning: ReasoningTool = final_completion.choices[0].message.tool_calls[0].function.parsed_arguments
        # Populate LLM call info for observability generation spans
        usage = final_completion.usage
        response_msg = final_completion.choices[0].message
        self._last_llm_call = {
            "name": "reasoning",
            "model": self.config.llm.model,
            "model_parameters": {"temperature": self.config.llm.temperature, "max_tokens": self.config.llm.max_tokens},
            "usage": {"input": usage.prompt_tokens, "output": usage.completion_tokens} if usage else None,
            "input": self._build_gen_input(messages, tool_defs),
            "output": response_msg.content or self._truncate(reasoning.model_dump_json()),
        }
        self.streaming_generator.add_tool_call(phase_id, reasoning)
        self.conversation.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "function",
                        "id": phase_id,
                        "function": {
                            "name": reasoning.tool_name,
                            "arguments": reasoning.model_dump_json(),
                        },
                    }
                ],
            }
        )
        tool_call_result = await reasoning(self._context, self.config)
        self.streaming_generator.add_tool_result(phase_id, tool_call_result, reasoning.tool_name)
        self.conversation.append({"role": "tool", "content": tool_call_result, "tool_call_id": phase_id})
        self._log_reasoning(reasoning)
        return reasoning

    async def _select_action_phase(self, reasoning: ReasoningTool) -> BaseTool:
        phase_id = f"{self._context.iteration}-action"
        messages = await self._prepare_context()
        tool_defs = await self._prepare_tools()
        async with self.openai_client.chat.completions.stream(
            messages=messages,
            tools=tool_defs,
            tool_choice=self.tool_choice,
            **self.config.llm.to_openai_client_kwargs(),
        ) as stream:
            async for event in stream:
                if event.type == "chunk":
                    self.streaming_generator.add_chunk(event.chunk, phase_id)
            completion = await stream.get_final_completion()
        # Populate LLM call info for observability generation spans
        usage = completion.usage
        response_msg = completion.choices[0].message
        self._last_llm_call = {
            "name": "action-selection",
            "model": self.config.llm.model,
            "model_parameters": {"temperature": self.config.llm.temperature, "max_tokens": self.config.llm.max_tokens},
            "usage": {"input": usage.prompt_tokens, "output": usage.completion_tokens} if usage else None,
            "input": self._build_gen_input(messages, tool_defs),
            "output": self._truncate(response_msg.content or str(response_msg.tool_calls)),
        }
        try:
            tool = completion.choices[0].message.tool_calls[0].function.parsed_arguments
        except (IndexError, AttributeError, TypeError):
            final_content = completion.choices[0].message.content or "Task completed successfully"
            tool = FinalAnswerTool(
                reasoning="Agent decided to complete the task",
                completed_steps=[],
                answer=final_content,
                status=AgentStatesEnum.COMPLETED,
            )
        if not isinstance(tool, BaseTool):
            raise ValueError("Selected tool is not a valid BaseTool instance")

        self.conversation.append(
            {
                "role": "assistant",
                "content": reasoning.remaining_steps[0] if reasoning.remaining_steps else "Completing",
                "tool_calls": [
                    {
                        "type": "function",
                        "id": phase_id,
                        "function": {
                            "name": tool.tool_name,
                            "arguments": tool.model_dump_json(),
                        },
                    }
                ],
            }
        )
        self.streaming_generator.add_tool_call(phase_id, tool)
        return tool

    async def _action_phase(self, tool: BaseTool) -> str:
        phase_id = f"{self._context.iteration}-action"
        result = await tool(self._context, self.config, **self.tool_configs.get(tool.tool_name, {}))
        self.conversation.append({"role": "tool", "content": result, "tool_call_id": phase_id})
        self.streaming_generator.add_tool_result(phase_id, result, tool.tool_name)
        self._log_tool_execution(tool, result)
        return result
