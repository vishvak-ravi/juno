"""``/settings``: the server's settings, for a client to show and change.

``GET /settings`` lists every setting of ``juno.config.ServerSettings`` by
section, each with its value, its default and what it does; the environment
variables the server reads, an API key shown only as set or not; and
``restart``, the settings saved with a value other than the one this process
runs with. ``PATCH /settings`` with ``{"set": {"annotate.model": "..."},
"reset": ["annotate.api_base"]}`` saves the changes to ``<data_dir>/settings.json``
and answers as ``GET`` does; they apply when the server restarts. A refused change
answers 422 with ``detail`` mapping each dotted path to why, and saves nothing; a
setting marked ``local`` (``juno.config.LOCAL``) is refused always, since the API
has no authentication and such a setting widens what a caller can have run. A
settings file that cannot be read answers 500 with its path in ``detail``.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from juno.config import ConfigError, ServerSettings
from juno.settings import (EnvView, SectionView, SettingsError, describe, environment,
                           get_path, keys_needed, leaf_paths, local_paths)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsView(BaseModel):
    sections: list[SectionView]
    environment: list[EnvView]
    # Dotted paths whose saved value is not the one the server runs with.
    restart: list[str]


class SettingsPatch(BaseModel):
    # Dotted path to its new value.
    set: dict[str, Any] = {}
    # Dotted paths put back to their defaults.
    reset: list[str] = []


def view(saved: ServerSettings, running: ServerSettings) -> SettingsView:
    saved_values = saved.model_dump(mode="json")
    running_values = running.model_dump(mode="json")
    restart = [path for path in leaf_paths(ServerSettings)
               if get_path(saved_values, path) != get_path(running_values, path)]
    return SettingsView(sections=describe(ServerSettings, saved),
                        environment=environment("server", keys_needed(saved)),
                        restart=restart)


LOCAL_ONLY = "changed only on the server's machine: python -m juno.settings server set"


@router.get("")
def get_settings(request: Request) -> SettingsView:
    state = request.app.state
    try:
        saved = state.settings_file.read()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return view(saved, state.settings)


@router.patch("")
def patch_settings(body: SettingsPatch, request: Request) -> SettingsView:
    state = request.app.state
    local = local_paths(ServerSettings)
    refused = {path: LOCAL_ONLY for path in [*body.set, *body.reset] if path in local}
    if refused:
        raise HTTPException(status_code=422, detail=refused)
    try:
        saved = state.settings_file.update(changes=body.set, resets=body.reset)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=exc.errors)
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return view(saved, state.settings)
