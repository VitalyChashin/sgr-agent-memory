"""Built-in context processor: require a work tool call before finishing (Issue 2).

A router agent can "answer itself" — call the terminal ``FinalAnswerTool`` with
zero prior *work* tool calls (a work tool is one with ``isSystemTool == False``,
e.g. delegating to a sub-agent). This processor counts work-tool calls per run
and, when the agent tries to finish with fewer than ``min_tool_calls``, vetoes
the finish and injects a corrective message — up to ``max_retries`` times, after
which it lets the agent finish (safety valve, never an infinite veto).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sgr_agent_core.context_processors.base import AgentContextProcessor, FinishDecision, emit_event_span

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.base_tool import BaseTool
    from sgr_agent_core.models import AgentContext

logger = logging.getLogger(__name__)

_DEFAULT_MESSAGE = (
    "On the previous step you did not call any tool. You must call a tool "
    "(e.g. delegate to a sub-agent) before finishing."
)


class MandatoryToolCallProcessor(AgentContextProcessor):
    """Veto a finish that happens before ``min_tool_calls`` work-tool calls.

    Config:
        min_tool_calls (int): required number of work-tool calls. Default 1.
        max_retries (int): how many times to veto before allowing finish. Default 2.
        message (str): corrective message injected on veto.
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        super().__init__(processor_config)
        self.min_tool_calls = int(self.processor_config.get("min_tool_calls", 1))
        self.max_retries = int(self.processor_config.get("max_retries", 2))
        self.message = self.processor_config.get("message", _DEFAULT_MESSAGE)
        self._work_calls = 0
        self._retries = 0

    async def on_tool_end(
        self,
        *,
        tool: BaseTool,
        result: str,
        context: AgentContext,
        config: AgentConfig,
        **kw: Any,
    ) -> None:
        if not getattr(tool, "isSystemTool", False):
            self._work_calls += 1

    async def on_before_finish(
        self,
        *,
        context: AgentContext,
        config: AgentConfig,
        provider: Any = None,
        parent_span: Any = None,
        **kw: Any,
    ) -> FinishDecision | None:
        if self._work_calls < self.min_tool_calls and self._retries < self.max_retries:
            self._retries += 1
            emit_event_span(
                provider,
                parent_span,
                name="ctx-processor.mandatory_tool_call.vetoed",
                metadata={
                    "work_calls": self._work_calls,
                    "min_tool_calls": self.min_tool_calls,
                    "retry": self._retries,
                    "iteration": getattr(context, "iteration", None),
                },
            )
            logger.info(
                "MandatoryToolCallProcessor vetoing finish (work_calls=%d < %d, retry %d/%d)",
                self._work_calls,
                self.min_tool_calls,
                self._retries,
                self.max_retries,
            )
            return FinishDecision(
                force_continue=True,
                inject_messages=[{"role": "user", "content": self.message}],
                reason="router finished with no work calls",
            )
        return None
