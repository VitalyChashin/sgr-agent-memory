from typing import Literal, Type

from openai import AsyncOpenAI

from sgr_agent_core.agent_config import AgentConfig
from sgr_agent_core.base_agent import BaseAgent
from sgr_agent_core.tools import (
    BaseTool,
)


class ToolCallingAgent(BaseAgent):
    """Tool Calling Research Agent relying entirely on LLM native function
    calling."""

    name: str = "tool_calling_agent"

    def __init__(
        self,
        task_messages: list,
        openai_client: AsyncOpenAI,
        agent_config: AgentConfig,
        toolkit: list[Type[BaseTool]],
        def_name: str | None = None,
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

    async def _reasoning_phase(self) -> None:
        """No explicit reasoning phase, reasoning is done internally by LLM."""
        return None

    async def _select_action_phase(self, reasoning=None) -> BaseTool:
        phase_id = f"{self._context.iteration}-action"
        # Prepare tools first: the prepare-tools seam may drop tools and inject
        # directives into the conversation, which _prepare_context must then snapshot.
        tool_defs = await self._prepare_tools()
        messages = await self._prepare_context()
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
        tool = completion.choices[0].message.tool_calls[0].function.parsed_arguments

        if not isinstance(tool, BaseTool):
            raise ValueError("Selected tool is not a valid BaseTool instance")
        self.conversation.append(
            {
                "role": "assistant",
                "content": None,
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
