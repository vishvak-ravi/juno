# Desktop client

The Electron client (`client/`): a chat window (`src/index.html`) and a
chat/session browser (`src/browse.html`) for the app server in `app/`.
Setup and run commands are in the [README](../README.md).

## Recorder

The client starts the recorder, `python -m capture` run with `app/.venv/bin/python`
from `app/` (so it reads the same `config.yaml` and `.env`), when it starts, and
quits only once the recorder has saved its session. The Status page shows
whether it is running and, if it exited, its status and last log lines; it is not
restarted until the app is. Status 2 is a backend that could not start: on macOS
a permission not granted, see [`capture.md`](capture.md). macOS grants
permissions to the app the client was launched from, the terminal when it is
started with `npm start`.

## Linux sandbox (Ubuntu 24.04 and later)

Electron aborts at startup with
`The SUID sandbox helper binary was found, but is not configured correctly`.
Ubuntu's AppArmor blocks unprivileged user namespaces
(`kernel.apparmor_restrict_unprivileged_userns = 1`) for any binary without a
profile allowing `userns`, so Chromium falls back to the setuid helper
`node_modules/electron/dist/chrome-sandbox`, which npm installs without root
ownership or the setuid bit. Give the npm Electron binary a profile, the same
one `/etc/apparmor.d/code` gives VS Code. Run from `client/`:

```
sudo tee /etc/apparmor.d/juno-electron >/dev/null <<EOF
abi <abi/5.0>,
include <tunables/global>

profile juno-electron $PWD/node_modules/electron/dist/electron flags=(unconfined) {
  userns,
  include if exists <local/juno-electron>
}
EOF
sudo apparmor_parser -r /etc/apparmor.d/juno-electron
```

The profile names the binary's path, so it survives `npm ci` and Electron
updates; redo it only if the checkout moves. Making `chrome-sandbox` root-owned
with mode 4755 also works but is undone by every reinstall.

## Server address and dictation

The app server is `http://<JUNO_SERVER_HOST>:8765`, the host read from
`app/.env` (see `app/.env.example`; default `127.0.0.1`). `JUNO_SERVER_URL`
overrides it.
Dictation needs `native/juno-dictate`, so it works only on macOS; elsewhere the
client logs `dictation helper: spawn ... ENOENT` and runs without it.
