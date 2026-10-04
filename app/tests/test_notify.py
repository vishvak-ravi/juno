import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from capture.notify import ChunkNotifier


class Recording(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.puts: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_PUT(self):
        self.server.puts.append(self.path)
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_each_completed_chunk_is_announced_once(tmp_path):
    server = Recording()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = tmp_path / "session_20260101_120000"
    session.mkdir()
    rows = session / "raw_aggregations.jsonl"
    notifier = ChunkNotifier(session, f"http://127.0.0.1:{server.server_port}/annotations",
                             chunk_size=3)
    try:
        notifier.on_aggregation()  # no file yet
        with open(rows, "a") as file:
            for written in range(1, 8):
                file.write("{}\n")
                file.flush()
                notifier.on_aggregation()
                if written == 7:
                    file.write('{"half a row')  # no newline: not counted
                    file.flush()
                    notifier.on_aggregation()
    finally:
        notifier.close()
        server.shutdown()
        server.server_close()
    # Seven rows in chunks of three: chunks 0 and 1 are full, chunk 2 is not.
    assert server.puts == ["/annotations/session_20260101_120000-0000",
                           "/annotations/session_20260101_120000-0001"]
