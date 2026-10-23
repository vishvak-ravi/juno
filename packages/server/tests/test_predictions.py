"""The prediction route and tool, against a stand-in for vLLM that records what it is sent."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Action, Annotation
from juno.sessions import session_dir
from annotate.request import AnnotationFrame
from annotate.sanitize import Sanitization
from annotate.store import new_caption, write_caption_file
from juno.config import LlmConfig, PredictConfig, ServerSettings, devices_dir
from predict.benchmarks import GradedRow, GraderSettings, PredictorSettings, run_file
from server.app import create_app

REPLY = "<actions>\n    <action>Saved the file.</action>\n</actions>"
SANITIZATION = Sanitization(algorithm="none", version=1, text=False, images=False,
                            redactions={})


class FakeModelServer(ThreadingHTTPServer):
    """Lists one model and answers every completion with ``REPLY``."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeModelHandler)
        self.requests: list[dict] = []  # bodies of the completion requests, in order

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class FakeModelHandler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def reply(self, body: dict):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply({"data": [{"id": "fake-model"}]})

    def do_POST(self):
        self.server.requests.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.reply({"choices": [{"message": {"role": "assistant", "content": REPLY},
                                 "finish_reason": "stop"}]})


@pytest.fixture
def model_server():
    server = FakeModelServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def client_for(tmp_path, base_url: str, **predict) -> TestClient:
    config = ServerSettings(llm=LlmConfig(base_url=base_url, max_tokens=77, temperature=0.5),
                            predict=PredictConfig(**{"past_len": 4, "future_len": 3,
                                             "num_imgs_per_sample": 2, **predict}))
    # The MCP endpoint only answers requests addressed to the server's own host.
    return TestClient(create_app(config, data_dir=tmp_path), base_url="http://127.0.0.1:8765")


def write_annotation(tmp_path, chunk: int, captions: list[str], dense_caption: str,
                     device: str = "laptop", finished_at: float = None):
    """A finished annotation with one frame and one action per caption."""
    session_id = f"{device}-session_20260101_120000"
    directory = session_dir(devices_dir(tmp_path), session_id)
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for number, _ in enumerate(captions, start=1):
        path = directory / f"{chunk}-{number}.png"
        Image.new("RGB", (40, 20), "white").save(path)
        frames.append(AnnotationFrame(label=f"Frame {number}", screenshot_path=str(path),
                                      timestamp=1000.0 + chunk, cursor=None, monitor=None,
                                      events=""))
    actions = [Action(start=number, end=number, caption=caption)
               for number, caption in enumerate(captions, start=1)]
    record = new_caption(session_id, chunk, "fake-annotator").model_copy(update={
        "status": "done", "frames": frames,
        "updated_at": 2000.0 + chunk if finished_at is None else finished_at,
        "annotation": Annotation(actions=actions, dense_caption=dense_caption,
                                 sanitization=SANITIZATION)})
    write_caption_file(directory, record)


def sent_parts(model_server) -> tuple[list[dict], str]:
    """The image parts and the text of the one message the model was sent."""
    [message] = model_server.requests[-1]["messages"]
    assert message["role"] == "user"
    images = [part for part in message["content"] if part["type"] == "image_url"]
    [text] = [part["text"] for part in message["content"] if part["type"] == "text"]
    return images, text


def test_no_prediction_until_enough_actions_are_annotated(tmp_path, model_server):
    write_annotation(tmp_path, 0, ["Opened the editor.", "Typed a line."], "editor text")
    client = client_for(tmp_path, model_server.url)

    response = client.post("/predictions")

    assert response.status_code == 409
    assert response.json()["detail"] == "not enough data (2/4)"
    assert model_server.requests == []


def test_a_prediction_is_the_models_reply_to_the_latest_actions(tmp_path, model_server):
    write_annotation(tmp_path, 0, ["Opened the editor.", "Typed a line."], "editor text")
    write_annotation(tmp_path, 1, ["Ran the tests.", "Read the failure.", "Fixed the bug."],
                     "terminal text")
    client = client_for(tmp_path, model_server.url)

    response = client.post("/predictions")

    assert response.status_code == 200
    assert response.json()["actions"] == REPLY
    body = model_server.requests[-1]
    assert body["temperature"] == 1.0 and body["max_tokens"] == 2048
    assert "tools" not in body
    images, text = sent_parts(model_server)
    assert len(images) == 2
    assert all(image["image_url"]["url"].startswith("data:image/png;base64,")
               for image in images)
    assert text.startswith("You will analyze user behavior")
    assert "Predict the next 3 actions the user will take." in text
    assert "Look at the images of their device" in text
    # The last four actions, oldest first, each caption change described once.
    text = text[:text.index("</actions>")]
    assert "Opened the editor." not in text
    assert text.index("Typed a line.") < text.index("<description>editor text</description>") \
        < text.index("Ran the tests.") < text.index("<description>terminal text</description>") \
        < text.index("Read the failure.") < text.index("Fixed the bug.")
    assert text.count("<description>terminal text</description>") == 1


def test_without_screenshots_the_request_is_text_only(tmp_path, model_server):
    write_annotation(tmp_path, 0, ["One.", "Two.", "Three.", "Four."], "some text")
    client = client_for(tmp_path, model_server.url, num_imgs_per_sample=None)

    assert client.post("/predictions").status_code == 200

    images, text = sent_parts(model_server)
    assert images == []
    assert "Look at the images" not in text


