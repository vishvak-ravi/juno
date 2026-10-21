"""Thin wrapper over the ``gh`` CLI. It is on every GitHub runner and reads
``GH_TOKEN``/``GITHUB_TOKEN`` from the environment, so no HTTP client needed."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def repo() -> str:
    r = os.environ.get("GITHUB_REPOSITORY")
    if not r:
        r = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    return r


def api(path: str, *, method: str = "GET", fields: dict[str, Any] | None = None, paginate: bool = False) -> Any:
    cmd = ["gh", "api", path, "-X", method]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    if fields:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd, input=json.dumps(fields) if fields else None, check=True, capture_output=True, text=True,
    )
    out = proc.stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    if paginate and isinstance(data, list) and data and isinstance(data[0], list):
        data = [item for page in data for item in page]
    return data


def list_issue_comments(pr: int) -> list[dict]:
    return api(f"repos/{repo()}/issues/{pr}/comments?per_page=100", paginate=True)


def get_comment(comment_id: int) -> dict:
    return api(f"repos/{repo()}/issues/comments/{comment_id}")


def create_comment(pr: int, body: str) -> dict:
    return api(f"repos/{repo()}/issues/{pr}/comments", method="POST", fields={"body": body})


def update_comment(comment_id: int, body: str) -> dict:
    return api(f"repos/{repo()}/issues/comments/{comment_id}", method="PATCH", fields={"body": body})


def react(comment_id: int, content: str) -> None:
    api(f"repos/{repo()}/issues/comments/{comment_id}/reactions", method="POST", fields={"content": content})


def pr_head_sha(pr: int) -> str:
    return api(f"repos/{repo()}/pulls/{pr}")["head"]["sha"]


def set_status(sha: str, state: str, description: str, context: str = "pr2test") -> None:
    api(
        f"repos/{repo()}/statuses/{sha}",
        method="POST",
        fields={"state": state, "description": description[:140], "context": context},
    )
