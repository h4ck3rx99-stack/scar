"""Local mock of a learning.ccbp.in coding page (problem + Monaco editor that blocks paste and POSTs its contents).
Run it, open http://127.0.0.1:8799/ (Python) or /html, say "Scar, solve this problem", then read editor_<lang>.txt."""
import http.server
import pathlib

HERE = pathlib.Path(__file__).parent
LANG = "python"


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        lang = "html" if self.path.startswith("/html") else LANG
        page = (HERE / ("page_html.html" if lang == "html" else "page.html")).read_text(encoding="utf-8").replace("__LANG__", lang)
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        (HERE / ("editor_" + self.path.strip("/") + ".txt")).write_bytes(self.rfile.read(n))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", 8799), H).serve_forever()
