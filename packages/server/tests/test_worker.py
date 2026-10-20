"""The annotation worker: what it picks from the store, in what order, how many at once."""

import json
import threading
import time

from PIL import Image

from annotate.label import Action, Annotation
from juno.sessions import session_dir
from annotate.store import new_record, read_record, write_record
from juno.config import AnnotateConfig, devices_dir
from server.worker import AnnotationWorker, Priority, find_work

OLD, NEW, OTHER = ("laptop-session_20260101_120000", "laptop-session_20260102_120000",
                   "desktop-session_20260101_180000")


def write_session(tmp_path, session_id: str, rows: int, start: float):
    """A stored session of ``rows`` aggregations a second apart from ``start``."""
    directory = session_dir(devices_dir(tmp_path), session_id)
    directory.mkdir(parents=True)
    lines = []
    for index in range(rows):
        path = directory / f"{index}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        shot = start + index
        lines.append(json.dumps({
            "timestamp": shot, "reason": "key_start", "event_type": "key",
            "request_state": "start", "screenshot_path": str(path),
            "screenshot_timestamp": shot, "end_screenshot_timestamp": shot + 1.0,
            "num_events": 0, "events": [], "cursor_position": None,
            "monitor": None, "burst_id": 1, "scale_factor": 1.0}))
    (directory / "raw_aggregations.jsonl").write_text("\n".join(lines) + "\n")
    return directory


def annotation(request) -> Annotation:
    return Annotation(actions=[Action(start=1, end=1, caption="Did it.")],
                      dense_caption="", sanitization=request.sanitization)


def settings(**overrides) -> AnnotateConfig:
    return AnnotateConfig(**{"chunk_size": 2, "retry_base_seconds": 0.01,
                             "scan_interval_seconds": 0.05, "tail_idle_seconds": 0, **overrides})


def wait_for(condition, what):
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(f"{what} did not happen")


def idle(worker) -> bool:
    status = worker.status()
    return status.running == 0 and not any(status.queued.values())


def status_of(tmp_path, session_id, chunk):
    record = read_record(session_dir(devices_dir(tmp_path), session_id), chunk)
    return record.status if record else None


def test_work_is_every_chunk_without_a_finished_annotation_of_the_rows_it_holds(tmp_path):
    old = write_session(tmp_path, OLD, rows=12, start=1000.0)   # chunks 0-5 full
    write_session(tmp_path, NEW, rows=3, start=3000.0)          # chunk 0 full, chunk 1 a tail
    done = {"status": "done", "rows": 2}
    write_record(old, new_record(OLD, 0, "any").model_copy(update=done))
    write_record(old, new_record(OLD, 1, "any").model_copy(update={"status": "done"}))  # old record
    write_record(old, new_record(OLD, 2, "any").model_copy(update={**done, "rows": 1}))  # grew since
    write_record(old, new_record(OLD, 3, "any"))                                         # pending
    write_record(old, new_record(OLD, 4, "any").model_copy(
        update={"status": "failed", "attempts": 1}))
    write_record(old, new_record(OLD, 5, "any").model_copy(
        update={"status": "failed", "attempts": 3}))                                    # gave up

    def found(now, **overrides):
        work = find_work(devices_dir(tmp_path), settings(**overrides), now)
        return sorted((w.session_id, w.chunk) for w in work)

    assert found(now=3002.5, tail_idle_seconds=60) == [
        (OLD, 2), (OLD, 3), (OLD, 4), (NEW, 0)]
    # The rows after the last full chunk count once the session has gone quiet.
    assert found(now=3100.0, tail_idle_seconds=60) == [
        (OLD, 2), (OLD, 3), (OLD, 4), (NEW, 0), (NEW, 1)]


def test_a_started_worker_annotates_the_whole_store_most_recent_first(tmp_path):
    write_session(tmp_path, OLD, rows=5, start=1000.0)
    write_session(tmp_path, OTHER, rows=2, start=2000.0)
    write_session(tmp_path, NEW, rows=2, start=3000.0)
    order = []

    def annotate_request(request):
        order.append(request.frames[0].timestamp)
        return annotation(request)

    worker = AnnotationWorker(devices_dir(tmp_path), settings(max_concurrency=1), annotate_request)
    worker.start()
    try:
        wait_for(lambda: len(order) == 5 and idle(worker), "the store being annotated")
    finally:
        worker.stop()

    assert order == [3000.0, 2000.0, 1004.0, 1002.0, 1000.0]
    tail = read_record(session_dir(devices_dir(tmp_path), OLD), 2)
    assert (tail.status, tail.rows, tail.attempts) == ("done", 1, 1)
    assert find_work(devices_dir(tmp_path), settings(), time.time()) == []


