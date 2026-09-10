#!/usr/bin/env bash
# Launch dsh-tui (this repo) against the onit/Qwen vLLM endpoint via dsh's
# `sdk` profile. Sources the host and API key from ~/.secrets/onit/ into
# this process's environment only -- never printed, never written to a
# config file in the clear. Same pattern as
# ../another-harness/scripts/run-onit.sh and
# ../investigation/probe/run_probe.sh / run_probe3.sh.
set -euo pipefail

HOST_FILE="${ONIT_HOST_FILE:-$HOME/.secrets/onit/host_address.txt}"
KEY_FILE="${ONIT_KEY_FILE:-$HOME/.secrets/onit/api_key.txt}"
PROVIDER="${DSH_TUI_PROVIDER:-onit}"
MODEL="${DSH_TUI_MODEL:-Qwen/Qwen3.8-27B}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Reuses the venv built for the Task 1/3 probes (it already had
# deepseek-harness-sdk and the dsh runtime installed and proven end to end
# against this endpoint) as dsh_tui's own runtime venv too, rather than
# maintaining a second one. If it doesn't exist -- a fresh clone, or a
# different machine -- it is created here from requirements.txt, so first
# run is self-bootstrapping and never touches
# investigation/probe/dsh-home (a separate, probe-only DSH_HOME).
VENV_DIR="$REPO_ROOT/investigation/probe/.venv"
VENV_PY="$VENV_DIR/bin/python"
DSH_HOME="$REPO_ROOT/.dsh-home"

[[ -f "$HOST_FILE" ]] || { echo "missing $HOST_FILE" >&2; exit 1; }
[[ -f "$KEY_FILE"  ]] || { echo "missing $KEY_FILE" >&2; exit 1; }

if [[ ! -x "$VENV_PY" ]]; then
  echo "no venv at $VENV_DIR -- creating it from requirements.txt" >&2
  python3 -m venv "$VENV_DIR"
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r "$REPO_ROOT/requirements.txt"
fi

[[ -x "$VENV_PY" ]] || { echo "venv creation at $VENV_DIR failed -- see errors above" >&2; exit 1; }

"$VENV_PY" -c "import rich" >/dev/null 2>&1 || "$VENV_PY" -m pip install -q rich

BASE_URL="$(cat "$HOST_FILE" | tr -d '\n\r ')/v1"

mkdir -p "$DSH_HOME"
sed \
  -e "s#__DSH_TUI_PROVIDER__#${PROVIDER}#g" \
  -e "s#__DSH_TUI_MODEL__#${MODEL}#g" \
  -e "s#__DSH_TUI_BASE_URL__#${BASE_URL}#g" \
  "$REPO_ROOT/dsh_tui/settings.yaml.template" \
  > "$DSH_HOME/settings.yaml"

export ONIT_API_KEY="$(cat "$KEY_FILE" | tr -d '\n\r ')"
export DSH_TUI_PROVIDER="$PROVIDER"
export DSH_TUI_MODEL="$MODEL"
export DSH_TUI_DSH_HOME="$DSH_HOME"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO_ROOT"
exec "$VENV_PY" -m dsh_tui "$@"
