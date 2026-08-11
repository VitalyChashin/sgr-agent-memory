"""Probe whether an endpoint actually does prompt-prefix caching.

Sends the framework's *real* request payload (`_prepare_tools()` +
`_prepare_context()`) repeatedly and reports `prompt_tokens` / `cached_tokens`.

Why this exists: `_select_action_phase` always streams, and a LiteLLM router in
front of vLLM synthesizes stream usage locally — dropping `prompt_tokens_details`.
So `usage.cached` reads zero even when caching works fine. This probe goes
**non-streaming** to get the truth, and also runs one streaming call so you can see
whether your gateway hides the numbers.
See research/prefix-caching-tool-calling-agent.md.

Usage:
    # is caching on at all?
    OPENAI_API_KEY=... uv run python scripts/prefix_cache_probe.py \
        --base-url https://your-litellm/v1 --model your-model

    # where does caching start? (providers often ignore short prompts)
    ... scripts/prefix_cache_probe.py --model your-model --sweep 1000,2000,4000,8000

API key: --api-key, or $OPENAI_API_KEY, or --key-var NAME to read another variable.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from openai import AsyncOpenAI

from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig
from sgr_agent_core.agents import ToolCallingAgent
from sgr_agent_core.tools import ClarificationTool, FinalAnswerTool

TOOLKIT = [ClarificationTool, FinalAnswerTool]


def _system_prompt(target_tokens: int) -> str:
    """A deterministic system prompt of roughly `target_tokens` tokens."""
    # ~20 tokens per line; exact size does not matter, actual prompt_tokens is printed.
    lines = "".join(
        f"Operating guideline {i}: verify every factual claim against a tool result "
        f"before asserting it, and never invent values for field group {i}.\n"
        for i in range(max(1, target_tokens // 20))
    )
    return f"You are a precise assistant.\n{lines}\n<TOOLS>\n{{available_tools}}\n</TOOLS>"


async def build_payload(client: AsyncOpenAI, args, target_tokens: int):
    """The exact tools+messages a ToolCallingAgent would send, made deterministic."""
    cfg = AgentConfig(
        llm=LLMConfig(api_key=args.api_key, base_url=args.base_url, model=args.model, temperature=0.0, max_tokens=64),
        prompts=PromptsConfig(system_prompt_str=_system_prompt(target_tokens)),
        execution=ExecutionConfig(max_iterations=8, logs_dir=None),
    )
    agent = ToolCallingAgent(
        task_messages=[{"role": "user", "content": "Summarise the reference material."}],
        openai_client=client,
        agent_config=cfg,
        toolkit=list(TOOLKIT),
        def_name="cache_probe",
    )
    # Pin the transcript: a live run's first message embeds the agent's UUID, which
    # would make every process send a different prefix and mask any caching.
    agent.conversation = [{"role": "assistant", "content": "Working on it."}]
    return await agent._prepare_tools(), await agent._prepare_context()


async def call(client, args, tools, messages, stream: bool) -> tuple[int, int | None]:
    """Returns (prompt_tokens, cached_tokens) — cached is None if the endpoint omits it."""
    kwargs = dict(
        model=args.model, messages=messages, tools=tools, tool_choice="required", max_tokens=256, temperature=0.0
    )
    if stream:
        # Raw stream, not the .stream() parse helper: we only want the usage chunk, and
        # the helper raises if the model hits max_tokens mid tool-call.
        usage = None
        async for chunk in await client.chat.completions.create(
            **kwargs, stream=True, stream_options={"include_usage": True}
        ):
            usage = chunk.usage or usage
    else:
        usage = (await client.chat.completions.create(**kwargs)).usage
    if usage is None:
        return 0, None
    details = getattr(usage, "prompt_tokens_details", None)
    return usage.prompt_tokens, (getattr(details, "cached_tokens", None) if details else None)


def _fmt(prompt: int, cached: int | None) -> str:
    if cached is None:
        return f"prompt={prompt:<8} cached=ABSENT (endpoint omits prompt_tokens_details)"
    return f"prompt={prompt:<8} cached={cached:<8} {100 * cached / prompt if prompt else 0:.0f}%"


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--key-var", default="OPENAI_API_KEY", help="env var holding the API key")
    p.add_argument("--api-key", default=None)
    p.add_argument("--repeat", type=int, default=4, help="identical non-streaming calls to send")
    p.add_argument("--tokens", type=int, default=8000, help="approx system-prompt size")
    p.add_argument("--sweep", default=None, help="comma-separated sizes to probe for the caching floor")
    args = p.parse_args()
    args.api_key = args.api_key or os.environ[args.key_var]

    client = AsyncOpenAI(api_key=args.api_key, base_url=args.base_url)
    print(f"endpoint={args.base_url} model={args.model}\n")

    if args.sweep:
        # `--repeat` calls per size, best result wins: a round-robin router scatters
        # consecutive calls across upstreams, so a single miss proves nothing.
        print(f"Caching floor sweep ({args.repeat} identical calls each, best shown):")
        for size in (int(s) for s in args.sweep.split(",")):
            tools, messages = await build_payload(client, args, size)
            best, prompt = None, 0
            for _ in range(args.repeat):
                prompt, cached = await call(client, args, tools, messages, stream=False)
                best = cached if best is None else max(best, cached or 0)
            print(f"  ~{size:<7} {_fmt(prompt, best)}")
        return

    tools, messages = await build_payload(client, args, args.tokens)

    print(f"Non-streaming, {args.repeat}x identical payload (call 1 is cold):")
    results = []
    ns_prompt = 0
    for i in range(args.repeat):
        ns_prompt, cached = await call(client, args, tools, messages, stream=False)
        results.append(cached)
        print(f"  call {i + 1}: {_fmt(ns_prompt, cached)}")

    print("\nStreaming, same payload (this is the path the agent actually uses):")
    stream_prompt, stream_cached = await call(client, args, tools, messages, stream=True)
    print(f"  stream : {_fmt(stream_prompt, stream_cached)}")
    if stream_prompt != ns_prompt:
        print(f"  NOTE: same payload counted as {ns_prompt} non-streaming vs {stream_prompt} streaming")
        print("        — the gateway is synthesizing stream usage instead of relaying it.")

    warm = [c for c in results[1:] if c]
    print("\nVerdict:")
    if all(c is None for c in results):
        print("  Endpoint never reports cached_tokens, even non-streaming. Caching is")
        print("  UNMEASURABLE from the client — check vLLM's gpu_prefix_cache_hit_rate.")
    elif not warm:
        print(f"  Reported, but 0 cached on every warm call at ~{ns_prompt} tokens.")
        print("  Either caching is off, or this prompt is under the provider's floor")
        print("  (re-run with --sweep 1000,2000,4000,8000,16000), or the router is")
        print("  scattering calls across upstreams with separate KV caches.")
    elif stream_cached is None:
        print(f"  Caching WORKS ({max(warm)}/{ns_prompt} cached non-streaming) but the")
        print("  streaming path reports nothing — so the agent's usage.cached is blind.")
        print("  Gateway-side issue (LiteLLM synthesizes stream usage). Not a caching bug.")
    else:
        print(f"  Caching works and is visible on streams ({stream_cached}/{stream_prompt}).")
        print("  usage.cached in agent logs is trustworthy here.")


if __name__ == "__main__":
    asyncio.run(main())
