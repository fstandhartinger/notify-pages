"""notify-pages: private unlisted copy pages for Telegram notification cards (stdlib only)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_ui import decorate, valid

import hmac
import json
import secrets
import math
import os
import re
import socket
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
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

# Decision presses (9 Nov 2026). The page only QUEUES a press together with the Telegram Mini App initData the
# browser got from Telegram. This server holds no Telegram secret and decides nothing: Sandy fetches the queue
# with the bearer token, verifies the initData signature and the open ask, applies the press once in the same
# ledger as a Telegram button tap, and reports the outcome back. Presses live in memory only.
PRESS_TTL, PRESS_DONE_TTL, PRESS_REDELIVER = 180, 120, 20
PRESS_IP_LIMIT, PRESS_PAGE_PENDING, PRESS_TOTAL = 12, 5, 300
PRESS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,32}$")
PRESS_STATUSES = ("applied", "already", "closed", "denied", "submitted", "retry")
_presses = {}
_press_hits = {}
_press_cv = threading.Condition()


def clean_state(value):
    """Small JSON object shown to the page script (open / decided / closed); None when unusable."""
    if not isinstance(value, dict):
        return None
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return value if len(raw) <= 2048 else None


def with_state(page):
    source = decorate(page)
    state = clean_state(page.get("state"))
    if state is None or "<!--STATE-->" not in source:
        return source
    raw = json.dumps(state, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return source.replace("<!--STATE-->", '<script type="application/json" id="st">%s</script>' % raw, 1)


def press_open(page):
    state = page.get("state") if isinstance(page.get("state"), dict) else {}
    decision = state.get("decision") if isinstance(state.get("decision"), dict) else {}
    n = page.get("decision_n")
    if not isinstance(n, int) or state.get("decided") or state.get("closed") or decision.get("open") is not True:
        return 0
    expires = decision.get("expires")
    if isinstance(expires, (int, float)) and time.time() > expires:
        return 0
    return n


def _press_gc(now):
    for key in [k for k, v in _presses.items()
                if now - v["ts"] > PRESS_TTL or (v["status"] != "pending" and now - v.get("done", now) > PRESS_DONE_TTL)]:
        del _presses[key]
    if len(_press_hits) > 5000:
        for key in [k for k, v in _press_hits.items() if now - v[-1] >= MISS_WINDOW]:
            del _press_hits[key]
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
    timeout = 15
    sys_version = ""

    def log_message(self, fmt, *args):
        # never log page ids (they are the secret): only method, route kind and status
        pass

    def _client(self):
        # the proxy appends the real peer last; earlier entries are client-controlled
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[-1].strip()

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
                for key in [k for k, v in _misses.items() if now - v[-1] >= MISS_WINDOW]:
                    del _misses[key]
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
                return self._send(200, with_state(page).encode("utf-8"), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": PAGE_CSP})
        m = re.match(r"^/api/press/([^/]+)/([^/]+)$", path)
        if m and ID_RE.match(m.group(1)) and PRESS_ID_RE.match(m.group(2)):
            with _press_cv:
                press = _presses.get(m.group(2))
                if press and press["page_id"] == m.group(1):
                    out = {"status": press["status"]}
                    out.update({k: press[k] for k in ("label", "message", "at") if press.get(k) is not None})
                    return self._send(200, json.dumps(out).encode(), "application/json")
            if _load(m.group(1)):
                # a reader of a real page polling a press this server forgot (restart): not a guessing attempt
                return self._send(404, b'{"status":"unknown"}', "application/json")
            return self._miss()
        if path == "/api/presses":
            if not self._authorized():
                return self._send(401, b"unauthorized")
            try:
                wait = min(25.0, max(0.0, float(re.search(r"[?&]wait=([0-9.]+)", self.path).group(1))))
            except (AttributeError, ValueError):
                wait = 0.0
            deadline = time.time() + wait
            with _press_cv:
                while True:
                    now = time.time()
                    _press_gc(now)
                    due = [(k, v) for k, v in _presses.items()
                           if v["status"] == "pending" and now - v.get("delivered", 0) >= PRESS_REDELIVER]
                    if due or now >= deadline:
                        break
                    _press_cv.wait(min(5.0, deadline - now))
                for _k, v in due:
                    v["delivered"] = now
                out = [{"id": k, "page_id": v["page_id"], "choice": v["choice"], "init_data": v["init_data"], "ts": v["ts"]}
                       for k, v in due]
            return self._send(200, json.dumps({"presses": out}).encode(), "application/json")
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
        sm = re.match(r"^/api/pages/([^/]+)/state$", self.path)
        if sm and ID_RE.match(sm.group(1)):
            return self._put_state(sm.group(1))
        if not m or not ID_RE.match(m.group(1)):
            return self._send(400, b"bad id")
        try:
            data = self._body()
            html, expires = data["html"], float(data["expires"])
            assert isinstance(html, str) and html and math.isfinite(expires)
        except Exception:
            return self._send(400, b"bad body")
        expires = min(expires, time.time() + MAX_AGE)
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        with _lock:
            self._store(m.group(1), data, html, expires)
        self._send(200, b"stored")

    def _store(self, page_id, data, html, expires):
        m = re.match(r"^(.*)$", page_id)
        tmp = _path(m.group(1)) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            metadata = {"html": html, "expires": expires}
            if valid(data.get('aid')):
                metadata['aid'] = data['aid']
            if isinstance(data.get('linked_ids'), list):
                metadata['linked_ids'] = list(dict.fromkeys(i for i in data['linked_ids'] if valid(i)))[:20]
            n = data.get('decision_n')
            if isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= 5:
                metadata['decision_n'] = n
            if clean_state(data.get('state')) is not None:
                metadata['state'] = data['state']
            previous = _load(m.group(1)) or {}
            old_state = previous.get('state') if isinstance(previous.get('state'), dict) else {}
            for key in ('decision_n', 'state'):
                # a later upload without these (footer update, resync of an older record) never reopens or forgets a
                # decision, and a full upload never replaces a decided/closed state (only PUT .../state may)
                if key in previous and (key not in metadata or (key == 'state' and (old_state.get('decided') or old_state.get('closed')))):
                    metadata[key] = previous[key]
            json.dump(metadata, fh)
        os.replace(tmp, _path(m.group(1)))

    def _put_state(self, page_id):
        try:
            state = clean_state(self._body()["state"])
            assert state is not None
        except Exception:
            return self._send(400, b"bad body")
        with _lock:
            page = _load(page_id)
            if not page:
                return self._send(404, b"no page")
            page["state"] = state
            tmp = _path(page_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(page, fh)
            os.replace(tmp, _path(page_id))
        self._send(200, b"stored")

    def _press(self, page_id):
        # public: anyone holding the page link can queue a press, so it is bounded and carries no authority
        origin = self.headers.get("Origin")
        host = self.headers.get("Host") or ""
        if origin is not None and origin not in ("https://" + host, "http://" + host):
            return self._send(403, b"bad origin")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._send(415, b"json only")
        now, ip = time.time(), self._client()
        with _press_cv:
            hits = [t for t in _press_hits.get(ip, []) if now - t < MISS_WINDOW] + [now]
            _press_hits[ip] = hits
        if len(hits) > PRESS_IP_LIMIT:
            return self._send(429, b"Too many requests")
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size <= 0 or size > 8192:
                raise ValueError("size")
            data = json.loads(self.rfile.read(size))
            choice, init_data = data["choice"], data["init_data"]
            assert isinstance(choice, int) and not isinstance(choice, bool)
            assert isinstance(init_data, str) and 20 <= len(init_data) <= 4096
        except Exception:
            return self._send(400, b"bad body")
        page = _load(page_id)
        if not page:
            return self._miss()
        n = press_open(page)
        if not 0 <= choice < n:
            return self._send(409, b"closed")
        with _press_cv:
            _press_gc(now)
            pending = [v for v in _presses.values() if v["status"] == "pending"]
            if len(pending) >= PRESS_TOTAL or len(_presses) >= 4 * PRESS_TOTAL or sum(1 for v in pending if v["page_id"] == page_id) >= PRESS_PAGE_PENDING:
                return self._send(429, b"Too many requests")
            press_id = secrets.token_urlsafe(18)
            _presses[press_id] = {"page_id": page_id, "choice": choice, "init_data": init_data, "ts": now, "status": "pending"}
            _press_cv.notify_all()
        self._send(200, json.dumps({"id": press_id}).encode(), "application/json")

    def _press_result(self, press_id):
        try:
            data = self._body()
            status = data["status"]
            assert status in PRESS_STATUSES
        except Exception:
            return self._send(400, b"bad body")
        with _press_cv:
            press = _presses.get(press_id)
            if not press:
                return self._send(404, b"no press")
            press["status"] = status
            press["done"] = time.time()
            press["init_data"] = ""
            for key, limit in (("label", 64), ("message", 240)):
                if isinstance(data.get(key), str):
                    press[key] = data[key][:limit]
            if isinstance(data.get("at"), (int, float)) and not isinstance(data.get("at"), bool):
                press["at"] = data["at"]
        self._send(200, b"stored")

    def do_POST(self):
        # POST /api/missing {"ids": [...]} -> ids not stored (for Sandy's hourly resync)
        m = re.match(r"^/api/press/([^/]+)$", self.path)
        if m and ID_RE.match(m.group(1)):
            return self._press(m.group(1))
        m = re.match(r"^/api/presses/([^/]+)/result$", self.path)
        if m and PRESS_ID_RE.match(m.group(1)):
            if not self._authorized():
                return self._send(401, b"unauthorized")
            return self._press_result(m.group(1))
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


class DualStack(ThreadingHTTPServer):
    # health checks probe "localhost", which resolves to ::1 first
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


if __name__ == "__main__":
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    threading.Thread(target=purge, daemon=True).start()
    DualStack(("::", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
