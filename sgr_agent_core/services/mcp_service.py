import importlib
import logging
from typing import Any, Type

from fastmcp import Client
from fastmcp.mcp_config import MCPConfig
from jambo import SchemaConverter
from pydantic import create_model

logger = logging.getLogger(__name__)


class MCP2ToolConverter:
    @staticmethod
    def _to_CamelCase(name: str) -> str:
        return name.replace("_", " ").title().replace(" ", "")

    @classmethod
    def _build_processor_chain(cls, processor_defs: list[dict[str, Any]]) -> tuple[Any, list[str]]:
        """Build a processor chain from config definitions.

        Returns:
            Tuple of (MCPPayloadProcessorChain or None, list of managed_fields).
        """
        if not processor_defs:
            return None, []

        # Ensure built-in processors are registered
        import sgr_agent_core.processors  # noqa: F401
        from sgr_agent_core.mcp_payload_processor import (
            MCPPayloadProcessorChain,
            PayloadProcessorDefinition,
            ProcessorRegistry,
        )

        processors = []
        all_managed_fields: list[str] = []

        for raw_def in processor_defs:
            defn = PayloadProcessorDefinition.model_validate(raw_def)
            all_managed_fields.extend(defn.managed_fields)

            # Resolve processor class: registry first, then import string
            processor_cls = ProcessorRegistry.get(defn.class_name)
            if processor_cls is None:
                # Try as import string
                try:
                    module_path, class_name = defn.class_name.rsplit(".", 1)
                    module = importlib.import_module(module_path)
                    processor_cls = getattr(module, class_name)
                except (ValueError, ImportError, AttributeError) as e:
                    raise ValueError(
                        f"Processor '{defn.class_name}' not found in registry and cannot be imported: {e}"
                    ) from e

            proc = processor_cls(defn.config)
            proc._span_mode = defn.span_mode
            processors.append(proc)

        chain = MCPPayloadProcessorChain(processors) if processors else None
        return chain, all_managed_fields

    @classmethod
    async def build_tools_from_mcp(cls, config: MCPConfig):
        from sgr_agent_core import BaseTool, MCPBaseTool

        tools = []
        if not config.mcpServers:
            return tools

        # Extract per-server processor configs before passing to Client
        server_processor_configs: dict[str, list[dict]] = {}
        if hasattr(config, "mcpServers") and config.mcpServers:
            for server_name, server_cfg in config.mcpServers.items():
                if hasattr(server_cfg, "model_extra") and server_cfg.model_extra:
                    server_processor_configs[server_name] = server_cfg.model_extra.get("payload_processors", [])
                elif isinstance(server_cfg, dict):
                    server_processor_configs[server_name] = server_cfg.get("payload_processors", [])

        client: Client = Client(config)
        async with client:
            mcp_tools = await client.list_tools()

            # Merge processor defs from all servers, deduplicating by class name
            seen_classes: set[str] = set()
            unique_processor_defs: list[dict] = []
            for defs in server_processor_configs.values():
                for d in defs:
                    cls_name = d.get("class", "")
                    if cls_name not in seen_classes:
                        seen_classes.add(cls_name)
                        unique_processor_defs.append(d)

            processor_chain, managed_fields = cls._build_processor_chain(unique_processor_defs)

            for t in mcp_tools:
                if not t.name or not t.inputSchema:
                    logger.error(f"Skipping tool due to missing name or input schema: {t}")
                    continue

                try:
                    t.inputSchema["title"] = cls._to_CamelCase(t.name)
                    PdModel = SchemaConverter.build(t.inputSchema)
                except Exception as e:
                    logger.error(f"Error creating model {t.name} from schema: {t.inputSchema}: {e}")
                    continue

                ToolCls: Type[BaseTool] = create_model(
                    f"MCP{cls._to_CamelCase(t.name)}", __base__=(PdModel, MCPBaseTool), __doc__=t.description or ""
                )
                ToolCls.tool_name = t.name
                ToolCls.description = t.description or ""
                ToolCls._client = client
                ToolCls._processor_chain = processor_chain
                ToolCls._managed_fields = managed_fields

                # Override model_json_schema to hide managed fields from LLM
                if managed_fields:
                    import copy

                    _fields_to_hide = list(managed_fields)
                    original_schema = ToolCls.model_json_schema

                    @classmethod  # type: ignore[misc]
                    def _filtered_schema(cls, *args: Any, **kwargs: Any) -> dict:
                        schema = copy.deepcopy(original_schema(*args, **kwargs))
                        props = schema.get("properties", {})
                        required = schema.get("required", [])
                        for field_name in _fields_to_hide:
                            props.pop(field_name, None)
                            if field_name in required:
                                required.remove(field_name)
                        return schema

                    ToolCls.model_json_schema = _filtered_schema  # type: ignore[assignment]

                tools.append(ToolCls)
                logger.info(f"Built MCP Tool: {ToolCls.tool_name}")

            logger.info(f"Built {len(tools)} MCP tools.")
            return tools
