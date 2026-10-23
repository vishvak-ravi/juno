"""scripts/migrate_store.py: a store written before the move reads the same after it."""

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

from annotate.label import Action, Annotation
from annotate.request import build_request
from annotate.sanitize import sanitize
from annotate.store import FileActivityCaptions, new_caption
from juno.config import devices_dir
from juno.sessions import Aggregation, session_dir
from keypoints.store import ChunkAnswers, FileQuestionAnswers, RowAnswers
from server.events import FileEvents
from server.integrity import check_store

ROOT = Path(__file__).resolve().parents[3]
SESSION = "laptop-session_20260101_120000"


def write_old_store(data_dir: Path) -> None:
    """One session of two rows, captioned and answered, in the layout before the move."""
    directory = session_dir(devices_dir(data_dir), SESSION)
    (directory / "screenshots").mkdir(parents=True)
    rows = []
    for index in range(2):
        path = directory / "screenshots" / f"{index}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        rows.append({"timestamp": 1000.0 + index, "reason": "key_start", "event_type": "key",
                     "request_state": "start", "screenshot_path": str(path),
                     "screenshot_timestamp": 1000.0 + index, "end_screenshot_timestamp": None,
                     "num_events": 0, "events": [], "cursor_position": None, "monitor": None,
                     "burst_id": 1, "scale_factor": 1.0})
    (directory / "raw_aggregations.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    request = sanitize(build_request([Aggregation.model_validate(r) for r in rows], prompt=""))
    caption = new_caption(SESSION, 0, "test").model_copy(update={
        "status": "done", "frames": request.frames, "rows": 2,
        "annotation": Annotation(actions=[Action(start=1, end=2, caption="Typed.")],
                                 dense_caption="", sanitization=request.sanitization)})
    (directory / "annotations").mkdir()
    (directory / "annotations" / "0000.json").write_text(caption.model_dump_json())

    answers = ChunkAnswers(session_id=SESSION, chunk=0, model="jev", questions="1",
                           caption_updated_at=caption.updated_at, created_at=caption.updated_at,
                           rows={0: RowAnswers(input_tokens=5)}).model_dump()
    answers["annotation_updated_at"] = answers.pop("caption_updated_at")
    (directory / "detections" / "jev").mkdir(parents=True)
    (directory / "detections" / "jev" / "0000.json").write_text(json.dumps(answers))


def migrate(tmp_path: Path) -> int:
    config = tmp_path / "config.yaml"
    config.write_text(f"capture:\n  data_dir: {tmp_path / 'data'}\n")
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_store.py")],
                          env={"JUNO_CONFIG_PATH": str(config), "PATH": ""}, cwd=tmp_path).returncode


def test_a_migrated_store_holds_the_same_rows_captions_and_answers(tmp_path):
    write_old_store(tmp_path / "data")
    assert migrate(tmp_path) == 0
    assert migrate(tmp_path) == 0  # a second run finds nothing to do

    store = devices_dir(tmp_path / "data")
    rows = FileEvents(store).rows(SESSION)
    assert [row.timestamp for row in rows] == [1000.0, 1001.0]
    assert all(Path(row.screenshot_path).is_file() for row in rows)
    caption = FileActivityCaptions(store).get(SESSION, 0)
    assert [a.caption for a in caption.annotation.actions] == ["Typed."]
    assert all(Path(frame.screenshot_path).is_file() for frame in caption.frames)
    answers = FileQuestionAnswers(store).get(SESSION, 0)
    assert answers.caption_updated_at == caption.updated_at
    assert check_store(store, chunk_size=2).issues == []
    directory = session_dir(store, SESSION)
    assert not (directory / "raw_aggregations.jsonl").exists()
    assert not (directory / "annotations").exists() and not (directory / "detections").exists()


def test_a_session_a_newer_server_already_wrote_to_is_left_alone(tmp_path):
    write_old_store(tmp_path / "data")
    directory = session_dir(devices_dir(tmp_path / "data"), SESSION)
    (directory / "event_rows.jsonl").write_text("{}\n")

    assert migrate(tmp_path) == 1
    assert (directory / "raw_aggregations.jsonl").exists()
    assert (directory / "annotations" / "0000.json").exists()
