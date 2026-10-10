#!/usr/bin/env bash
#
# Run a command inside the Nix development environment, with a Docker
# fallback when Nix is not installed. Used as Make's SHELL.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Make invokes SHELL as: run-in-nix.sh -c "command"
if [ "${1:-}" = "-c" ]; then
  shift
  set -- bash -c "$@"
fi

# Already inside the dev shell: run directly.
if [ -n "${IN_NIX_SHELL:-}" ]; then
  exec "${@:-bash}"
fi

if [ $# -gt 0 ]; then
  CMD_ARGS=(--command bash "$ROOT_DIR/scripts/run-in-nix.sh" "$@")
else
  CMD_ARGS=()
fi

if command -v nix >/dev/null 2>&1; then
  exec nix develop --experimental-features 'nix-command flakes' "$ROOT_DIR#default" "${CMD_ARGS[@]}"
elif command -v docker >/dev/null 2>&1; then
  DOCKER_ARGS=(-v "$ROOT_DIR:/app" -w /app)
  [ $# -eq 0 ] && DOCKER_ARGS+=(-it)
  exec docker run --rm "${DOCKER_ARGS[@]}" nixos/nix \
    nix develop --experimental-features 'nix-command flakes' .#default "${CMD_ARGS[@]//$ROOT_DIR/\/app}"
else
  echo "Error: neither nix nor docker is available." >&2
  exit 1
fi
