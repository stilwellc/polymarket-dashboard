#!/bin/bash
# Stores the Polymarket API credentials in the macOS login Keychain (encrypted, unlocked by your login).
# Run this yourself in Terminal. What you type is not shown, not written to any file, and not in shell history.
set -euo pipefail
SERVICE="polymarket-dashboard"
store() {  # store <account> <prompt> <hidden: 1|0>
  local value
  if [ "$3" = "1" ]; then read -r -s -p "$2: " value; echo; else read -r -p "$2: " value; fi
  if [ -z "$value" ]; then echo "  skipped (empty)"; return; fi
  security add-generic-password -U -s "$SERVICE" -a "$1" -w "$value" >/dev/null
  echo "  saved $1"
}
echo "Polymarket credentials -> Keychain service \"$SERVICE\". Press Enter on any line to skip it."
store POLYMARKET_ADDRESS        "Wallet / funder address (public)" 0
store POLYMARKET_API_KEY        "API key" 1
store POLYMARKET_API_SECRET     "API secret" 1
store POLYMARKET_API_PASSPHRASE "API passphrase" 1
echo "Done. Check what is stored (names only) with: ./keys.sh list"
