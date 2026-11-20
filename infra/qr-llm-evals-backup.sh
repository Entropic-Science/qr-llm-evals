#!/usr/bin/env bash
# Daily backup of the qr-llm-evals state that cannot be rebuilt:
#   - accounts.sqlite (accounts, sessions, quotas, usage ledger), taken with
#     SQLite's online backup API so the running service is not stopped
#   - /etc/qr-llm-evals and /etc/qr-llm-api (env files, tunnel tokens, key digests)
# Output: /var/backups/qr-llm-evals (root only). Keeps the newest 60 of each.
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

# Prune: keep the newest 60 of each kind. Backups only; never the live data.
for pat in 'accounts-2*.sqlite' 'etc-2*.tgz'; do
  find "$DEST" -maxdepth 1 -type f -name "$pat" -printf '%T@ %p\n' |
    sort -rn | tail -n +61 | cut -d' ' -f2- | xargs -r rm -f --
done
echo "backup ok: $STAMP"
