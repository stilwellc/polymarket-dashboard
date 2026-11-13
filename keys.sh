#!/bin/bash
# ./keys.sh list            which credentials are stored (never prints values)
# ./keys.sh delete          remove all of them from the Keychain
# eval "$(./keys.sh env)"   load them into THIS shell's environment for the dashboard process
set -euo pipefail
SERVICE="polymarket-dashboard"
NAMES=(POLYMARKET_ADDRESS POLYMARKET_API_KEY POLYMARKET_API_SECRET POLYMARKET_API_PASSPHRASE)
case "${1:-list}" in
  list)   for n in "${NAMES[@]}"; do
            if security find-generic-password -s "$SERVICE" -a "$n" >/dev/null 2>&1; then echo "stored   $n"; else echo "missing  $n"; fi
          done ;;
  delete) for n in "${NAMES[@]}"; do security delete-generic-password -s "$SERVICE" -a "$n" >/dev/null 2>&1 && echo "deleted $n" || true; done ;;
  env)    for n in "${NAMES[@]}"; do
            v=$(security find-generic-password -s "$SERVICE" -a "$n" -w 2>/dev/null) || continue
            printf 'export %s=%q\n' "$n" "$v"
          done ;;
  *) echo "usage: $0 list|delete|env" >&2; exit 2 ;;
esac
