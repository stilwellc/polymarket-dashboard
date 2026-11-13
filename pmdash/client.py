"""Read-only client for the Polymarket US API (https://docs.polymarket.us).

Credentials come from the macOS Keychain (service "polymarket-dashboard", stored by ./add_keys.sh) or, failing that,
the environment. They stay in this process: nothing here prints, logs or writes them.

Auth (docs: api-reference/authentication): every request carries
    X-PM-Access-Key  the Key ID
    X-PM-Timestamp   Unix time in milliseconds (within 30 s of server time)
    X-PM-Signature   base64( Ed25519_sign( timestamp + METHOD + path ) )   path WITHOUT the query string
The secret is base64; its first 32 bytes are the Ed25519 seed.

This module only ever sends GET requests. The same key can place and cancel orders through other endpoints, so
nothing that writes is implemented here on purpose."""
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.asymmetric import ed25519

BASE = "https://api.polymarket.us"
SERVICE = "polymarket-dashboard"


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:300]}"); self.status = status; self.body = body


def _secret(name):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get(name, "").strip()


class Client:
    def __init__(self):
        self._key_id = _secret("POLYMARKET_API_KEY")
        secret = _secret("POLYMARKET_API_SECRET")
        if not self._key_id or not secret:
            raise SystemExit("No Polymarket US credentials found. Run ./add_keys.sh first.")
        raw = base64.b64decode(secret + "=" * (-len(secret) % 4))
        if len(raw) < 32:
            raise SystemExit("The stored secret is not a base64 Ed25519 key (decodes to under 32 bytes).")
        self._key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])

    def get(self, path, params=None):
        """GET only. `path` is signed without its query string."""
        ts = str(int(time.time() * 1000))
        sig = base64.b64encode(self._key.sign(f"{ts}GET{path}".encode())).decode()
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        req = urllib.request.Request(url, method="GET", headers={
            "X-PM-Access-Key": self._key_id, "X-PM-Timestamp": ts, "X-PM-Signature": sig,
            "Content-Type": "application/json", "User-Agent": "pmdash/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")) from None

    def paged(self, path, key, params=None, max_pages=200):
        """Follow nextCursor/eof. `key` is the list (or map) field to collect."""
        items, cursor, params, seen = [], None, dict(params or {}), set()
        for _ in range(max_pages):
            data = self.get(path, dict(params, cursor=cursor))
            chunk = data.get(key) or ([] if key != "positions" else {})
            items.extend(chunk.items() if isinstance(chunk, dict) else chunk)
            cursor = data.get("nextCursor")
            if data.get("eof") or not cursor or not chunk:
                return items
            if cursor in seen:                  # the API handed back a cursor it already gave: stop, don't loop
                break
            seen.add(cursor)
        # never return a partial history: P&L built on a truncated list is wrong without looking wrong
        raise ApiError(0, f"{path}: history did not end after {len(items)} rows; refusing to compute from a partial list")

    def balances(self):
        return self.get("/v1/account/balances").get("balances") or []

    def positions(self):
        return dict(self.paged("/v1/portfolio/positions", "positions", {"limit": 100}))

    def activities(self):
        return self.paged("/v1/portfolio/activities", "activities", {"limit": 100, "sortOrder": "SORT_ORDER_ASCENDING"})
