import os
import types
from pathlib import Path

import litellm
import pytest
from PIL import Image

from annotate.label import Action, AnnotationError, UnsanitizedRequest, annotate
from juno.sessions import read_aggregations
from annotate.request import AnnotationFrame, AnnotationRequest, build_requests
from annotate.sanitize import sanitize
from juno.config import AnnotateConfig

# The live test runs only with both of these set.
SESSION_ENV = "JUNO_ANNOTATE_SESSION"
KEY_ENV = "GEMINI_API_KEY"


@pytest.fixture
def request_of_one_frame(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (40, 20), "white").save(path)
    frame = AnnotationFrame(label="Frame 1", screenshot_path=str(path), cursor=None,
                            monitor=None, events="")
    return sanitize(AnnotationRequest(frames=[frame], prompt="Describe."))


def reply_with(monkeypatch, content, finish_reason="stop"):
    def completion(**params):
        message = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message, finish_reason=finish_reason)])
    monkeypatch.setattr(litellm, "completion", completion)


def test_the_reply_is_parsed_into_actions_and_a_dense_caption(monkeypatch, request_of_one_frame):
    reply_with(monkeypatch, '{"actions": [{"start": 1, "end": 1, "caption": "Opened a file."}],'
                            ' "dense_caption": "notes.txt"}')
    annotation = annotate(request_of_one_frame, model="gemini/any", max_tokens=100, api_key="k")
    assert annotation.actions == [Action(start=1, end=1, caption="Opened a file.")]
    assert annotation.dense_caption == "notes.txt"
    assert annotation.sanitization == request_of_one_frame.sanitization


def test_a_request_that_was_not_sanitized_is_not_sent(monkeypatch, request_of_one_frame):
    def completion(**params):
        raise AssertionError("the model was called")
    monkeypatch.setattr(litellm, "completion", completion)
    unsanitized = request_of_one_frame.model_copy(update={"sanitization": None})
    with pytest.raises(UnsanitizedRequest):
        annotate(unsanitized, model="gemini/any", max_tokens=100, api_key="k")


@pytest.mark.parametrize("content", [None, "", '{"actions": [', '{"actions": []}'])
def test_an_unusable_reply_is_reported_with_the_raw_text(monkeypatch, request_of_one_frame,
                                                         content):
    reply_with(monkeypatch, content, finish_reason="length")
    with pytest.raises(AnnotationError) as error:
        annotate(request_of_one_frame, model="gemini/any", max_tokens=100, api_key="k")
    assert repr(content) in str(error.value) and "'length'" in str(error.value)


@pytest.mark.skipif(not (os.environ.get(SESSION_ENV) and os.environ.get(KEY_ENV)),
                    reason=f"set {SESSION_ENV} and {KEY_ENV} to call the model")
def test_live_one_chunk_of_a_real_session_is_annotated():
    """One real model call on the first chunk of the session in JUNO_ANNOTATE_SESSION."""
    config = AnnotateConfig()
    aggregations = read_aggregations(Path(os.environ[SESSION_ENV]))
    request = sanitize(next(build_requests(aggregations, chunk_size=config.chunk_size)))

    annotation = annotate(request, model=config.model, max_tokens=config.max_tokens)

    assert annotation.actions, "the model described no action"
    assert annotation.dense_caption.strip()
    assert annotation.sanitization == request.sanitization
    for action in annotation.actions:
        assert 1 <= action.start <= action.end <= len(request.frames)
        assert action.caption.strip()


def test_a_model_on_an_openai_compatible_server_is_held_to_the_annotation_schema(
        monkeypatch, request_of_one_frame):
    sent = []

    def completion(**params):
        sent.append(params)
        message = types.SimpleNamespace(content='{"actions": [], "dense_caption": ""}')
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message, finish_reason="stop")])
    monkeypatch.setattr(litellm, "completion", completion)

    annotate(request_of_one_frame, model="openai/local", max_tokens=100, api_key="k",
             api_base="http://localhost:8000/v1")

    assert sent[0]["api_base"] == "http://localhost:8000/v1"
    response_format = sent[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"]["required"] == ["actions", "dense_caption"]
