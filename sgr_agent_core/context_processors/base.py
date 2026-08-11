"""Agent Context Processor plugin system.

The third processor-plugin family in SGR Agent Core (after the MCP payload
processor and the metrics processor). It fills the "read-write at loop seams"
quadrant: a context processor runs at agent-loop seams and may *change* what the
agent does — drop a repeated tool before tool selection, or veto a premature
finish and inject a corrective message.

It reuses the established skeleton (see ``notes/processor-plugin-pattern.md``):
an ABC that auto-registers concrete subclasses, a ``*Definition`` Pydantic
model, a ``*Chain`` runner, and registry-then-import-string resolution.

Observability is **optional and per-processor**. The base contract emits
nothing; every hook is merely *offered* the active ``provider`` and a
``parent_span`` via kwargs. A processor that wants monitoring calls
``emit_event_span`` (a no-op under ``NoOpProvider``); one that doesn't ignores
the kwargs.
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from sgr_agent_core.observability.context import processor_span_end, processor_span_start
from sgr_agent_core.services.registry import Registry

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.base_tool import BaseTool
    from sgr_agent_core.models import AgentContext

logger = logging.getLogger(__name__)


@dataclass
class FinishDecision:
    """Directive returned by ``on_before_finish`` to veto a premature finish.

    ``force_continue`` keeps the agent running; ``inject_messages`` are OpenAI
    message dicts appended to the conversation before the next iteration.
    """

    force_continue: bool = False
    inject_messages: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None


@dataclass
class PrepareToolsResult:
    """Outcome of the ``on_prepare_tools`` seam.

    ``drop`` are tool names removed from the toolkit before selection.
    ``inject_messages`` are OpenAI message dicts appended to the conversation so
    the directive reaches the **same** iteration's action-selection call (the
    agent prepares tools before context — see the reorder in the FC agents).
    """

    drop: set[str] = field(default_factory=set)
    inject_messages: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def coerce(cls, value: Any) -> PrepareToolsResult:
        """Normalize a hook return into a ``PrepareToolsResult``.

        Accepts a legacy ``set[str]`` (drops only, no messages), an existing
        ``PrepareToolsResult``, or ``None``/falsey (empty result).
        """
        if isinstance(value, PrepareToolsResult):
            return value
        if not value:
            return cls()
        if isinstance(value, (set, frozenset, list, tuple)):
            return cls(drop=set(value))
        # Unknown shape: ignore defensively rather than crash the seam.
        logger.warning("on_prepare_tools returned unexpected type %s; ignoring.", type(value).__name__)
        return cls()


class AgentContextProcessorRegistry(Registry["AgentContextProcessor"]):
    """Registry for agent context processor classes."""


class AgentContextProcessor(ABC):
    """Abstract base for agent context processors.

    Runs read-write at agent-loop seams. All hooks are optional and default to
    a no-op; override only the seams you need. Subclasses auto-register in
    ``AgentContextProcessorRegistry`` on definition.

    Every hook also receives ``provider`` (the active ``ObservabilityProvider``)
    and ``parent_span`` (a span handle or ``None``) in ``**kw`` for *optional*
    span emission via :func:`emit_event_span`. The base class emits nothing.
    """

    #: Span emission mode, set per-processor by ``build_context_processor_chain``.
    #: "off" → no spans (provider withheld); "fired" → only fired-action markers
    #: (default, today's behaviour); "always" → also wrap every invocation in a span.
    _span_mode: str = "fired"

    def __init__(self, processor_config: dict[str, Any] | None = None):
        self.processor_config = processor_config or {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", set()):
            AgentContextProcessorRegistry.register(cls, name=cls.__name__)

    async def on_tool_end(
        self,
        *,
        tool: BaseTool,
        result: str,
        context: AgentContext,
        config: AgentConfig,
        **kw: Any,
    ) -> None:
        """Called after each tool executes. Receives the tool *instance*."""
        return None

    async def on_prepare_tools(
        self,
        *,
        toolkit: list[type[BaseTool]],
        context: AgentContext,
        config: AgentConfig,
        **kw: Any,
    ) -> set[str] | PrepareToolsResult:
        """Called before tool selection.

        Return a ``set[str]`` of tool names to drop (legacy), or a
        :class:`PrepareToolsResult` to also inject conversation messages (e.g. a
        "tool X disabled" directive) into the *same* iteration's selection call.
        """
        return set()

    async def on_before_finish(
        self,
        *,
        context: AgentContext,
        config: AgentConfig,
        **kw: Any,
    ) -> FinishDecision | None:
        """Called when the agent enters a finish state. Return a veto or None."""
        return None


class AgentContextProcessorChain:
    """Ordered chain of context processors. Fail-safe-but-visible per hook."""

    def __init__(self, processors: list[AgentContextProcessor]):
        self.processors = processors

    @staticmethod
    def _span_setup(p: AgentContextProcessor, provider: Any, parent_span: Any, hook: str) -> tuple[Any, Any]:
        """Resolve the effective provider + wrapping span for one processor invocation.

        ``span_mode`` "off" withholds the provider (so the processor's own
        ``emit_event_span`` markers also fall silent); "always" opens a timed wrapping
        span; "fired" (default) leaves today's behaviour untouched.
        """
        mode = getattr(p, "_span_mode", "fired")
        if mode == "off":
            return None, None
        span = None
        if mode == "always":
            span = processor_span_start(provider, parent_span, name=f"ctx-processor.{type(p).__name__}.{hook}")
        return provider, span

    async def run_on_tool_end(
        self,
        tool: BaseTool,
        result: str,
        context: AgentContext,
        config: AgentConfig,
        *,
        provider: Any = None,
        parent_span: Any = None,
    ) -> None:
        for p in self.processors:
            p_provider, span = self._span_setup(p, provider, parent_span, "on_tool_end")
            try:
                await p.on_tool_end(
                    tool=tool,
                    result=result,
                    context=context,
                    config=config,
                    provider=p_provider,
                    parent_span=parent_span,
                )
                processor_span_end(p_provider, span)
            except Exception as e:
                processor_span_end(p_provider, span, output={"error": str(e)}, level="ERROR")
                logger.warning("ctx-proc %s.on_tool_end failed: %s: %s", type(p).__name__, type(e).__name__, e)

    async def run_prepare_tools(
        self,
        toolkit: list[type[BaseTool]],
        context: AgentContext,
        config: AgentConfig,
        *,
        provider: Any = None,
        parent_span: Any = None,
    ) -> PrepareToolsResult:
        drop: set[str] = set()
        inject_messages: list[dict[str, Any]] = []
        for p in self.processors:
            p_provider, span = self._span_setup(p, provider, parent_span, "on_prepare_tools")
            try:
                result = PrepareToolsResult.coerce(
                    await p.on_prepare_tools(
                        toolkit=toolkit,
                        context=context,
                        config=config,
                        provider=p_provider,
                        parent_span=parent_span,
                    )
                )
                drop |= result.drop
                inject_messages.extend(result.inject_messages)
                processor_span_end(
                    p_provider,
                    span,
                    output={"dropped": sorted(result.drop), "injected": len(result.inject_messages)},
                )
            except Exception as e:
                # Fail-safe: this processor contributes no drops/messages, agent keeps full toolkit.
                processor_span_end(p_provider, span, output={"error": str(e)}, level="ERROR")
                logger.warning("ctx-proc %s.on_prepare_tools failed: %s: %s", type(p).__name__, type(e).__name__, e)
        # NEVER drop system/terminal tools — the agent must always be able to finish.
        system_names = {t.tool_name for t in toolkit if getattr(t, "isSystemTool", False)}
        return PrepareToolsResult(drop=drop - system_names, inject_messages=inject_messages)

    async def run_before_finish(
        self,
        context: AgentContext,
        config: AgentConfig,
        *,
        provider: Any = None,
        parent_span: Any = None,
    ) -> FinishDecision:
        merged = FinishDecision()
        for p in self.processors:
            p_provider, span = self._span_setup(p, provider, parent_span, "on_before_finish")
            try:
                d = await p.on_before_finish(
                    context=context,
                    config=config,
                    provider=p_provider,
                    parent_span=parent_span,
                )
            except Exception as e:
                # Fail-safe: a throwing veto lets the agent finish (never loop forever).
                processor_span_end(p_provider, span, output={"error": str(e)}, level="ERROR")
                logger.warning("ctx-proc %s.on_before_finish failed: %s: %s", type(p).__name__, type(e).__name__, e)
                continue
            processor_span_end(p_provider, span, output={"force_continue": bool(d and d.force_continue)})
            if d and d.force_continue:
                merged.force_continue = True
                merged.inject_messages.extend(d.inject_messages)
        return merged


class ContextProcessorDefinition(BaseModel, extra="allow"):
    """Definition of a single context processor in YAML config."""

    class_name: str = Field(alias="class", description="Processor class name or import string")
    config: dict[str, Any] = Field(default_factory=dict)
    span_mode: str = Field(
        default="fired",
        description="Span emission: 'off' (none), 'fired' (only fired-action markers, default), "
        "'always' (also wrap every invocation in a timed span).",
    )


def build_context_processor_chain(config: AgentConfig) -> AgentContextProcessorChain | None:
    """Build a per-agent context processor chain from config.

    Returns ``None`` when no processors are configured (zero-cost path). Built
    once per run so processor instances hold per-request state with no
    cross-request leakage.
    """
    defs = getattr(config, "context_processors", None) or []
    if not defs:
        return None

    processors: list[AgentContextProcessor] = []
    for raw in defs:
        try:
            defn = (
                raw if isinstance(raw, ContextProcessorDefinition) else ContextProcessorDefinition.model_validate(raw)
            )
        except Exception as e:
            logger.warning("Failed to parse context processor definition %r: %s. Skipping.", raw, e)
            continue
        # Resolve class: registry first, then dotted import string.
        cls = AgentContextProcessorRegistry.get(defn.class_name)
        if cls is None:
            try:
                module_path, class_name = defn.class_name.rsplit(".", 1)
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
            except (ValueError, ImportError, AttributeError) as e:
                logger.warning(
                    "Context processor '%s' not found in registry and cannot be imported: %s. Skipping.",
                    defn.class_name,
                    e,
                )
                continue
        try:
            proc = cls(defn.config)
            proc._span_mode = defn.span_mode
            processors.append(proc)
        except Exception as e:
            logger.warning("Failed to initialize context processor '%s': %s. Skipping.", defn.class_name, e)

    if not processors:
        return None

    logger.info("Context processor chain initialized with %d processor(s)", len(processors))
    return AgentContextProcessorChain(processors)


def emit_event_span(provider: Any, parent_span: Any, name: str, metadata: dict[str, Any]) -> None:
    """Emit a zero-duration event span. No-op under ``NoOpProvider``, so always safe to call.

    Used by built-in processors to record *only when they actually fire* (a
    tool is dropped / a finish is vetoed). Not referenced by the base class.
    """
    if provider is None:
        return
    try:
        span = provider.start_span(
            name=name,
            span_type="event",
            input=metadata,
            metadata=metadata,
            _parent=parent_span,
        )
        provider.end_span(span, output=metadata)
    except Exception as e:
        logger.warning("emit_event_span(%s) failed: %s: %s", name, type(e).__name__, e)
