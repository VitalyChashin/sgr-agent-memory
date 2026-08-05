"""Emit a ToolCallingAgent trace carrying action-selection reasoning/CoT.

Companion to `langfuse_tool_trace_demo.py`, aimed at the reasoning-tracing work:
each action-selection generation records the model's internal CoT plus a
reasoning/cached token breakdown (see notes/action-selection-reasoning-tracing.md).

Needs a provider that actually returns reasoning *text* — OpenAI only reports
reasoning token counts, so this uses OpenRouter with a thinking model.

Usage:
    OPENROUTER_API_KEY=... uv run python scripts/langfuse_reasoning_trace_demo.py

Requires the local Langfuse at http://localhost:3000 and the langfuse SDK
(`uv pip install "langfuse>=2.0.0,<3.0.0"`).
"""

from __future__ import annotations

import asyncio
import os

from openai import AsyncOpenAI
from pydantic import Field

from sgr_agent_core.agent_config import GlobalConfig
from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig
from sgr_agent_core.agents import ToolCallingAgent
from sgr_agent_core.base_tool import BaseTool
from sgr_agent_core.observability import get_provider, init_provider
from sgr_agent_core.observability.config import LangfuseConfig, ObservabilityConfig
from sgr_agent_core.tools import FinalAnswerTool

LANGFUSE_PUBLIC_KEY = "pk-lf-519ae97e-7f15-42fa-ab0f-42e7be77fd64"
LANGFUSE_SECRET_KEY = "sk-lf-fa49d4be-6763-400d-9542-76736c056e65"
LANGFUSE_HOST = "http://localhost:3000"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Cheap thinking model that streams `reasoning` deltas and reports both
# reasoning_tokens and cached_tokens. Any tools+reasoning OpenRouter model works.
MODEL = os.environ.get("DEMO_MODEL", "z-ai/glm-4.7-flash")


class GetWeatherTool(BaseTool):
    """Get the current weather for a given city."""

    tool_name = "get_weather"
    city: str = Field(description="The city to get the current weather for")

    async def __call__(self, context, config, **kwargs) -> str:  # noqa: ANN001
        return f"The current weather in {self.city} is 18 degrees Celsius, partly cloudy."


class CelsiusToFahrenheitTool(BaseTool):
    """Convert a temperature from Celsius to Fahrenheit."""

    tool_name = "celsius_to_fahrenheit"
    celsius: float = Field(description="Temperature in degrees Celsius")

    async def __call__(self, context, config, **kwargs) -> str:  # noqa: ANN001
        return f"{self.celsius}C = {self.celsius * 9 / 5 + 32}F"


def configure_observability() -> None:
    gc = GlobalConfig()
    gc.observability = ObservabilityConfig(
        enabled=True,
        provider="langfuse",
        capture_tool_definitions=True,
        langfuse=LangfuseConfig(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            base_url=LANGFUSE_HOST,
            flush_at=1,
            flush_interval=1.0,
        ),
    )
    init_provider(gc)


async def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY to run this demo.")

    configure_observability()

    cfg = AgentConfig(
        llm=LLMConfig(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            model=MODEL,
            temperature=0.4,
            max_tokens=2000,
            # LLMConfig allows extras, and they flow into the client kwargs — this is
            # how OpenRouter is asked to stream the reasoning trace back.
            extra_body={"reasoning": {"enabled": True}},
        ),
        prompts=PromptsConfig(),
        execution=ExecutionConfig(max_iterations=8),
    )

    client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)

    task = (
        "What is the weather in Paris right now? Then convert that temperature to "
        "Fahrenheit, and give me a final answer summarizing both."
    )

    agent = ToolCallingAgent(
        task_messages=[{"role": "user", "content": task}],
        openai_client=client,
        agent_config=cfg,
        toolkit=[GetWeatherTool, CelsiusToFahrenheitTool, FinalAnswerTool],
        def_name="reasoning_demo_agent",
    )

    result = await agent.execute()

    provider = get_provider()
    provider.flush()
    provider.shutdown()

    print("\n=== Agent result ===")
    print(result)
    print(f"\nTrace emitted to {LANGFUSE_HOST} (model: {MODEL}).")
    print("Langfuse UI → latest trace 'reasoning_demo_agent' → any action-selection")
    print("generation → Output: {content, tool_call, reasoning}; Metadata: reasoning_tokens.")


if __name__ == "__main__":
    asyncio.run(main())
