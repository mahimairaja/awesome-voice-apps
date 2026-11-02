#!/usr/bin/env bash
# Stage the eval agent and the Coffee demo it serves in build/.
# Sourced by deploy.sh and local.sh.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
demo="$here/../../../demos/drive-thru-coffee/agent.py"
build="$here/build"
mkdir -p "$build"
cp "$here/agent.py" "$here/Dockerfile" "$build/"
cp "$(dirname "$demo")/pyproject.toml" "$(dirname "$demo")/uv.lock" "$build/"
cp "$demo" "$build/demo.py"