def test_earlier_similar_activity_is_given_as_context(tmp_path, model_server):
    write_annotation(tmp_path, 0, ["Wrote the quarterly budget summary."],
                     "quarterly budget spreadsheet totals", finished_at=1000.0)
    write_annotation(tmp_path, 1, ["Watched a video."], "cooking pasta recipe video",
                     finished_at=1100.0)
    write_annotation(tmp_path, 2, ["Opened the budget.", "Edited a total.", "Sorted rows.",
                                   "Saved it."],
                     "quarterly budget spreadsheet totals for the new year", finished_at=5000.0)
    client = client_for(tmp_path, model_server.url)

    retrieved = client.post("/predictions").json()["retrieved"]

    assert "quarterly budget spreadsheet totals" in retrieved
    assert "  - Wrote the quarterly budget summary." in retrieved
    assert "pasta" not in retrieved
    _, text = sent_parts(model_server)
    assert f"<context>\n{retrieved}\n</context>" in text

    # A second prediction over the same activity retrieves the same context.
    assert client.post("/predictions").json()["retrieved"] == retrieved


def test_a_device_filter_predicts_from_that_devices_actions_only(tmp_path, model_server):
    write_annotation(tmp_path, 0, ["A.", "B.", "C.", "D."], "laptop text", device="laptop")
    write_annotation(tmp_path, 1, ["Phone one.", "Phone two."], "phone text", device="phone")
    client = client_for(tmp_path, model_server.url)

    assert client.post("/predictions?device=phone").status_code == 409
    assert client.post("/predictions?device=laptop").status_code == 200
    _, text = sent_parts(model_server)
    assert "Phone" not in text


def test_an_unreachable_model_server_is_a_502(tmp_path):
    write_annotation(tmp_path, 0, ["One.", "Two.", "Three.", "Four."], "some text")
    client = client_for(tmp_path, "http://127.0.0.1:1")

    assert client.post("/predictions").status_code == 502


def write_graded(tmp_path, run: str, graded_at: list[float], device: str = "laptop",
                 image_path=None) -> None:
    """A benchmark run's file with one row per ``graded_at`` time, the first with a screenshot."""
    session_id = f"{device}-session_20260101_120000"
    directory = session_dir(devices_dir(tmp_path), session_id)
    if image_path is None:
        image_path = directory / "screenshots" / "1.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 20), "white").save(image_path)
    path = run_file(directory, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, at in enumerate(graded_at):
        content = [{"type": "text", "text": "Predict the next 2 actions."}]
        if index == 0:
            content.insert(0, {"type": "image_url", "image_url": {"url": image_path.as_uri()}})
        rows.append(GradedRow(
            session_id=session_id, run=run, index=index, graded_at=at, dataset="d.jsonl",
            predictor=PredictorSettings(model="hosted_vllm/predictor"),
            grader=GraderSettings(model="gemini/grader"), cutoff_ts=1000.0, future_len=2,
            messages=[{"role": "user", "content": content}], retrieved="",
            ground_truth="<actions><action>A</action><action>B</action></actions>",
            prediction="<actions><action>A</action><action>C</action></actions>",
            reward=0.6, accuracy=0.5, formatting=0.7,
            accuracy_prompt="judge accuracy", accuracy_reply='{"candidates": [{"score": 0.5}]}',
            formatting_prompt="judge formatting", formatting_reply='{"score": 0.7}'))
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows))


def test_graded_predictions_of_every_run_are_listed_most_recent_first(tmp_path):
    write_graded(tmp_path, "20260101_120000", [10.0, 30.0])
    write_graded(tmp_path, "20260102_120000", [20.0], device="desktop")
    client = client_for(tmp_path, "http://127.0.0.1:9")

    page = client.get("/predictions", params={"limit": 2}).json()

    assert [row["graded_at"] for row in page["data"]] == [30.0, 20.0]
    assert page["data"][0]["id"] == "laptop-session_20260101_120000-20260101_120000-0001"
    assert page["data"][0]["predictor"]["model"] == "hosted_vllm/predictor"
    assert page["data"][0]["grader"]["model"] == "gemini/grader"
    assert "messages" not in page["data"][0]
    assert page["has_more"]
    rest = client.get("/predictions", params={"after": page["last_id"]}).json()
    assert [row["graded_at"] for row in rest["data"]] == [10.0]
    assert not rest["has_more"]
    desktop = client.get("/predictions", params={"device": "desktop"}).json()
    assert [row["device_id"] for row in desktop["data"]] == ["desktop"]


def test_a_graded_prediction_shows_what_each_model_was_sent(tmp_path):
    write_graded(tmp_path, "20260101_120000", [10.0])
    client = client_for(tmp_path, "http://127.0.0.1:9")
    graded_id = "laptop-session_20260101_120000-20260101_120000-0000"

    row = client.get(f"/predictions/{graded_id}").json()

    image, text = row["messages"][0]["content"]
    assert image["image_url"]["url"] == f"/predictions/{graded_id}/images/0"
    assert text["text"] == "Predict the next 2 actions."
    assert row["accuracy_prompt"] == "judge accuracy"
    assert row["formatting_reply"] == '{"score": 0.7}'
    screenshot = client.get(f"/predictions/{graded_id}/images/0")
    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert client.get(f"/predictions/{graded_id}/images/1").status_code == 404
    assert client.get(
        "/predictions/laptop-session_20260101_120000-20260101_120000-0009").status_code == 404


def test_a_screenshot_outside_the_store_is_not_served(tmp_path):
    outside = tmp_path / "elsewhere.png"
    Image.new("RGB", (4, 4)).save(outside)
    write_graded(tmp_path, "20260101_120000", [10.0], image_path=outside)
    client = client_for(tmp_path, "http://127.0.0.1:9")

    response = client.get(
        "/predictions/laptop-session_20260101_120000-20260101_120000-0000/images/0")

    assert response.status_code == 404
