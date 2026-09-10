#!/usr/bin/env bash
# Renders settings.yaml from the template (substituting the real base URL
# only inside this script's own process, never printed) and runs the
# multiturn probe. Never echoes ONIT_API_KEY or the base URL.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_FILE="$HOME/.secrets/onit/host_address.txt"
KEY_FILE="$HOME/.secrets/onit/api_key.txt"

BASE_URL="$(cat "$HOST_FILE" | tr -d '\n\r ')/v1"
sed "s#__ONIT_BASE_URL__#${BASE_URL}#" "$HERE/settings.yaml.template" \
  > "$HERE/dsh-home/settings.yaml"

export ONIT_API_KEY="$(cat "$KEY_FILE" | tr -d '\n\r ')"

"$HERE/.venv/bin/python" "$HERE/multiturn_probe.py" "$@"
