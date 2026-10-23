"""Settings files: the values that differ from the defaults in ``juno.config``.

``SettingsFile`` reads one file into its model (``ServerSettings`` or
``DeviceSettings``) and changes it by dotted path (``annotate.model``). The file
keeps only the values that differ from the model's defaults, so a default
changed in code reaches every install that has not set it. ``describe`` lists
every field with its value and default for a client to show.

``python -m juno.settings server|device`` prints that description as JSON;
``set PATH=VALUE...`` and ``reset PATH...`` change the file; ``patch`` reads
``{"set": {...}, "reset": [...]}`` from stdin. Values are YAML (``null``,
``8``, ``[640, 360]``). The server reads its file when it starts and writes it
through ``PATCH /settings``; a ``set`` run on its machine while it is up and a
``PATCH`` at the same time each replace the file whole, so the later one wins.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import threading
import types
import typing
from pathlib import Path
from typing import Any, Generic, Literal, Optional, TypeVar, Union

import yaml
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ValidationError

from juno.config import (ENVIRONMENT, ConfigError, DeviceSettings, Section,
                         ServerSettings, data_dir, device_settings_file,
                         server_settings_file)
from juno.sessions import replace_file

M = TypeVar("M", bound=Section)

# The key each model provider's litellm string reads.
PROVIDER_KEYS = {"gemini/": "GEMINI_API_KEY", "openrouter/": "OPENROUTER_API_KEY"}


class SettingsError(ConfigError):
    """A change was refused; ``errors`` maps each dotted path to why."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{path}: {message}" for path, message in errors.items()))
        self.errors = errors


class FieldView(BaseModel):
    """One setting as a client shows it."""

    path: str
    # bool, int, float, string, enum (one of options), or json (a list or pair).
    kind: Literal["bool", "int", "float", "string", "enum", "json"]
    # Whether it may be empty (null).
    nullable: bool
    options: Optional[list[str]] = None
    description: Optional[str] = None
    advanced: bool
    # Whether it is changed only on the machine that holds the file, never over the API.
    local: bool
    default: Any
    value: Any


class SectionView(BaseModel):
    """The settings of one section, in the order the model declares them."""

    path: str
    description: Optional[str]
    fields: list[FieldView]


class EnvView(BaseModel):
    """An environment variable of the process that answered: set or not, and its value unless secret."""

    name: str
    description: str
    secret: bool
    set: bool
    value: Optional[str] = None
    # The settings whose current values need it, e.g. annotate.model for GEMINI_API_KEY.
    needed_by: list[str] = []


def section_fields(model: type[BaseModel]) -> list[tuple[str, Any]]:
    return list(model.model_fields.items())


def is_section(annotation: Any) -> bool:
    return inspect.isclass(annotation) and issubclass(annotation, Section)


def unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """The type inside ``Optional[...]`` and whether it was optional."""
    if typing.get_origin(annotation) in (Union, types.UnionType):
        inner = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(inner) == 1:
            return inner[0], True
    return annotation, False


def kind_of(annotation: Any) -> tuple[str, Optional[list[str]]]:
    if typing.get_origin(annotation) is Literal:
        return "enum", [str(arg) for arg in typing.get_args(annotation)]
    if annotation is bool:
        return "bool", None
    if annotation is int:
        return "int", None
    if annotation is float:
        return "float", None
    if annotation in (str, Path):
        return "string", None
    return "json", None


def leaf_paths(model: type[BaseModel], prefix: str = "") -> dict[str, Any]:
    """Every settable dotted path of ``model`` and its field info."""
    paths = {}
    for name, info in section_fields(model):
        path = f"{prefix}{name}"
        if is_section(info.annotation):
            paths.update(leaf_paths(info.annotation, f"{path}."))
        else:
            paths[path] = info
    return paths


def local_paths(model: type[BaseModel]) -> set[str]:
    """The dotted paths of ``model`` marked ``LOCAL``: changed only where the file is."""
    return {path for path, info in leaf_paths(model).items()
            if isinstance(info.json_schema_extra, dict) and info.json_schema_extra.get("local")}


def get_path(tree: dict[str, Any], path: str) -> Any:
    for key in path.split("."):
        tree = tree[key]
    return tree


