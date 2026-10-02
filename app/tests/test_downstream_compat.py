"""The session must stay readable by the napsack code Tada's labeler runs later."""

import json
import sys
import time
import types

import pytest

from capture.recorder import Recorder
from capture.sources.base import Backend
from capture.sources.fake import FakeCursor, FakeScreenSource

from test_recorder import ManualInput, type_and_click


@pytest.fixture(autouse=True)
def _stub_tinfoil():
    # Tada's labeler stubs this optional client before importing napsack.label.
    name = "napsack.label.clients.tinfoil"
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.TinfoilClient = object
        sys.modules[name] = module


@pytest.mark.parametrize("cursor_position", [(10, 20), None])
def test_sanitize_and_label_models_accept_our_aggregations(tmp_path, cursor_position):
    from napsack.label.models import Aggregation
    from napsack.record.sanitize import sanitize_records

    backend = Backend("fake", FakeScreenSource(change_every=1), ManualInput(),
                      FakeCursor(cursor_position))
    recorder = Recorder(backend, data_dir=tmp_path, fps=40, poll_interval=0.05)
    recorder.start()
    time.sleep(0.1)
    type_and_click(backend.input.listener)
    time.sleep(0.15)
    recorder.stop()

    lines = (recorder.session_dir / "raw_aggregations.jsonl").read_text().splitlines()
    records = sanitize_records([json.loads(line) for line in lines])
    assert sum(r["num_events"] for r in records) == 6

    aggregations = [Aggregation.from_dict(r) for r in records]
    for aggregation in aggregations:
        aggregation.to_prompt("00:00")  # must not raise, null cursor included

    events = [e for a in aggregations for e in a.events]
    assert [e.details.get("key") for e in events if e.event_type == "key_press"] == ["h", "i"]
    clicks = [e for e in events if e.event_type == "mouse_down"]
    assert len(clicks) == 1
    assert clicks[0].details.get("button") == "Button.left"
