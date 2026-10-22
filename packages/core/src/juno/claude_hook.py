"""The Claude Code hook a device installs and the server answers.

``capture.claude_hook`` adds to Claude Code's settings an ``http`` hook on
UserPromptSubmit that posts the hook's input to ``HOOK_PATH?device=<device id>``
on the server; ``server.routes.hooks`` keeps each prompt as a moment of that
device. The body does not say where Claude Code runs, so the hook sends two of
its environment variables as headers.
"""

HOOK_PATH = "/hooks/claude-code"

# The hook's headers: header -> the Claude Code environment variable interpolated into it.
# CLAUDE_CODE_ENTRYPOINT is "cli" in a terminal; CLAUDE_CODE_REMOTE is "true" in a cloud session.
ENTRYPOINT_HEADER = "X-Claude-Code-Entrypoint"
REMOTE_HEADER = "X-Claude-Code-Remote"
HEADER_VARIABLES = {
    ENTRYPOINT_HEADER: "CLAUDE_CODE_ENTRYPOINT",
    REMOTE_HEADER: "CLAUDE_CODE_REMOTE",
}
