#!/usr/bin/env bash
# Deploy the hosted playground worker to LiveKit Cloud (project voiceproject).
#
#   lk project set-default voiceproject
#   deployment/livekit/deploy.sh create SECRETS_FILE   first time
#   deployment/livekit/deploy.sh                       every later deploy
#
# LiveKit Cloud builds from a directory holding a Dockerfile, so this stages
# exactly what deployment/playground.Dockerfile copies (its COPY lines are the
# one list) into build/, with the Dockerfile at the top. The secrets file is
# KEY=value lines kept outside the repository; LiveKit Cloud supplies
# LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET itself.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
build="$here/build"
rm -rf "$build"
mkdir -p "$build"
grep -E '^COPY ' "$root/deployment/playground.Dockerfile" | while read -r _ args; do
  read -ra parts <<<"$args"
  for src in "${parts[@]:0:${#parts[@]}-1}"; do
    for path in $(cd "$root" && compgen -G "$src"); do
      mkdir -p "$build/$(dirname "$path")"
      cp "$root/$path" "$build/$path"
    done
  done
done
cp "$root/deployment/playground.Dockerfile" "$build/Dockerfile"
# The /status heartbeat reports which commit is running.
sha="$(git -C "$root" rev-parse --short=12 HEAD)"
sed -i.bak "s|^CMD |ENV PLAYGROUND_VERSION=$sha\nCMD |" "$build/Dockerfile" && rm "$build/Dockerfile.bak"
# Resolve the secrets file before leaving the caller's directory.
secrets=""
if [ "${1:-}" = "create" ]; then
  secrets="$(cd "$(dirname "${2:?usage: deploy.sh create SECRETS_FILE}")" && pwd)/$(basename "$2")"
fi
[ -f "$here/livekit.toml" ] && cp "$here/livekit.toml" "$build/"
cd "$build"
if [ "${1:-}" = "create" ]; then
  lk agent create --secrets-file "$secrets"
  cp livekit.toml "$here/livekit.toml"
  echo "Commit deployment/livekit/livekit.toml so later deploys update this agent."
else
  [ -f livekit.toml ] || { echo "No livekit.toml: run deploy.sh create first." >&2; exit 1; }
  lk agent deploy
fi
