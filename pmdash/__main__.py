"""python -m pmdash  ->  the P&L dashboard at http://127.0.0.1:8765

Local only: the server binds to 127.0.0.1 and refuses any request whose Host header is not this machine, so no
other device (and no web page using DNS tricks) can read the account through it. Account data is fetched with GET
requests only and kept in data/raw.json (owner-readable, git-ignored). Credentials stay in the Keychain."""
import json
import os
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import pnl
from .client import ApiError, Client

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PMDASH_PORT", "8765"))
MAX_AGE = 60                    # seconds a fetch is reused before asking the API again
_lock = threading.Lock()
_cache = {"t": 0.0, "data": None}


_seen = {}


def _reload_if_edited():
    """Pick up edits to the P&L code without a restart (the page itself is re-read on every request)."""
    import importlib
    from . import client as client_mod
    for mod in (client_mod, pnl):
        m = pathlib.Path(mod.__file__).stat().st_mtime
        if _seen.setdefault(mod.__name__, m) != m:
            importlib.reload(mod); _seen[mod.__name__] = m; _cache.update(t=0.0, data=None)


def load(force=False):
    _reload_if_edited()
    from .client import Client                                  # the possibly reloaded class
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["t"] < MAX_AGE:
            return _cache["data"]
        c = Client()
        raw = {"balances": c.balances(), "positions": c.positions(), "activities": c.activities()}
        (ROOT / "data").mkdir(mode=0o700, exist_ok=True)
        fd = os.open(ROOT / "data" / "raw.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(raw, f)
        _cache.update(t=time.time(), data=pnl.build(raw))
        return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host", "") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return self._send(403, "forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, (ROOT / "pmdash" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/fonts/InterVariable.woff2", "/fonts/GeistMono.woff2"):     # both SIL Open Font License, served locally
            return self._send(200, (ROOT / "pmdash" / "fonts" / path.rsplit("/", 1)[1]).read_bytes(), "font/woff2")
        if path == "/api/data":
            try:
                return self._send(200, json.dumps(load("refresh=1" in self.path)), "application/json")
            except ApiError as e:
                return self._send(502, json.dumps({"error": f"Polymarket US answered {e.status}: {e.body[:200]}"}), "application/json")
            except Exception as e:                                  # network down, bad key format, ...
                return self._send(500, json.dumps({"error": str(e)[:300]}), "application/json")
        self._send(404, "not found", "text/plain")

    def log_message(self, *a):                                      # no request log: nothing account-related on stdout
        pass


def main():
    if "--check" in sys.argv:                                       # fetch once, print the reconciliation only
        d = load(True); c = d["check"]
        print(f"cash computed - cash reported: {c['diff']:+.2f}   markets: {d['summary']['markets']}   as of {d['asof']}")
        for w in d["warnings"] or ["no warnings"]:
            print("  -", w)
        return
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"P&L dashboard: http://127.0.0.1:{PORT}   (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
