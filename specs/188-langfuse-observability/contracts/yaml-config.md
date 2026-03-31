# Contract: YAML Configuration Schema

**Feature**: 188-langfuse-observability
**Date**: 2026-03-26

## Overview

The observability configuration is a new top-level section in `config.yaml`. It is entirely optional — missing section means disabled.

## Schema

```yaml
# config.yaml — new section (all fields optional)
observability:
  enabled: true                    # default: false
  provider: "langfuse"             # default: "langfuse" (only used when enabled)

  langfuse:
    public_key: "pk-lf-..."        # or LANGFUSE_PUBLIC_KEY env var
    secret_key: "sk-lf-..."        # or LANGFUSE_SECRET_KEY env var
    base_url: "http://localhost:3000"  # or LANGFUSE_BASE_URL env var
    environment: "development"     # default: "development"
    flush_at: 512                  # default: 512
    flush_interval: 5.0            # default: 5.0
    sample_rate: 1.0               # default: 1.0 (range: 0.0–1.0)
    debug: false                   # default: false
```

## Environment Variable Overrides

Following SGR's existing convention (`SGR__<section>__<field>`):

| Env Var | Config Path | Notes |
|---------|------------|-------|
| `SGR__OBSERVABILITY__ENABLED` | `observability.enabled` | SGR convention |
| `LANGFUSE_PUBLIC_KEY` | `observability.langfuse.public_key` | Langfuse SDK native |
| `LANGFUSE_SECRET_KEY` | `observability.langfuse.secret_key` | Langfuse SDK native |
| `LANGFUSE_BASE_URL` | `observability.langfuse.base_url` | Langfuse SDK native |

The Langfuse SDK natively reads `LANGFUSE_*` env vars when constructor params are `None`. This means a minimal deployment can skip the `langfuse:` YAML block entirely:

```yaml
observability:
  enabled: true
```

With env vars:
```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="http://langfuse:3000"
```

## Backward Compatibility

- Missing `observability:` section → `enabled: false` → `NoOpProvider` → zero behavior change
- No new required fields in existing config sections
- No existing field semantics changed
