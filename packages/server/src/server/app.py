"""FastAPI application and route registration."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from annotate.label import Annotation, annotate_async
from annotate.request import AnnotationRequest
from juno.config import (Config, claude_results_dir, control_file, devices_dir,
                         draw_sessions_dir, load_config, resolve_data_dir, server_host,
                         workspace_dir)
from keypoints.detect import Ask, DetectionWorker, JevAsk
from keypoints.questions import load_questions
from predict.predictor import Predictor
from server.control import read_state
from server.integrity import StoreReport, check_store
from server.agent.mcp import MCP_PATH, create_mcp
from server.routes.chats import router as chats_router
from server.routes.control import router as control_router
from server.routes.draw import router as draw_router
from server.routes.events import router as events_router
from server.routes.indices import router as indices_router
from server.routes.predictions import router as predictions_router
from server.routes.sessions import router as sessions_router
from server.routes.web import WEB_DIR
from server.routes.web import router as web_router
from server.agent.tools import create_chat_tools, session_tools
from server.worker import AnnotateRequest, AnnotationWorker, QueueStatus

logger = logging.getLogger(__name__)

TYPESAFE_KEY_ENV = "TYPESAFE_API_KEY"

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status():
    return {"status": "ok"}


@router.get("/integrity")
def get_integrity(request: Request) -> StoreReport:
    """What the store holds per device and session, and everything wrong with it."""
    state = request.app.state
    return check_store(state.devices_dir, state.annotate.chunk_size)


@router.get("/queue")
def get_queue(request: Request) -> QueueStatus:
    """How many chunks are waiting to be annotated, and how many calls are in flight."""
    return request.app.state.worker.status()


def log_store_report(report: StoreReport) -> None:
    for device in report.devices:
        logger.info("store: %s has %d sessions, %d rows; of %d chunks %d done, %d pending, "
                    "%d failed, %d not requested", device.id, device.sessions, device.rows,
                    device.chunks, device.done, device.pending, device.failed,
                    device.not_requested)
    for issue in report.issues:
        logger.warning("store: %s in %s (%s): %s", issue.kind, issue.session_id,
                       issue.subject, issue.detail)


def create_app(config: Optional[Config] = None,
               annotate_request: Optional[AnnotateRequest] = None,
               ask: Optional[Ask] = None,
               ) -> FastAPI:
    """
    Args:
        config: the app settings; read from the YAML config file if None.
            Chats are stored under ``chats/`` in the capture data directory,
            uploaded sessions under ``screen/devices/``.
        annotate_request: the model call for one sanitized annotation request;
            by default ``annotate.label.annotate`` with the configured model.
        ask: the Jev call for one row; by default ``keypoints.detect.JevAsk``
            with ``keypoints.model``, which needs TYPESAFE_API_KEY.
    """
    if config is None:
        config = load_config()
    if annotate_request is None:
        async def annotate_request(request: AnnotationRequest) -> Annotation:
            return await annotate_async(request, model=config.annotate.model,
                                        max_tokens=config.annotate.max_tokens,
                                        api_base=config.annotate.api_base)

    data_dir = resolve_data_dir(config.capture)
    chats_dir = data_dir / "chats"
    predictor = Predictor(config.predict)
    chat_tools = create_chat_tools(devices_dir(data_dir), chats_dir,
                                   config.llm.tool_result_chars, config.tools,
                                   workspace_dir(data_dir), claude_results_dir(data_dir),
                                   predictor, config.llm)
    mcp = create_mcp(session_tools(devices_dir(data_dir), config.llm.tool_result_chars,
                                   predictor, config.llm))

    if ask is None and config.keypoints.enabled:
        if os.environ.get(TYPESAFE_KEY_ENV):
            ask = JevAsk(config.keypoints.model)
        else:
            logger.warning("keypoints are off: %s is not set", TYPESAFE_KEY_ENV)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_store_report(check_store(app.state.devices_dir, config.annotate.chunk_size))
        app.state.worker.start()
        if app.state.detector is not None:
            app.state.detector.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            app.state.worker.stop()
            if app.state.detector is not None:
                app.state.detector.stop()

    app = FastAPI(title="Juno Server", lifespan=lifespan)
    app.state.chats_dir = chats_dir
    app.state.devices_dir = devices_dir(data_dir)
    app.state.llm = config.llm
    app.state.chat_tools = chat_tools
    app.state.annotate = config.annotate
    app.state.predictor = predictor
    app.state.control = config.control
    app.state.control_file = control_file(data_dir)
    app.state.capture = config.capture
    app.state.draw = config.draw
    app.state.draw_sessions_dir = draw_sessions_dir(data_dir)
    annotating = read_state(app.state.control_file).server.annotate
    app.state.worker = AnnotationWorker(
        app.state.devices_dir, config.annotate, annotate_request, annotating=annotating)
    app.state.keypoints = config.keypoints
    app.state.questions = load_questions()
    # None when keypoints are off or no key is set.
    app.state.detector = (DetectionWorker(app.state.devices_dir, config.annotate,
                                          config.keypoints, app.state.questions, ask,
                                          detecting=annotating)
                          if config.keypoints.enabled and ask is not None else None)
    app.include_router(router)
    app.include_router(web_router)
    app.include_router(chats_router)
    app.include_router(control_router)
    app.include_router(draw_router)
    app.include_router(events_router)
    app.include_router(indices_router)
    app.include_router(predictions_router)
    app.include_router(sessions_router)
    # The modules the web pages import, at the path the pages name them by.
    app.mount("/src", StaticFiles(directory=WEB_DIR / "src"), name="web-src")
    # After the routes: the MCP app answers MCP_PATH and nothing else.
    # Requests must name this server by an address it is reached at (blocks DNS rebinding).
    names = {server_host(), "127.0.0.1", "localhost"}
    allowed_hosts = sorted(names | {f"{name}:*" for name in names})
    app.mount("/", mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts)))
    return app
