"""Install the Claude Code hook that reports prompts: ``python -m capture.claude_hook install``.

``install`` adds to Claude Code's user settings (``settings.json`` in
CLAUDE_CONFIG_DIR, default ``~/.claude``) an ``http`` hook on UserPromptSubmit
that posts each prompt's hook input to the server this device's recorder
uploads to (JUNO_SERVER_HOST, ``server.port``), under this device's id; see
``juno.claude_hook``. Installing again replaces the hook, so a changed address,
device id or ``claude_hook.timeout_seconds`` is picked up. ``uninstall``
removes it. Every other setting is kept as it is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from dotenv import find_dotenv, load_dotenv

from juno.claude_hook import HEADER_VARIABLES, HOOK_PATH
from juno.config import ConfigError, load_config, resolve_device_id, server_host

CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
EVENT = "UserPromptSubmit"


def settings_path() -> Path:
    """Claude Code's user settings file."""
    return Path(os.environ.get(CLAUDE_CONFIG_DIR_ENV) or "~/.claude").expanduser() / "settings.json"


def hook_of(url: str, timeout_seconds: int) -> dict[str, Any]:
    """The hook entry that posts to ``url``."""
    return {"type": "http", "url": url, "timeout": timeout_seconds,
            "headers": {header: f"${variable}" for header, variable in HEADER_VARIABLES.items()},
            "allowedEnvVars": list(HEADER_VARIABLES.values())}


def is_ours(hook: dict[str, Any]) -> bool:
    return hook.get("type") == "http" and HOOK_PATH + "?" in str(hook.get("url", ""))


def without_hook(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` with every Juno prompt hook taken out, and a group or event left empty by that."""
    hooks = dict(settings.get("hooks") or {})
    groups = []
    for group in hooks.get(EVENT, []):
        kept = [hook for hook in group.get("hooks", []) if not is_ours(hook)]
        if kept or not group.get("hooks"):
            groups.append({**group, "hooks": kept})
    if groups:
        hooks[EVENT] = groups
    else:
        hooks.pop(EVENT, None)
    result = {**settings, "hooks": hooks}
    if not hooks:
        del result["hooks"]
    return result


def with_hook(settings: dict[str, Any], hook: dict[str, Any]) -> dict[str, Any]:
    """``settings`` with ``hook`` as the one Juno prompt hook."""
    result = without_hook(settings)
    hooks = dict(result.get("hooks") or {})
    hooks[EVENT] = [*hooks.get(EVENT, []), {"hooks": [hook]}]
    return {**result, "hooks": hooks}


def read_settings(path: Path) -> dict[str, Any]:
    try:
        settings = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not JSON ({exc}); fix it, then run this again")
    if not isinstance(settings, dict):
        raise ConfigError(f"{path} is not a JSON object")
    return settings


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    """Replace the file in one step, so Claude Code never reads half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".juno.tmp")
    temporary.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(temporary, path)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m capture.claude_hook",
                                     description="Report each Claude Code prompt to the Juno server.")
    parser.add_argument("action", choices=["install", "uninstall"])
    args = parser.parse_args(argv)
    # This device's settings may live in a .env file.
    load_dotenv(find_dotenv(usecwd=True))
    path = settings_path()
    try:
        settings = read_settings(path)
        if args.action == "uninstall":
            write_settings(path, without_hook(settings))
            print(f"removed the Juno prompt hook from {path}")
            return 0
        config = load_config()
        url = f"http://{server_host()}:{config.server.port}{HOOK_PATH}?device={resolve_device_id()}"
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_settings(path, with_hook(settings, hook_of(url, config.claude_hook.timeout_seconds)))
    print(f"Claude Code sessions started from now on report each prompt to {url} ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
