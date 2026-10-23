"""Every setting of the server, the recorder and the scripts, with its default.

Settings have one of three owners:

- ``ServerSettings``: what the server (``python -m server``) and the scripts
  run on its machine read. Kept in ``<data_dir>/settings.json`` on the server,
  changed through ``GET``/``PATCH /settings`` or ``python -m juno.settings server``.
- ``DeviceSettings``: what one computer's recorder (``python -m capture``) and
  Claude Code hook read. Kept in ``<data_dir>/device.json`` on that computer,
  changed by its desktop client or ``python -m juno.settings device``.
- The environment (or a ``.env`` file): what must be known before either file
  can be read or the server reached, and the API keys (``ENVIRONMENT``).

Each file holds only the values that differ from the defaults below
(``juno.settings``). A field's ``description`` is what the client shows; a field
marked ``ADVANCED`` is shown folded.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DATA_DIR_ENV = "JUNO_DATA_DIR"
SERVER_HOST_ENV = "JUNO_SERVER_HOST"
SERVER_PORT_ENV = "JUNO_SERVER_PORT"
SERVER_URL_ENV = "JUNO_SERVER_URL"
DEVICE_ID_ENV = "JUNO_DEVICE_ID"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8765

# json_schema_extra of a field the client shows folded: tuning that rarely needs changing.
ADVANCED = {"advanced": True}


class ConfigError(Exception):
    """A settings file or an environment variable is invalid."""


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled key is an error


class EnvVar(BaseModel):
    """A setting read from the environment, never from a settings file."""

    name: str
    description: str
    # Whether its value is a secret: shown as set or not, never read back.
    secret: bool
    # Which processes read it: the server's, each device's recorder and hook, or both.
    owner: Literal["server", "device", "both"]


ENVIRONMENT = [
    EnvVar(name=DATA_DIR_ENV, owner="both", secret=False, description=(
        "Base directory: the recorder's sessions, the server's store and chats, and both "
        "settings files. Default: $XDG_DATA_HOME/juno, or ~/.local/share/juno.")),
    EnvVar(name=SERVER_HOST_ENV, owner="both", secret=False, description=(
        "The address the server listens on; also the host of the default JUNO_SERVER_URL. "
        f"Default: {DEFAULT_SERVER_HOST}.")),
    EnvVar(name=SERVER_PORT_ENV, owner="both", secret=False, description=(
        f"The port the server listens on. Default: {DEFAULT_SERVER_PORT}.")),
    EnvVar(name=SERVER_URL_ENV, owner="device", secret=False, description=(
        "The server this computer's recorder and Claude Code hook send to. The desktop "
        "client passes the address saved in its settings. Default: "
        "http://<JUNO_SERVER_HOST>:<JUNO_SERVER_PORT>.")),
    EnvVar(name=DEVICE_ID_ENV, owner="device", secret=False, description=(
        "The name this computer's sessions are stored under on the server: lowercase "
        "letters and digits. Default: the host name, reduced to those.")),
    EnvVar(name="GEMINI_API_KEY", owner="server", secret=True, description=(
        "Key of a gemini/ model.")),
    EnvVar(name="OPENROUTER_API_KEY", owner="server", secret=True, description=(
        "Key of an openrouter/ model.")),
    EnvVar(name="TYPESAFE_API_KEY", owner="server", secret=True, description=(
        "Key of TypeSafe's API, which answers keypoints.model's questions.")),
]


class LlmConfig(Section):
    """The chat model."""

    base_url: str = Field("http://localhost:8000", description=(
        "The OpenAI-compatible server chats are completed by. The model is whichever "
        "one that server lists."))
    max_tokens: int = Field(2048, description="Upper bound on a reply.")
    temperature: float = 0.7
    timeout_seconds: float = Field(300.0, description="Seconds to wait for a reply.",
                                   json_schema_extra=ADVANCED)
    max_tool_rounds: int = Field(8, description=(
        "Most model calls for one reply while the model keeps calling tools."),
        json_schema_extra=ADVANCED)
    tool_result_chars: int = Field(12000, description=(
        "Characters of a tool result the model is shown."), json_schema_extra=ADVANCED)


class BashConfig(Section):
    """Bash commands the chat model runs on the server's machine."""

    enabled: bool = Field(True, description=(
        "Whether the chat model may run bash commands. Anyone who can reach this server "
        "can have them run, through /chats or /mcp."))
    sandbox: bool = Field(True, description=(
        "Run each command under bubblewrap: system directories read-only, no home "
        "directory, only <data_dir>/workspace writable; the network stays open. Off: the "
        "command runs as the server's user, with everything that user can read and write."))
    timeout_seconds: float = Field(60.0, description=(
        "Seconds a command may run before it and everything it started are killed."),
        json_schema_extra=ADVANCED)


