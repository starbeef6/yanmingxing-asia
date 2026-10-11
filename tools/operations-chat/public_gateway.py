#!/usr/bin/env python3
"""Strict CORS gateway from approved website origins to the local chat API."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.parse import urlsplit


LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8788
BACKEND_URL = "http://127.0.0.1:8787/api/chat"
BACKEND_HEALTH_URL = "http://127.0.0.1:8787/healthz"
BACKEND_HOST = "127.0.0.1:8787"
BACKEND_ORIGIN = "http://127.0.0.1:8787"
MAX_BODY = 24 * 1024
MAX_RESPONSE = 64 * 1024
FORWARD_TIMEOUT = 50
HEALTH_TIMEOUT = 4
ALLOWED_ORIGINS = frozenset({
    "http://yanmingxing.asia",
    "https://yanmingxing.asia",
    "http://www.yanmingxing.asia",
    "https://www.yanmingxing.asia",
    "http://127.0.0.1:8787",
    "https://127.0.0.1:8787",
    "http://localhost:8787",
    "https://localhost:8787",
    "https://starbeef6.github.io",
})
PUBLIC_POLICY = {
    "model": "gpt-6-luna",
    "reasoning": {"effort": "low", "mode": "standard"},
    "service_tier": "default",
    "store": False,
    "max_output_tokens": 500,
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_backend(request, timeout):
    # Bypass ambient proxy settings and refuse redirects so all traffic remains
    # on the one fixed loopback destination.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _origin_allowed(origin):
    return isinstance(origin, str) and origin in ALLOWED_ORIGINS


def _safe_reply(value):
    if not isinstance(value, dict):
        return None
    reply = value.get("reply")
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 8000:
        return None
    raw_usage = value.get("usage")
    usage = {}
    if isinstance(raw_usage, dict):
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            count = raw_usage.get(name)
            if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 10_000_000:
                usage[name] = count
    return {"reply": reply.strip(), "policy": PUBLIC_POLICY, "usage": usage}


def _health():
    request = Request(BACKEND_HEALTH_URL, headers={"Accept": "application/json", "Host": BACKEND_HOST}, method="GET")
    try:
        with _open_backend(request, timeout=HEALTH_TIMEOUT) as response:
            if response.status != 200:
                return False
            raw = response.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            return False
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("ready") is not True:
            return False
        policy = value.get("policy")
        return policy == PUBLIC_POLICY
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError):
        return False


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "OperationsChatGateway/1.0"
    sys_version = ""

    def log_message(self, _format, *args):
        # Never log paths, headers, body text, or upstream details.
        return

    def _send(self, status, payload, origin=None, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin")
        if origin and _origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _path(self):
        return urlsplit(self.path).path

    def do_GET(self):
        if self._path() != "/healthz":
            self._send(404, {"error": "Not found."})
            return
        origin = self.headers.get("Origin")
        if origin and not _origin_allowed(origin):
            self._send(403, {"error": "Origin not allowed."})
            return
        # This endpoint is intentionally public for local readiness checks. A
        # supplied browser Origin must still be in the allowlist.
        self._send(200, {"ready": _health(), "policy": PUBLIC_POLICY}, origin)

    def do_OPTIONS(self):
        if self._path() != "/api/chat":
            self._send(404, {"error": "Not found."})
            return
        origin = self.headers.get("Origin")
        method = self.headers.get("Access-Control-Request-Method", "")
        requested_headers = self.headers.get("Access-Control-Request-Headers", "")
        requested = [item.strip().lower() for item in requested_headers.split(",") if item.strip()]
        if not _origin_allowed(origin) or method != "POST" or any(item != "content-type" for item in requested):
            self._send(403, {"error": "Preflight rejected."})
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_POST(self):
        if self._path() != "/api/chat":
            self._send(404, {"error": "Not found."})
            return
        origin = self.headers.get("Origin")
        if not _origin_allowed(origin):
            self._send(403, {"error": "Origin not allowed."})
            return
        if self.headers.get_content_type() != "application/json":
            self._send(415, {"error": "JSON required."}, origin)
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            self._send(413, {"error": "Request too large."}, origin)
            return
        body = self.rfile.read(size)
        if len(body) != size:
            self._send(400, {"error": "Invalid request."}, origin)
            return
        try:
            parsed = json.loads(body.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            self._send(400, {"error": "Invalid request."}, origin)
            return

        # Client-supplied Host, forwarding headers, and URLs are never copied.
        request = Request(
            BACKEND_URL,
            data=body,
            headers={
                "Host": BACKEND_HOST,
                "Origin": BACKEND_ORIGIN,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with _open_backend(request, timeout=FORWARD_TIMEOUT) as response:
                status = response.status
                if status < 200 or status >= 300:
                    self._send(self._mapped_error_status(status), {"error": "Chat service unavailable."}, origin)
                    return
                if "application/json" not in (response.headers.get("Content-Type", "").lower()):
                    self._send(502, {"error": "Invalid chat response."}, origin)
                    return
                result = response.read(MAX_RESPONSE + 1)
            if len(result) > MAX_RESPONSE:
                self._send(502, {"error": "Invalid chat response."}, origin)
                return
            safe = _safe_reply(json.loads(result.decode("utf-8")))
            if safe is None:
                self._send(502, {"error": "Invalid chat response."}, origin)
                return
            self._send(200, safe, origin)
        except HTTPError as error:
            # Do not inspect the response body; a redirect is also never followed.
            status = self._mapped_error_status(error.code)
            self._send(status, {"error": "Chat service unavailable."}, origin)
        except (URLError, OSError, TimeoutError, ValueError, UnicodeError):
            self._send(502, {"error": "Chat service unavailable."}, origin)

    @staticmethod
    def _mapped_error_status(upstream_status):
        if upstream_status in {400, 413, 415, 429, 503}:
            return upstream_status
        return 502

    def do_PUT(self):
        self._send(404, {"error": "Not found."})

    do_PATCH = do_PUT
    do_DELETE = do_PUT
    do_CONNECT = do_PUT
    do_PROPFIND = do_PUT
    do_MKCOL = do_PUT
    do_COPY = do_PUT
    do_MOVE = do_PUT
    do_LOCK = do_PUT
    do_UNLOCK = do_PUT
    def do_HEAD(self):
        self._send(404, {"error": "Not found."})

    do_TRACE = do_PUT


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), GatewayHandler)
    server.daemon_threads = True
    print(f"Public chat gateway listening on http://{LISTEN_HOST}:{LISTEN_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
