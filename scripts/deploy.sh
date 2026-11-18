#!/usr/bin/env bash
# Run on qr-server from a reviewed, clean checkout. No SSH or live rollout from CI.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/rollback.sh
source scripts/rollback.sh
[[ $# -le 1 ]] || fail "usage: sudo bash scripts/deploy.sh [committed-ref]"
# Includes staged, unstaged AND untracked files; ignored caches are not deployed.
[[ -z $(git status --porcelain --untracked-files=all) ]] || fail "dirty tree; commit first"
COMMIT=$(git rev-parse --verify --end-of-options "${1:-HEAD}^{commit}")
lock_deployment
umask 022 # release must be readable/traversable by the unprivileged service
OLD=
if [[ -L $ROOT/current ]]; then
  OLD=$(release_path "$ROOT/current") || fail "current release invalid"
elif [[ -e $ROOT/current ]]; then
  fail "current must be a symlink"
fi
unit=qr-llm-evals.service
[[ $(readlink "/etc/systemd/system/$unit") == "$ROOT/current/infra/$unit" ]] || \
  fail "install documented unit symlink first: $unit"
RELEASE="$ROOT/releases/$(date -u +%Y%m%dT%H%M%SZ)-${COMMIT:0:12}"
mkdir -m 0755 "$RELEASE" # never overwrite an existing release
# Only tracked bytes from the selected immutable commit; no working-tree copy.
git archive --format=tar "$COMMIT" | tar -x --no-same-owner -C "$RELEASE"
printf '%s\n' "$COMMIT" > "$RELEASE/DEPLOYED_COMMIT"
printf 'QR_EVALS_COMMIT=%s\nPYTHONPATH=%s/backend/src\n' "$COMMIT" "$RELEASE" > "$RELEASE/release.env"
python3.11 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip install --require-hashes -r "$RELEASE/requirements.lock"
# Import from the archive, keeping registry/config paths beside the source.
# Optional reviewed Linux locks can be supplied by a future committed release.
for extra in analysis inspect metrics; do
  if [[ -f $RELEASE/requirements-$extra.lock ]]; then
    "$RELEASE/.venv/bin/python" -m pip install --require-hashes -r "$RELEASE/requirements.lock" -r "$RELEASE/requirements-$extra.lock"
  fi
done
"$RELEASE/.venv/bin/python" -m pip install --no-deps -e "$RELEASE"
"$RELEASE/.venv/bin/python" -m pip check
chmod -R go-w "$RELEASE"
activate "$RELEASE" "$OLD"
echo "Deployed $COMMIT. The shared API tunnel is not touched; no study auto-started."
