# Architecture Proposal: MCP Payload Processor

> **Feature:** 002-mcp-payload-processor  
> **Status:** Draft  
> **Created:** 2026-03-25  
> **Depends on:** 001-mcp-ask-endpoint (implemented)

---

## 1. Problem Statement

When SGR Agent A calls SGR Agent B via MCP (using the `ask` tool from feature 001), the MCP call payload contains a mix of two fundamentally different field categories:

- **LLM-generated fields** — e.g., `query` — filled by Agent A's reasoning during the action phase.
- **Programmatic fields** — e.g., `traceId`, `userId` — must be populated or transformed by application code, not by the LLM.

The current `MCPBaseTool.__call__()` implementation has no hook for this. It dumps the entire Pydantic model and sends it directly:

```python
# base_tool.py — current implementation (v0.7.0)
class MCPBaseTool(BaseTool):
    async def __call__(self, context: AgentContext, config: AgentConfig, **kwargs) -> str:
        config = GlobalConfig()
        payload = self.model_dump(mode="json")          # ← all fields from LLM
        async with self._client:
            result = await self._client.call_tool(self.tool_name, payload)
            ...
```

This means every field in the MCP tool schema is either filled by the LLM (unreliable for system-level metadata) or carries a hardcoded default. There is no mechanism to:

1. Inject runtime values (trace context, user identity, session metadata) into the payload before the MCP call.
2. Transform LLM-provided values (e.g., validate, truncate, enrich).
3. Receive incoming request-scoped values and forward or map them to outgoing MCP calls.
4. Extend the set of programmatic fields without modifying the core framework.

As the platform evolves and agents compose via MCP chains, this becomes critical: trace propagation, authorization context, rate-limit tokens, billing tags, and arbitrary orchestrator metadata all need to flow through MCP calls without LLM involvement.

---

## 2. How SGR Currently Calls MCP — Analysis

### 2.1 MCP Tool Discovery and Construction

`MCP2ToolConverter.build_tools_from_mcp()` in `sgr_agent_core/services/mcp_service.py`:

1. Connects to each configured MCP server via `fastmcp.Client`.
2. Calls `client.list_tools()` to get available tools and their `inputSchema`.
3. For each tool, builds a dynamic Pydantic model from the JSON schema using `jambo.SchemaConverter.build()`.
4. Creates a new class that inherits from **both** the dynamic schema model and `MCPBaseTool`:
   ```python
   ToolCls = create_model(
       f"MCP{CamelCase(t.name)}",
       __base__=(PdModel, MCPBaseTool),
       __doc__=t.description
   )
   ```
5. The resulting tool class has Pydantic fields matching the MCP server's schema. For the `ask` tool: `query`, `traceId`, `userId`.

### 2.2 How the LLM Fills MCP Tool Fields

During the agent's select-action phase, the LLM sees the tool's schema (via `pydantic_function_tool()` or the SGR structured output schema) and generates values for its fields. The result is a populated Pydantic model instance.

In `ToolCallingAgent._select_action_phase()`:
```python
tool = completion.choices[0].message.tool_calls[0].function.parsed_arguments
# tool is now an MCPBaseTool subclass instance with all fields set by LLM
```

### 2.3 How the Tool Executes the MCP Call

`MCPBaseTool.__call__()` in `base_tool.py`:

```python
async def __call__(self, context: AgentContext, config: AgentConfig, **kwargs) -> str:
    payload = self.model_dump(mode="json")
    async with self._client:
        result = await self._client.call_tool(self.tool_name, payload)
        return json.dumps([m.model_dump_json() for m in result.content], ...)
```

**Key observation:** `context` (an `AgentContext` instance containing iteration count, searches used, execution state, etc.) and `config` (an `AgentConfig` with LLM settings, execution limits, etc.) are both available at call time but never used to modify the payload. The `**kwargs` parameter carries `tool_configs` dict entries but is also unused in the current `MCPBaseTool`.

### 2.4 Intervention Point

The natural place to inject programmatic payload processing is between `self.model_dump(mode="json")` and `self._client.call_tool(self.tool_name, payload)` — exactly where `MCPBaseTool.__call__()` currently has a straight pipeline.

---

## 3. Proposed Architecture: MCPPayloadProcessor

### 3.1 Core Concept

Introduce an **MCPPayloadProcessor** — an abstract class that transforms the MCP call payload before it is sent and optionally processes the response after it is received. Processors are attached to MCP tool definitions via configuration and execute as a chain.

