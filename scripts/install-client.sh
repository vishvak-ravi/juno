#!/usr/bin/env bash
# Installs the Juno desktop client on this computer (macOS or Linux):
#   curl -fsSL https://raw.githubusercontent.com/vishvak-ravi/juno/main/scripts/install-client.sh | bash
# Clones the repository into $JUNO_DIR (default ~/juno), installs the client's
# dependencies (the Electron app and the recorder it starts), asks for the
# capture permissions, offers the passive agent module on macOS, asks for the
# server's address, and prints the command that
# starts the client. The client is described in docs/client.md, the recorder
# and its permissions in docs/capture.md.
set -euo pipefail

REPO_URL=https://github.com/vishvak-ravi/juno.git
JUNO_DIR=${JUNO_DIR:-$HOME/juno}
# process.loadEnvFile, which the client reads .env with.
NODE_MAJOR=22

platform=$(uname -s)
case "$platform" in
  Darwin | Linux) ;;
  *) echo "error: the client runs on macOS and Linux, not $platform" >&2; exit 1 ;;
esac

# Piped into bash, stdin is this script, so answers are read from the terminal.
ask() { local answer; read -r -p "$1" answer </dev/tty; echo "$answer"; }
confirm() { case "$(ask "$1 [y/N] ")" in y | Y | yes) ;; *) return 1 ;; esac; }
# set_env KEY VALUE: set KEY in .env, keeping the other lines.
set_env() {
  local tmp; tmp=$(mktemp)
  grep -v "^$1=" .env >"$tmp" || true
  echo "$1=$2" >>"$tmp"
  mv "$tmp" .env
}

if ! confirm "Are this computer and the server on one network (Tailscale or a LAN)?"; then
  echo "Put them on one network first, then run this again."
  exit 1
fi

command -v git >/dev/null || { echo "error: git is required" >&2; exit 1; }
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt "$NODE_MAJOR" ]; then
  echo "error: Node.js $NODE_MAJOR or later is required (https://nodejs.org)" >&2
  exit 1
fi
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$JUNO_DIR/.git" ]; then
  git -C "$JUNO_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$JUNO_DIR"
fi
# The recorder the client starts.
cd "$JUNO_DIR"
uv sync --package juno-capture --no-dev
# The client itself.
cd "$JUNO_DIR/apps/desktop"
npm ci

# The passive agent module (docs/client.md): the gesture model and the dictation helper.
passive_agent=0
if [ "$platform" = Darwin ]; then
  cat <<MESSAGE

The passive agent module adds a camera preview with hand keypoints, gestures that
dictate and send, and dictation. Installing it downloads the gesture model and
builds the dictation helper, which needs the Xcode command line tools.
MESSAGE
  if confirm "Install the passive agent module?"; then
    npm run model
    if command -v swiftc >/dev/null; then
      npm run native
    else
      echo "swiftc not found: gestures will work; for dictation, install the Xcode command line tools and run npm run native here."
    fi
    passive_agent=1
  fi
fi

# Capture permissions.
cd "$JUNO_DIR"
start="npm start"
if [ "$platform" = Darwin ]; then
  cat <<MESSAGE

The recorder needs Screen Recording, Accessibility and Input Monitoring, granted
in System Settings > Privacy & Security to the app the client is started from:
this terminal, if you start it here.
MESSAGE
  if confirm "Request them now (dialogs will open)?"; then
    uv run python -c 'from capture.sources.macos import request_access; request_access()'
    echo "Turn them on in System Settings, then quit and reopen this terminal before starting the client."
  fi
else
  if ! id -nG | tr ' ' '\n' | grep -qx input; then
    echo
    echo "The recorder reads /dev/input, which needs your user in the input group."
    if confirm "Add $USER to the input group (sudo)?"; then
      sudo usermod -aG input "$USER"
      echo "The group applies after you log out and in; until then start the client with: sg input -c '$start'"
      start="sg input -c '$start'"
    fi
  fi
  if [ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ]; then
    echo
    echo "AppArmor restricts user namespaces here, which stops Electron from starting (docs/client.md)."
    if confirm "Install the AppArmor profile for the client's Electron binary (sudo)?"; then
      sudo tee /etc/apparmor.d/juno-electron >/dev/null <<PROFILE
abi <abi/5.0>,
include <tunables/global>

profile juno-electron $JUNO_DIR/apps/desktop/node_modules/electron/dist/electron flags=(unconfined) {
  userns,
  include if exists <local/juno-electron>
}
PROFILE
      sudo apparmor_parser -r /etc/apparmor.d/juno-electron
    fi
  fi
fi

# This computer's settings.
[ -f .env ] || cp .env.example .env
echo
host=$(ask "Server address (what the server install printed, without http:// or the port): ")
[ -n "$host" ] || { echo "error: the server address is required" >&2; exit 1; }
set_env JUNO_SERVER_HOST "$host"
default_id=$(uv run python -c 'from juno.config import resolve_device_id; print(resolve_device_id())')
device_id=$(ask "Name for this computer on the server (lowercase letters and digits) [$default_id]: ")
set_env JUNO_DEVICE_ID "${device_id:-$default_id}"
set_env JUNO_PASSIVE_AGENT "$passive_agent"

cat <<MESSAGE

Installed. Start the client with:

    cd $JUNO_DIR/apps/desktop && $start

Its Status page shows whether this computer is recording.
MESSAGE