class WebSearchConfig(Section):
    """Web searches by the chat model."""

    enabled: bool = Field(True, description="Whether the chat model may search the web.")
    max_results: int = Field(8, description=(
        "Most results one search returns, whatever number the model asks for."))


class ClaudeConfig(Section):
    """Prompts the chat model forwards to Claude Code in a tmux window on the server's machine."""

    enabled: bool = Field(True, description=(
        "Whether the chat model may forward prompts to Claude Code. Claude runs as the "
        "server's user, outside the bash sandbox, and is billed to the account `claude` "
        "is logged in to."))
    model: Optional[str] = Field(None, description=(
        "Model of a new conversation when the call names none: an alias such as sonnet or "
        "opus, or a full model id. Empty: Claude Code's own default."))
    permission_mode: Optional[Literal["default", "acceptEdits", "auto", "plan", "dontAsk",
                                      "bypassPermissions"]] = Field(None, description=(
        "--permission-mode of new conversations. Empty: whatever Claude Code's settings say."))
    timeout_seconds: float = Field(600.0, description=(
        "Seconds to wait for Claude to finish one turn."), json_schema_extra=ADVANCED)
    command: str = Field("claude", description="The Claude Code executable.",
                         json_schema_extra=ADVANCED)
    cwd: Optional[Path] = Field(None, description=(
        "The directory a new conversation starts in. Empty: <data_dir>/workspace."),
        json_schema_extra=ADVANCED)
    tmux_session: str = Field("juno", description=(
        "Name of the session created when no tmux server is running. With one running, "
        "new windows go to the session tmux considers current."), json_schema_extra=ADVANCED)
    window_name: str = Field("claude", description="Name of a new window when the call gives none.",
                             json_schema_extra=ADVANCED)
    tmux_socket: Optional[str] = Field(None, description=(
        "tmux server socket name (tmux -L). Empty: the user's default server."),
        json_schema_extra=ADVANCED)


class ToolsConfig(Section):
    """Tools of the chat model besides the record's (`server.agent.tools`)."""

    bash: BashConfig = Field(default_factory=BashConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)


class AnnotateConfig(Section):
    """Captions of recorded sessions; Tada's labeling model."""

    model: str = Field("gemini/gemini-3.1-flash-lite", description=(
        "The litellm model string <provider>/<name>: gemini/<model> (GEMINI_API_KEY), "
        "openrouter/<vendor>/<model> (OPENROUTER_API_KEY), or openai/<served name> for a "
        "model on an OpenAI-compatible server (llama.cpp, vLLM, SGLang) at api_base. Each "
        "annotation records it."))
    api_base: Optional[str] = Field(None, description=(
        "The server an openai/ model is sent to, e.g. http://localhost:8000/v1; it needs "
        "no key. Empty: the provider's own endpoint."))
    backfill: bool = Field(True, description=(
        "Whether the server, on its own, annotates every chunk in its store that has no "
        "finished annotation, most recent first, behind the chunks recorders are filling "
        "now. Every stored screenshot then goes to the model's provider."))
    max_tokens: int = Field(8192, description="Upper bound on the reply, hidden reasoning included.",
                            json_schema_extra=ADVANCED)
    chunk_size: int = Field(10, description=(
        "Aggregations (screenshots) per request. Stored annotations are numbered by it, "
        "so changing it orphans them."), json_schema_extra=ADVANCED)
    max_concurrency: int = Field(4, description="Model calls the server keeps in flight at once.",
                                 json_schema_extra=ADVANCED)
    max_attempts: int = Field(3, description=(
        "Calls made for one chunk before its annotation is left failed. A call is repeated "
        "only when the provider was busy or the reply was not an annotation."),
        json_schema_extra=ADVANCED)
    timeout_seconds: float = Field(300.0, description="Seconds one call may take.",
                                   json_schema_extra=ADVANCED)
    retry_base_seconds: float = Field(5.0, description=(
        "Seconds every call waits after the provider was busy: doubles with each attempt "
        "of the chunk, up to retry_max_seconds."), json_schema_extra=ADVANCED)
    retry_max_seconds: float = Field(120.0, json_schema_extra=ADVANCED)
    scan_interval_seconds: float = Field(60.0, description=(
        "Seconds between the server's looks through its store for chunks to backfill."),
        json_schema_extra=ADVANCED)
    tail_idle_seconds: float = Field(300.0, description=(
        "Seconds a session must have gone without a new row before the rows after its "
        "last full chunk are annotated."), json_schema_extra=ADVANCED)


