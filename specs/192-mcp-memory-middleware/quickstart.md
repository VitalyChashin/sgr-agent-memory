# Quickstart: Memory-Enhanced MCP ask Tool

## Prerequisites

- SGR Agent Core running with MCP server enabled
- Memory middleware enabled in `config.yaml` (feature 191)
- External memory microservice running

## 1. Enable Memory and MCP Server

In your `config.yaml`:

```yaml
memory:
  enabled: true
  service_url: "http://localhost:9100"

mcp_server:
  enabled: true
  host: "0.0.0.0"
  port: 8011
  transport: "sse"
```

## 2. Send MCP Tool Calls with Session Context

Using any MCP client, call the `ask` tool with a `sessionId`:

```json
{
  "method": "tools/call",
  "params": {
    "name": "ask",
    "arguments": {
      "query": "What are the latest AI market trends?",
      "sessionId": "sess-abc123",
      "userId": "user-001"
    }
  }
}
```

## 3. Topic-Filtered Context (Automatic)

Subsequent calls in the same session automatically get topic-filtered context:

```json
{
  "method": "tools/call",
  "params": {
    "name": "ask",
    "arguments": {
      "query": "How does this compare to last year?",
      "sessionId": "sess-abc123"
    }
  }
}
```

When the topic shifts:

```json
{
  "method": "tools/call",
  "params": {
    "name": "ask",
    "arguments": {
      "query": "What is the current EU climate policy?",
      "sessionId": "sess-abc123"
    }
  }
}
```

Response includes topic metadata:

```json
{
  "response": "The EU's current climate policy framework...",
  "traceId": "trace-default-001",
  "topicId": "topic-002",
  "topicLabel": "EU climate policy",
  "topicShift": true
}
```

## 4. Without Session (Current Behavior)

Omit `sessionId` for standard single-query behavior:

```json
{
  "method": "tools/call",
  "params": {
    "name": "ask",
    "arguments": {
      "query": "Hello world"
    }
  }
}
```

Response is unchanged from current behavior:

```json
{
  "response": "Hello! How can I help you?",
  "traceId": "trace-default-001"
}
```
