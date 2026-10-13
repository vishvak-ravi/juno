# Juno

Records what you do on your computers, stores it on one server, and predicts
and chats over it. Requirements, decisions and details: [`docs/`](docs/).

## What runs where

Three things are hosted separately, usually on different machines: the **API
server** (one machine), the **recorder** (every computer you use) and the
**model server** (the GPU machine).

| Process | Runs on | Command (dir) | Linux Wayland | Linux X11 | macOS |
|---|---|---|---|---|---|
| API server: stores every device's events, annotates, chats, predicts | one machine | `uv run python -m server` (`app/`) | yes | untested | untested; set `tools.bash.sandbox: false` |
| Recorder: screen and input capture, uploads to the server | each computer | `uv run python -m capture` (`app/`) | yes (GNOME 50) | written, never run | yes (15.6) |
| Model server: vLLM, for chat and predictions | GPU machine | `juno` (`~/gemma_chat.py`, not in repo) | NVIDIA | NVIDIA | no |
| Annotation model | Gemini API, nothing to host | `GEMINI_API_KEY` in `app/.env` | | | |
| Desktop client: chat window and browser | any computer | `npm start` (`client/`) | yes | untested | yes, with dictation |
| Phone | none | | | | |

## Setup (once per machine)

```
cd app && uv sync && cp .env.example .env   # set JUNO_SERVER_HOST, JUNO_DEVICE_ID, GEMINI_API_KEY
sudo usermod -aG input $USER                # Wayland recorder; log out and in
cd client && npm ci && npm run model        # client; on macOS also: npm run native
```

macOS recorder: grant Screen Recording, Accessibility and Input Monitoring to
the terminal. Linux client on Ubuntu 24.04+: AppArmor profile in
[`docs/client.md`](docs/client.md).

## Commands

```
uv run python -m juno               # server + recorder on one machine   (app/)
uv run python -m capture.check      # check the newest session           (app/)
uv run python -m capture.upload     # copy recorded sessions to server   (app/)
uv run pytest                       # tests                              (app/)
JUNO_PAGE=browse.html npm start     # session browser                    (client/)
uv run --project app python scripts/make_dataset.py SESSION_ID
uv run --project app python scripts/benchmark.py CONFIG.yaml
```

Settings: `app/config.yaml` (shared), `app/.env` (per device). Server routes:
`http://<server>:8765/docs`.
