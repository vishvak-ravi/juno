#!/usr/bin/env bash
# Installs the Juno server on this machine (macOS or Linux):
#   curl -fsSL https://raw.githubusercontent.com/vishvak-ravi/juno/main/scripts/install-server.sh | bash
# Clones the repository into $JUNO_DIR (default ~/juno), installs the Python
# dependencies, writes this machine's settings to app/.env and prints the
# command that starts the server. What the server does is in docs/capture.md.
set -euo pipefail

REPO_URL=https://github.com/vishvak-ravi/juno.git
JUNO_DIR=${JUNO_DIR:-$HOME/juno}

case "$(uname -s)" in
  Darwin | Linux) ;;
  *) echo "error: the server runs on macOS and Linux, not $(uname -s)" >&2; exit 1 ;;
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

if ! confirm "Are this machine and the devices it will record on one network (Tailscale or a LAN)?"; then
  echo "Put them on one network first, then run this again."
  exit 1
fi

command -v git >/dev/null || { echo "error: git is required" >&2; exit 1; }
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$JUNO_DIR/.git" ]; then
  git -C "$JUNO_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$JUNO_DIR"
fi
cd "$JUNO_DIR/app"
uv sync --no-dev

[ -f .env ] || cp .env.example .env
# The address clients reach this machine at: its Tailscale address if it has one.
default_host=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
host=$(ask "Address the server listens on and clients connect to [${default_host:-127.0.0.1}]: ")
set_env JUNO_SERVER_HOST "${host:-${default_host:-127.0.0.1}}"
key=$(ask "Gemini API key for annotation (Enter to leave unset): ")
[ -z "$key" ] || set_env GEMINI_API_KEY "$key"

port=$(uv run python -c 'from juno.config import load_config; print(load_config().server.port)')
cat <<MESSAGE

Installed. Start the server with:

    cd $JUNO_DIR/app && uv run python -m server

Clients connect to http://${host:-${default_host:-127.0.0.1}}:$port (the client install asks for this address).
MESSAGE
