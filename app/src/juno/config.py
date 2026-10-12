"""Settings for the app and the processes it starts, read from one YAML file.

The file is ``config.yaml`` in the working directory, or the path in
``JUNO_CONFIG_PATH``. Keys left out of the file take the defaults below.
Settings that differ between devices are not in the file: they are read from
the environment (or a .env file), see ``server_host`` and ``resolve_device_id``.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG_PATH_ENV = "JUNO_CONFIG_PATH"
SERVER_HOST_ENV = "JUNO_SERVER_HOST"
DEVICE_ID_ENV = "JUNO_DEVICE_ID"
DEFAULT_CONFIG_PATH = Path("config.yaml")


class ConfigError(Exception):
    """The config file is missing or invalid."""


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled key is an error


class ServerConfig(Section):
    port: int = 8765


class CaptureConfig(Section):
    # Base directory; sessions go under screen/sessions/, chats under chats/.
    # None: default_data_dir().
    data_dir: Optional[Path] = None
    # Platform sources to use; "auto" detects them from the session.
    backend: str = "auto"
    fps: int = 5
    # Seconds of frames to keep in memory.
    buffer_seconds: int = 120
    # Target DPI for saved screenshots.
    dpi: int = 100
    # Scale factor for screenshots (0.0-1.0); overrides dpi.
    scale: Optional[float] = None
    # Maximal screenshot resolution as (width, height).
    max_res: Optional[tuple[int, int]] = None
    # Event types to skip: move, scroll, click, key.
    disable: list[str] = ["move"]
    # Whether screenshot files are written.
    screenshots: bool = True
    # Stop after this many seconds. None: run until told to stop.
    duration: Optional[float] = None
    verbose: bool = False


class LlmConfig(Section):
    # The OpenAI-compatible server that chats are completed by. The model is
    # whichever one that server lists.
    base_url: str = "http://localhost:8000"
    max_tokens: int = 2048
    temperature: float = 0.7
    # Seconds to wait for a reply.
    timeout_seconds: float = 300.0
    # Most model calls for one reply while the model keeps calling tools.
    max_tool_rounds: int = 8
    # Characters of a tool result the model is shown.
    tool_result_chars: int = 12000


class BashConfig(Section):
    # Whether the chat model may run bash commands on the server's machine.
    enabled: bool = True
    # Run each command under bubblewrap: system directories read-only, no home
    # directory, only the workspace writable. False: the command runs as the
    # server's user, with everything that user can read and write.
    sandbox: bool = True
    # Seconds a command may run before it and everything it started are killed.
    timeout_seconds: float = 60.0


class WebSearchConfig(Section):
    # Whether the chat model may search the web.
    enabled: bool = True
    # Most results one search returns, whatever number the model asks for.
    max_results: int = 8


class ClaudeConfig(Section):
    # Whether the chat model may forward prompts to Claude Code in tmux. Claude
    # runs as the server's user, outside the bash sandbox, and is billed to the
    # account `claude` is logged in to.
    enabled: bool = True
    # The Claude Code executable.
    command: str = "claude"
    # The model of a new conversation when the call names none: an alias such
    # as sonnet or opus, or a full model id. None: Claude Code's own default.
    model: Optional[str] = None
    # Passed as --permission-mode: default, acceptEdits, auto, plan, dontAsk or
    # bypassPermissions. None: whatever Claude Code's settings say.
    permission_mode: Optional[str] = None
    # Seconds to wait for Claude to finish one turn.
    timeout_seconds: float = 600.0
    # The directory a new conversation starts in. None: workspace_dir().
    cwd: Optional[Path] = None
    # Name of the session created when no tmux server is running. With one
    # running, new windows go to the session tmux considers current.
    tmux_session: str = "juno"
    # Name of a new window when the call gives none.
    window_name: str = "claude"
    # tmux server socket name (tmux -L). None: the user's default server.
    tmux_socket: Optional[str] = None


class ToolsConfig(Section):
    # Tools of the chat model besides the session and past-chat ones (`server.tools`).
    bash: BashConfig = Field(default_factory=BashConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)


class AnnotateConfig(Section):
    # The litellm model that annotates recorded sessions; Tada's labeling model.
    # A gemini/ model reads its key from GEMINI_API_KEY.
    model: str = "gemini/gemini-3.1-flash-lite"
    # Upper bound on the reply, hidden reasoning included.
    max_tokens: int = 8192
    # Aggregations (screenshots) per request.
    chunk_size: int = 10
    # Whether the recorder uploads each aggregation to the server, which
    # annotates each chunk as it fills. Every screenshot then goes to the
    # model's provider.
    live: bool = True
    # Model calls the server keeps in flight at once.
    max_concurrency: int = 4
    # Calls made for one chunk before its annotation is left failed. A call is
    # repeated only when the provider was busy or the reply was not an annotation.
    max_attempts: int = 3
    # Seconds one call may take.
    timeout_seconds: float = 300.0
    # Seconds every call waits after the provider was busy: the base doubles with
    # each attempt of the chunk, up to the maximum.
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 120.0
    # Whether the server, on its own, annotates every chunk in its store that
    # has no finished annotation, most recent first, behind the chunks recorders
    # are filling now. Every stored screenshot then goes to the model's provider.
    backfill: bool = True
    # Seconds between the server's looks through its store for such chunks.
    scan_interval_seconds: float = 60.0
    # Seconds a session must have gone without a new row before the rows after
    # its last full chunk are annotated.
    tail_idle_seconds: float = 300.0


class PredictConfig(Section):
    # Next-action prediction (`POST /predictions`); the defaults are Tada's.
    # Annotated actions the model is shown; a prediction needs at least this many.
    past_len: int = 50
    # Actions the model is asked to predict.
    future_len: int = 8
    # Screenshots sent, those of the newest actions. None: text only.
    num_imgs_per_sample: Optional[int] = 5
    max_tokens: int = 2048
    temperature: float = 1.0
    # Earlier activity retrieved with BM25, and how many of those are kept
    # after MMR removes near-duplicates (alpha 1.0: relevance only).
    top_k: int = 20
    mmr_k: int = 10
    mmr_alpha: float = 0.5
    # How fast older activity loses retrieval score, per day of age.
    time_decay_lambda: float = 0.5


class ControlConfig(Section):
    # Seconds between a recorder's reports to the server, each of which also
    # tells it whether to record; the status page refreshes as often.
    poll_seconds: float = 5.0
    # Seconds without a report after which a device shows as offline.
    offline_seconds: float = 30.0


class Config(Section):
    # Seconds a process gets to exit after SIGTERM before it is killed.
    stop_timeout_seconds: float = 30.0
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    annotate: AnnotateConfig = Field(default_factory=AnnotateConfig)
    predict: PredictConfig = Field(default_factory=PredictConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "juno"


def resolve_data_dir(config: CaptureConfig) -> Path:
    """The base directory the recorder writes to under ``config``."""
    return config.data_dir.expanduser() if config.data_dir else default_data_dir()


def server_host() -> str:
    """The address the server listens on and recorders upload to: JUNO_SERVER_HOST."""
    return os.environ.get(SERVER_HOST_ENV) or "127.0.0.1"


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


def sessions_dir(data_dir: Path) -> Path:
    """Where the recorder writes its sessions under the base directory ``data_dir``."""
    return data_dir / "screen" / "sessions"


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
    """Where Claude Code reports the end of each turn (`server.claude`)."""
    return data_dir / "claude"


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH)


def load_config(path: Optional[Path] = None) -> Config:
    """Read and validate the config file at ``path`` (default: ``config_path()``)."""
    path = config_path() if path is None else path
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ConfigError(f"could not read the config file {path}: {exc}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}")
    try:
        return Config.model_validate({} if raw is None else raw)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}")
