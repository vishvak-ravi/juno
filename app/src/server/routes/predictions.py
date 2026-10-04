"""``/predictions``: what the user is likely to do next, from their annotated activity.

``POST /predictions`` builds the request Tada's prompted user model sends (see
``predict.predictor``) from the finished annotations and completes it with the
model server the chats use. Nothing is stored.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from annotate.store import list_records
from predict.predictor import prediction_events
from server.llm import LlmError, complete
from server.routes.paging import DeviceFilter

router = APIRouter(prefix="/predictions", tags=["predictions"])


class Prediction(BaseModel):
    # The model's reply as written: an <actions> block with one <action> per predicted action.
    actions: str
    # The earlier activity the model was shown as context; empty if none was retrieved.
    retrieved: str
    # When the prediction was made, local ISO 8601.
    timestamp: str


@router.post("")
def create_prediction(request: Request, device: DeviceFilter = None) -> Prediction:
    """Predict the next actions from the latest annotated ones, of one device or of all.

    409 until ``predict.past_len`` actions have been annotated. 502 if the
    model server gives no reply.
    """
    state = request.app.state
    predictor = state.predictor
    config = predictor.config
    done = [record for record in list_records(state.devices_dir, device_id=device)
            if record.status == "done"]
    events = prediction_events(done)
    if len(events) < config.past_len:
        raise HTTPException(status_code=409,
                            detail=f"not enough data ({len(events)}/{config.past_len})")
    try:
        with predictor.lock:
            predictor.index_context(events)
            messages, retrieved = predictor.build_messages(events[-config.past_len:])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not read a screenshot: {exc}")
    llm = state.llm.model_copy(update={"max_tokens": config.max_tokens,
                                       "temperature": config.temperature})
    try:
        actions = complete(llm, messages)
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Prediction(actions=actions, retrieved=retrieved,
                      timestamp=datetime.now().isoformat())
