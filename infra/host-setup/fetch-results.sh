#!/usr/bin/env bash
# Copies results/<campaign-id>/ from loadgen into this checkout. Safe to re-run (incremental).
#
#   ./infra/host-setup/fetch-results.sh <campaign-id>            # everything, incl. raw/ (git-ignored)
#   ./infra/host-setup/fetch-results.sh <campaign-id> --no-raw   # summaries only
set -euo pipefail
# shellcheck source=infra/host-setup/lib.sh
source "$(dirname "$0")/lib.sh"

CAMPAIGN="${1:-}"
[[ -n "$CAMPAIGN" ]] || die "usage: $0 <campaign-id> [--no-raw]"
[[ "$CAMPAIGN" =~ ^[A-Za-z0-9._-]+$ && "$CAMPAIGN" != . && "$CAMPAIGN" != .. ]] || die "invalid campaign id: $CAMPAIGN"
EXTRA=()
case "${2:-}" in
  "") ;;
  --no-raw) EXTRA=(--exclude=raw/) ;;
  *) die "unknown option: $2" ;;
esac

require_cmds rsync
load_outputs
rssh loadgen "test -d $REMOTE_REPO/results/$CAMPAIGN" || die "loadgen has no results/$CAMPAIGN"

DEST="$REPO_ROOT/results/$CAMPAIGN"
mkdir -p "$DEST"
log "loadgen:results/$CAMPAIGN -> $DEST"
rsync -az ${EXTRA[@]+"${EXTRA[@]}"} -e "$(ssh_cmd_string)" \
  "$SSH_USER@$(pub_ip loadgen):$REMOTE_REPO/results/$CAMPAIGN/" "$DEST/"
# Image ids are recorded outside the campaign folder; keep a copy with the results.
rsync -az -e "$(ssh_cmd_string)" "$SSH_USER@$(pub_ip loadgen):$REMOTE_REPO/results/image-digests.json" \
  "$DEST/image-digests.json" 2>/dev/null || log "no results/image-digests.json on loadgen (skipped)"
log "done: $(du -sh "$DEST" | cut -f1) in results/$CAMPAIGN"
