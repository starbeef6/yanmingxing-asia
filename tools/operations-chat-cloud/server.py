#!/usr/bin/env python3
"""HTTPS request relay for the existing private, outbound-only Luna worker.

TLS terminates at Cloud Run. No model credential or configurable forwarding URL
belongs in this service; its authenticated worker pulls one bounded job at a time.
Run this service with one Cloud Run instance so the ephemeral queue is coherent.
"""
from __future__ import annotations

import datetime as dt
from collections import OrderedDict
import hmac
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
MAX_BODY = 24 * 1024
MAX_RESULT = 64 * 1024
MAX_MESSAGE = 2000
MAX_HISTORY = 4
JOB_TIMEOUT = 70
# The worker can spend up to 45 seconds waiting for the fixed local backend.
HEARTBEAT_TIMEOUT = 60
PER_MINUTE = 12
PER_DAY = 100
RESULT_TTL = 180
MAX_CACHED_RESULTS = 32
POLICY = {
    "model": "gpt-6-luna",
    "reasoning": {"effort": "low", "mode": "standard"},
    "service_tier": "default",
    "store": False,
    "max_output_tokens": 500,
}
ALLOWED_ORIGINS = frozenset({
    "https://yanmingxing.asia", "https://www.yanmingxing.asia",
    "http://yanmingxing.asia", "http://www.yanmingxing.asia",
    "https://starbeef6.github.io",
})
ALLOWED_HEADERS = frozenset({
    "content-type", "cache-control", "pragma", "accept", "accept-language", "content-language",
})
ERRORS = {
    400: "请求内容无效。", 413: "请求内容过大。", 415: "请求格式无效。",
    429: "熊大爷正在回复，或当前请求次数已达上限，请稍后再试。",
    502: "模型暂时无法回复，请稍后重试。",
    503: "对话服务暂时离线，请稍后重试。",
    504: "对话请求超时，请稍后重试。",
}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def diagnostic(code):
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    print(timestamp + " operations cloud relay: " + code, flush=True)


def valid_worker_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None


def _valid_policy(value):
    return (
        isinstance(value, dict) and value == POLICY
        and value.get("store") is False
        and type(value.get("max_output_tokens")) is int
    )


