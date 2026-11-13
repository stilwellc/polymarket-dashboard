# Polymarket US P&L dashboard

Local, read-only view of one Polymarket US account.

    ./add_keys.sh     # once: Key ID + Secret Key into the macOS Keychain (from polymarket.us/developer)
    ./run.sh          # then open http://127.0.0.1:8765
    .venv/bin/python -m pmdash --check    # fetch once and print the cash reconciliation

- Credentials live only in the Keychain (service `polymarket-dashboard`); `./keys.sh list|delete`.
- The code sends GET requests only (`pmdash/client.py`). The key itself is NOT read-only: Polymarket US keys can
  also place and cancel orders, so treat it like a trading password and revoke it at polymarket.us/developer if exposed.
- The server binds to 127.0.0.1 and rejects other Host headers. Account data is cached in `data/` (git-ignored).
- P&L is built from cash flows and must reconcile to the reported cash; the page says so in its header
  (`pmdash/pnl.py` explains the model).

Fonts in `pmdash/fonts/` are Inter and Geist Mono, both under the SIL Open Font License.
