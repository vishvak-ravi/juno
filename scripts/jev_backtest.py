"""Ask a local vision model Jev's questions about rows Jev already answered, and compare.

    uv run python scripts/jev_backtest.py run [--seed N] [--out DIR]
    uv run python scripts/jev_backtest.py report [--out DIR]
    uv run python scripts/jev_backtest.py serve [--out DIR] [--host HOST] [--port PORT]

``run`` picks captioned chunks with stored Jev answers in a random order and
asks the model at ``annotate.api_base`` (``annotate.model``) about each of
their rows, one call per row, until stopped. The row's state is what Jev was
sent (``keypoints.ask.row_state``): the screenshot with the pointer drawn
in, scaled as for Jev, the row's input text and its caption. Every question
the row was asked becomes a numbered multiple-choice item, the reply is one
``<number>: <letter>`` line per item, and an answer's probabilities are the
model's probabilities of each letter at that item's answer token. Each row is
one line of ``rows.jsonl`` in ``--out``; a restarted run skips rows already
there.

``report`` writes ``report.html`` in ``--out``: per question, how far the
model's answers are from Jev's; the moments each one's answers give
(``keypoints.derive``); and every row in time order with both answers.
``serve`` shows the same page over HTTP, rebuilt from ``rows.jsonl`` on every
request, so a run still in progress shows what it has asked so far.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import math
import mimetypes
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from dotenv import find_dotenv, load_dotenv
from pydantic import TypeAdapter

from annotate.request import frame_image, framed_rows
from annotate.store import FileActivityCaptions
from juno.config import ConfigError, data_dir, devices_dir
from juno.settings import server_settings
from keypoints.derive import probability, session_moments
from keypoints.ask import fitted, frame_captions
from keypoints.questions import QuestionSet, active, load_questions
from keypoints.store import Answer, FileQuestionAnswers
from server.events import FileEvents

ROOT_DIR = Path(__file__).resolve().parents[1]
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Alternatives read at each answer token; enough to hold every letter of an item.
TOP_LOGPROBS = 20
# Reply tokens allowed per item: the number, the colon, the letter and the newline.
TOKENS_PER_ITEM = 6
ANSWER_SLOT = re.compile(r"(?:^|\n)\s*(\d+)\s*[:.)]\s*$")
ANSWERS = TypeAdapter(dict[str, Answer])


def default_out(model: str) -> Path:
    return ROOT_DIR / "data" / "jev_backtest" / model.replace("/", "_")


def served_name(model: str) -> str:
    """The name the OpenAI-compatible server knows ``annotate.model`` by."""
    return model.removeprefix("openai/")


def options(question: dict[str, Any]) -> list[tuple[str, str]]:
    """(answer key, meaning) per option, in the order the letters are given."""
    criteria = question["criteria"]
    if question["type"] == "noul":
        return [("true", criteria["true"]), ("false", criteria["false"])]
    if question["type"] == "score":
        return [(str(level), text) for level, text in enumerate(criteria)]
    return list(criteria.items())


def prompt_text(inputs: str, caption: Optional[str], asked: dict[str, dict[str, Any]]) -> str:
    """The text after the screenshot: the row's state, then every question as a lettered item."""
    lines = ["The image is `screenshot`.", "", "`inputs`:", inputs or "(none)", "",
             "`caption`:", caption or "(none)", "",
             "Answer every question below about this state. Reply with exactly one line per "
             "question, in order, of the form `<number>: <letter>`, and nothing else.", ""]
    for number, question in enumerate(asked.values(), start=1):
        lines.append(f"{number}. {question['instructions']}")
        for letter, (_, meaning) in zip(LETTERS, options(question)):
            lines.append(f"   {letter}: {meaning}")
        lines.append("")
    return "\n".join(lines)


def letter_probabilities(top: list[dict[str, Any]], count: int) -> tuple[list[float], float]:
    """Probabilities of the first ``count`` letters among a token's alternatives, renormalized.

    Also returns the probability mass the letters held before renormalizing.
    """
    mass = [0.0] * count
    for alternative in top:
        token = alternative["token"].strip()
        if len(token) == 1 and token in LETTERS[:count]:
            mass[LETTERS.index(token)] += math.exp(alternative["logprob"])
    total = sum(mass)
    if total == 0:
        return [1 / count] * count, 0.0
    return [m / total for m in mass], total