def clean_request(value):
    if not isinstance(value, dict):
        raise ValueError
    for field in ("model", "reasoning", "service_tier", "max_output_tokens"):
        if field in value and (value[field] != POLICY[field] or (
            field == "max_output_tokens" and type(value[field]) is not int
        )):
            raise ValueError
    if "store" in value and value["store"] is not False:
        raise ValueError
    if "reasoning_effort" in value and value["reasoning_effort"] != "low":
        raise ValueError
    if "reasoning_mode" in value and value["reasoning_mode"] != "standard":
        raise ValueError
    message = value.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
        raise ValueError
    message = message.strip()
    history = []
    if isinstance(value.get("messages"), list):
        for item in value["messages"]:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            content = content.strip()[:MAX_MESSAGE]
            if item["role"] == "user" and content == message:
                continue
            history.append({"role": item["role"], "content": content})
    context = value.get("context")
    if not isinstance(context, dict):
        context = {}
    # JSON's depth can otherwise exceed Python's stack. The local backend applies
    # the authoritative semantic context allowlist after this transport bound.
    def check_depth(item, depth=0):
        if depth > 12:
            raise ValueError
        if isinstance(item, dict):
            for child in item.values():
                check_depth(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check_depth(child, depth + 1)
    check_depth(context)
    result = {"message": message, "messages": history[-MAX_HISTORY:], "context": context, **POLICY}
    if len(json_bytes(result)) > MAX_BODY:
        raise ValueError
    return result


def clean_result(status, body):
    if type(status) is not int or not isinstance(body, dict):
        raise ValueError
    if status == 200:
        reply = body.get("reply")
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 8000 or not _valid_policy(body.get("policy")):
            raise ValueError
        usage = {}
        if isinstance(body.get("usage"), dict):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                count = body["usage"].get(key)
                if type(count) is int and 0 <= count <= 10_000_000:
                    usage[key] = count
        return status, {"reply": reply.strip(), "policy": POLICY, "usage": usage}
    if status not in ERRORS:
        raise ValueError
    # Even an authenticated worker must not publish raw proxy errors or secrets.
    return status, {"error": ERRORS[status]}


class RelayState:
    def __init__(self, worker_token, *, job_timeout=JOB_TIMEOUT, heartbeat_timeout=HEARTBEAT_TIMEOUT):
        self.worker_token = worker_token
        self.job_timeout = job_timeout
        self.heartbeat_timeout = heartbeat_timeout
        self.lock = threading.Lock()
        self.last_heartbeat = None
        self.active = None
        self.daily_date = None
        self.daily_count = 0
        self.minute_stamps = []
        self.completed = OrderedDict()

    def authenticated(self, authorization):
        if not self.worker_token or not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return False
        return hmac.compare_digest(authorization[7:].encode("utf-8"), self.worker_token.encode("utf-8"))

    def _ready(self, now):
        return bool(self.worker_token and self.last_heartbeat is not None and now - self.last_heartbeat < self.heartbeat_timeout)

    def ready(self):
        with self.lock:
            return self._ready(time.monotonic())

    def _expire(self, now):
        for identifier, (completed_at, _owner) in list(self.completed.items()):
            if now - completed_at >= RESULT_TTL:
                self.completed.pop(identifier, None)
        if self.active and now >= self.active["deadline"]:
            expired = self.active
            self.active = None
            expired["result"] = (504, {"error": ERRORS[504]})
            expired["event"].set()
            diagnostic("job_expired")

    def enqueue(self, payload):
        with self.lock:
            now = time.monotonic()
            self._expire(now)
            if not self._ready(now):
                return 503, None
            if self.active is not None:
                return 429, None
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
            if self.daily_date != today:
                self.daily_date, self.daily_count = today, 0
            self.minute_stamps = [stamp for stamp in self.minute_stamps if now - stamp < 60]
            if self.daily_count >= PER_DAY or len(self.minute_stamps) >= PER_MINUTE:
                return 429, None
            self.daily_count += 1
            self.minute_stamps.append(now)
            job = {
                "id": uuid.uuid4().hex, "payload": payload, "deadline": now + self.job_timeout,
                "claimed": False, "owner": None, "protocol": 1,
                "event": threading.Event(), "result": None,
            }
            self.active = job
            return 200, job

    def pull(self, *, protocol=1, worker_id=None):
        with self.lock:
            now = time.monotonic()
            self.last_heartbeat = now
            self._expire(now)
            if self.active is None or self.active["claimed"]:
                return {"job": None}
            if protocol == 2:
                if not valid_worker_id(worker_id):
                    raise ValueError
                if self.active["owner"] not in (None, worker_id):
                    return {"job": None}
                self.active["owner"] = worker_id
                self.active["protocol"] = 2
                # Delivery is repeatable until this same worker acknowledges
                # start. No lease may hand an already-started model call away.
                return {"job": {"id": self.active["id"], "payload": self.active["payload"], "requires_start": True}}
            if self.active["owner"] is not None:
                return {"job": None}
            self.active["claimed"] = True
            return {"job": {"id": self.active["id"], "payload": self.active["payload"]}}

    def start(self, identifier, worker_id):
        with self.lock:
            now = time.monotonic()
            self.last_heartbeat = now
            self._expire(now)
            if (not self.active or self.active["id"] != identifier
                    or self.active["protocol"] != 2 or self.active["owner"] != worker_id):
                return False
            if not self.active["claimed"]:
                self.active["claimed"] = True
                diagnostic("job_started")
            return True

    def complete(self, identifier, result, worker_id=None):
        with self.lock:
            now = time.monotonic()
            self.last_heartbeat = now
            self._expire(now)
            saved = self.completed.get(identifier)
            if saved is not None:
                return saved[1] == worker_id
            if not self.active or not self.active["claimed"] or identifier != self.active["id"]:
                return False
            if self.active["protocol"] == 2 and self.active["owner"] != worker_id:
                return False
            job, self.active = self.active, None
            self.completed[identifier] = (now, job["owner"])
            while len(self.completed) > MAX_CACHED_RESULTS:
                self.completed.popitem(last=False)
            job["result"] = result
            job["event"].set()
            diagnostic("job_completed")
            return True

    def wait(self, job):
        job["event"].wait(max(0, job["deadline"] - time.monotonic()))
        with self.lock:
            self._expire(time.monotonic())
            return job["result"] or (504, {"error": ERRORS[504]})


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "OperationsChatRelay/1.0"
    sys_version = ""

    def log_message(self, _format, *args):
        return

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def _send(self, status, payload, origin=None):
        body = b"" if status == 204 else json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        if status == 204:
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", ", ".join(sorted(ALLOWED_HEADERS)))
            self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _read(self, maximum):
        if self.headers.get_content_type() != "application/json":
            return 415, None
        if self.headers.get("Transfer-Encoding"):
            return 400, None
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            return 413, None
        if size < 0 or size > maximum:
            return 413, None
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                return 400, None
            payload = json.loads(raw.decode("utf-8"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(payload, dict):
                return 400, None
            return 200, payload
        except (ValueError, UnicodeError, RecursionError, TimeoutError, OSError):
            return 400, None

    def do_GET(self):
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            self._send(403, {"error": "请求来源无效。"})
            return
        if urlsplit(self.path).path != "/api/status":
            self._send(404, {"error": "接口不存在。"}, origin)
            return
        self._send(200, {"ready": self.server.relay.ready(), "policy": POLICY}, origin)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        requested_headers = {part.strip().lower() for part in self.headers.get("Access-Control-Request-Headers", "").split(",") if part.strip()}
        if (urlsplit(self.path).path != "/api/chat" or origin not in ALLOWED_ORIGINS
            or self.headers.get("Access-Control-Request-Method") != "POST"
            or not requested_headers.issubset(ALLOWED_HEADERS)):
            self._send(403, {"error": "请求来源无效。"})
            return
        self._send(204, {}, origin)

    def do_POST(self):
        path = urlsplit(self.path).path
        origin = self.headers.get("Origin")
        if path in {"/internal/pull", "/internal/start", "/internal/complete"}:
            if origin is not None or not self.server.relay.authenticated(self.headers.get("Authorization")):
                self._send(403, {"error": "请求来源无效。"})
                return
            status, payload = self._read(MAX_RESULT if path == "/internal/complete" else MAX_BODY)
            if status != 200:
                self._send(status, {"error": ERRORS[status]})
                return
            if path == "/internal/pull":
                protocol = payload.get("protocol", 1)
                owner = payload.get("worker_id")
                if type(protocol) is not int or protocol not in (1, 2) or (protocol == 2 and not valid_worker_id(owner)):
                    self._send(400, {"error": ERRORS[400]})
                    return
                self._send(200, self.server.relay.pull(protocol=protocol, worker_id=owner))
                return
            try:
                identifier = payload.get("id")
                if not isinstance(identifier, str) or len(identifier) != 32:
                    raise ValueError
                owner = payload.get("worker_id")
                if owner is not None and not valid_worker_id(owner):
                    raise ValueError
                if path == "/internal/start":
                    if owner is None:
                        raise ValueError
                    accepted = self.server.relay.start(identifier, owner)
                else:
                    result = clean_result(payload.get("status"), payload.get("body"))
                    accepted = self.server.relay.complete(identifier, result, owner)
            except ValueError:
                self._send(400, {"error": ERRORS[400]})
                return
            self._send(200, {"accepted": accepted})
            return
        if path != "/api/chat":
            self._send(404, {"error": "接口不存在。"})
            return
        if origin not in ALLOWED_ORIGINS:
            self._send(403, {"error": "请求来源无效。"})
            return
        status, data = self._read(MAX_BODY)
        if status != 200:
            self._send(status, {"error": ERRORS[status]}, origin)
            return
        try:
            payload = clean_request(data)
        except (ValueError, RecursionError):
            self._send(400, {"error": ERRORS[400]}, origin)
            return
        status, job = self.server.relay.enqueue(payload)
        if job is None:
            self._send(status, {"error": ERRORS[status]}, origin)
            return
        status, result = self.server.relay.wait(job)
        self._send(status, result, origin)

    def do_HEAD(self):
        self._send(405, {"error": "不支持此请求。"})

    do_PUT = do_DELETE = do_PATCH = do_HEAD


def make_server(address=(HOST, PORT), *, worker_token=None, **state_options):
    server = ThreadingHTTPServer(address, RelayHandler)
    server.daemon_threads = True
    server.relay = RelayState(os.environ.get("RELAY_WORKER_TOKEN", "") if worker_token is None else worker_token, **state_options)
    return server


def main():
    if not os.environ.get("RELAY_WORKER_TOKEN"):
        raise SystemExit("RELAY_WORKER_TOKEN is required.")
    server = make_server()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