```
LLM fills tool fields
        │
        ▼
  self.model_dump()
        │
        ▼  payload: dict
┌───────────────────────┐
│  PayloadProcessor #1  │  ← e.g., TraceContextProcessor
│  .pre_call(payload,   │     injects traceId from request scope
│     context, config)  │
└───────┬───────────────┘
        ▼
┌───────────────────────┐
│  PayloadProcessor #2  │  ← e.g., AuthContextProcessor
│  .pre_call(payload,   │     injects userId from caller identity
│     context, config)  │
└───────┬───────────────┘
        ▼
  client.call_tool(payload)
        │
        ▼  result
┌───────────────────────┐
│  PayloadProcessor #2  │
│  .post_call(result,   │
│     context, config)  │
└───────┬───────────────┘
        ▼
┌───────────────────────┐
│  PayloadProcessor #1  │
│  .post_call(result,   │
│     context, config)  │
└───────┘
```

### 3.2 Abstract Base Class

```python
# sgr_agent_core/mcp_payload_processor.py

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.models import AgentContext


class MCPPayloadProcessor(ABC):
    """
    Abstract base for MCP payload processors.
    
    A processor can modify the outgoing payload before an MCP call
    and/or transform the response after. Processors form an ordered
    chain: pre_call runs top-down, post_call runs bottom-up.
    
    Subclass this to inject traceId, userId, billing tags,
    authorization tokens, or any request-scoped metadata.
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        """
        Args:
            processor_config: Arbitrary config dict from YAML. 
                              Processor-specific parameters.
        """
        self.processor_config = processor_config or {}

    @abstractmethod
    async def pre_call(
        self,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Transform the payload before the MCP tool call.
        
        Args:
            payload: The serialized tool arguments (from model_dump).
            context: Current agent context (iteration, state, sources, etc.).
            config: Agent configuration (LLM, execution, prompts, etc.).
            **kwargs: Tool-level config kwargs from tool_configs.
            
        Returns:
            The (possibly modified) payload dict.
        """
        ...

    async def post_call(
        self,
        result: str,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> str:
        """
        Transform the result after the MCP tool call (optional).
        
        Default: pass through unchanged.
        """
        return result
```

### 3.3 Concrete Processor Examples

#### TraceContextProcessor

```python
class TraceContextProcessor(MCPPayloadProcessor):
    """
    Injects or propagates traceId.
    
    Behavior:
    - If request scope has a traceId → forward it.
    - Otherwise → generate a new one.
    - Always overwrites the LLM-provided value.
    """
    
    async def pre_call(self, payload, context, config, **kwargs):
        # Priority: explicit from request scope > config default > generate
        trace_id = (
            getattr(context, "trace_id", None)
            or self.processor_config.get("default_trace_id")
            or f"trace-{context.agent_id}-{context.iteration}"
        )
        payload["traceId"] = trace_id
        return payload
```

#### AuthContextProcessor

```python
class AuthContextProcessor(MCPPayloadProcessor):
    """
    Injects userId from the request scope or config.
    """

    async def pre_call(self, payload, context, config, **kwargs):
        user_id = (
            getattr(context, "user_id", None)
            or self.processor_config.get("default_user_id", "user-default-001")
        )
        payload["userId"] = user_id
        return payload
```

#### Field Redaction Processor (future)

```python
class FieldRedactionProcessor(MCPPayloadProcessor):
    """
    Removes or masks sensitive fields before sending to an external MCP server.
    """

    async def pre_call(self, payload, context, config, **kwargs):
        redact_fields = self.processor_config.get("redact_fields", [])
        for field in redact_fields:
            if field in payload:
                payload[field] = "[REDACTED]"
        return payload
```

### 3.4 Processor Chain Execution

```python
# sgr_agent_core/mcp_payload_processor.py (continued)

class MCPPayloadProcessorChain:
    """Ordered chain of payload processors."""

    def __init__(self, processors: list[MCPPayloadProcessor]):
        self.processors = processors

    async def run_pre_call(
        self, payload: dict, context, config, **kwargs
    ) -> dict:
        for processor in self.processors:
            payload = await processor.pre_call(
                payload, context, config, **kwargs
            )
        return payload

    async def run_post_call(
        self, result: str, payload: dict, context, config, **kwargs
    ) -> str:
        for processor in reversed(self.processors):
            result = await processor.post_call(
                result, payload, context, config, **kwargs
            )
        return result
```

### 3.5 Modified MCPBaseTool

