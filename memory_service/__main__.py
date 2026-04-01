"""Entry point for the memory microservice."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from memory_service.config import ServiceConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory microservice")
    parser.add_argument("--config", "-c", required=True, help="Path to YAML config file")
    args = parser.parse_args()

    config = ServiceConfig.from_yaml(args.config)

    # Store config for app lifespan access
    import memory_service.app as app_module

    app_module._service_config = config

    uvicorn.run(
        "memory_service.app:app",
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
