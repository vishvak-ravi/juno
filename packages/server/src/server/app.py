"""FastAPI application and route registration."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from annotate.label import Annotation, annotate_async
from annotate.request import AnnotationRequest
from juno.config import (DeviceSettings, ServerSettings, claude_results_dir, control_file,
                         data_dir as default_data_dir, devices_dir, draw_sessions_dir,
                         server_host, workspace_dir)
from juno.settings import device_settings, server_settings
from annotate.store import FileActivityCaptions
from keypoints.ask import Ask, JevAsk, QuestionWorker
from keypoints.questions import load_questions
from keypoints.store import FileQuestionAnswers
from predict.predictor import Predictor
from server.control import FileControl
from server.events import FileEvents
from server.integrity import StoreReport, check_store
from server.agent.mcp import MCP_PATH, create_mcp
from server.routes.changes import router as changes_router
from server.routes.chats import router as chats_router
from server.routes.control import router as control_router
from server.routes.draw import router as draw_router
from server.routes.events import router as events_router
from server.routes.hooks import router as hooks_router
from server.routes.indices import router as indices_router
from server.routes.predictions import router as predictions_router
from server.routes.sessions import router as sessions_router
from server.routes.settings import router as settings_router
from server.routes.web import WEB_DIR
from server.routes.web import router as web_router
from server.timing import TimingMiddleware
from server.versions import SessionVersions, session_fingerprint
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


def create_app(config: Optional[ServerSettings] = None,
               data_dir: Optional[Path] = None,
               device: Optional[DeviceSettings] = None,
               annotate_request: Optional[AnnotateRequest] = None,
               ask: Optional[Ask] = None,
               ) -> FastAPI:
    """
    Args:
        config: the server's settings; read from ``<data_dir>/settings.json`` if None.
        data_dir: the base directory (default ``juno.config.data_dir()``): chats
            under ``chats/``, uploaded sessions under ``screen/devices/``, the
            settings file at its root.
        device: this machine's capture settings, which the /draw page is recorded
            with; read from ``<data_dir>/device.json`` if None.
        annotate_request: the model call for one sanitized annotation request;
            by default ``annotate.label.annotate`` with the configured model.
        ask: the Jev call for one row; by default ``keypoints.ask.JevAsk``
            with ``keypoints.model``, which needs TYPESAFE_API_KEY.
    """
    data_dir = data_dir or default_data_dir()
    settings_file = server_settings(data_dir)
    if config is None:
        config = settings_file.read()
    if device is None:
        device = device_settings(data_dir).read()
    if annotate_request is None:
        async def annotate_request(request: AnnotationRequest) -> Annotation:
            return await annotate_async(request, model=config.annotate.model,
                                        max_tokens=config.annotate.max_tokens,
                                        api_base=config.annotate.api_base)

    chats_dir = data_dir / "chats"
    predictor = Predictor(config.predict)
    events = FileEvents(devices_dir(data_dir))
    captions = FileActivityCaptions(devices_dir(data_dir))
    answers = FileQuestionAnswers(devices_dir(data_dir))
    control = FileControl(control_file(data_dir))
    questions = load_questions()

    if ask is None and config.keypoints.enabled:
        if os.environ.get(TYPESAFE_KEY_ENV):
            ask = JevAsk(config.keypoints.model)
        else:
            logger.warning("keypoints are off: %s is not set", TYPESAFE_KEY_ENV)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_store_report(check_store(app.state.devices_dir, config.annotate.chunk_size))
        app.state.worker.start()
        if app.state.question_worker is not None:
            app.state.question_worker.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            app.state.worker.stop()
            if app.state.question_worker is not None:
                app.state.question_worker.stop()

    app = FastAPI(title="Juno Server", lifespan=lifespan)
    app.add_middleware(TimingMiddleware)
    # The settings file, and the settings this process started with: a change applies on restart.
    app.state.settings_file = settings_file
    app.state.settings = config
    app.state.chats_dir = chats_dir
    app.state.devices_dir = devices_dir(data_dir)
    app.state.llm = config.llm
    app.state.annotate = config.annotate
    app.state.predictor = predictor
    app.state.events = events
    # Each session's events, built once per change, and which sessions changed since a cursor.
    app.state.versions = SessionVersions(session_fingerprint(events, captions, answers, devices_dir(data_dir)))
    app.state.captions = captions
    app.state.answers = answers
    app.state.control = control
    app.state.control_config = config.control
    app.state.capture = device.capture
    app.state.draw = config.draw
    app.state.draw_sessions_dir = draw_sessions_dir(data_dir)
    annotating = control.read().server.annotate
    app.state.worker = AnnotationWorker(events, captions, config.annotate, annotate_request,
                                        annotating=annotating)
    app.state.keypoints = config.keypoints
    app.state.questions = questions
    # None when keypoints are off or no key is set.
    app.state.question_worker = (QuestionWorker(events, captions, answers, config.annotate,
                                                config.keypoints, app.state.questions, ask,
                                                asking=annotating)
                                 if config.keypoints.enabled and ask is not None else None)
    # The tools read events as the routes do, through the state above.
    app.state.chat_tools = create_chat_tools(app.state, chats_dir, config.llm.tool_result_chars,
                                             config.tools, workspace_dir(data_dir),
                                             claude_results_dir(data_dir))
    mcp = create_mcp(session_tools(app.state, config.llm.tool_result_chars))
    app.include_router(router)
    app.include_router(web_router)
    app.include_router(changes_router)
    app.include_router(chats_router)
    app.include_router(control_router)
    app.include_router(draw_router)
    app.include_router(events_router)
    app.include_router(hooks_router)
    app.include_router(indices_router)
    app.include_router(predictions_router)
    app.include_router(sessions_router)
    app.include_router(settings_router)
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