```python
class MCPBaseTool(BaseTool):
    """Base model for MCP Tool schema."""

    _client: ClassVar[Client | None] = None
    _processor_chain: ClassVar[MCPPayloadProcessorChain | None] = None  # NEW

    async def __call__(self, context: AgentContext, config: AgentConfig, **kwargs) -> str:
        global_config = GlobalConfig()
        payload = self.model_dump(mode="json")
        
        # NEW: Apply processor chain before MCP call
        if self._processor_chain:
            payload = await self._processor_chain.run_pre_call(
                payload, context, config, **kwargs
            )
        
        try:
            async with self._client:
                result = await self._client.call_tool(self.tool_name, payload)
                result_str = json.dumps(
                    [m.model_dump_json() for m in result.content],
                    ensure_ascii=False,
                )[:global_config.execution.mcp_context_limit]
                
                # NEW: Apply processor chain after MCP call
                if self._processor_chain:
                    result_str = await self._processor_chain.run_post_call(
                        result_str, payload, context, config, **kwargs
                    )
                
                return result_str
        except Exception as e:
            logger.error(f"Error processing MCP tool {self.tool_name}: {e}")
            return f"Error: {e}"
```

### 3.6 AgentContext Extension

To carry request-scoped values (traceId, userId, etc.) from the incoming request to the processors, `AgentContext` needs extensible metadata:

```python
# sgr_agent_core/models.py — AgentContext extension

class AgentContext(BaseModel):
    # ... existing fields ...
    
    # NEW: Request-scoped metadata passed to MCP payload processors
    request_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary request-scoped metadata for processor chain."
    )
```

Processors access it via `context.request_metadata.get("traceId")` instead of `getattr(context, "trace_id", None)`. This keeps the core model clean while allowing any metadata key.

The metadata is populated at agent creation time — either from the REST API request, from the outer MCP call handler (when the agent itself is called via MCP), or explicitly by the orchestrator.

---

## 4. Configuration Design

### 4.1 YAML Config Schema

Processors are configured per MCP server (global) and can be overridden per agent:

```yaml
# config.yaml

mcp:
  mcpServers:
    sgr_agent_b:
      url: "http://localhost:8011/sse"
      # NEW: processors applied to all tools from this server
      payload_processors:
        - class: "TraceContextProcessor"
          config:
            default_trace_id: "trace-system-001"
        - class: "AuthContextProcessor"
          config:
            default_user_id: "user-system-001"

# Per-agent override (agents.yaml)
agents:
  orchestrator_agent:
    base_class: "SGRToolCallingAgent"
    mcp:
      mcpServers:
        sgr_agent_b:
          url: "http://localhost:8011/sse"
          payload_processors:
            - class: "myproject.processors.CustomTraceProcessor"
              config:
                trace_prefix: "orch"
```

### 4.2 ProcessorConfig Pydantic Model

```python
# sgr_agent_core/mcp_payload_processor.py

class PayloadProcessorDefinition(BaseModel, extra="allow"):
    """Definition of a single payload processor in config."""
    
    class_name: str = Field(alias="class", description="Processor class name or import string")
    config: dict[str, Any] = Field(default_factory=dict)
```

### 4.3 Processor Resolution

Follows the same pattern as tool and agent resolution:

1. Built-in processors registered in `ProcessorRegistry` (auto-registration via `__init_subclass__`).
2. Custom processors via import strings (e.g., `myproject.processors.CustomProcessor`).
3. Resolution order: registry by name → registry by PascalCase → import string.

### 4.4 Processor Construction in MCP2ToolConverter

The `build_tools_from_mcp()` method already iterates MCP servers and creates tool classes. The processor chain is built at the same point and attached to the tool class:

```python
# mcp_service.py — modified

class MCP2ToolConverter:
    @classmethod
    async def build_tools_from_mcp(cls, config: MCPConfig):
        ...
        for server_name, server_config in config.mcpServers.items():
            # Build processor chain for this server
            processor_chain = cls._build_processor_chain(
                server_config.get("payload_processors", [])
            )
            
            client = Client(config)
            async with client:
                mcp_tools = await client.list_tools()
                for t in mcp_tools:
                    ...
                    ToolCls._processor_chain = processor_chain  # Attach chain
                    ...
```

---

## 5. Request-Scoped Value Propagation

The most important design question: how do programmatic values (traceId from an incoming request, userId from authentication) reach the processors?

### 5.1 Propagation Flow