class KeypointsConfig(Section):
    """Jev, TypeSafe's System One model, asked the questions in keypoints/questions.json
    about every row of a chunk once the chunk is annotated. Runs under the annotate switch."""

    enabled: bool = Field(True, description=(
        "Whether rows are asked about (TYPESAFE_API_KEY). Every screenshot then goes to TypeSafe."))
    model: str = "jev-1.14.0"
    moment_on: float = Field(0.7, description=(
        "A moment starts at a probability of at least this; one of the same type that "
        "stays on screen starts again only after the probability has dropped below moment_off."))
    moment_off: float = 0.4
    max_concurrency: int = Field(8, description="Rows asked about at once.",
                                 json_schema_extra=ADVANCED)
    bucket_seconds: float = Field(300.0, description=(
        "Seconds per bucket of GET /indices when the request names none."),
        json_schema_extra=ADVANCED)


class GradeConfig(Section):
    """The judge that scores a candidate sequence of action captions against a reference one
    (`scripts/grade_action_captions.py`, `scripts/benchmark_prediction.py`); Tada's reward_llm."""

    model: str = Field("gemini/gemini-3.1-flash-lite", description=(
        "litellm model string, as annotate.model."))
    api_base: Optional[str] = Field(None, description=(
        "The OpenAI-compatible server a local judge is served from. Empty: the provider's "
        "own endpoint."))
    max_attempts: int = Field(5, description="Calls made for one judgement while the provider is busy.",
                              json_schema_extra=ADVANCED)
    retry_seconds: float = Field(60.0, description=(
        "Seconds to wait between them when the provider names no time."),
        json_schema_extra=ADVANCED)


class PredictConfig(Section):
    """Next-action prediction (POST /predictions) by the chat model; the defaults are Tada's."""

    past_len: int = Field(50, description=(
        "Annotated actions the model is shown; a prediction needs at least this many."),
        json_schema_extra=ADVANCED)
    future_len: int = Field(8, description="Actions the model is asked to predict.",
                            json_schema_extra=ADVANCED)
    num_imgs_per_sample: Optional[int] = Field(5, description=(
        "Screenshots sent, those of the newest actions. Empty: text only. The model server "
        "must accept images."), json_schema_extra=ADVANCED)
    max_tokens: int = Field(2048, json_schema_extra=ADVANCED)
    temperature: float = Field(1.0, json_schema_extra=ADVANCED)
    top_k: int = Field(20, description=(
        "Earlier activity retrieved with BM25; mmr_k of those are kept after MMR removes "
        "near-duplicates (mmr_alpha 1.0: relevance only)."), json_schema_extra=ADVANCED)
    mmr_k: int = Field(10, json_schema_extra=ADVANCED)
    mmr_alpha: float = Field(0.5, json_schema_extra=ADVANCED)
    time_decay_lambda: float = Field(0.5, description=(
        "How fast older activity loses retrieval score, per day of age."),
        json_schema_extra=ADVANCED)


class ControlConfig(Section):
    """How often devices and clients check in with the server."""

    poll_seconds: float = Field(5.0, description=(
        "Seconds between a recorder's reports to the server, each of which also tells it "
        "whether to record; the devices panel refreshes as often."), json_schema_extra=ADVANCED)
    offline_seconds: float = Field(30.0, description=(
        "Seconds without a report after which a device shows as offline."),
        json_schema_extra=ADVANCED)
    live_seconds: float = Field(2.0, description=(
        "Seconds between a client's checks of what changed in the timeline (GET /changes)."),
        json_schema_extra=ADVANCED)


class DrawConfig(Section):
    """The /draw page, recorded by the server with its own machine's capture settings."""

    device_id: str = Field("tablet", pattern=r"^[a-z0-9]+$", description=(
        "The device id the /draw page's sessions are stored under; its recording switch is "
        "in the devices panel like any recorder's."))


