"""FastAPI application instance creation and configuration."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sgr_agent_core import AgentFactory, AgentRegistry, ToolRegistry, __version__
from sgr_agent_core.server.endpoints import router
from sgr_agent_core.services import StreamingGeneratorRegistry
from sgr_agent_core.services.overlayfs_manager import OverlayFSManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    for tool in ToolRegistry.list_items():
        logger.info(f"Tool registered: {tool.__name__}")
    for agent in AgentRegistry.list_items():
        logger.info(f"Agent registered: {agent.__name__}")
    for defn in AgentFactory.get_definitions_list():
        logger.info(f"Agent definition loaded: {defn}")
    for gen in StreamingGeneratorRegistry.list_items():
        logger.info(f"Streaming generator loaded: {gen.__name__}")

    # Initialize OverlayFS for RunCommandTool if configured
    await OverlayFSManager.initialize_from_config()

    # Start MCP server if enabled
    mcp_task = None
    from sgr_agent_core.agent_config import GlobalConfig

    config = GlobalConfig()
    if config.mcp_server.enabled:
        from sgr_agent_core.mcp_server.server import create_mcp_server

        transport = config.mcp_server.transport
        if transport != "sse":
            raise ValueError(f"Unsupported MCP transport '{transport}'. Only 'sse' is currently supported.")

        mcp = create_mcp_server(config)
        mcp_task = asyncio.create_task(mcp.run_sse_async(host=config.mcp_server.host, port=config.mcp_server.port))

        def _mcp_task_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error(f"MCP server crashed: {exc}", exc_info=exc)

        mcp_task.add_done_callback(_mcp_task_done)
        logger.info(f"MCP server started on {config.mcp_server.host}:{config.mcp_server.port} (transport={transport})")
    else:
        logger.info("MCP server disabled")

    yield

    # Shutdown MCP server
    if mcp_task is not None:
        mcp_task.cancel()
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass
        logger.info("MCP server stopped")

    # Cleanup OverlayFS on shutdown
    await OverlayFSManager.cleanup()


app = FastAPI(title="SGR Agent Core API", version=__version__, lifespan=lifespan)
# Don't use this CORS setting in production!
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
