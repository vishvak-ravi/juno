import pytest

from capture.sources.base import Monitor
from capture.sources.fake import FakeCursor, FakeScreenSource

LEFT = Monitor(index=0, left=0, top=0, width=640, height=360)
RIGHT = Monitor(index=1, left=640, top=0, width=320, height=240)


class RecordingQueue:
    """Stands in for napsack's queues: just remembers what was enqueued."""

    def __init__(self):
        self.items = []

    def enqueue(self, item):
        self.items.append(item)


@pytest.fixture
def queue():
    return RecordingQueue()


@pytest.fixture
def screen():
    source = FakeScreenSource(monitors=[LEFT, RIGHT])
    source.start()
    return source


@pytest.fixture
def cursor():
    return FakeCursor((10, 20))
