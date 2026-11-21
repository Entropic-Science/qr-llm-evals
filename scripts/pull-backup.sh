#!/usr/bin/env bash
# Pull the qr-llm-evals backups from the box to this machine.
#
# A backup that stays on the box does not survive losing the box. This script
# copies it off: run it from the folder that should hold the copies, on a
# machine that can `ssh qr`.
#
#   bash pull-backup.sh            # newest accounts database and config archive
#   bash pull-backup.sh --runs     # also the run artifacts (the paid-for study data)
#   bash pull-backup.sh --fresh    # take a new backup on the box first
#
# Options combine. Set QR_BOX to use another ssh host name than "qr".
#
# What lands here:
#   accounts-<UTC>.sqlite, etc-<UTC>.tgz   checked against the box's SHA-256
#   runs-<UTC>.tar.gz                      the box's copy of every run, checked by
#                                          reading the archive back and counting files
#
# The copies hold password hashes, key digests, the tunnel token and study
# data. Keep them private and out of any repository. Nothing is ever removed on
# the box; a download that fails its check is removed here and the script stops.
set -euo pipefail
BOX=${QR_BOX:-qr}
REMOTE=/var/backups/qr-llm-evals
fresh=0
runs=0
for arg in "$@"; do
  case $arg in
    --fresh) fresh=1 ;;
    --runs) runs=1 ;;
    *) echo "unknown option: $arg (use --fresh and/or --runs)" >&2; exit 2 ;;
  esac
done

if (( fresh )); then ssh "$BOX" 'sudo systemctl start qr-llm-evals-backup.service'; fi

for link in accounts-latest.sqlite etc-latest.tgz; do
  f=$(ssh "$BOX" "sudo readlink $REMOTE/$link")
  ssh "$BOX" "sudo cat $REMOTE/$f" > "$f.part"
  want=$(ssh "$BOX" "sudo sha256sum $REMOTE/$f" | cut -d' ' -f1)
  got=$(sha256sum "$f.part" | cut -d' ' -f1)
  [[ $want == "$got" ]] || { rm -f -- "$f.part"; echo "checksum mismatch: $f" >&2; exit 1; }
  mv -f -- "$f.part" "$f"
  echo "ok  $f"
done

if (( runs )); then
  if ! ssh "$BOX" "sudo test -d $REMOTE/runs/studies"; then
    echo "no run artifacts are backed up on the box yet ($REMOTE/runs/studies is missing)" >&2
    exit 1
  fi
  out="runs-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  # Count first: a run that is generating may add files while the archive is
  # read, so the archive must hold at least this many, never fewer.
  want=$(ssh "$BOX" "sudo find $REMOTE/runs/studies -type f | wc -l")
  ssh "$BOX" "sudo tar -C $REMOTE/runs -czf - studies" > "$out.part"
  got=$(tar -tzf "$out.part" | grep -vc '/$' || true)
  if (( got < want )); then
    rm -f -- "$out.part"
    echo "run archive is incomplete: $got files, the box has $want" >&2
    exit 1
  fi
  mv -f -- "$out.part" "$out"
  echo "ok  $out ($got files, $(du -h "$out" | cut -f1))"
fi
