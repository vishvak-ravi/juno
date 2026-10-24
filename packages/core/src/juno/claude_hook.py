"""The Claude Code hook a device installs and the server answers.

``capture.claude_hook`` adds to Claude Code's settings an ``http`` hook on
UserPromptSubmit that posts the hook's input to ``HOOK_PATH?device=<device id>``
on the server; ``server.routes.hooks`` keeps each prompt as a moment of that
device. The body does not say where Claude Code runs, so the hook sends two of
its environment variables as headers.
"""

HOOK_PATH = "/hooks/claude-code"

# The hook's headers: header -> the Claude Code environment variable interpolated into it.
# CLAUDE_CODE_ENTRYPOINT names where Claude Code runs (``TERMINAL``, ``DESKTOP_APP`` below, among
# others); CLAUDE_CODE_REMOTE is "true" in a cloud session; CLAUDE_CODE_HOST_SESSION_ID is the
# Claude desktop app's own id of the session (``local_…``), which its claude://code/continue link
# takes, and is unset in a terminal.
ENTRYPOINT_HEADER = "X-Claude-Code-Entrypoint"
REMOTE_HEADER = "X-Claude-Code-Remote"
HOST_SESSION_HEADER = "X-Claude-Code-Host-Session"
HEADER_VARIABLES = {
    ENTRYPOINT_HEADER: "CLAUDE_CODE_ENTRYPOINT",
    REMOTE_HEADER: "CLAUDE_CODE_REMOTE",
    HOST_SESSION_HEADER: "CLAUDE_CODE_HOST_SESSION_ID",
}

# The CLAUDE_CODE_ENTRYPOINT values of the Claude Codes whose prompts the server keeps, each one
# the device can reopen: ``claude`` in a terminal, and the Code tab of the Claude desktop app.
TERMINAL = "cli"
DESKTOP_APP = "claude-desktop"
KEPT_ENTRYPOINTS = (TERMINAL, DESKTOP_APP)
