"""Settings for the app and the processes it starts, read from one YAML file.

The file is ``config.yaml`` in the working directory, or the path in
``JUNO_CONFIG_PATH``. Keys left out of the file take the defaults below.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG_PATH_ENV = "JUNO_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path("config.yaml")


class ConfigError(Exception):
    """The config file is missing or invalid."""


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelled key is an error


class ServerConfig(Section):
    host: str = "127.0.0.1"
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


class Config(Section):
    # Seconds a process gets to exit after SIGTERM before it is killed.
    stop_timeout_seconds: float = 30.0
    server: ServerConfig = Field(default_factory=ServerConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "juno"


def resolve_data_dir(config: CaptureConfig) -> Path:
    """The base directory the recorder writes to under ``config``."""
    return config.data_dir.expanduser() if config.data_dir else default_data_dir()


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
