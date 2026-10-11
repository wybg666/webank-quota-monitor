"""Loopback-only HTTP interface with strict Host and CSRF protection."""
from __future__ import annotations
import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from . import __version__
from .engine import now_text
from .notify import dispatch

STATIC = Path(__file__).parent / "static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, port, monitor):
        self.monitor = monitor
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)

class Handler(BaseHTTPRequestHandler):
    server_version = "WebankQuotaMonitor/" + __version__
    sys_version = ""
    def setup(self):
        super().setup()
        self.connection.settimeout(15)
    def log_message(self, *_): pass
    def respond(self, code, value, content_type="application/json; charset=utf-8"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        for name, value in {"Content-Type": content_type, "Content-Length": str(len(body)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
    def target(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            raise ValueError("请求地址无效")
        path = urlsplit(self.path)
        if path.scheme or path.netloc: raise ValueError("请求地址无效")
        return path.path
    def do_GET(self):
        try:
            path = self.target()
            monitor = self.server.monitor
            if path in ASSETS:
                filename, kind = ASSETS[path]
                self.respond(200, (STATIC / filename).read_bytes(), kind)
            elif path == "/api/session":
                self.respond(200, {"ok": True, "token": self.server.token})
            elif path == "/api/status": self.respond(200, monitor.snapshot())
            elif path == "/api/settings": self.respond(200, {"ok": True, "settings": monitor.get_settings()})
            elif path == "/api/health": self.respond(200, {"ok": True, "version": __version__, "demo": monitor.demo, "notifications_allowed": monitor.allow_external})
            else: self.respond(404, {"ok": False, "error": "不存在此资源"})
        except ValueError as exc: self.respond(400, {"ok": False, "error": str(exc)})
        except Exception: self.respond(500, {"ok": False, "error": "本机服务处理失败"})
    def body(self):
        self.target()
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + self.headers.get("Host", ""):
            raise PermissionError()
        if self.headers.get("Sec-Fetch-Site") == "cross-site": raise PermissionError()
        token = self.headers.get("X-Webank-Token", "")
        if not token.isascii() or not hmac.compare_digest(token, self.server.token): raise PermissionError()
        if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
            raise ValueError("只接受 JSON 请求")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1: raise ValueError("请求长度无效")
        length = int(lengths[0])
        if not 0 < length <= 32768: raise ValueError("请求长度无效")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict): raise ValueError("请求内容须为对象")
        return value
    def do_POST(self):
        try:
            value = self.body()
            path = urlsplit(self.path).path
            monitor = self.server.monitor
            if path == "/api/settings":
                saved = monitor.save_settings(value.get("settings", value))
                self.respond(200, {"ok": True, "settings": saved, "message": "设置已保存；秘密字段不会回显"})
            elif path == "/api/query":
                accepted = monitor.query_once()
                self.respond(202 if accepted else 409, {"ok": accepted, "error": "请先停止监控或等待当前查询结束" if not accepted else "", "message": "只查询，不发送通知"})
            elif path == "/api/start":
                accepted = monitor.start()
                self.respond(202 if accepted else 409, {"ok": accepted, "error": "已有监控或查询进行中" if not accepted else ""})
            elif path == "/api/stop":
                monitor.stop()
                self.respond(200, {"ok": True})
            elif path == "/api/known":
                snapshot = monitor.snapshot()
                if not snapshot["data"]: raise ValueError("请先成功查询产品列表")
                settings = monitor.save_settings({"known_products": [p["name"] for p in snapshot["data"]["products"]]})
                self.respond(200, {"ok": True, "settings": settings, "message": "已将当前页产品保存为新品判断基准"})
            elif path == "/api/test-notifications":
                if not monitor.allow_external:
                    self.respond(403, {"ok": False, "error": "未允许通知外发；演示模式也禁止通知"})
                    return
                with monitor.lock: raw = json.loads(json.dumps(monitor.settings["notifications"]))
                result = dispatch({"name": "测试消息（非产品）", "available_quota": None, "sale_status": "", "kind": "手动测试", "timestamp": now_text()}, raw, allow_external=True)
                self.respond(200, {"ok": True, "delivery": result})
            else: self.respond(404, {"ok": False, "error": "不存在此操作"})
        except PermissionError: self.respond(403, {"ok": False, "error": "本机页面令牌或来源校验失败，请刷新页面"})
        except (ValueError, UnicodeError, TypeError) as exc: self.respond(400, {"ok": False, "error": str(exc)[:160] if not isinstance(exc, json.JSONDecodeError) else "JSON 格式错误"})
        except Exception: self.respond(500, {"ok": False, "error": "操作失败，未返回敏感细节"})
    def unsupported(self):
        self.close_connection = True
        self.respond(405, {"ok": False, "error": "不支持此方法"})
    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = unsupported