def test_calls_run_at_once_up_to_the_limit(tmp_path):
    write_session(tmp_path, OLD, rows=12, start=1000.0)
    in_flight, peak, lock = [0], [0], threading.Lock()

    def annotate_request(request):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.1)
        with lock:
            in_flight[0] -= 1
        return annotation(request)

    worker = AnnotationWorker(devices_dir(tmp_path), settings(max_concurrency=3), annotate_request)
    worker.start()
    try:
        wait_for(lambda: all(status_of(tmp_path, OLD, chunk) == "done" for chunk in range(6)),
                 "six chunks being annotated")
    finally:
        worker.stop()
    assert peak[0] == 3


def test_a_live_chunk_goes_ahead_of_requested_ones_and_those_ahead_of_backfill(tmp_path):
    write_session(tmp_path, OLD, rows=10, start=1000.0)
    release, order = threading.Event(), []

    def annotate_request(request):
        release.wait(5.0)
        order.append(int(request.frames[0].timestamp - 1000.0) // 2)
        return annotation(request)

    worker = AnnotationWorker(devices_dir(tmp_path), settings(max_concurrency=1, backfill=False),
                              annotate_request)
    try:
        worker.request(OLD, 0)  # taken at once; the others queue up behind it
        wait_for(lambda: worker.status().running == 1, "the first call starting")
        worker.request(OLD, 1, Priority.BACKFILL, end_time=1003.0)
        worker.request(OLD, 2, Priority.BACKFILL, end_time=1005.0)
        worker.request(OLD, 3, Priority.REQUESTED)
        worker.request(OLD, 4, Priority.LIVE)
        assert worker.status().queued == {"live": 1, "requested": 1, "backfill": 2}
        worker.request(OLD, 1, Priority.LIVE)  # asked for again, more urgently: it moves up
        release.set()
        wait_for(lambda: len(order) == 5, "every chunk being annotated")
    finally:
        release.set()
        worker.stop()

    # Chunk 4 was the live one queued first; 1 joined it; 3 was requested; 2 is backfill.
    assert order[0] == 0 and set(order[1:3]) == {1, 4} and order[3:] == [3, 2]


def test_a_busy_provider_is_tried_again_up_to_the_limit_and_other_failures_are_not(tmp_path):
    write_session(tmp_path, OLD, rows=6, start=1000.0)
    calls = {0: 0, 1: 0, 2: 0}

    def annotate_request(request):
        chunk = int(request.frames[0].timestamp - 1000.0) // 2
        calls[chunk] += 1
        if chunk == 0 and calls[chunk] < 3:
            raise TimeoutError("busy")
        if chunk == 1:
            raise TimeoutError("still busy")
        if chunk == 2:
            raise RuntimeError("the request is wrong")
        return annotation(request)

    worker = AnnotationWorker(devices_dir(tmp_path), settings(max_attempts=3, backfill=False),
                              annotate_request)
    try:
        for chunk in range(3):
            worker.request(OLD, chunk)
        wait_for(lambda: idle(worker), "every chunk settling")
    finally:
        worker.stop()

    records = [read_record(session_dir(devices_dir(tmp_path), OLD), chunk) for chunk in range(3)]
    assert [(r.status, r.attempts) for r in records] == [("done", 3), ("failed", 3), ("failed", 1)]
    assert "still busy" in records[1].error
    assert calls == {0: 3, 1: 3, 2: 1}


def test_what_a_stopped_server_left_pending_is_taken_up_without_backfill(tmp_path):
    directory = write_session(tmp_path, OLD, rows=4, start=1000.0)
    write_record(directory, new_record(OLD, 0, "any"))  # queued, then the server stopped

    worker = AnnotationWorker(devices_dir(tmp_path), settings(backfill=False), annotation)
    worker.start()
    try:
        wait_for(lambda: status_of(tmp_path, OLD, 0) == "done", "the pending chunk being annotated")
        wait_for(lambda: idle(worker), "the worker going idle")
    finally:
        worker.stop()
    assert status_of(tmp_path, OLD, 1) is None  # never asked for, and not the server's to find
