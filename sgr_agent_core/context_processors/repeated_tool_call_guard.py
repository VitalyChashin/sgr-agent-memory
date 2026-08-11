"""Built-in context processor: guard against repeated tool calls (Issue 1).

A leaf agent can get stuck calling the same tool over and over (often a failing
MCP call that returns ``"Error: ..."``) until ``max_iterations``. This guard
counts tool calls per run and, once a tool crosses ``max_repeats``, drops it
from the toolkit at the next ``on_prepare_tools`` seam so the agent is forced to
finish instead of looping.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from sgr_agent_core.context_processors.base import AgentContextProcessor, PrepareToolsResult, emit_event_span

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.base_tool import BaseTool
    from sgr_agent_core.models import AgentContext

logger = logging.getLogger(__name__)

_DEFAULT_MESSAGE = (
    "The tool '{tool}' has been disabled after repeated failed calls. Do not "
    "attempt to call it again — finalize your answer with the information you "
    "already have."
)


class RepeatedToolCallGuard(AgentContextProcessor):
    """Drop a tool after it has been called ``max_repeats`` times in a run.

    Config:
        max_repeats (int): threshold at which the tool is dropped. Default 3.
        scope ("exact_args" | "tool_name"): count identical-argument calls, or
            all calls of the same tool name. Default "exact_args".
        failed_only (bool): only count calls whose result starts with "Error:".
            Default False.
        announce (bool): inject a one-time "tool disabled" directive into the
            conversation the first time each tool is dropped, so the model learns
            why the tool vanished instead of reconciling a silent contradiction.
            Default True.
        message (str): template for the injected directive; ``{tool}`` is
            substituted with the dropped tool name.
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        super().__init__(processor_config)
        self.max_repeats = int(self.processor_config.get("max_repeats", 3))
        self.scope = self.processor_config.get("scope", "exact_args")
        self.failed_only = bool(self.processor_config.get("failed_only", False))
        self.announce = bool(self.processor_config.get("announce", True))
        self.message = self.processor_config.get("message", _DEFAULT_MESSAGE)
        self._counts: dict[str, int] = {}
        self._key_tool: dict[str, str] = {}
        # Tools whose "disabled" directive has already been injected (announce once).
        self._announced: set[str] = set()

    def _key(self, tool: BaseTool) -> str:
        if self.scope == "tool_name":
            return tool.tool_name
        payload = json.dumps(tool.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(f"{tool.tool_name}{payload}".encode("utf-8")).hexdigest()

    async def on_tool_end(
        self,
        *,
        tool: BaseTool,
        result: str,
        context: AgentContext,
        config: AgentConfig,
        **kw: Any,
    ) -> None:
        if getattr(tool, "isSystemTool", False):
            return
        if self.failed_only and not str(result).startswith("Error:"):
            return
        key = self._key(tool)
        self._counts[key] = self._counts.get(key, 0) + 1
        self._key_tool[key] = tool.tool_name

    async def on_prepare_tools(
        self,
        *,
        toolkit: list[type[BaseTool]],
        context: AgentContext,
        config: AgentConfig,
        provider: Any = None,
        parent_span: Any = None,
        **kw: Any,
    ) -> PrepareToolsResult:
        # Map every crossed key back to its tool name.
        crossed = {k: c for k, c in self._counts.items() if c >= self.max_repeats}
        drop = {self._key_tool[k] for k in crossed}
        inject_messages: list[dict[str, Any]] = []
        for name in sorted(drop):
            count = max(c for k, c in crossed.items() if self._key_tool[k] == name)
            # Announce each dropped tool exactly once, on the turn it first crosses.
            announced_now = self.announce and name not in self._announced
            if announced_now:
                self._announced.add(name)
                inject_messages.append({"role": "user", "content": self.message.format(tool=name)})
            emit_event_span(
                provider,
                parent_span,
                name="ctx-processor.repeated_tool_call_guard.dropped",
                metadata={
                    "tool": name,
                    "count": count,
                    "scope": self.scope,
                    "announce": self.announce,
                    "announced_now": announced_now,
                    "iteration": getattr(context, "iteration", None),
                },
            )
            logger.info("RepeatedToolCallGuard dropping tool '%s' after %d repeats", name, count)
        return PrepareToolsResult(drop=drop, inject_messages=inject_messages)
