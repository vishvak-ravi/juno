"""The next-action prediction request, built the way Tada's ``PromptedPredictor`` builds it.

Tada keeps a buffer of events, one per action its labeler captioned, and asks a
model for the next actions given the last ``past_len`` of them, the screenshots
of the newest few, and earlier activity retrieved with BM25. Here the events
come from the stored annotations (``annotate.store``); the texts, the retrieval
and the message layout are Tada's (``tada/src/user_models/prompted/predictor.py``
and ``powernap/longnap/trainer_utils.py``), and ``predict.retrievers`` is a copy
of its ``retrievers`` package.

There is no system prompt: Tada sends one user message, images first.
"""

from __future__ import annotations

import base64
import io
import threading
from typing import Any

from PIL import Image

from annotate.store import AnnotationRecord
from juno.config import PredictConfig
from predict.retrievers import InMemoryBM25Temporal, jaccard_ngrams, mmr_select

TASK_DESCRIPTION = (
    "You will analyze user behavior and predict what the user will do next. "
    "Below are the actions the user took."
)

TASK_DESCRIPTION_WITH_IMAGES = (
    "You will analyze user behavior and predict what the user will do next. "
    "Below are the actions the user took. "
    "Look at the images of their device to help you predict the user's next action."
)

# The retriever namespace Tada indexes its buffer under.
CONTEXT_NAMESPACE = "context"


def prediction_events(records: list[AnnotationRecord]) -> list[dict[str, Any]]:
    """Tada's buffer items for finished annotations, oldest first.

    One event per action, as Tada's labeler makes them: the action's caption,
    the annotation's dense caption, and the screenshot of the action's last
    frame. An action that ends on a frame an earlier action ended on is
    dropped. The time is when the annotation was finished, which is when Tada
    writes a label to its buffer.
    """
    events = []
    for record in sorted(records, key=lambda r: r.updated_at):
        if record.annotation is None or not record.frames:
            continue
        last = len(record.frames) - 1
        assigned = set()
        for action in record.annotation.actions:
            start = max(0, min(action.start - 1, last))
            end = max(start, min(action.end - 1, last))
            if end in assigned:
                continue
            assigned.add(end)
            events.append({
                "timestamp": record.updated_at,
                "text": action.caption,
                "dense_caption": record.annotation.dense_caption,
                "img_path": record.frames[end].screenshot_path,
            })
    return events


def collect_dense_captions(events: list[dict[str, Any]]) -> str:
    """The distinct dense captions of ``events``, in order of first appearance."""
    seen = set()
    captions = []
    for event in events:
        caption = event.get("dense_caption", "")
        if caption and caption not in seen:
            seen.add(caption)
            captions.append(caption)
    return "\n".join(captions)


def build_actions_block(events: list[dict[str, Any]], include_descriptions: bool = False) -> str:
    """The ``<actions>`` block; with descriptions, each new dense caption follows its action."""
    lines = []
    last_dense_caption = ""
    for event in events:
        lines.append(f"<action>{event['text']}</action>")
        if not include_descriptions:
            continue
        dense_caption = (event.get("dense_caption", "") or "").strip()
        if dense_caption and dense_caption != last_dense_caption:
            lines.append(f"<description>{dense_caption}</description>")
            last_dense_caption = dense_caption
    return "<actions>\n" + "\n".join("    " + line for line in lines) + "\n</actions>"


def format_retrieved(hits: list[dict[str, Any]]) -> str:
    """Each retrieved caption followed by the actions taken under it."""
    blocks = []
    for hit in hits:
        actions = hit.get("meta", {}).get("actions", [])
        if actions:
            action_lines = "\n".join(f"  - {action}" for action in actions)
            blocks.append(f"{hit['text']}\n{action_lines}")
        else:
            blocks.append(hit["text"])
    return "\n\n".join(blocks)


