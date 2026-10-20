"""What the annotation model sees: one ``AnnotationRequest`` per chunk of aggregations.

The request is built the way Tada's ``Labeler.label_chunk`` builds it, from
napsack's own sanitizer, event formatter and prompt files, so the model input
matches Tada's. Everything the model is shown is on the request: ``str(request)``
is its text, and ``to_messages`` adds the images.
"""

from __future__ import annotations

import base64
import io
from importlib.resources import files
from typing import Iterator, Optional, Sequence

from napsack.label.models import Aggregation as NapsackAggregation
from napsack.record.sanitize import sanitize_records
from PIL import Image, ImageDraw
from pydantic import BaseModel

from juno.sessions import Aggregation
from annotate.sanitize import Sanitization

# Aggregations per request; Tada's MIN_CHUNK. The CLI takes it from the config.
DEFAULT_CHUNK_SIZE = 10
# Height of the drawn pointer, as a fraction of the screenshot's height.
CURSOR_HEIGHT_FRACTION = 1 / 50
# An arrow pointer with its tip at (0, 0), in units of its own height.
CURSOR_OUTLINE = ((0.0, 0.0), (0.0, 0.82), (0.19, 0.64), (0.34, 1.0),
                  (0.47, 0.94), (0.32, 0.6), (0.58, 0.6))


class AnnotationFrame(BaseModel):
    """One screenshot and the input that followed it, as shown to the model."""

    label: str
    screenshot_path: str
    # When the screenshot was taken, Unix seconds.
    timestamp: Optional[float] = None
    # Where the pointer is drawn, in desktop coordinates; None draws nothing.
    cursor: Optional[tuple[int, int]]
    monitor: Optional[dict[str, int]]
    # The event text, exactly as sent.
    events: str

    def __str__(self) -> str:
        return f"{self.label}\n{self.events}"


class AnnotationRequest(BaseModel):
    frames: list[AnnotationFrame]
    prompt: str
    # Set by ``annotate.sanitize.sanitize``; None means not yet safe to send out.
    sanitization: Optional[Sanitization] = None

    def __str__(self) -> str:
        return "\n".join([*(str(frame) for frame in self.frames), self.prompt])


def load_prompt() -> str:
    """Tada's labeling prompt: napsack's default prompt with the dense image output format."""
    prompts = files("napsack.label") / "prompts"
    base = (prompts / "default.txt").read_text()
    output_format = (prompts / "output" / "dense_image.txt").read_text()
    # Tada sends the event logs next to each frame, not inside the prompt.
    return base.replace("{{OUTPUT_FORMAT}}", output_format).replace("{{LOGS}}", "")


def build_request(aggregations: Sequence[Aggregation],
                  prompt: Optional[str] = None) -> AnnotationRequest:
    """The request for one chunk of consecutive aggregations."""
    # Tada drops an aggregation that has no screenshot before sanitizing, so
    # its events are not handed to a neighbouring frame.
    records = sanitize_records([a.model_dump() for a in aggregations
                                if a.screenshot_path is not None])
    frames = []
    cursor = None
    for number, record in enumerate(records, start=1):
        label = f"Frame {number}"
        # The pointer as of the first event after the screenshot; without
        # events it stays where the previous frame left it.
        cursor = next((tuple(e["cursor_position"]) for e in record["events"]
                       if e["cursor_position"] is not None), cursor)
        frames.append(AnnotationFrame(
            label=label,
            screenshot_path=record["screenshot_path"],
            timestamp=record["screenshot_timestamp"],
            cursor=cursor,
            monitor=record["monitor"],
            events=NapsackAggregation.from_dict(record).to_prompt(label),
        ))
        positions = [e["cursor_position"] for e in record["events"]
                     if e["cursor_position"] is not None]
        if positions:
            cursor = tuple(positions[-1])
    return AnnotationRequest(frames=frames, prompt=load_prompt() if prompt is None else prompt)


def build_requests(aggregations: Sequence[Aggregation],
                   chunk_size: int = DEFAULT_CHUNK_SIZE,
                   prompt: Optional[str] = None) -> Iterator[AnnotationRequest]:
    """One request per ``chunk_size`` aggregations, the last one possibly shorter."""
    for start in range(0, len(aggregations), chunk_size):
        request = build_request(aggregations[start:start + chunk_size], prompt)
        if request.frames:
            yield request


def chunk_request(aggregations: Sequence[Aggregation], chunk: int,
                  chunk_size: int = DEFAULT_CHUNK_SIZE,
                  prompt: Optional[str] = None) -> Optional[AnnotationRequest]:
    """The request for chunk ``chunk`` (from 0) of a session's aggregations.

    None if the session has no such rows or none of them has a screenshot.
    """
    rows = aggregations[chunk * chunk_size:(chunk + 1) * chunk_size]
    request = build_request(rows, prompt) if rows else None
    return request if request is not None and request.frames else None


def draw_cursor(image: Image.Image, cursor: Optional[tuple[int, int]],
                monitor: Optional[dict[str, int]]) -> Image.Image:
    """``image`` with an arrow pointer at ``cursor``; unchanged if the position is unknown."""
    if cursor is None or not monitor:
        return image
    x = (cursor[0] - monitor["left"]) * image.width / monitor["width"]
    y = (cursor[1] - monitor["top"]) * image.height / monitor["height"]
    size = image.height * CURSOR_HEIGHT_FRACTION
    image = image.copy()
    ImageDraw.Draw(image).polygon(
        [(x + dx * size, y + dy * size) for dx, dy in CURSOR_OUTLINE],
        fill="black", outline="white", width=max(1, round(size / 12)))
    return image


def frame_image(frame: AnnotationFrame) -> Image.Image:
    """The screenshot the model is shown for ``frame``, pointer drawn in."""
    with Image.open(frame.screenshot_path) as image:
        return draw_cursor(image.convert("RGB"), frame.cursor, frame.monitor)


def to_messages(request: AnnotationRequest) -> list[dict]:
    """The chat messages for ``request``, laid out as napsack's ``LiteLLMClient`` does."""
    content = []
    for frame in request.frames:
        buffer = io.BytesIO()
        frame_image(frame).save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode("utf-8")
        content.append({"type": "text", "text": frame.label})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{data}"}})
        if frame.events:
            content.append({"type": "text", "text": frame.events})
    content.append({"type": "text", "text": request.prompt})
    return [{"role": "user", "content": content}]
