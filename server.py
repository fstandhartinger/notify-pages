"""notify-pages: private unlisted copy pages for Telegram notification cards (stdlib only)."""
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("NOTIFY_PAGES_TOKEN", "")
DIR = os.environ.get("PAGES_DIR", "/tmp/pages")
MAX_BODY = 2 * 1024 * 1024
MAX_AGE = 31 * 86400
ID_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
MISS_LIMIT, MISS_WINDOW = 30, 600
_misses = {}
_lock = threading.Lock()

BASE_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
NOT_FOUND = b"<!doctype html><meta charset=utf-8><meta name=robots content=noindex><title>Not found</title><p>Not found or expired.</p>"


def _path(page_id):
    return os.path.join(DIR, page_id + ".json")


def _load(page_id):
    try:
        with open(_path(page_id), encoding="utf-8") as fh:
            page = json.load(fh)
    except (OSError, ValueError):
        return None
    if page.get("expires", 0) < time.time():
        try:
            os.remove(_path(page_id))
        except OSError:
            pass
        return None
    return page


def purge():
    while True:
        try:
            for name in os.listdir(DIR):
                if name.endswith(".json"):
                    _load(name[:-5])
        except OSError:
            pass
        time.sleep(3600)


class Handler(BaseHTTPRequestHandler):
    server_version = "pages"
    sys_version = ""

    def log_message(self, fmt, *args):
        # never log page ids (they are the secret): only method, route kind and status
        pass

    def _client(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        for k, v in {**BASE_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorized(self):
        got = self.headers.get("Authorization", "")
        return bool(TOKEN) and len(TOKEN) >= 32 and hmac.compare_digest(got.encode(), ("Bearer " + TOKEN).encode())

    def _miss(self):
        now, ip = time.time(), self._client()
        with _lock:
            hits = [t for t in _misses.get(ip, []) if now - t < MISS_WINDOW] + [now]
            _misses[ip] = hits
            if len(_misses) > 5000:
                _misses.clear()
        if len(hits) > MISS_LIMIT:
            return self._send(429, b"Too many requests")
        self._send(404, NOT_FOUND, "text/html; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, b"ok")
        if path == "/robots.txt":
            return self._send(200, b"User-agent: *\nDisallow: /\n")
        m = re.match(r"^/p/([^/]+)/?$", path)
        if m and ID_RE.match(m.group(1)):
            page = _load(m.group(1))
            if page:
                return self._send(200, page["html"].encode("utf-8"), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": PAGE_CSP})
        return self._miss()

    def _body(self):
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0 or size > MAX_BODY:
            raise ValueError("size")
        return json.loads(self.rfile.read(size))

    def do_PUT(self):
        m = re.match(r"^/api/pages/([^/]+)$", self.path)
        if not self._authorized():
            return self._send(401, b"unauthorized")
        if not m or not ID_RE.match(m.group(1)):
            return self._send(400, b"bad id")
        try:
            data = self._body()
            html, expires = data["html"], float(data["expires"])
            assert isinstance(html, str) and html
        except Exception:
            return self._send(400, b"bad body")
        expires = min(expires, time.time() + MAX_AGE)
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        tmp = _path(m.group(1)) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"html": html, "expires": expires}, fh)
        os.replace(tmp, _path(m.group(1)))
        self._send(200, b"stored")

    def do_POST(self):
        # POST /api/missing {"ids": [...]} -> ids not stored (for Sandy's hourly resync)
        if self.path != "/api/missing":
            return self._send(404, b"")
        if not self._authorized():
            return self._send(401, b"unauthorized")
        try:
            ids = [i for i in self._body()["ids"] if isinstance(i, str) and ID_RE.match(i)][:5000]
        except Exception:
            return self._send(400, b"bad body")
        missing = [i for i in ids if not os.path.isfile(_path(i))]
        self._send(200, json.dumps({"missing": missing}).encode(), "application/json")

    def do_DELETE(self):
        m = re.match(r"^/api/pages/([^/]+)$", self.path)
        if not self._authorized():
            return self._send(401, b"unauthorized")
        if m and ID_RE.match(m.group(1)):
            try:
                os.remove(_path(m.group(1)))
            except OSError:
                pass
        self._send(200, b"deleted")


if __name__ == "__main__":
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    threading.Thread(target=purge, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
