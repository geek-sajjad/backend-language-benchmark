#!/usr/bin/env bash
# Builds the stack images ON the app host (same CPU/arch as the measurement) and records their
# image ids in results/image-digests.json, on app and on loadgen (for the runner's manifest).
#
#   ./infra/host-setup/build-images.sh                    # every stacks/<name>/ that has a compose.yml
#   ./infra/host-setup/build-images.sh rust-axum go-gin   # only these (entries for others are kept)
#
# Run deploy.sh first (it syncs the repo). Builds run one at a time; nothing is measured meanwhile.
set -euo pipefail
# shellcheck source=infra/host-setup/lib.sh
source "$(dirname "$0")/lib.sh"

load_outputs
DIGESTS="$REMOTE_REPO/results/image-digests.json"

log "app: building stack images"
rbash app "$REMOTE_REPO" "$@" <<'EOF'
set -euo pipefail
cd "$1"
shift
stacks=("$@")
if ((${#stacks[@]} == 0)); then
  for f in stacks/*/compose.yml; do [[ -f "$f" ]] && stacks+=("$(basename "$(dirname "$f")")"); done
fi
((${#stacks[@]} > 0)) || { echo "no stacks/*/compose.yml found" >&2; exit 1; }

mkdir -p results
records=()
for s in "${stacks[@]}"; do
  compose="stacks/$s/compose.yml"
  [[ -f "$compose" ]] || { echo "no $compose" >&2; exit 1; }
  echo "=== $s"
  start=$SECONDS
  docker compose -f "$compose" build
  for img in $(docker compose -f "$compose" config --images); do
    id="$(docker image inspect -f '{{.Id}}' "$img")"
    created="$(docker image inspect -f '{{.Created}}' "$img")"
    records+=("$s|$img|$id|$created|$((SECONDS - start))")
    echo "    $img $id"
  done
done

python3 - results/image-digests.json "${records[@]}" <<'PY'
import datetime, json, os, subprocess, sys

path, records = sys.argv[1], sys.argv[2:]

def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None

data = {"stacks": {}}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
rebuilt = {r.split("|")[0] for r in records}
for s in rebuilt:
    data["stacks"][s] = {"compose": f"stacks/{s}/compose.yml", "images": {}}
for r in records:
    stack, image, image_id, created, build_s = r.split("|")
    entry = data["stacks"][stack]
    entry["images"][image] = image_id
    entry["created"] = created
    entry["build_s"] = int(build_s)
    entry["built_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
data["host"] = run("hostname")
data["arch"] = run("uname", "-m")
data["docker_version"] = run("docker", "version", "--format", "{{.Server.Version}}")
data["git_commit"] = run("git", "rev-parse", "HEAD")
status = run("git", "status", "--porcelain", "--untracked-files=no")
data["git_dirty"] = None if status is None else bool(status)
data["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2, sort_keys=True)
    f.write("\n")
os.replace(tmp, path)
PY
echo "wrote $PWD/results/image-digests.json"
EOF

log "copying image-digests.json to loadgen"
rssh app "cat $DIGESTS" | rssh loadgen "mkdir -p $REMOTE_REPO/results && cat >$DIGESTS.tmp && mv $DIGESTS.tmp $DIGESTS"
log "build done; image ids in $DIGESTS (app + loadgen)"
