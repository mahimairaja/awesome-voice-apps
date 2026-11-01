#!/usr/bin/env bash
# Run the eval agent on this machine as a worker of a LiveKit Cloud project,
# so it takes no agent deployment slot. ENV_FILE holds LIVEKIT_URL,
# LIVEKIT_API_KEY and LIVEKIT_API_SECRET; this script never prints them.
#   ./local.sh ENV_FILE           start the worker, run the Coffee evals, stop
#   ./local.sh ENV_FILE worker    start the worker only (Ctrl-C stops it)
set -euo pipefail
env_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
mode="${2:-evals}"
. "$(dirname "$0")/stage.sh"
set -a
. "$env_file"
set +a
: "${LIVEKIT_URL:?missing in $env_file}" "${LIVEKIT_API_KEY:?missing}" "${LIVEKIT_API_SECRET:?missing}"

cd "$build"
uv sync --frozen --quiet
uv run --frozen python agent.py download-files >/dev/null
if [ "$mode" = "worker" ]; then
  exec uv run --frozen python agent.py dev
fi

log="$build/worker.log"
uv run --frozen python agent.py start >"$log" 2>&1 &
worker=$!
trap 'kill $worker 2>/dev/null || true' EXIT
for _ in $(seq 60); do
  grep -qi "registered worker" "$log" && break
  kill -0 $worker 2>/dev/null || { cat "$log"; exit 1; }
  sleep 1
done
grep -qi "registered worker" "$log" || { echo "worker did not register"; tail -20 "$log"; exit 1; }
echo "eval worker registered; calling it"

cd "$here/.."
mkdir -p recordings
EVAL_TARGET=cloud EVAL_DEMOS="${EVAL_DEMOS:-coffee}" EVAL_RECORDS=recordings/calls.json \
  EVAL_AUDIO_DIR=recordings uv run deepeval test run test_demos.py
echo "calls: $PWD/recordings/calls.json  worker log: $log"
