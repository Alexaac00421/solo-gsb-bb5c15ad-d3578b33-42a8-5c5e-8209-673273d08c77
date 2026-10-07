"""零依赖 HTTP 服务（标准库 http.server）。

- JSON API 前缀 /api；
- 其余路径返回静态前端（static/index.html、app.js、style.css）；
- 启动时幂等初始化 SQLite 结构。
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, service

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise service.ServiceError("请求体不是合法 JSON", status=400, code="bad_json")
    if not isinstance(data, dict):
        raise service.ServiceError("请求体必须为 JSON 对象", status=422, code="bad_json")
    return data


def _serve_static(handler, path):
    if path in ("", "/"):
        path = "/index.html"
    rel = os.path.normpath(path.lstrip("/"))
    full = os.path.join(STATIC_DIR, rel)
    if not full.startswith(STATIC_DIR + os.sep) or not os.path.isfile(full):
        full = os.path.join(STATIC_DIR, "index.html")  # 前端 hash 路由兜底
    ext = os.path.splitext(full)[1]
    with open(full, "rb") as fh:
        body = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type",
                        _CONTENT_TYPES.get(ext, "application/octet-stream"))
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClauseDraft/1.0"

    def log_message(self, fmt, *args):  # 简洁访问日志
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    # 路由: (method, regex) -> handler(self, match, data)
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        try:
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/"):
                if method == "GET":
                    _serve_static(self, path)
                    return
                raise service.ServiceError("Not Found", status=404, code="not_found")

            api = path[len("/api/"):]
            data = _read_json(self) if method == "POST" else {}
            route = self._match_route(method, api)
            if route is None:
                raise service.ServiceError("接口不存在", status=404, code="not_found")
            fn, match = route
            result, status = fn(self, match, data)
            _json_response(self, result, status)
        except service.ServiceError as exc:
            _json_response(self, {"error": exc.code, "message": exc.message},
                           exc.status)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免连接挂起
            self.send_error(500, "Internal Server Error")
            print(f"[error] {type(exc).__name__}: {exc}")

    def _match_route(self, method, api):
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            match = pattern.fullmatch(api)
            if match:
                return fn, match
        return None

    # ------------------------------------------------------------ handlers
    def _h_drafts(self, match, data):
        if self.command == "POST":
            return service.create_draft(data), 201
        return {"drafts": service.list_drafts()}, 200

    def _h_draft(self, match, data):
        return service.get_draft(int(match["id"])), 200

    def _h_proposals(self, match, data):
        return service.submit_proposal(int(match["id"]), data), 201

    def _h_resolve(self, match, data):
        return service.resolve_proposal(
            int(match["id"]), int(match["pid"]), data), 200

    def _h_publish(self, match, data):
        return service.publish_draft(int(match["id"]), data), 201

    def _h_diff(self, match, data):
        qs = self._query()
        try:
            frm = int(qs.get("from", "0"))
            to = int(qs.get("to", ""))
        except ValueError:
            raise service.ServiceError("from/to 必须为整数版本号",
                                       status=422, code="invalid_field")
        return service.get_diff(int(match["id"]), frm, to), 200

    def _h_revisions(self, match, data):
        return {"revisions": service.get_revisions(int(match["id"]))}, 200

    def _h_snapshots(self, match, data):
        return {"snapshots": service.list_snapshots(int(match["id"]))}, 200

    def _h_snapshot(self, match, data):
        return service.get_snapshot(int(match["id"]), int(match["sid"])), 200

    def _query(self):
        if "?" not in self.path:
            return {}
        from urllib.parse import parse_qs
        parsed = parse_qs(self.path.split("?", 1)[1])
        return {k: v[-1] for k, v in parsed.items()}


ROUTES = [
    ("GET", re.compile(r"drafts"), Handler._h_drafts),
    ("POST", re.compile(r"drafts"), Handler._h_drafts),
    ("GET", re.compile(r"drafts/(?P<id>\d+)"), Handler._h_draft),
    ("POST", re.compile(r"drafts/(?P<id>\d+)/proposals"), Handler._h_proposals),
    ("POST", re.compile(r"drafts/(?P<id>\d+)/proposals/(?P<pid>\d+)/resolve"),
        Handler._h_resolve),
    ("POST", re.compile(r"drafts/(?P<id>\d+)/publish"), Handler._h_publish),
    ("GET", re.compile(r"drafts/(?P<id>\d+)/diff"), Handler._h_diff),
    ("GET", re.compile(r"drafts/(?P<id>\d+)/revisions"), Handler._h_revisions),
    ("GET", re.compile(r"drafts/(?P<id>\d+)/snapshots"), Handler._h_snapshots),
    ("GET", re.compile(r"drafts/(?P<id>\d+)/snapshots/(?P<sid>\d+)"),
        Handler._h_snapshot),
]


def create_server(host=None, port=None):
    db.init_db()
    host = host or os.environ.get("CLAUSE_HOST", "0.0.0.0")
    port = int(port or os.environ.get("CLAUSE_PORT", "8000"))
    return ThreadingHTTPServer((host, port), Handler)


def main():
    server = create_server()
    host, port = server.server_address[:2]
    print(f"条款草案服务已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
