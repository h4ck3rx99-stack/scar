"""Local mock of a learning.ccbp.in coding page, for testing Scar's "solve this problem" end to end.

  python bench/editor_mock/server.py          then open http://127.0.0.1:8799/     (Python, long scrolling problem)
                                                         or http://127.0.0.1:8799/web  (HTML / CSS / JS tabs)
The Monaco editor blocks paste (like CCBP). Run executes the code on the sample input; Submit runs hidden test cases.
Every change and every Run / Submit is saved next to this file (editor_*.txt, events.log) so a test can check them."""
import http.server
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
PY_TESTS = [("3\nRavi 92\nAsha 67\nJohn 40\n", "Ravi: A\nAsha: C\nJohn: F\nTop: Ravi"),
            ("2\nZed 80\nAmy 80\n", "Zed: B\nAmy: B\nTop: Zed"),
            ("2\nAmy 80\nZed 80\n", "Amy: B\nZed: B\nTop: Amy"),
            ("1\nKim 50\n", "Kim: C\nTop: Kim")]


def log(line):
    with (HERE / "events.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_py(code, stdin):
    r = subprocess.run([sys.executable, "-c", code], input=stdin, capture_output=True, text=True, timeout=10)
    return (r.stdout + r.stderr).strip()


def web_checks(html, css):
    import re
    checks = [bool(re.search(r'<div[^>]*class="[^"]*\bcard\b', html)),
              bool(re.search(r"<h1[^>]*>\s*Asha Rao\s*</h1>", html)),
              bool(re.search(r'<p[^>]*class="[^"]*\brole\b[^"]*"[^>]*>\s*Frontend Developer\s*</p>', html)),
              bool(re.search(r"<button[^>]*>\s*Follow\s*</button>", html)),
              bool(re.search(r"\.card\s*\{[^}]*background-color\s*:\s*(?:#e8f0fe|#E8F0FE)", css)),
              bool(re.search(r"\.role\s*\{[^}]*color\s*:\s*(?:#6b7280|#6B7280)", css))]
    return sum(checks), len(checks)


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        page = "page_web.html" if self.path.startswith("/web") else "page.html"
        self._send((HERE / page).read_text(encoding="utf-8"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        kind = self.path.strip("/")
        if kind == "save":
            (HERE / f"editor_{body['tab']}.txt").write_text(body["code"], encoding="utf-8")
            return self._send("{}", "application/json")
        if kind == "run":
            out = run_py(body["code"], PY_TESTS[0][0])
            log(f"RUN -> {out[:200]!r}")
            return self._send(json.dumps({"output": out}), "application/json")
        if kind == "submit":
            if body.get("web"):
                ok, total = web_checks(body.get("html", ""), body.get("css", ""))
            else:
                ok = sum(run_py(body["code"], i) == o for i, o in PY_TESTS)
                total = len(PY_TESTS)
            verdict = "All test cases passed" if ok == total else f"{ok}/{total} test cases passed"
            log(f"SUBMIT -> {verdict}")
            return self._send(json.dumps({"verdict": verdict}), "application/json")
        self._send("{}", "application/json", 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    for f in HERE.glob("editor_*.txt"):
        f.unlink()
    (HERE / "events.log").write_text("", encoding="utf-8")
    http.server.ThreadingHTTPServer(("127.0.0.1", 8799), H).serve_forever()
