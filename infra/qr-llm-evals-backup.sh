#!/usr/bin/env bash
# Daily backup of the qr-llm-evals state that cannot be rebuilt:
#   - accounts.sqlite (accounts, sessions, quotas, usage ledger), taken with
#     SQLite's online backup API so the running service is not stopped
#   - /etc/qr-llm-evals and /etc/qr-llm-api (env files, tunnel tokens, key digests)
#   - the run artifacts under studies/ and _quarantine/ (samples, token records,
#     judge rows): paid-for data, copied into runs/ and never pruned or deleted
#     there, so a file removed or damaged in the live data stays in the copy
# Output: /var/backups/qr-llm-evals (root only). Keeps the newest 60 of each
# accounts and etc file. This is a second copy on the same disk: it guards
# against a mistake, not against losing the disk. Pull it off the box with
# scripts/pull-backup.sh.
set -euo pipefail
umask 077
DEST=/var/backups/qr-llm-evals
SRC=/var/lib/qr-llm-evals/accounts.sqlite
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$DEST"

if [[ -f $SRC ]]; then
  OUT="$DEST/accounts-$STAMP.sqlite"
  python3 - "$SRC" "$OUT" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit(f"integrity_check failed: {ok}")
PY
  ln -sfn "accounts-$STAMP.sqlite" "$DEST/accounts-latest.sqlite"
fi

tar czf "$DEST/etc-$STAMP.tgz" -C /etc qr-llm-evals qr-llm-api
ln -sfn "etc-$STAMP.tgz" "$DEST/etc-latest.tgz"

# Run artifacts. A plain copy with no deletion on the receiving side: files
# only ever arrive in the copy. A file that changed in the live data (a run
# still writing its JSONL, a run.json whose state moved on) replaces its copy,
# and the copy's previous version is first moved to runs/changed-<stamp>/, so
# a file damaged or cut short in the live data cannot silently overwrite a
# good copy. Finished runs do not change, so they add nothing there. The
# changed-<stamp> folders are never removed by this script.
DATA=/var/lib/qr-llm-evals
install -d -o root -g root -m 0700 "$DEST/runs"
for part in studies _quarantine; do
  if [[ -d $DATA/$part ]]; then
    rsync -a --no-owner --no-group \
      --backup --backup-dir="$DEST/runs/changed-$STAMP/$part" \
      "$DATA/$part/" "$DEST/runs/$part/"
  fi
done

# Prune: keep the newest 60 of each kind. Backups only; never the live data.
for pat in 'accounts-2*.sqlite' 'etc-2*.tgz'; do
  find "$DEST" -maxdepth 1 -type f -name "$pat" -printf '%T@ %p\n' |
    sort -rn | tail -n +61 | cut -d' ' -f2- | xargs -r rm -f --
done
echo "backup ok: $STAMP"
