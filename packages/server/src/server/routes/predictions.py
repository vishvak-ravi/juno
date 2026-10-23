"""``/predictions``: what the user is likely to do next, from their annotated activity.

``POST /predictions`` makes one prediction with ``server.prediction``. Nothing
is stored.

``GET /predictions`` lists the graded predictions of every benchmark run
(``predict.benchmarks``), the most recently graded first, without what the
models were sent. ``GET /predictions/{id}`` is one of them in full: the
predictor's messages, with each screenshot at
``/predictions/{id}/images/{n}`` (``n`` from 0, in message order), and each
judge's prompt and reply.
"""

from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel

from predict.benchmarks import (GRADED_ID, GradedPrediction, GraderSettings, PredictorSettings,
                                list_graded)
from server.agent.llm import LlmError
from server.prediction import NotEnoughData, Prediction, predict
from server.routes.paging import DeviceFilter, Page, page_of

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.post("")
def create_prediction(request: Request, device: DeviceFilter = None) -> Prediction:
    """Predict the next actions from the latest annotated ones, of one device or of all.

    409 until ``predict.past_len`` actions have been annotated. 502 if the
    model server gives no reply.
    """
    state = request.app.state
    try:
        return predict(state.captions, state.predictor, state.llm, device)
    except NotEnoughData as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not read a screenshot: {exc}")
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


GradedId = Annotated[str, PathParam(pattern=GRADED_ID.pattern, max_length=160)]


class GradedSummary(BaseModel):
    """A graded prediction in a list: everything but what the models were sent and replied."""
    id: str
    device_id: str
    session_id: str
    run: str
    index: int
    graded_at: float
    dataset: str
    predictor: PredictorSettings
    grader: GraderSettings
    cutoff_ts: Optional[float]
    future_len: int
    ground_truth: str
    prediction: Optional[str]
    error: Optional[str]
    reward: Optional[float]
    accuracy: Optional[float]
    formatting: Optional[float]
    validation_errors: list[str]
    unparsed_replies: list[str]


def find_graded(request: Request, graded_id: str) -> GradedPrediction:
    device_id = graded_id.split("-", 1)[0]
    for row in list_graded(request.app.state.devices_dir, device_id):
        if row.id == graded_id:
            return row
    raise HTTPException(status_code=404, detail=f"no graded prediction with id {graded_id}")


def image_paths(row: GradedPrediction) -> list[str]:
    """The ``file://`` screenshot URLs of the predictor's messages, in order."""
    return [part["image_url"]["url"] for message in row.messages
            if isinstance(message.get("content"), list)
            for part in message["content"] if part.get("type") == "image_url"]


@router.get("")
def list_graded_predictions(
    request: Request,
    device: DeviceFilter = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    order: Literal["asc", "desc"] = "desc",
    after: Optional[str] = None,
) -> Page[GradedSummary]:
    """Graded predictions of every benchmark run, most recently graded first by default."""
    rows = [GradedSummary.model_validate(row.model_dump())
            for row in list_graded(request.app.state.devices_dir, device)]
    return page_of(rows, limit, order, after)


@router.get("/{graded_id}")
def get_graded_prediction(graded_id: GradedId, request: Request) -> GradedPrediction:
    """One graded prediction, with what the predictor and each judge were sent."""
    row = find_graded(request, graded_id)
    n = 0
    for message in row.messages:
        for part in message["content"] if isinstance(message.get("content"), list) else []:
            if part.get("type") == "image_url":
                part["image_url"]["url"] = f"/predictions/{graded_id}/images/{n}"
                n += 1
    return row


@router.get("/{graded_id}/images/{n}")
def get_graded_image(graded_id: GradedId, n: int, request: Request) -> FileResponse:
    """Screenshot ``n`` (from 0) the predictor was sent."""
    urls = image_paths(find_graded(request, graded_id))
    if not 0 <= n < len(urls):
        raise HTTPException(status_code=404, detail=f"{graded_id} has no image {n}")
    path = Path(unquote(urlparse(urls[n]).path)).resolve()
    # Only screenshots stored on this server are served.
    if not path.is_file() or request.app.state.devices_dir.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail=f"image {n} of {graded_id} is not stored here")
    return FileResponse(path, media_type="image/png")
