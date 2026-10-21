"""Show two annotators' output on the same chunks side by side, next to what they were sent.

    uv run python scripts/compare_annotations.py A B [--host HOST] [--port PORT]

A and B are annotation stores of the same session (a directory holding
``annotations/NNNN.json``): a session in the server's store, or a run of
``scripts/annotate_session_activity.py`` under ``data/annotations/``. ``/`` lists
the chunks; ``/chunk/N`` shows chunk N's frames and event text, then each
store's actions and dense caption. Records are read on every request, so a run
still in progress shows what it has done so far. Screenshots are served only
when a record of either store names them.
"""

from __future__ import annotations

import argparse
import html
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from annotate.store import ANNOTATIONS_DIR, AnnotationRecord, read_record

STYLE = """
body { font: 13px/1.4 system-ui, sans-serif; margin: 12px; }
table { border-collapse: collapse; }
td, th { border: 1px solid #ccc; padding: 3px 6px; vertical-align: top; text-align: left; }
.grid { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px; }
.frame { margin-bottom: 10px; }
.frame img { width: 100%; }
pre { white-space: pre-wrap; margin: 2px 0; font-size: 12px; }
.failed { color: #b00; }
"""


def stored_chunks(store: Path) -> set[int]:
    return {int(path.stem) for path in (store / ANNOTATIONS_DIR).glob("*.json")
            if path.stem.isdigit()}


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{STYLE}</style>{body}").encode()


def summary_cell(record: Optional[AnnotationRecord]) -> str:
    if record is None:
        return "<td>-</td>"
    if record.status != "done":
        return f"<td class=failed>{html.escape(record.status)}</td>"
    return f"<td>{len(record.annotation.actions)} actions</td>"


def index_page(stores: list[Path]) -> bytes:
    chunks = sorted(set().union(*(stored_chunks(store) for store in stores)))
    records = {chunk: [read_record(store, chunk) for store in stores] for chunk in chunks}
    models = [next((r.model for row in records.values() for r in [row[i]] if r), str(store))
              for i, store in enumerate(stores)]
    rows = "".join(
        f"<tr><td><a href=/chunk/{chunk}>{chunk}</a></td>"
        + "".join(summary_cell(record) for record in records[chunk]) + "</tr>"
        for chunk in chunks)
    head = "".join(f"<th>{html.escape(model)}</th>" for model in models)
    return page("chunks", f"<table><tr><th>chunk</th>{head}</tr>{rows}</table>")


def output_column(record: Optional[AnnotationRecord]) -> str:
    if record is None:
        return "<div><i>not annotated</i></div>"
    title = f"<h3>{html.escape(record.model)}</h3>"
    if record.status != "done":
        return f"<div>{title}<pre class=failed>{html.escape(record.error or record.status)}</pre></div>"
    actions = "".join(
        f"<li><b>{action.start}-{action.end}</b> {html.escape(action.caption)}</li>"
        for action in record.annotation.actions)
    return (f"<div>{title}<ol>{actions}</ol><h4>dense caption</h4>"
            f"<pre>{html.escape(record.annotation.dense_caption)}</pre></div>")


def chunk_page(stores: list[Path], chunk: int) -> bytes:
    records = [read_record(store, chunk) for store in stores]
    frames_from = next((r for r in records if r is not None and r.frames), None)
    frames = "".join(
        f"<div class=frame><b>{html.escape(frame.label)}</b>"
        f"<img loading=lazy src=/img/{chunk}/{number}><pre>{html.escape(frame.events)}</pre></div>"
        for number, frame in enumerate(frames_from.frames if frames_from else []))
    nav = f"<a href=/chunk/{chunk - 1}>prev</a> | <a href=/>all</a> | <a href=/chunk/{chunk + 1}>next</a>"
    body = (f"<p>{nav} &nbsp; <b>chunk {chunk}</b></p><div class=grid>"
            f"<div><h3>input</h3>{frames or '<i>no frames</i>'}</div>"
            + "".join(output_column(record) for record in records) + "</div>")
    return page(f"chunk {chunk}", body)


def screenshot(stores: list[Path], chunk: int, number: int) -> Optional[Path]:
    """The screenshot of frame ``number`` of ``chunk``, as a stored record names it."""
    for store in stores:
        record = read_record(store, chunk)
        if record is not None and 0 <= number < len(record.frames):
            return Path(record.frames[number].screenshot_path)
    return None


def handler_for(stores: list[Path]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                return self.send(200, index_page(stores), "text/html")
            if match := re.fullmatch(r"/chunk/(\d+)", self.path):
                return self.send(200, chunk_page(stores, int(match[1])), "text/html")
            if match := re.fullmatch(r"/img/(\d+)/(\d+)", self.path):
                path = screenshot(stores, int(match[1]), int(match[2]))
                if path is not None and path.is_file():
                    return self.send(200, path.read_bytes(),
                                     mimetypes.guess_type(path.name)[0] or "image/png")
            self.send(404, b"not found", "text/plain")

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stores", type=Path, nargs=2, metavar="STORE",
                        help="an annotation store: a directory holding annotations/")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args(argv)
    for store in args.stores:
        if not (store / ANNOTATIONS_DIR).is_dir():
            print(f"error: {store} has no {ANNOTATIONS_DIR}/ directory", file=sys.stderr)
            return 2
    server = ThreadingHTTPServer((args.host, args.port), handler_for(args.stores))
    print(f"http://{args.host}:{args.port}/", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
