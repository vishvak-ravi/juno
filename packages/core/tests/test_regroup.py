"""Rows read back hold the inputs their screenshot precedes, whichever row the recorder wrote them in."""

import json

from juno.sessions import read_aggregations


def event(at: float, kind: str = "mouse_down", cursor=(3, 4)) -> dict:
    return {"timestamp": at, "monitor_index": 0, "monitor": None, "event_type": kind,
            "details": {}, "cursor_position": list(cursor)}


def row(at: float, shot: float, state: str, end, events: list[dict]) -> dict:
    """A row as the recorder writes it: its events are whatever had arrived when it was written."""
    return {"timestamp": at, "reason": f"click_{state}_stale", "event_type": "click",
            "request_state": state, "screenshot_path": None, "screenshot_timestamp": shot,
            "end_screenshot_timestamp": end, "num_events": len(events), "events": events,
            "cursor_position": events[0]["cursor_position"] if events else None,
            "monitor": None, "burst_id": 1, "scale_factor": 1.0}


def read(tmp_path, rows: list[dict]):
    (tmp_path / "raw_aggregations.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return read_aggregations(tmp_path)


def inputs(aggregations) -> list[list[float]]:
    return [[e.timestamp for e in a.events] for a in aggregations]


def test_an_end_row_written_open_gives_the_next_burst_its_inputs(tmp_path):
    # The end row was the recorder's last row when written: no end screenshot
    # time, and the next burst's click is in it instead of in that burst's row.
    rows = [row(10.2, 10.0, "start", 10.5, [event(10.2)]),
            row(10.2, 10.5, "end", None, [event(11.2, cursor=(8, 9)), event(11.3, "mouse_up", (8, 9))]),
            row(11.2, 11.0, "start", 11.9, []),
            row(11.3, 11.9, "end", None, [])]
    aggregations = read(tmp_path, rows)
    assert inputs(aggregations) == [[10.2], [], [11.2, 11.3], []]
    assert (aggregations[2].num_events, aggregations[2].cursor_position) == (2, (8, 9))
    assert (aggregations[1].num_events, aggregations[1].cursor_position) == (0, None)
    # Rows keep their place, so their ids do not change.
    assert [a.timestamp for a in aggregations] == [10.2, 10.2, 11.2, 11.3]


def test_an_input_at_a_shared_screenshot_time_goes_to_the_later_row(tmp_path):
    rows = [row(10.2, 10.0, "start", 10.5, [event(10.2)]),
            row(10.2, 10.5, "end", 10.5, []),
            row(10.6, 10.5, "start", 11.0, [event(10.5), event(10.6)])]
    assert inputs(read(tmp_path, rows)) == [[10.2], [], [10.5, 10.6]]


def test_rows_are_windowed_by_screenshot_time_not_file_order(tmp_path):
    # An end row's screenshot is taken after its last input and the next start
    # row's before its first, so after a short gap the end row's comes later.
    rows = [row(10.2, 10.0, "start", 10.5, [event(10.2)]),
            row(10.2, 10.58, "end", None, [event(10.55), event(10.7)]),
            row(10.55, 10.5, "start", 10.58, []),
            row(10.7, 10.9, "end", None, [])]
    assert inputs(read(tmp_path, rows)) == [[10.2], [10.7], [10.55], []]


def test_a_session_of_one_row_keeps_its_inputs(tmp_path):
    aggregations = read(tmp_path, [row(10.2, 10.0, "start", None, [event(10.2), event(12.0)])])
    assert inputs(aggregations) == [[10.2, 12.0]]
