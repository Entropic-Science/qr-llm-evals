#!/usr/bin/env bash
# On-box rollback. Also provides deployment helpers when sourced by deploy.sh.
set -euo pipefail
ROOT=/opt/qr-llm-evals
SERVICE=qr-llm-evals.service

fail() { echo "refusing: $*" >&2; exit 1; }
lock_deployment() {
  [[ $EUID == 0 ]] || fail "run on qr-server with sudo bash"
  install -d -m 0755 "$ROOT/releases"
  exec 9>"$ROOT/deploy.lock"
  flock -n 9 || fail "another deployment/rollback holds the lock"
}
release_path() {
  local path
  path=$(readlink -f -- "$1") || return 1
  [[ $path == "$ROOT/releases/"* && -f "$path/release.env" && -x "$path/.venv/bin/python" ]] || return 1
  printf '%s\n' "$path"
}
flip() {
  # Atomic rename on the same filesystem; never move or delete research data.
  local target=$1 link=$2
  [[ ! -e "$link.next" && ! -L "$link.next" ]] || fail "stale $link.next; inspect it first"
  ln -s -- "$target" "$link.next" || return 1
  mv -Tf -- "$link.next" "$link"
}
preflight() {
  local target=$1
  systemd-run --quiet --wait --pipe --collect \
    -p User=qr-llm-evals -p Group=qr-llm-evals \
    -p "WorkingDirectory=$target" \
    -p EnvironmentFile=/etc/qr-llm-evals/qr-llm-evals.env \
    -p "EnvironmentFile=$target/release.env" \
    -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    -p Environment=HOME=/var/lib/qr-llm-evals \
    -p ProtectSystem=strict -p ProtectHome=true -p PrivateTmp=true \
    -p PrivateDevices=true -p NoNewPrivileges=true \
    -p ReadWritePaths=/var/lib/qr-llm-evals \
    "$target/.venv/bin/python" -m qr_llm_evals.preflight
}
health() {
  local target=$1 expected out
  expected=$(cat "$target/DEPLOYED_COMMIT")
  for _ in {1..15}; do
    if out=$(curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8830/api/health) &&
      printf '%s' "$out" | "$target/.venv/bin/python" -c \
      'import json,sys; d=json.load(sys.stdin); sys.exit(not (d.get("status")=="ok" and d.get("commit")==sys.argv[1] and d.get("engine",{}).get("reachable")))' "$expected"; then
      echo "healthy: $expected"
      return 0
    fi
    sleep 2
  done
  return 1
}
activate() {
  local target=$1 old=$2
  preflight "$target" || return 1
  flip "$target" "$ROOT/current" || return 1
  if systemctl daemon-reload && systemctl restart "$SERVICE" && health "$target"; then
    if [[ -n $old ]]; then flip "$old" "$ROOT/previous" || return 1; fi
    return 0
  fi
  echo "Activation failed; retaining releases and restoring previous service" >&2
  if [[ -n $old ]]; then
    if ! flip "$old" "$ROOT/current"; then
      echo "RESTORE FAILED: cannot restore current symlink; inspect manually" >&2
      return 1
    fi
    systemctl daemon-reload
    if ! systemctl restart "$SERVICE" || ! health "$old"; then
      echo "RESTORE FAILED: inspect journalctl -u $SERVICE; data untouched" >&2
    fi
  else
    systemctl stop "$SERVICE" || true
    echo "First deployment failed; service stopped, candidate retained for inspection" >&2
  fi
  return 1
}
main() {
  [[ $# == 0 ]] || fail "usage: sudo bash scripts/rollback.sh"
  lock_deployment
  local old target
  old=$(release_path "$ROOT/current") || fail "current release invalid"
  target=$(release_path "$ROOT/previous") || fail "no valid previous release"
  [[ $old != "$target" ]] || fail "previous equals current"
  activate "$target" "$old"
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
