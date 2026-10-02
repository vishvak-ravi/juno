"""FastAPI application and route registration."""

from typing import Optional

from fastapi import APIRouter, FastAPI

from juno.config import Config, load_config, resolve_data_dir
from server.chats import router as chats_router

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    return {"status": "ok"}


def create_app(config: Optional[Config] = None) -> FastAPI:
    """
    Args:
        config: the app settings; read from the YAML config file if None.
            Chats are stored under ``chats/`` in the capture data directory,
            next to the recorder's ``screen/``.
    """
    if config is None:
        config = load_config()
    app = FastAPI(title="Juno Server")
    app.state.chats_dir = resolve_data_dir(config.capture) / "chats"
    app.state.llm = config.llm
    app.include_router(router)
    app.include_router(chats_router)
    return app
