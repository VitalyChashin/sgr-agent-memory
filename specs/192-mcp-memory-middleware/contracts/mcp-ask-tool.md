# Contract: MCP ask Tool (Memory-Enhanced)

**Owner**: `sgr_agent_core.mcp_server.server`
**Consumer**: MCP clients (Claude Desktop, Cursor, custom agents)
**Protocol**: MCP tool call via SSE transport

## Tool Schema

### ask

**Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | — | Research question or task |
| `traceId` | string | No | `"trace-default-001"` | Trace correlation ID |
| `userId` | string | No | `"user-default-001"` | User identifier |
| `sessionId` | string | No | `""` | Session ID for memory-enabled sessions. Empty = memory skipped. |

### Response (JSON)

**When memory is NOT active** (sessionId absent or memory disabled):
```json
{
  "response": "The agent's answer...",
  "traceId": "trace-abc123"
}
```

**When memory IS active** (sessionId provided, memory service available):
```json
{
  "response": "The agent's answer...",
  "traceId": "trace-abc123",
  "topicId": "topic-002",
  "topicLabel": "EU climate policy",
  "topicShift": true
}
```

Topic metadata fields (`topicId`, `topicLabel`, `topicShift`) are only present when memory was successfully used. They are omitted (not null) when memory is inactive or fell back.

## Backward Compatibility

- Existing clients that do not send `sessionId` see identical behavior.
- The `extra="allow"` model config on AskResponse means existing clients parsing the response will ignore unknown fields.
- The tool schema advertises `sessionId` as optional with an empty-string default, so MCP clients can discover the capability via tool listing.
