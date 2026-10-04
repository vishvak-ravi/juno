"""``/predictions``: what the user is likely to do next, from their annotated activity.

``POST /predictions`` makes one prediction with ``server.prediction``. Nothing
is stored.
"""

from fastapi import APIRouter, HTTPException, Request

from server.llm import LlmError
from server.prediction import NotEnoughData, Prediction, predict
from server.routes.paging import DeviceFilter

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.post("")
def create_prediction(request: Request, device: DeviceFilter = None) -> Prediction:
    """Predict the next actions from the latest annotated ones, of one device or of all.

    409 until ``predict.past_len`` actions have been annotated. 502 if the
    model server gives no reply.
    """
    state = request.app.state
    try:
        return predict(state.devices_dir, state.predictor, state.llm, device)
    except NotEnoughData as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not read a screenshot: {exc}")
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