def image_part(path: str) -> dict[str, Any]:
    """The screenshot at ``path`` as a chat-completions image part, PNG-encoded."""
    buffer = io.BytesIO()
    Image.open(path).save(buffer, format="PNG")
    data = base64.b64encode(buffer.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


class Predictor:
    """Builds prediction requests, keeping the BM25 index of past activity between them.

    The index lives in memory: the first request after a start indexes every
    event, later ones only the events that are new.
    """

    def __init__(self, config: PredictConfig):
        self.config = config
        self.retriever = InMemoryBM25Temporal(
            dedup_threshold=0.8, dedup_sim_fn=lambda a, b: jaccard_ngrams(a, b, n=3))
        # The index and the bookkeeping below change together; hold this around
        # ``index_context`` and ``build_messages``.
        self.lock = threading.Lock()
        self._caption_groups: dict[str, list[str]] = {}
        self._caption_ts: dict[str, int] = {}
        # Time of the newest event indexed so far.
        self._last_indexed_ts = float("-inf")

    def index_context(self, events: list[dict[str, Any]]) -> None:
        """Index the events of ``events`` (oldest first) that are newer than the last call's.

        Events are grouped by dense caption: the caption is the document and
        the actions taken under it collect in its metadata.
        """
        for event in events:
            if event["timestamp"] <= self._last_indexed_ts:
                continue
            text = event.get("text", "")
            dense_caption = event.get("dense_caption", "")
            if dense_caption:
                actions = self._caption_groups.setdefault(dense_caption, [])
                if text:
                    actions.append(text)
                ts = self._caption_ts.setdefault(dense_caption, int(event["timestamp"]))
                self.retriever.add(dense_caption, event_ts=ts, namespace=CONTEXT_NAMESPACE,
                                   metadata={"actions": list(actions)})
            elif text:
                self.retriever.add(text, event_ts=int(event["timestamp"]),
                                   namespace=CONTEXT_NAMESPACE)
        if events:
            self._last_indexed_ts = max(self._last_indexed_ts, events[-1]["timestamp"])

    def retrieve(self, past: list[dict[str, Any]], actions_block: str) -> str:
        """Activity from before ``past`` that resembles it, formatted for the prompt."""
        query = actions_block
        dense_caption = collect_dense_captions(past)
        if dense_caption:
            query = query + "\n\n" + dense_caption
        hits = self.retriever.query(
            query, k=self.config.top_k, cutoff_ts=int(past[0]["timestamp"]),
            namespaces=[CONTEXT_NAMESPACE], time_decay_lambda=self.config.time_decay_lambda)
        if hits:
            items = [(hit["text"], hit["score"], hit) for hit in hits]
            selected = mmr_select(items, top_m=self.config.mmr_k, alpha=self.config.mmr_alpha)
            hits = [item[2] for item in selected]
        return format_retrieved(hits)

    def build_messages(self, past: list[dict[str, Any]]) -> tuple[list[dict], str]:
        """The chat messages asking for the actions after ``past``, and the retrieved text in them.

        Raises ``OSError`` if a screenshot cannot be read.
        """
        actions_block = build_actions_block(past, include_descriptions=True)

        image_parts = []
        if self.config.num_imgs_per_sample is not None:
            for event in past[-self.config.num_imgs_per_sample:]:
                if event.get("img_path") is not None:
                    image_parts.append(image_part(event["img_path"]))

        retrieved = self.retrieve(past, actions_block)

        task = TASK_DESCRIPTION_WITH_IMAGES if image_parts else TASK_DESCRIPTION
        text = task + "\n\n" + actions_block
        if retrieved:
            text += (f"\n\nHere is some relevant context about this user:\n"
                     f"<context>\n{retrieved}\n</context>")
        text += (f"\n\nPredict the next {self.config.future_len} actions the user will take. "
                 f"Output ONLY <action>...</action> tags inside a larger <actions>...</actions> "
                 f"block, with each action wrapped in its own <action> tag.")
        content = image_parts + [{"type": "text", "text": text}]
        return [{"role": "user", "content": content}], retrieved