class ServerSettings(Section):
    """What the server and the scripts on its machine read."""

    llm: LlmConfig = Field(default_factory=LlmConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    annotate: AnnotateConfig = Field(default_factory=AnnotateConfig)
    keypoints: KeypointsConfig = Field(default_factory=KeypointsConfig)
    predict: PredictConfig = Field(default_factory=PredictConfig)
    grade: GradeConfig = Field(default_factory=GradeConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    draw: DrawConfig = Field(default_factory=DrawConfig)


class CaptureConfig(Section):
    """This computer's recorder; the defaults are Tada's."""

    upload: bool = Field(True, description=(
        "Whether the recorder uploads each aggregation to the server, which annotates it, "
        "and asks the server whether to record. Off: it records here only, always."))
    fps: int = 5
    screenshots: bool = Field(True, description="Whether screenshot files are written.")
    scale: Optional[float] = Field(None, description=(
        "Scale factor for screenshots (0.0-1.0); overrides dpi. On a 4K screen 0.5 is much lighter."))
    dpi: int = Field(100, description="Target DPI for saved screenshots.")
    buffer_seconds: int = Field(120, description="Seconds of frames to keep in memory.",
                                json_schema_extra=ADVANCED)
    max_res: Optional[tuple[int, int]] = Field(None, description=(
        "Largest screenshot resolution as [width, height]."), json_schema_extra=ADVANCED)
    disable: list[Literal["move", "scroll", "click", "key"]] = Field(
        default_factory=lambda: ["move"], description="Event types not recorded.",
        json_schema_extra=ADVANCED)
    backend: Literal["auto", "wayland", "x11", "macos", "fake"] = Field("auto", description=(
        "Platform sources; auto detects them from the platform and session. fake records "
        "generated frames and scripted input."), json_schema_extra=ADVANCED)
    duration: Optional[float] = Field(None, description=(
        "Stop after this many seconds. Empty: run until told to stop."), json_schema_extra=ADVANCED)
    verbose: bool = Field(False, description="Log every step.", json_schema_extra=ADVANCED)


class ClaudeHookConfig(Section):
    """The Claude Code hook that reports each prompt typed on this computer
    (`python -m capture.claude_hook install`)."""

    timeout_seconds: int = Field(3, description=(
        "Seconds Claude Code waits for the server before sending the prompt on anyway; "
        "written into the hook when it is installed."), json_schema_extra=ADVANCED)


class DeviceSettings(Section):
    """What one computer's recorder and Claude Code hook read."""

    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    claude_hook: ClaudeHookConfig = Field(default_factory=ClaudeHookConfig)


def data_dir() -> Path:
    """The base directory: JUNO_DATA_DIR, else $XDG_DATA_HOME/juno, else ~/.local/share/juno."""
    explicit = os.environ.get(DATA_DIR_ENV)
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "juno"


def server_host() -> str:
    """The address the server listens on: JUNO_SERVER_HOST."""
    return os.environ.get(SERVER_HOST_ENV) or DEFAULT_SERVER_HOST


def server_port() -> int:
    """The port the server listens on: JUNO_SERVER_PORT."""
    raw = os.environ.get(SERVER_PORT_ENV)
    if not raw:
        return DEFAULT_SERVER_PORT
    if not raw.isdigit():
        raise ConfigError(f"{SERVER_PORT_ENV} must be a port number, not {raw!r}")
    return int(raw)


def server_url() -> str:
    """The server this computer sends to: JUNO_SERVER_URL, else built from the host and port."""
    explicit = os.environ.get(SERVER_URL_ENV)
    if explicit:
        return explicit.rstrip("/")
    return f"http://{server_host()}:{server_port()}"


def resolve_device_id() -> str:
    """The id this computer's sessions are stored under on the server.

    JUNO_DEVICE_ID (lowercase letters and digits), or the host name reduced to those.
    """
    device_id = os.environ.get(DEVICE_ID_ENV)
    if device_id:
        if not re.fullmatch(r"[a-z0-9]+", device_id):
            raise ConfigError(f"{DEVICE_ID_ENV} must be lowercase letters and digits")
        return device_id
    device_id = re.sub(r"[^a-z0-9]", "", socket.gethostname().lower())
    if not device_id:
        raise ConfigError(f"the host name has no letter or digit; set {DEVICE_ID_ENV}")
    return device_id


def server_settings_file(data_dir: Path) -> Path:
    """The server's settings that differ from the defaults (``ServerSettings``)."""
    return data_dir / "settings.json"


def device_settings_file(data_dir: Path) -> Path:
    """This computer's recorder settings that differ from the defaults (``DeviceSettings``)."""
    return data_dir / "device.json"


def sessions_dir(data_dir: Path) -> Path:
    """Where the recorder writes its sessions under the base directory ``data_dir``."""
    return data_dir / "screen" / "sessions"


def draw_sessions_dir(data_dir: Path) -> Path:
    """Where the server records the /draw page's sessions before uploading them to itself."""
    return data_dir / "screen" / "draw"


def devices_dir(data_dir: Path) -> Path:
    """Where the server stores the sessions uploaded to it, one directory per device."""
    return data_dir / "screen" / "devices"


def control_file(data_dir: Path) -> Path:
    """The server's switches and each device's last report (`server.control`)."""
    return data_dir / "control.json"


def workspace_dir(data_dir: Path) -> Path:
    """Where the chat model's bash commands run, and the one place they can keep files."""
    return data_dir / "workspace"


def claude_results_dir(data_dir: Path) -> Path:
    """Where Claude Code reports the end of each turn (`server.agent.claude`)."""
    return data_dir / "claude"
