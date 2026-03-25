# Data Model: Custom MCP Tool Name and Description

**Date**: 2026-03-25

## Modified Entity: MCPServerConfig

| Field            | Type        | Required | Default                                              | Status   |
|------------------|-------------|----------|------------------------------------------------------|----------|
| enabled          | boolean     | no       | `false`                                              | Existing |
| host             | string      | no       | `"0.0.0.0"`                                          | Existing |
| port             | integer     | no       | `8011`                                               | Existing |
| transport        | string      | no       | `"sse"`                                              | Existing |
| default_agent    | string/null | no       | `null`                                               | Existing |
| **tool_name**    | string      | no       | `"ask"`                                              | **NEW**  |
| **tool_description** | string  | no       | `"Send a research query to an SGR Agent..."` | **NEW**  |

**Validation**: Empty strings for `tool_name` or `tool_description` fall back to their defaults at usage time (not at model level).
