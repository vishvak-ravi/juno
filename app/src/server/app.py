"""FastAPI application and route registration."""

from fastapi import APIRouter, FastAPI

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    return {"status": "ok"}


def create_app() -> FastAPI:
    app = FastAPI(title="Juno Server")
    app.include_router(router)
    return app