```
Incoming Request (REST or MCP)
    │  carries: traceId, userId, custom headers
    ▼
Agent Creation (AgentFactory.create)
    │  populates: context.request_metadata = {"traceId": ..., "userId": ...}
    ▼
Agent Execution Loop
    │
    ├─ Reasoning Phase → LLM decides to call MCP tool
    ├─ Select Action Phase → LLM fills tool fields (query, etc.)
    ├─ Action Phase → MCPBaseTool.__call__()
    │       │
    │       ├─ payload = model_dump()  ← LLM-generated
    │       ├─ processor_chain.pre_call(payload, context, config)
    │       │       │
    │       │       ├─ TraceContextProcessor reads context.request_metadata["traceId"]
    │       │       │   → sets payload["traceId"]
    │       │       │
    │       │       └─ AuthContextProcessor reads context.request_metadata["userId"]
    │       │           → sets payload["userId"]
    │       │
    │       └─ client.call_tool(tool_name, payload)  ← enriched payload
    │
    └─ Loop continues...
```

### 5.2 Entry Points for Metadata

**REST API path** (`/v1/chat/completions`):

The server endpoint extracts metadata from the request (e.g., headers, body fields) and passes it to `AgentFactory.create()`:

```python
# server/endpoints.py — sketch
@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    request_metadata = {
        "traceId": request.headers.get("X-Trace-Id", str(uuid4())),
        "userId": request.headers.get("X-User-Id"),
    }
    agent = await AgentFactory.create(
        agent_def=...,
        task_messages=...,
        request_metadata=request_metadata,  # NEW parameter
    )
```

**MCP server path** (when this agent is called via MCP `ask` tool):

The MCP `ask` handler extracts traceId/userId from the MCP call arguments and injects them into the agent's context:

```python
# mcp_server/server.py — sketch (from feature 001)
@mcp.tool()
async def ask(query: str, traceId: str = ..., userId: str = ...):
    agent = await AgentFactory.create(
        agent_def=...,
        task_messages=[{"role": "user", "content": query}],
        request_metadata={"traceId": traceId, "userId": userId},
    )
```

### 5.3 AgentFactory Changes

```python
# agent_factory.py — modified create()
@classmethod
async def create(
    cls,
    agent_def: AgentDefinition,
    task_messages: list[ChatCompletionMessageParam],
    request_metadata: dict[str, Any] | None = None,  # NEW
) -> Agent:
    ...
    agent = BaseClass(
        task_messages=task_messages,
        ...,
    )
    if request_metadata:
        agent._context.request_metadata = request_metadata
    return agent
```

---

## 6. Hiding Programmatic Fields from the LLM

An important corollary: if `traceId` and `userId` are always set by processors, the LLM should not waste tokens reasoning about them. Two approaches:

### 6.1 Option A: Schema Field Annotation (Recommended)

Add a `processor_managed: true` annotation to fields in the MCP tool schema. During `_prepare_tools()`, the agent strips these fields from the schema shown to the LLM, but keeps them on the Pydantic model for the processor to fill.

This requires minimal changes: the processor config declares which fields it manages, and `MCPBaseTool` overrides `model_json_schema()` to exclude them when generating the LLM-facing schema.

### 6.2 Option B: Separate LLM and Wire Schemas

Maintain two schemas per MCP tool: a "reasoning schema" (shown to LLM, no programmatic fields) and a "wire schema" (full, sent to MCP server). More complex but cleaner separation.

### 6.3 Recommendation

Option A for the initial implementation. The processor configuration already lists which fields it manages. Use that list to filter the schema:

```yaml
payload_processors:
  - class: "TraceContextProcessor"
    managed_fields: ["traceId"]    # Hidden from LLM schema
    config:
      default_trace_id: "trace-system-001"
  - class: "AuthContextProcessor"
    managed_fields: ["userId"]     # Hidden from LLM schema
```

---

## 7. Extensibility Considerations

### 7.1 Adding New Payload Fields

When the MCP `ask` tool schema evolves (e.g., adding `sessionId`, `billingTag`, `priority`):

1. The MCP server's tool schema is updated — the new fields appear automatically in the dynamic Pydantic model.
2. A new processor is written (or an existing one extended) to populate the new field.
3. The processor is added to YAML config.
4. No framework code changes required.

### 7.2 Custom Processor Composition

Processors are composable. Complex scenarios (multi-hop tracing, JWT token refresh, response caching) are each a separate processor in the chain.

### 7.3 Per-Tool Processor Override

For cases where different tools from the same MCP server need different processing:

```yaml
mcp:
  mcpServers:
    agent_b:
      url: "http://localhost:8011/sse"
      payload_processors:
        - class: "TraceContextProcessor"
      tool_overrides:
        ask:
          payload_processors:
            - class: "TraceContextProcessor"
            - class: "BillingTagProcessor"
```

### 7.4 Processor Registry

Same auto-registration pattern as `ToolRegistry` and `AgentRegistry`:

```python
class ProcessorRegistryMixin:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__name__ != "MCPPayloadProcessor":
            ProcessorRegistry.register(cls, name=cls.__name__)
```

---

