"""FastAPI application and route registration."""

from typing import Callable, Optional

from fastapi import APIRouter, FastAPI

from annotate.label import Annotation, annotate
from annotate.request import AnnotationRequest
from juno.config import Config, load_config, resolve_data_dir, sessions_dir
from server.routes.aggregations import router as aggregations_router
from server.routes.annotations import router as annotations_router
from server.routes.chats import router as chats_router
from server.routes.sessions import router as sessions_router
from server.worker import AnnotationWorker

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    return {"status": "ok"}


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
    app = FastAPI(title="Juno Server")
    app.state.chats_dir = data_dir / "chats"
    app.state.sessions_dir = sessions_dir(data_dir)
    app.state.llm = config.llm
    app.state.annotate = config.annotate
    app.state.worker = AnnotationWorker(app.state.sessions_dir, config.annotate,
                                        annotate_request)
    app.include_router(router)
    app.include_router(chats_router)
    app.include_router(sessions_router)
    app.include_router(aggregations_router)
    app.include_router(annotations_router)
    return app
