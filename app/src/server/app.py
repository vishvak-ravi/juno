"""FastAPI application and route registration."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from mcp.server.transport_security import TransportSecuritySettings

from annotate.label import Annotation, annotate
from annotate.request import AnnotationRequest
from juno.config import Config, load_config, resolve_data_dir, sessions_dir
from server.mcp import MCP_PATH, create_mcp
from server.routes.aggregations import router as aggregations_router
from server.routes.annotations import router as annotations_router
from server.routes.chats import router as chats_router
from server.routes.sessions import router as sessions_router
from server.worker import AnnotationWorker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    return {"status": "ok"}


# The phone client: one page that calls the routes below from the address it was opened at.
WEB_PAGE = Path(__file__).resolve().parent / "web" / "index.html"
web_router = APIRouter(tags=["web"])


@web_router.get("/", include_in_schema=False)
def get_web_page() -> FileResponse:
    # no-cache: a copy saved to the phone's Home Screen asks for the current page each time.
    return FileResponse(WEB_PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})


def create_app(config: Optional[Config] = None,
               annotate_request: Optional[Callable[[AnnotationRequest], Annotation]] = None,
               ) -> FastAPI:
    """
    Args:
        config: the app settings; read from the YAML config file if None.
            Chats are stored under ``chats/`` in the capture data directory,
            next to the recorder's ``screen/``.
        annotate_request: the model call for one sanitized annotation request;
            by default ``annotate.label.annotate`` with the configured model.
    """
    if config is None:
        config = load_config()
    if annotate_request is None:
        def annotate_request(request: AnnotationRequest) -> Annotation:
            return annotate(request, model=config.annotate.model,
                            max_tokens=config.annotate.max_tokens)

    data_dir = resolve_data_dir(config.capture)
    mcp = create_mcp(sessions_dir(data_dir))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resumed = app.state.worker.resume_pending()
        if resumed:
            logger.info("resuming %d annotations left pending", resumed)
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Juno Server", lifespan=lifespan)
    app.state.chats_dir = data_dir / "chats"
    app.state.sessions_dir = sessions_dir(data_dir)
    app.state.llm = config.llm
    app.state.mcp = mcp
    app.state.annotate = config.annotate
    app.state.worker = AnnotationWorker(app.state.sessions_dir, config.annotate,
                                        annotate_request)
    app.include_router(router)
    app.include_router(web_router)
    app.include_router(chats_router)
    app.include_router(sessions_router)
    app.include_router(aggregations_router)
    app.include_router(annotations_router)
    # After the routes: the MCP app answers MCP_PATH and nothing else.
    # Requests must name this server by an address it is reached at (blocks DNS rebinding).
    names = {config.server.host, "127.0.0.1", "localhost"}
    allowed_hosts = sorted(names | {f"{name}:*" for name in names})
    app.mount("/", mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts)))
    return app
