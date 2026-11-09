"""Press queue of the page server: public queueing is bounded and decides nothing; queue, results and state are bearer-only."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import server

TOKEN = "t" * 40


def call(url, method, path, body=None, headers=None):
    req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Presses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = server.DIR, server.TOKEN
        server.DIR, server.TOKEN = self.tmp.name, TOKEN
        server._presses.clear(); server._press_hits.clear(); server._misses.clear()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.http.server_address[1]
        self.auth = {"Authorization": "Bearer " + TOKEN}
        self.page = "p" * 32
        html = "<html><body><main>x</main><!--STATE--></body></html>"
        self.assertEqual(call(self.url, "PUT", "/api/pages/" + self.page, {"html": html, "expires": time.time() + 600, "decision_n": 2,
                                                                         "state": {"decision": {"open": True}}}, self.auth)[0], 200)

    def tearDown(self):
        self.http.shutdown(); self.http.server_close()
        server.DIR, server.TOKEN = self.old
        self.tmp.cleanup()

    def press(self, choice=0, init="i" * 40, headers=None, page=None):
        return call(self.url, "POST", "/api/press/" + (page or self.page), {"choice": choice, "init_data": init}, headers)

    def test_queue_result_and_auth(self):
        code, body = self.press(1)
        self.assertEqual(code, 200)
        pid = json.loads(body)["id"]
        self.assertEqual(json.loads(call(self.url, "GET", "/api/press/%s/%s" % (self.page, pid))[1]), {"status": "pending"})
        self.assertEqual(call(self.url, "GET", "/api/presses")[0], 401)
        got = json.loads(call(self.url, "GET", "/api/presses", headers=self.auth)[1])["presses"]
        self.assertEqual([(p["id"], p["page_id"], p["choice"], p["init_data"]) for p in got], [(pid, self.page, 1, "i" * 40)])
        self.assertEqual(json.loads(call(self.url, "GET", "/api/presses", headers=self.auth)[1])["presses"], [])   # not redelivered at once
        self.assertEqual(call(self.url, "POST", "/api/presses/%s/result" % pid, {"status": "applied", "label": "B"})[0], 401)
        self.assertEqual(call(self.url, "POST", "/api/presses/%s/result" % pid, {"status": "won"}, self.auth)[0], 400)
        self.assertEqual(call(self.url, "POST", "/api/presses/%s/result" % pid, {"status": "applied", "label": "B", "at": 5}, self.auth)[0], 200)
        self.assertEqual(json.loads(call(self.url, "GET", "/api/press/%s/%s" % (self.page, pid))[1]), {"status": "applied", "label": "B", "at": 5})
        self.assertEqual(server._presses[pid]["init_data"], "")
        self.assertEqual(call(self.url, "GET", "/api/press/%s/%s" % ("q" * 32, pid))[0], 404)       # other page id
        self.assertEqual(call(self.url, "GET", "/api/press/%s/%s" % (self.page, "z" * 24))[0], 404)

    def test_refusals(self):
        self.assertEqual(self.press(2)[0], 409)
        self.assertEqual(self.press(-1)[0], 409)
        self.assertEqual(self.press(True)[0], 400)
        self.assertEqual(self.press(0, "short")[0], 400)
        self.assertEqual(self.press(0, "i" * 5000)[0], 400)
        self.assertEqual(self.press(headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.press(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.press(page="n" * 32)[0], 404)
        for state in ({"decision": {"open": False}}, {"decision": {"open": True}, "decided": {"label": "A"}},
                      {"decision": {"open": True}, "closed": "zu"}, {"decision": {"open": True, "expires": time.time() - 1}},
                      {"decision": {"open": False, "telegram_only": "money"}}):
            self.assertEqual(call(self.url, "PUT", "/api/pages/%s/state" % self.page, {"state": state}, self.auth)[0], 200)
            self.assertEqual(self.press()[0], 409, state)
        self.assertEqual(call(self.url, "PUT", "/api/pages/%s/state" % self.page, {"state": {"decision": {"open": True}}})[0], 401)

    def test_state_is_injected_escaped_and_survives_reupload(self):
        evil = {"decision": {"open": True}, "x": "</script><script>alert(1)</script>"}
        self.assertEqual(call(self.url, "PUT", "/api/pages/%s/state" % self.page, {"state": evil}, self.auth)[0], 200)
        body = call(self.url, "GET", "/p/" + self.page)[1]
        self.assertIn('<script type="application/json" id="st">', body)
        self.assertNotIn("</script><script>alert", body)
        html = "<html><body><main>y</main><!--STATE--></body></html>"
        self.assertEqual(call(self.url, "PUT", "/api/pages/" + self.page, {"html": html, "expires": time.time() + 600}, self.auth)[0], 200)
        self.assertEqual(self.press()[0], 200)                                # state and decision_n kept
        closed = {"decision": {"open": False}, "closed": "zu"}
        call(self.url, "PUT", "/api/pages/%s/state" % self.page, {"state": closed}, self.auth)
        call(self.url, "PUT", "/api/pages/" + self.page, {"html": html, "expires": time.time() + 600, "state": {"decision": {"open": True}}}, self.auth)
        self.assertEqual(self.press()[0], 409)                                # a full upload cannot reopen it

    def test_bounds(self):
        codes = [self.press()[0] for _ in range(7)]
        self.assertEqual(codes, [200] * 5 + [429] * 2)                        # pending per page
        server._presses.clear()
        codes = [self.press()[0] for _ in range(8)]
        self.assertIn(429, codes)                                             # per client


if __name__ == "__main__":
    unittest.main()
