FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY memory_service/ ./memory_service/
COPY memory-config.yaml.example ./config.yaml

RUN pip install --no-cache-dir \
    fastapi>=0.116.1 \
    uvicorn>=0.35.0 \
    pydantic>=2.0 \
    pydantic-settings>=2.0 \
    redis>=5.0 \
    openai>=1.0 \
    pyyaml>=6.0 \
    httpx>=0.25.0


FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 memgroup \
 && useradd --no-create-home --shell /bin/false --uid 1000 --gid 1000 --system memuser

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app
COPY --from=builder --chown=memuser:memgroup /app/memory_service/ ./memory_service/
COPY --from=builder --chown=memuser:memgroup /app/config.yaml ./config.yaml

USER memuser:memgroup

EXPOSE 9100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:9100/health || exit 1

ENTRYPOINT ["python", "-m", "memory_service", "--config", "/app/config.yaml"]
