# Feature Specification: Metrics Processor Plugin System

**Feature Branch**: `190-metrics-processors`
**Created**: 2026-03-26
**Status**: Draft
**Input**: Custom metrics plugin report from `specs/189-enrich-langfuse-traces/custom-metrics-plugin-report.md`
**Depends on**: 188-langfuse-observability (observability foundation), 189-enrich-langfuse-traces (enriched traces)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Track Token Usage Across Agent Runs (Priority: P1)

As a platform operator monitoring costs, I want the system to automatically calculate and attach total token usage and token efficiency metrics to every agent execution trace so that I can identify expensive patterns, set budgets, and compare cost across agent configurations without manually summing generation spans.

**Why this priority**: Cost visibility is the primary driver for observability adoption. While individual generation spans already show per-call tokens, operators need aggregated per-run metrics to make budget decisions and compare agent performance.

**Independent Test**: Run an agent that makes multiple LLM calls. Open the trace in the observability dashboard. Verify that trace-level scores include "total_tokens" and "token_efficiency_ratio" without any manual calculation.

**Acceptance Scenarios**:

1. **Given** an agent completes an execution with 3 LLM calls totaling 5,000 tokens, **When** the trace is viewed in the dashboard, **Then** a score named "total_tokens" with value 5000 is attached to the trace.
2. **Given** an agent completes an execution, **When** the trace is viewed, **Then** a score named "token_efficiency_ratio" (output tokens / input tokens) is attached, enabling operators to spot agents that consume disproportionately many input tokens.
3. **Given** an LLM call returns no usage data (provider doesn't support it), **When** that call's tokens are counted, **Then** it is gracefully skipped (zero contribution) and the aggregated metrics reflect only the calls that reported usage.

---

### User Story 2 - Track Tool Usage Patterns (Priority: P1)

As a platform operator analyzing agent behavior, I want the system to automatically track how many tools were used and how many total tool calls were made per agent run so that I can identify redundant tool calls, understand agent decision patterns, and detect agents that loop on the same tool.

**Why this priority**: Tool usage patterns are the clearest signal of agent quality. An agent calling the same search tool 5 times is likely stuck; an agent using 4 different tools efficiently is well-configured. This metric is cheap to compute and immediately actionable.

**Independent Test**: Run an agent that calls 3 different tools across 4 iterations. Open the trace. Verify scores "unique_tools_used" (3) and "total_tool_calls" (4) are attached.

**Acceptance Scenarios**:

1. **Given** an agent calls web_search twice and final_answer once, **When** the trace is viewed, **Then** "unique_tools_used" is 2 and "total_tool_calls" is 3.
2. **Given** an agent calls only final_answer (single iteration), **When** the trace is viewed, **Then** "unique_tools_used" is 1 and "total_tool_calls" is 1.

---

### User Story 3 - Plugin Interface for Custom Metrics (Priority: P1)

As a developer extending the agent framework, I want to create my own metrics processor that runs at defined points during agent execution and attaches custom scores to traces so that I can measure domain-specific quality metrics (e.g., search relevance, response accuracy) without modifying the core agent code.

**Why this priority**: The built-in processors (US1, US2) prove the pattern works, but the real value is extensibility. Every deployment has unique KPIs — a research agent needs search quality metrics, a customer service agent needs resolution rate metrics. The plugin interface must be a first-class feature, not an afterthought.

**Independent Test**: Create a custom metrics processor class that counts iterations and attaches "iteration_count" to the trace. Register it in configuration. Run an agent. Verify the "iteration_count" score appears in the trace.

**Acceptance Scenarios**:

1. **Given** a developer creates a custom processor class with an `on_trace_end` method, **When** they add it to the configuration, **Then** the processor runs automatically after every agent execution without modifying any core code.
2. **Given** a custom processor is defined in an external module (outside the core package), **When** its fully-qualified class name is specified in configuration, **Then** the system discovers and loads it automatically.
3. **Given** a custom processor raises an exception during execution, **When** the agent is running, **Then** the agent execution continues unaffected — the failure is logged as a warning and other processors still run.

---

### User Story 4 - Configure Metrics Processors via YAML (Priority: P2)

As a platform operator deploying agents, I want to enable, disable, and configure metrics processors through the existing YAML configuration file so that I can control which metrics are collected per deployment without code changes.

**Why this priority**: Operators need to tailor metrics collection to their environment. A development deployment might want all metrics with verbose logging; a production deployment might want only cost metrics with sampling.

**Independent Test**: Add a metrics processor to config.yaml with custom configuration parameters. Restart the server. Verify the processor runs with the configured parameters.

**Acceptance Scenarios**:

1. **Given** a metrics processor is listed in the configuration with custom parameters, **When** the server starts, **Then** the processor is instantiated with those parameters and runs on every agent execution.
2. **Given** no metrics processors are configured, **When** an agent executes, **Then** no metrics processing occurs and there is zero performance overhead.
3. **Given** a processor class name in configuration doesn't match any known processor, **When** the server starts, **Then** a clear error message is logged identifying the unknown processor and the server continues without it.

---

### Edge Cases

- What happens when multiple processors attach scores with the same name? Each processor should use unique score names. If duplicates occur, both scores are attached (the observability backend handles multiple scores with the same name).
- What happens when a processor's hook takes a very long time (e.g., makes an external API call)? Processors run in the agent's async context. Long-running processors delay the agent response. Operators should design processors to be fast (in-memory operations only) or use fire-and-forget patterns for external calls.
- What happens when the observability provider is disabled (NoOp)? Metrics processors that call `provider.score_trace()` will hit the NoOp implementation, which is a no-op. The processor still runs (accumulating data in memory) but scores go nowhere. This is acceptable — the processor's logic is harmless.
- What happens during concurrent agent executions? Each agent execution gets its own processor instances (new chain per execution). Processors accumulate state per-execution, not globally.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a plugin interface with defined hook points that run at key moments during agent execution: trace start, iteration end, tool end, generation end, and trace end.
- **FR-002**: System MUST include a built-in token efficiency processor that attaches "total_tokens" and "token_efficiency_ratio" scores to each agent trace.
- **FR-003**: System MUST include a built-in tool usage processor that attaches "unique_tools_used" and "total_tool_calls" scores to each agent trace.
- **FR-004**: System MUST support user-defined processor classes discoverable by class name (built-in registry) or fully-qualified import path (external modules).
- **FR-005**: System MUST allow processor configuration via the existing YAML config file, with per-processor custom parameters passed to the processor at initialization.
- **FR-006**: System MUST execute all processors in a fail-silent manner — a failure in any processor must never crash or delay agent execution. Failures are logged as warnings.
- **FR-007**: System MUST create a new processor chain instance per agent execution to ensure processors are stateful per-execution and safe for concurrent use.
- **FR-008**: System MUST pass the observability provider, trace handle, agent context, and agent configuration to each hook so that processors have full context to compute and attach metrics.
- **FR-009**: System MUST support an ordered chain of processors — processors run in the order listed in configuration.
- **FR-010**: System MUST add zero overhead when no metrics processors are configured — the hook calls are skipped entirely if the chain is empty.

### Key Entities

- **MetricsProcessor**: A plugin that runs at defined hook points during agent execution. Has lifecycle methods for each hook point. Receives execution context and can attach scores/metadata via the observability provider. Stateful per execution (accumulates data across iterations).
- **MetricsProcessorChain**: An ordered collection of processors. Invokes each processor at each hook point. Catches and logs exceptions per processor to ensure fail-silent behavior.
- **MetricsProcessorDefinition**: Configuration model for a single processor — specifies the class name and custom parameters.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every agent trace with token efficiency processor enabled shows "total_tokens" and "token_efficiency_ratio" scores — 100% of traces have these scores when the processor is configured.
- **SC-002**: Every agent trace with tool usage processor enabled shows "unique_tools_used" and "total_tool_calls" scores.
- **SC-003**: A developer can create, register, and deploy a custom metrics processor in under 30 minutes by following the plugin interface pattern and adding a configuration entry.
- **SC-004**: A failing processor does not increase agent execution failure rate — 0% impact on agent reliability from metrics processor errors.
- **SC-005**: Agents with no configured processors show identical performance and behavior to agents running before this feature was added — zero overhead when unconfigured.
- **SC-006**: Operators can see aggregated custom metrics in the observability dashboard, enabling data-driven decisions about agent configuration and quality.

## Assumptions

- The observability foundation (feature 188) and enriched traces (feature 189) are deployed — trace handles, span handles, and the provider's `score_trace()` method are available.
- Processors are designed for in-memory operations (counting, aggregating). Processors that make external API calls are the developer's responsibility to make non-blocking.
- The existing MCP Payload Processor pattern (auto-registration, registry, chain, YAML config) is the proven reference architecture for this plugin system.
- Built-in processors ship with the framework and are automatically registered on import. Custom processors from external modules are loaded via import path at startup.
- Score names are namespaced by convention (e.g., "sgr.total_tokens", "sgr.unique_tools_used") to avoid collisions with user-defined scores. The exact naming convention will be determined during planning.
