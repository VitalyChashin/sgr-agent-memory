# Quickstart: Langfuse Observability

**Feature**: 188-langfuse-observability

## Prerequisites

- SGR Agent Core running (Docker or local)
- Langfuse server (self-hosted or cloud account)
- Langfuse API keys (public key + secret key)

## Step 1: Install the observability extra

```bash
pip install sgr-agent-core[observability]
# or with uv:
uv pip install sgr-agent-core[observability]
```

## Step 2: Configure observability in config.yaml

Add to your `config.yaml`:

```yaml
observability:
  enabled: true
  provider: "langfuse"
  langfuse:
    base_url: "http://localhost:3000"   # Your Langfuse server URL
    environment: "development"
```

Set API keys via environment variables:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-your-key"
export LANGFUSE_SECRET_KEY="sk-lf-your-key"
```

## Step 3: Start the server

```bash
sgr --config-file config.yaml
```

The server logs will show:
```
Langfuse observability initialized (base_url=http://localhost:3000, environment=development)
```

## Step 4: Send a request and view traces

Send a normal chat completion request. Then open your Langfuse dashboard to see:

- A root trace per agent execution with agent metadata
- Iteration spans showing the reasoning loop
- LLM generation details with token usage and cost
- Tool invocation spans with inputs and outputs

## Docker Compose

For Docker deployments, add environment variables:

```yaml
services:
  backend:
    environment:
      - LANGFUSE_PUBLIC_KEY=pk-lf-your-key
      - LANGFUSE_SECRET_KEY=sk-lf-your-key
      - LANGFUSE_BASE_URL=http://langfuse:3000
    volumes:
      - ./config.yaml:/app/config.yaml:ro   # with observability section
```

## Disabling Observability

Set `enabled: false` (or remove the section entirely):

```yaml
observability:
  enabled: false
```

No traces are recorded, no Langfuse SDK is loaded, zero overhead.
