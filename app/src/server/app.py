"""FastAPI application and route registration."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, FastAPI

from juno.config import load_config, resolve_data_dir
from server.chats import router as chats_router

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    return {"status": "ok"}


def create_app(data_dir: Optional[Path] = None) -> FastAPI:
    """
    Args:
        data_dir: base directory the server stores under; chats go in its
            ``chats/``, next to the recorder's ``screen/``. Taken from the
            YAML config if None.
    """
    if data_dir is None:
        data_dir = resolve_data_dir(load_config().capture)
    app = FastAPI(title="Juno Server")
    app.state.chats_dir = data_dir / "chats"
    app.include_router(router)
    app.include_router(chats_router)
    return app