## 8. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent A (SGR)                            │
│                                                                 │
│  ┌──────────────┐    ┌───────────────────┐    ┌──────────────┐ │
│  │  Reasoning    │───▶│  Select Action    │───▶│  Action      │ │
│  │  Phase        │    │  Phase            │    │  Phase       │ │
│  │              │    │  LLM fills:       │    │              │ │
│  │              │    │  query="..."      │    │  MCPBaseTool │ │
│  │              │    │  traceId=default  │    │  .__call__() │ │
│  │              │    │  userId=default   │    │              │ │
│  └──────────────┘    └───────────────────┘    └──────┬───────┘ │
│                                                      │         │
│                                              model_dump()      │
│                                                      │         │
│                                                      ▼         │
│                                          ┌────────────────┐    │
│                     request_metadata ───▶│ Processor Chain │    │
│                     from AgentContext     │                │    │
│                                          │ ┌────────────┐ │    │
│                                          │ │ TraceCxt   │ │    │
│                                          │ │ Processor  │ │    │
│                                          │ └─────┬──────┘ │    │
│                                          │       ▼        │    │
│                                          │ ┌────────────┐ │    │
│                                          │ │ AuthCxt    │ │    │
│                                          │ │ Processor  │ │    │
│                                          │ └─────┬──────┘ │    │
│                                          └───────┼────────┘    │
│                                                  │             │
│                                          enriched payload      │
│                                                  │             │
└──────────────────────────────────────────────────┼─────────────┘
                                                   │
                                                   ▼
                                        ┌─────────────────────┐
                                        │  MCP (SSE transport)│
                                        └─────────┬───────────┘
                                                  │
                                                  ▼
                                        ┌─────────────────────┐
                                        │  Agent B (SGR)      │
                                        │  ask tool handler   │
                                        │                     │
                                        │  Receives:          │
                                        │  query="..."        │
                                        │  traceId="orch-123" │
                                        │  userId="user-42"   │
                                        └─────────────────────┘
```

---

## 9. Alignment with Platform Principles

| Principle | Alignment | Notes |
|-----------|-----------|-------|
| **P1: Deterministic First, Semantic Second** | ✅ | Processors are deterministic rules applied before/after the LLM-semantic phase |
| **P2: Prefer Agent-as-Tool** | ✅ | This pattern enables clean Agent-as-Tool composition via MCP |
| **P4: Gateway as Single Control Point** | ⚠️ Neutral | Processors operate at the tool level, not gateway. If centralized enforcement is needed, processors can be defined at the global MCP config level |
| **P5: Evolution, Not Revolution** | ✅ | Extends `MCPBaseTool` with a single optional hook. No existing behavior changes when no processors are configured |
| **P6: Self-Improving Loop** | ✅ | `post_call` processors can log metrics, trace data, and failure signals for feedback loops |

---

## 10. Implementation Tasks

| # | Task | Dependencies | Parallel |
|---|------|-------------|----------|
| 1 | Create `MCPPayloadProcessor` ABC and `MCPPayloadProcessorChain` | — | — |
| 2 | Create `ProcessorRegistry` with auto-registration | Task 1 | — |
| 3 | Implement `TraceContextProcessor` and `AuthContextProcessor` | Task 1 | [P] |
| 4 | Add `PayloadProcessorDefinition` config model | Task 1 | [P] |
| 5 | Add `request_metadata` to `AgentContext` | — | [P] |
| 6 | Modify `MCPBaseTool.__call__()` to invoke processor chain | Tasks 1, 5 | — |
| 7 | Modify `MCP2ToolConverter` to build and attach processor chains | Tasks 2, 4, 6 | — |
| 8 | Add `request_metadata` parameter to `AgentFactory.create()` | Task 5 | [P] |
| 9 | Propagate metadata from REST API endpoint | Task 8 | — |
| 10 | Propagate metadata from MCP `ask` handler (feature 001) | Task 8 | [P] |
| 11 | Implement `managed_fields` LLM schema filtering | Task 7 | — |
| 12 | Unit tests for processor chain | Tasks 1, 3 | [P] |
| 13 | Integration test: Agent A → MCP → Agent B with trace propagation | Tasks 6-10 | — |

---

## 11. Summary

The MCPPayloadProcessor pattern introduces a clean, extensible middleware layer between the LLM-generated tool payload and the actual MCP wire call. It solves the immediate need for traceId/userId injection while providing a general-purpose hook for any programmatic payload transformation — current and future. The design follows SGR's existing patterns (Pydantic models, auto-registration, YAML config, ClassVar on tool classes) and requires no changes to the agent reasoning or tool selection logic.