def set_path(tree: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    for key in parents:
        tree = tree.setdefault(key, {})
    tree[leaf] = value


def differences(values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """The parts of ``values`` that differ from ``defaults``, nested as they are."""
    changed = {}
    for key, value in values.items():
        default = defaults.get(key)
        if isinstance(value, dict) and isinstance(default, dict):
            inner = differences(value, default)
            if inner:
                changed[key] = inner
        elif value != default:
            changed[key] = value
    return changed


def validation_errors(error: ValidationError) -> dict[str, str]:
    return {".".join(str(part) for part in item["loc"]) or "(file)": item["msg"]
            for item in error.errors()}


def describe(model: type[M], settings: M) -> list[SectionView]:
    """Every section of ``model`` that has settings, each field with its value and default."""
    defaults = model().model_dump(mode="json")
    values = settings.model_dump(mode="json")
    sections: list[SectionView] = []

    def visit(section: type[BaseModel], prefix: str) -> None:
        fields = []
        for name, info in section_fields(section):
            path = f"{prefix}{name}"
            if is_section(info.annotation):
                continue
            inner, nullable = unwrap_optional(info.annotation)
            kind, options = kind_of(inner)
            extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
            fields.append(FieldView(path=path, kind=kind, nullable=nullable, options=options,
                                description=info.description,
                                advanced=bool(extra.get("advanced")), local=bool(extra.get("local")),
                                default=get_path(defaults, path), value=get_path(values, path)))
        if fields:
            doc = inspect.cleandoc(section.__doc__) if section.__doc__ else None
            sections.append(SectionView(path=prefix.rstrip("."), description=doc, fields=fields))
        for name, info in section_fields(section):
            if is_section(info.annotation):
                visit(info.annotation, f"{prefix}{name}.")

    for name, info in section_fields(model):
        visit(info.annotation, f"{name}.")
    return sections


def keys_needed(settings: ServerSettings) -> dict[str, list[str]]:
    """Each API key the server's settings need, with the settings that need it."""
    needed: dict[str, list[str]] = {}
    models = {"annotate.model": settings.annotate.model, "grade.model": settings.grade.model}
    for path, model in models.items():
        for prefix, key in PROVIDER_KEYS.items():
            if model.startswith(prefix):
                needed.setdefault(key, []).append(path)
    if settings.keypoints.enabled:
        needed.setdefault("TYPESAFE_API_KEY", []).append("keypoints.enabled")
    return needed


def environment(owner: Literal["server", "device"],
                needed: Optional[dict[str, list[str]]] = None) -> list[EnvView]:
    """The environment variables a process of ``owner`` reads, as this process sees them."""
    needed = needed or {}
    views = []
    for var in ENVIRONMENT:
        if var.owner not in (owner, "both"):
            continue
        value = os.environ.get(var.name) or None
        views.append(EnvView(name=var.name, description=var.description, secret=var.secret,
                             set=value is not None, value=None if var.secret else value,
                             needed_by=needed.get(var.name, [])))
    return views


class SettingsFile(Generic[M]):
    """One settings file holding the values of ``model`` that differ from its defaults."""

    def __init__(self, path: Path, model: type[M]):
        self.path = path
        self.model = model
        # An update reads, edits and replaces the file; two at once must not interleave.
        self.lock = threading.Lock()

    def read(self) -> M:
        """The settings: the defaults with the file's values over them."""
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"could not read {self.path}: {exc}")
        try:
            return self.model.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"{self.path}: {'; '.join(f'{path}: {message}' for path, message in validation_errors(exc).items())}")

    def update(self, changes: Optional[dict[str, Any]] = None,
               resets: Optional[list[str]] = None) -> M:
        """Set each dotted path in ``changes`` and put each in ``resets`` back to its default.

        Raises ``SettingsError`` naming every unknown path or invalid value; the
        file is then left as it was.
        """
        changes = changes or {}
        resets = resets or []
        settable = leaf_paths(self.model)
        unknown = {path: "no such setting" for path in [*changes, *resets] if path not in settable}
        if unknown:
            raise SettingsError(unknown)
        defaults = self.model().model_dump(mode="json")
        with self.lock:
            values = self.read().model_dump(mode="json")
            for path in resets:
                set_path(values, path, get_path(defaults, path))
            for path, value in changes.items():
                set_path(values, path, value)
            try:
                settings = self.model.model_validate(values)
            except ValidationError as exc:
                raise SettingsError(validation_errors(exc))
            changed = differences(settings.model_dump(mode="json"), defaults)
            replace_file(self.path, json.dumps(changed, indent=2) + "\n")
            return settings


def server_settings(base: Optional[Path] = None) -> SettingsFile[ServerSettings]:
    """The server's settings file under ``base`` (default: ``juno.config.data_dir()``)."""
    return SettingsFile(server_settings_file(base or data_dir()), ServerSettings)


def device_settings(base: Optional[Path] = None) -> SettingsFile[DeviceSettings]:
    """This computer's recorder settings file under ``base`` (default: ``juno.config.data_dir()``)."""
    return SettingsFile(device_settings_file(base or data_dir()), DeviceSettings)


def description(owner: Literal["server", "device"], settings: BaseModel) -> dict[str, Any]:
    """What ``python -m juno.settings`` prints: the sections and the environment."""
    model = ServerSettings if owner == "server" else DeviceSettings
    needed = keys_needed(settings) if isinstance(settings, ServerSettings) else None
    return {"sections": [section.model_dump(mode="json") for section in describe(model, settings)],
            "environment": [var.model_dump(mode="json") for var in environment(owner, needed)]}


USAGE = ("usage: python -m juno.settings server|device "
         "[set PATH=VALUE... | reset PATH... | patch < {\"set\": {...}, \"reset\": [...]}]")


def parse_assignments(arguments: list[str]) -> dict[str, Any]:
    changes = {}
    for argument in arguments:
        path, sep, text = argument.partition("=")
        if not sep:
            raise SettingsError({argument: "expected PATH=VALUE"})
        changes[path] = yaml.safe_load(text) if text else None
    return changes


def main(argv: list[str]) -> int:
    load_dotenv(find_dotenv(usecwd=True))
    if not argv or argv[0] not in ("server", "device"):
        print(USAGE, file=sys.stderr)
        return 2
    owner, command, rest = argv[0], argv[1] if len(argv) > 1 else None, argv[2:]
    store = server_settings() if owner == "server" else device_settings()
    try:
        if command is None:
            settings = store.read()
        elif command == "set":
            settings = store.update(changes=parse_assignments(rest))
        elif command == "reset":
            settings = store.update(resets=rest)
        elif command == "patch":
            body = json.loads(sys.stdin.read() or "{}")
            settings = store.update(changes=body.get("set"), resets=body.get("reset"))
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except SettingsError as exc:
        print(json.dumps({"errors": exc.errors}))
        return 1
    except (ConfigError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(json.dumps({"errors": {"(file)": str(exc)}}))
        return 1
    print(json.dumps(description(owner, settings)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