def answer_slots(tokens: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Item number -> the reply token that answers it: the first token after ``<number>:``."""
    slots: dict[int, dict[str, Any]] = {}
    text = ""
    for token in tokens:
        match = ANSWER_SLOT.search(text)
        if match and token["token"].strip():
            slots.setdefault(int(match.group(1)), token)
        text += token["token"]
    return slots


def to_answer(question: dict[str, Any], probabilities: list[float]) -> dict[str, Any]:
    """The answer in Jev's shape; a Choice or Score's confidence is its top probability."""
    keys = [key for key, _ in options(question)]
    if question["type"] == "noul":
        return {"type": "noul", "noul": round(probabilities[0], 4)}
    if question["type"] == "score":
        by_level = {int(key): round(p, 4) for key, p in zip(keys, probabilities)}
        return {"type": "score", "score": round(sum(l * p for l, p in by_level.items()), 4),
                "confidence": round(max(probabilities), 4), "probabilities": by_level}
    by_option = {key: round(p, 4) for key, p in zip(keys, probabilities)}
    return {"type": "choice", "choice": max(by_option, key=by_option.get),
            "confidence": round(max(probabilities), 4), "probabilities": by_option}


def ask_row(client: httpx.Client, api_base: str, model: str, image_jpeg: bytes, inputs: str,
            caption: Optional[str], asked: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One call for every question of a row; returns its answers, letter mass and usage."""
    data = base64.b64encode(image_jpeg).decode()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}},
            {"type": "text", "text": prompt_text(inputs, caption, asked)}]}],
        "max_tokens": TOKENS_PER_ITEM * len(asked) + 8,
        "temperature": 0,
        "logprobs": True,
        "top_logprobs": TOP_LOGPROBS,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    started = time.perf_counter()
    response = client.post(f"{api_base.rstrip('/')}/chat/completions", json=body)
    response.raise_for_status()
    seconds = time.perf_counter() - started
    reply = response.json()
    choice = reply["choices"][0]
    slots = answer_slots((choice.get("logprobs") or {}).get("content") or [])
    answers, letter_mass, missing = {}, {}, []
    for number, (key, question) in enumerate(asked.items(), start=1):
        if number not in slots:
            missing.append(key)
            continue
        probabilities, mass = letter_probabilities(slots[number]["top_logprobs"],
                                                   len(options(question)))
        answers[key] = to_answer(question, probabilities)
        letter_mass[key] = round(mass, 4)
    return {"answers": answers, "letter_mass": letter_mass, "missing": missing,
            "reply": choice["message"].get("content"), "seconds": round(seconds, 3),
            "prompt_tokens": reply.get("usage", {}).get("prompt_tokens"),
            "timings": reply.get("timings")}


