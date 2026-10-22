"""``/hooks``: what an assistant's own hook reports from the device it runs on.

``POST /hooks/claude-code?device=<id>`` takes the input of Claude Code's
UserPromptSubmit hook as its body (the ``http`` hook ``capture.claude_hook``
installs) and keeps the prompt as a moment of that device (``server.hooks``).
It answers 204 with no body whether or not the prompt is kept, so Claude Code
sends the prompt on either way.
"""

import logging
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel

from juno.claude_hook import ENTRYPOINT_HEADER, HOOK_PATH, REMOTE_HEADER
from juno.sessions import DEVICE_ID_PATTERN
from server.hooks import Prompt, append_prompt

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hooks"])

DeviceId = Annotated[str, Query(pattern=f"^{DEVICE_ID_PATTERN}$", max_length=64)]


class HookInput(BaseModel):
    """The fields of Claude Code's hook input read here; the others are ignored."""
    hook_event_name: str
    session_id: str
    cwd: str
    prompt_id: Optional[str] = None
    # Set when the hook fires inside a subagent.
    agent_id: Optional[str] = None


def unsupported(body: HookInput, entrypoint: Optional[str], remote: Optional[str]) -> Optional[str]:
    """Why a prompt is not kept as a moment; None when it is.

    A kept prompt's moment carries a button that runs ``claude --resume
    <session_id>`` in a new terminal in ``cwd`` on the device, so only a prompt
    typed into Claude Code in that device's terminal is kept:

    - a cloud session (``CLAUDE_CODE_REMOTE`` is ``true``) keeps its conversation
      in the cloud and its ``cwd`` is a directory of the cloud machine;
    - the Claude desktop app (``claude-desktop``), an IDE extension
      (``claude-vscode``) and the SDK (``sdk-cli`` for ``claude -p``, ``sdk-ts``,
      ``sdk-py``) open their conversations in their own window, or are scripts;
    - a subagent's prompt is not the user's.

    Chats in claude.ai and in the Claude apps' chat run no hooks and never reach here.
    """
    if body.hook_event_name != "UserPromptSubmit":
        return f"{body.hook_event_name} is not a prompt"
    if remote == "true":
        return "a cloud session"
    if entrypoint != "cli":
        return f"entrypoint {entrypoint or 'not sent'}, not the terminal (cli)"
    if body.agent_id is not None:
        return "a subagent's prompt"
    return None


@router.post(HOOK_PATH, status_code=204)
def claude_code_hook(
    body: HookInput,
    request: Request,
    device: DeviceId,
    entrypoint: Annotated[Optional[str], Header(alias=ENTRYPOINT_HEADER)] = None,
    remote: Annotated[Optional[str], Header(alias=REMOTE_HEADER)] = None,
) -> Response:
    """Keep a prompt typed into Claude Code in a terminal of ``device`` as a moment."""
    reason = unsupported(body, entrypoint, remote)
    if reason is not None:
        logger.info("not keeping a Claude Code prompt of %s: %s", device, reason)
        return Response(status_code=204)
    append_prompt(request.app.state.devices_dir / device,
                  Prompt(timestamp=time.time(), session_id=body.session_id, cwd=body.cwd,
                         prompt_id=body.prompt_id))
    return Response(status_code=204)
