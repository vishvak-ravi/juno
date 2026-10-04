"""The prediction route, against a stand-in for vLLM that records what it is sent."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from annotate.label import Action, Annotation
from annotate.records import session_dir
from annotate.request import AnnotationFrame
from annotate.sanitize import Sanitization
from annotate.store import new_record, write_record
from juno.config import CaptureConfig, Config, LlmConfig, PredictConfig, devices_dir
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
    config = Config(capture=CaptureConfig(data_dir=tmp_path),
                    llm=LlmConfig(base_url=base_url, max_tokens=77, temperature=0.5),
                    predict=PredictConfig(**{"past_len": 4, "future_len": 3,
                                             "num_imgs_per_sample": 2, **predict}))
    return TestClient(create_app(config))


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
    record = new_record(session_id, chunk, "fake-annotator").model_copy(update={
        "status": "done", "frames": frames,
        "updated_at": 2000.0 + chunk if finished_at is None else finished_at,
        "annotation": Annotation(actions=actions, dense_caption=dense_caption,
                                 sanitization=SANITIZATION)})
    write_record(directory, record)


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
