"""Bash commands for the chat model: one fresh process per command, as in mini-swe-agent.

Nothing carries over from one command to the next except the files in the
workspace directory, which every command starts in.

With ``BashConfig.sandbox`` the command runs under bubblewrap (``bwrap``): the
system directories are read-only, the workspace is the only writable directory
that outlasts the command, and the user's home directory and the server's
environment variables are not there at all.
The network is the host's, so the command can reach the internet and every
port on localhost.
"""

import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from juno.config import BashConfig

# What the sandbox sees of the host, read-only. /etc carries the resolver and
# TLS settings; /run/systemd/resolve is where /etc/resolv.conf points on Ubuntu.
SANDBOX_READ_ONLY = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/run/systemd/resolve")
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
READ_CHUNK_BYTES = 65536
# Characters of the result kept for the lines this module adds around the output.
NOTE_CHARS = 200


class BashError(Exception):
    """The command could not be started."""


def sandbox_argv(workspace: Path) -> list[str]:
    """The bubblewrap command line that a bash command line is appended to."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise BashError("bash is configured to run sandboxed but bwrap (bubblewrap) "
                        "is not installed")
    # --clearenv: the server's environment holds API keys.
    argv = [bwrap, "--die-with-parent", "--new-session", "--clearenv",
            "--setenv", "PATH", SANDBOX_PATH, "--setenv", "LANG", "C.UTF-8",
            "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for path in SANDBOX_READ_ONLY:
        argv += ["--ro-bind-try", path, path]
    return argv + ["--bind", str(workspace), str(workspace), "--setenv", "HOME", str(workspace)]


def cut_middle(head: bytes, tail: bytes, total: int) -> str:
    """The output as text: whole if ``head`` holds all ``total`` bytes, else its start and end."""
    if total <= len(head):
        return head.decode(errors="replace")
    tail = tail[-(total - len(head)):]  # the tail may overlap the head
    cut = total - len(head) - len(tail)
    middle = f"\n[{cut} bytes of output cut here]\n" if cut else ""
    return head.decode(errors="replace") + middle + tail.decode(errors="replace")


def run_bash(command: str, config: BashConfig, workspace: Path, result_chars: int) -> str:
    """Run ``command`` and return what the model is shown: stdout and stderr in
    the order written, then the exit code if it is not 0.

    Args:
        workspace: the directory the command starts in; created if missing.
        result_chars: the most characters to return. Longer output keeps its
            start and its end; the command is not stopped for printing too much.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    argv = sandbox_argv(workspace) if config.sandbox else []
    try:
        # A session of its own, so that the timeout can kill everything the command started.
        process = subprocess.Popen(argv + ["bash", "-c", command], cwd=workspace,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        raise BashError(f"could not start the command: {exc}")

    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    half = max(0, result_chars - NOTE_CHARS) // 2
    head, tail, total = b"", b"", 0
    timer = threading.Timer(config.timeout_seconds, kill)
    timer.start()
    try:
        # Reads until every process holding the pipe has exited or been killed.
        while chunk := process.stdout.read1(READ_CHUNK_BYTES):
            total += len(chunk)
            if len(head) < half:
                head += chunk[:half - len(head)]
            tail = (tail + chunk)[-half:] if half else b""
        process.wait()
    finally:
        timer.cancel()
        process.stdout.close()

    output = cut_middle(head, tail, total)
    if output and not output.endswith("\n"):
        output += "\n"
    if timed_out.is_set():
        return output + f"[killed: still running after {config.timeout_seconds:g} seconds]"
    if process.returncode != 0:
        return output + f"[exit code {process.returncode}]"
    return output or "[no output]"
