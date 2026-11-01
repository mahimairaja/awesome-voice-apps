#!/usr/bin/env bash
# Build and deploy the eval agent to the LiveKit Cloud project set as default
# in the lk CLI (lk project set-default voiceproject).
#   ./deploy.sh          first time: lk agent create
#   ./deploy.sh update   later: lk agent deploy
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
demo="$here/../../../demos/drive-thru-coffee/agent.py"
build="$here/build"
mkdir -p "$build"
cp "$here/agent.py" "$here/Dockerfile" "$build/"
cp "$(dirname "$demo")/pyproject.toml" "$(dirname "$demo")/uv.lock" "$build/"
cp "$demo" "$build/demo.py"
[ -f "$here/livekit.toml" ] && cp "$here/livekit.toml" "$build/"
cd "$build"
if [ "${1:-}" = "update" ]; then
  lk agent deploy
else
  lk agent create
  cp livekit.toml "$here/livekit.toml"
fi
