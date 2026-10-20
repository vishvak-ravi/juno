"""One next-action prediction, for ``POST /predictions`` and the ``predict_next_actions`` tool.

The request Tada's prompted user model sends (see ``predict.predictor``) is
built from the finished annotations and completed with the model server the
chats use. Nothing is stored.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from annotate.store import list_records
from juno.config import LlmConfig
from predict.predictor import Predictor, prediction_events
from server.agent.llm import complete


class NotEnoughData(Exception):
    """Fewer than ``predict.past_len`` actions have been annotated."""


class Prediction(BaseModel):
    # The model's reply as written: an <actions> block with one <action> per predicted action.
    actions: str
    # The earlier activity the model was shown as context; empty if none was retrieved.
    retrieved: str
    # When the prediction was made, local ISO 8601.
    timestamp: str


def predict(devices_dir: Path, predictor: Predictor, llm: LlmConfig,
            device: Optional[str] = None) -> Prediction:
    """Predict the next actions from the latest annotated ones, of one device or of all.

    Raises ``NotEnoughData`` until ``predict.past_len`` actions have been
    annotated, ``OSError`` if a screenshot cannot be read and
    ``server.agent.llm.LlmError`` if the model server gives no reply.
    """
    config = predictor.config
    done = [record for record in list_records(devices_dir, device_id=device)
            if record.status == "done"]
    events = prediction_events(done)
    if len(events) < config.past_len:
        raise NotEnoughData(f"not enough data ({len(events)}/{config.past_len})")
    with predictor.lock:
        predictor.index_context(events)
        messages, retrieved = predictor.build_messages(events[-config.past_len:])
    llm = llm.model_copy(update={"max_tokens": config.max_tokens,
                                 "temperature": config.temperature})
    actions = complete(llm, messages)
    return Prediction(actions=actions, retrieved=retrieved,
                      timestamp=datetime.now().isoformat())