def jpeg(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def done_rows(path: Path) -> set[tuple[str, int]]:
    if not path.exists():
        return set()
    return {(row["session_id"], row["row"]) for row in read_lines(path)}


def read_lines(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def run(out: Path, seed: int) -> int:
    load_dotenv(find_dotenv(usecwd=True))
    try:
        config = server_settings().read()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if config.annotate.api_base is None:
        print("error: annotate.api_base names no local server", file=sys.stderr)
        return 2
    model = served_name(config.annotate.model)
    store = devices_dir(data_dir())
    questions = load_questions()
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    done = done_rows(rows_file)

    events, captions, answers = FileEvents(store), FileActivityCaptions(store), FileQuestionAnswers(store)
    chunks = [(record.session_id, record.chunk) for record in captions.all()
              if answers.get(record.session_id, record.chunk)]
    random.Random(seed).shuffle(chunks)
    print(f"{len(chunks)} chunks with Jev answers; {len(done)} rows already asked -> {rows_file}",
          file=sys.stderr)

    aggregations_of: dict[str, list] = {}
    with httpx.Client(timeout=config.annotate.timeout_seconds) as client:
        for session_id, chunk in chunks:
            if session_id not in aggregations_of:
                aggregations_of[session_id] = events.rows(session_id)
            aggregations = aggregations_of[session_id]
            record = captions.get(session_id, chunk)
            detection = answers.get(session_id, chunk)
            captions = frame_captions(record)
            pairs = zip(framed_rows(aggregations, chunk, config.annotate.chunk_size),
                        record.frames)
            for index, (row, frame) in enumerate(pairs):
                jev = detection.rows.get(row)
                if (session_id, row) in done or jev is None or not jev.answers:
                    continue
                asked = {**questions.screen,
                         **(questions.action if active(aggregations[row]) else {})}
                image = jpeg(fitted(frame_image(frame)))
                try:
                    local = ask_row(client, config.annotate.api_base, model, image, frame.events,
                                    captions.get(index), asked)
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    print(f"{session_id} row {row}: {exc}", file=sys.stderr)
                    continue
                line = {"session_id": session_id, "chunk": chunk, "row": row,
                        "timestamp": frame.timestamp, "screenshot_path": frame.screenshot_path,
                        "inputs": frame.events, "caption": captions.get(index),
                        "active": active(aggregations[row]), "model": model,
                        "questions": questions.version, "asked_at": time.time(),
                        "jev": {key: answer.model_dump(mode="json")
                                for key, answer in jev.answers.items()},
                        "jev_input_tokens": jev.input_tokens, **local}
                with open(rows_file, "a") as file:
                    file.write(json.dumps(line) + "\n")
                done.add((session_id, row))
                print(f"{len(done)} rows  {session_id} row {row}  {local['seconds']:.2f}s "
                      f"{local['prompt_tokens']} tok  missing={local['missing']}", file=sys.stderr)
    return 0


def auroc(scores: list[float], labels: list[bool]) -> Optional[float]:
    """Area under the ROC curve of ``scores`` against ``labels``; None without both classes."""
    positives = [s for s, y in zip(scores, labels) if y]
    negatives = [s for s, y in zip(scores, labels) if not y]
    if not positives or not negatives:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def total_variation(a: dict[str, float], b: dict[str, float]) -> float:
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def fmt(value: Optional[float], digits: int = 3) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def question_metrics(rows: list[dict[str, Any]], key: str,
                     question: dict[str, Any]) -> list[tuple[str, str]]:
    """(metric, value) pairs comparing the model's answers to Jev's on one question."""
    pairs = [(row["jev"][key], row["answers"][key]) for row in rows
             if key in row["jev"] and key in row["answers"]]
    if not pairs:
        return [("rows", "0")]
    metrics = [("rows", str(len(pairs)))]
    if question["type"] == "noul":
        jev = [j["noul"] for j, _ in pairs]
        local = [l["noul"] for _, l in pairs]
        labels = [p >= 0.5 for p in jev]
        metrics += [
            ("mean |Δp|", fmt(statistics.fmean(abs(a - b) for a, b in zip(jev, local)))),
            ("agree at 0.5", fmt(statistics.fmean((a >= 0.5) == (b >= 0.5)
                                                  for a, b in zip(jev, local)))),
            ("Jev yes rate", fmt(statistics.fmean(labels))),
            ("local yes rate", fmt(statistics.fmean(b >= 0.5 for b in local))),
            ("AUROC vs Jev ≥ 0.5", fmt(auroc(local, labels))),
            ("Brier vs Jev p", fmt(statistics.fmean((a - b) ** 2 for a, b in zip(jev, local))))]
    elif question["type"] == "choice":
        metrics += [
            ("top-1 agree", fmt(statistics.fmean(j["choice"] == l["choice"] for j, l in pairs))),
            ("mean TV distance", fmt(statistics.fmean(
                total_variation(j["probabilities"], l["probabilities"]) for j, l in pairs)))]
    else:
        metrics += [
            ("mean |Δscore|", fmt(statistics.fmean(abs(j["score"] - l["score"])
                                                   for j, l in pairs))),
            ("top level agree", fmt(statistics.fmean(
                max(j["probabilities"], key=j["probabilities"].get)
                == max(l["probabilities"], key=l["probabilities"].get) for j, l in pairs))),
            ("Jev mean score", fmt(statistics.fmean(j["score"] for j, _ in pairs))),
            ("local mean score", fmt(statistics.fmean(l["score"] for _, l in pairs)))]
    return metrics


def moments_by_row(rows: list[dict[str, Any]], side: str, questions: QuestionSet,
                   moment_on: float, moment_off: float) -> dict[tuple[str, int], list[str]]:
    """(session, row) -> moment types, from one side's answers, each session read in row order."""
    by_session: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        by_session[row["session_id"]][row["row"]] = ANSWERS.validate_python(row[side])
    found = {}
    for session_id, answers in by_session.items():
        for row, moments in session_moments(answers, questions, moment_on, moment_off).items():
            found[(session_id, row)] = [m.type + (f" ({m.label})" if m.label else "")
                                        for m in moments]
    return found


def cell(question: dict[str, Any], jev: Optional[dict], local: Optional[dict]) -> str:
    """One question's answers in the event list, shaded by how far apart they are."""
    if jev is None and local is None:
        return "<td></td>"

    def show(answer: Optional[dict]) -> str:
        if answer is None:
            return "–"
        if answer["type"] == "choice":
            return f"{answer['choice']} {answer['confidence']:.2f}"
        return f"{probability(ANSWERS.validate_python({'a': answer})['a']):.2f}"

    if jev is None or local is None:
        gap = 1.0
    elif question["type"] == "choice":
        gap = total_variation(jev["probabilities"], local["probabilities"])
    else:
        answers = ANSWERS.validate_python({"j": jev, "l": local})
        gap = abs(probability(answers["j"]) - probability(answers["l"]))
    return (f"<td style='background:rgba(220,60,60,{gap * 0.6:.2f})'>"
            f"{html.escape(show(jev))}<br><b>{html.escape(show(local))}</b></td>")


STYLE = """
body { font: 13px/1.4 system-ui, sans-serif; margin: 12px; }
table { border-collapse: collapse; margin-bottom: 18px; }
td, th { border: 1px solid #ccc; padding: 3px 6px; vertical-align: top; text-align: left; }
th { background: #f3f3f3; position: sticky; top: 0; }
img { width: 220px; }
pre { white-space: pre-wrap; margin: 0; max-width: 320px; max-height: 120px; overflow: auto;
      font-size: 11px; }
.m { color: #b00; font-weight: 600; }
"""


def sorted_rows(out: Path) -> list[dict[str, Any]]:
    """The asked rows in event-list order: by session, then row."""
    return sorted(read_lines(out / "rows.jsonl"), key=lambda r: (r["session_id"], r["row"]))


def report_page(out: Path, image_url: Callable[[int, dict[str, Any]], str]) -> str:
    """The comparison page; ``image_url`` gives the src of row ``index``'s screenshot."""
    config = server_settings().read().keypoints
    questions = load_questions()
    all_questions = {**questions.screen, **questions.action}
    rows = sorted_rows(out)
    asked_at = [r["asked_at"] for r in rows]
    seconds = [r["seconds"] for r in rows]
    tokens = [r["prompt_tokens"] for r in rows if r.get("prompt_tokens")]
    masses = [m for r in rows for m in r["letter_mass"].values()]
    missing = sum(len(r["missing"]) for r in rows)

    parts = [f"<h1>{html.escape(rows[0]['model'] if rows else '')} vs Jev</h1>",
             f"<p>{len(rows)} rows, {len({r['session_id'] for r in rows})} sessions. "
             f"Local seconds per row: median {fmt(statistics.median(seconds) if seconds else None, 2)}, "
             f"mean {fmt(statistics.fmean(seconds) if seconds else None, 2)}. "
             f"Prompt tokens per row: median {statistics.median(tokens) if tokens else '–'} "
             f"(Jev input tokens: median "
             f"{statistics.median([r['jev_input_tokens'] for r in rows if r.get('jev_input_tokens')] or [0])}). "
             f"Letter mass at answer tokens: mean {fmt(statistics.fmean(masses) if masses else None)}, "
             f"min {fmt(min(masses) if masses else None)}. Unanswered items: {missing}.</p>",
             f"<p>Last row asked: "
             f"{datetime.fromtimestamp(max(asked_at)).strftime('%H:%M:%S') if asked_at else '–'}; "
             f"rows per minute since the first: "
             f"{fmt(60 * (len(rows) - 1) / (max(asked_at) - min(asked_at)), 1) if len(asked_at) > 1 and max(asked_at) > min(asked_at) else '–'}. "
             f"Reload for newer rows.</p>",
             "<h2>Per question</h2>"]
    for kind, group in (("screen (every row)", questions.screen),
                        ("action (rows with input)", questions.action)):
        parts.append(f"<h3>{kind}</h3>")
        for key, question in group.items():
            metrics = question_metrics(rows, key, question)
            parts.append(f"<table><tr><th>{html.escape(key)} ({question['type']})</th>"
                         + "".join(f"<th>{html.escape(name)}</th>" for name, _ in metrics)
                         + "</tr><tr><td></td>"
                         + "".join(f"<td>{value}</td>" for _, value in metrics) + "</tr></table>")

    jev_moments = moments_by_row(rows, "jev", questions, config.moment_on, config.moment_off)
    local_moments = moments_by_row(rows, "answers", questions, config.moment_on,
                                   config.moment_off)
    parts.append(f"<h2>Moments (moment_on {config.moment_on}, moment_off {config.moment_off})</h2>"
                 "<table><tr><th>type</th><th>Jev</th><th>local</th><th>same row</th></tr>")
    for moment_type in questions.moments.state + questions.moments.action:
        def rows_with(found: dict) -> set:
            return {k for k, types in found.items() if any(t.split(" ")[0] == moment_type
                                                            for t in types)}
        j, l = rows_with(jev_moments), rows_with(local_moments)
        parts.append(f"<tr><td>{moment_type}</td><td>{len(j)}</td><td>{len(l)}</td>"
                     f"<td>{len(j & l)}</td></tr>")
    parts.append("</table>")

    parts.append("<h2>Events</h2><p>Each cell: Jev on top, <b>local</b> below; red = further "
                 "apart. Nouls and Scores show a 0–1 probability, Choices the option and its "
                 "probability.</p><table><tr><th>time</th><th>screenshot</th>"
                 "<th>inputs / caption</th><th>moments (Jev / <b>local</b>)</th>"
                 + "".join(f"<th>{html.escape(k)}</th>" for k in all_questions) + "</tr>")
    for index, row in enumerate(rows):
        key = (row["session_id"], row["row"])
        when = (datetime.fromtimestamp(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                if row.get("timestamp") else "")
        moments = (", ".join(jev_moments.get(key, [])) + "<br><b>"
                   + ", ".join(local_moments.get(key, [])) + "</b>")
        flag = " class=m" if jev_moments.get(key) != local_moments.get(key) else ""
        parts.append(
            f"<tr><td>{when}<br>{html.escape(row['session_id'])}<br>row {row['row']} "
            f"{'active' if row['active'] else 'idle'}<br>{row['seconds']:.2f}s</td>"
            f"<td><a href='{html.escape(image_url(index, row))}'>"
            f"<img loading=lazy src='{html.escape(image_url(index, row))}'></a></td>"
            f"<td><pre>{html.escape(row['inputs'] or '')}</pre>"
            f"<pre>{html.escape(row['caption'] or '')}</pre></td><td{flag}>{moments}</td>"
            + "".join(cell(q, row["jev"].get(k), row["answers"].get(k))
                      for k, q in all_questions.items()) + "</tr>")
    parts.append("</table>")
    return (f"<!doctype html><meta charset=utf-8><title>Jev backtest</title>"
            f"<style>{STYLE}</style>" + "\n".join(parts))


def report(out: Path) -> int:
    if not (out / "rows.jsonl").exists():
        print(f"error: {out / 'rows.jsonl'} does not exist; run first", file=sys.stderr)
        return 2
    path = out / "report.html"
    path.write_text(report_page(out, lambda _, row: f"file://{row['screenshot_path']}"))
    print(path)
    return 0


def handler_for(out: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                page = report_page(out, lambda index, _: f"/img/{index}")
                return self.send(200, page.encode(), "text/html; charset=utf-8")
            if match := re.fullmatch(r"/img/(\d+)", self.path):
                rows = sorted_rows(out)
                index = int(match[1])
                if index < len(rows):
                    path = Path(rows[index]["screenshot_path"])
                    if path.is_file():
                        return self.send(200, path.read_bytes(),
                                         mimetypes.guess_type(path.name)[0] or "image/png")
            self.send(404, b"not found", "text/plain")

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


def serve(out: Path, host: str, port: int) -> int:
    if not (out / "rows.jsonl").exists():
        print(f"error: {out / 'rows.jsonl'} does not exist; run first", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((host, port), handler_for(out))
    print(f"http://{host}:{port}/", file=sys.stderr)
    server.serve_forever()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["run", "report", "serve"])
    parser.add_argument("--seed", type=int, default=0, help="order the chunks are asked in")
    parser.add_argument("--out", type=Path,
                        help="where results go; default: data/jev_backtest/<model>/")
    parser.add_argument("--host", default="127.0.0.1", help="address serve listens on")
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args(argv)
    try:
        model = server_settings().read().annotate.model
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = (args.out or default_out(model)).resolve()
    if args.command == "run":
        return run(out, args.seed)
    return report(out) if args.command == "report" else serve(out, args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
