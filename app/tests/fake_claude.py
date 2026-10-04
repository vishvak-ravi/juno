#!/usr/bin/env python3
"""Stands in for the ``claude`` executable in a tmux pane.

Answers its first prompt (the last argument) and then every line typed into
the pane with "<model>: <prompt>", by running the Stop hook from ``--settings``
the way Claude Code does at the end of a turn. A prompt containing "hang" gets
no answer; "exit" ends the process, which closes the pane.
"""

import json
import subprocess
import sys


def main() -> None:
    args = sys.argv[1:]
    settings = json.loads(args[args.index("--settings") + 1])
    model = args[args.index("--model") + 1] if "--model" in args else "default"
    hook = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    prompt = args[-1]
    while True:
        print(f"> {prompt}", flush=True)
        if "exit" in prompt:
            return
        if "hang" in prompt:
            print("Do you want to proceed?", flush=True)
        else:
            subprocess.run(hook, shell=True, check=True, text=True, input=json.dumps(
                {"hook_event_name": "Stop", "last_assistant_message": f"{model}: {prompt}"}))
        prompt = sys.stdin.readline().rstrip("\n")


if __name__ == "__main__":
    main()
