"""The recorder's report to the server, and what its answer does."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from capture.__main__ import report


def serving(body: bytes) -> HTTPServer:
    """A server on a free port answering every PUT with ``body``."""
    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def report_url(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}/control/devices/laptop/report"


def test_the_answer_says_whether_to_record_and_when_to_report_again():
    server = serving(b'{"capture": false, "poll_seconds": 2.5}')
    try:
        answer = report(report_url(server), "laptop-session_20260101_120000")
    finally:
        server.shutdown()
    assert (answer.capture, answer.poll_seconds) == (False, 2.5)


@pytest.mark.parametrize("body", [
    b'{"capture": true}',                           # no poll interval
    b'{"capture": "soon", "poll_seconds": 1}',      # wrong type
    b"<html>busy</html>",                           # not JSON
])
def test_an_answer_that_is_not_an_instruction_is_a_failed_report(body):
    server = serving(body)
    try:
        assert report(report_url(server), None) is None
    finally:
        server.shutdown()


def test_an_unreachable_server_is_a_failed_report():
    server = serving(b"{}")
    url = report_url(server)
    server.shutdown()
    server.server_close()
    assert report(url, None) is None
