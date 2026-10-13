#!/usr/bin/env python3
"""Pull website chat jobs over HTTPS and use the existing loopback-only backend.

The relay has no model credential. This worker never reads model credentials;
the existing backend retains its model policy, concurrency and daily budget.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import signal
import stat
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


PRIVATE_DIR = Path(__file__).resolve().parent / ".private"
CONFIG_FILE = PRIVATE_DIR / "cloud-relay-config.json"
TOKEN_FILE = PRIVATE_DIR / "cloud-relay-token"
BACKEND_URL = "http://127.0.0.1:8787/api/chat"
BACKEND_HOST = "127.0.0.1:8787"
BACKEND_ORIGIN = "http://127.0.0.1:8787"
MAX_BODY = 24 * 1024
MAX_RESPONSE = 64 * 1024
PULL_TIMEOUT = 10
BACKEND_TIMEOUT = 50
COMPLETE_TIMEOUT = 10
POLL_INTERVAL = 5
MAX_BACKOFF = 30
COMPLETION_RETRIES = 2
RESULT_TTL = 180
MAX_CACHED_RESULTS = 32
POLICY = {
    "model": "gpt-6-luna",
    "reasoning": {"effort": "low", "mode": "standard"},
    "service_tier": "default",
    "store": False,
    "max_output_tokens": 500,
}


class WorkerError(Exception):
    """Carry a fixed diagnostic code, never URLs, headers or response bodies."""

    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise WorkerError("invalid_json") from None


def validate_relay_url(value):
    """Accept an HTTPS Cloud Run service origin, with no additional URL parts."""
    if not isinstance(value, str) or not value or len(value) > 300:
        raise WorkerError("invalid_relay_url")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise WorkerError("invalid_relay_url")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname or ""
    except ValueError:
        raise WorkerError("invalid_relay_url") from None
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.a\.run\.app", hostname)):
        raise WorkerError("invalid_relay_url")
    return "https://" + hostname


def _read_private_file(path, maximum, enforce_private=False):
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise WorkerError("invalid_private_config")
        if enforce_private and (stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_uid != os.getuid()):
            raise WorkerError("unsafe_token_permissions")
        with path.open("rb") as stream:
            value = stream.read(maximum + 1)
        if len(value) > maximum:
            raise WorkerError("invalid_private_config")
        return value
    except (OSError, UnicodeError):
        raise WorkerError("private_config_unavailable") from None


def load_connection(config_file=CONFIG_FILE, token_file=TOKEN_FILE):
    """Read private runtime inputs; callers must never print the returned token."""
    try:
        config = json.loads(_read_private_file(Path(config_file), 8192).decode("utf-8"))
        if not isinstance(config, dict):
            raise WorkerError("invalid_private_config")
        base_url = validate_relay_url(config.get("url"))
        token = _read_private_file(Path(token_file), 1024, enforce_private=True).decode("ascii").strip()
        if not 16 <= len(token) <= 512 or not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
            raise WorkerError("invalid_private_config")
        return base_url, token
    except (ValueError, UnicodeError):
        raise WorkerError("invalid_private_config") from None


def _open_backend(request, timeout):
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def _open_cloud(request, timeout):
    # Allow the computer's existing HTTPS transport proxy, while refusing any
    # redirect that could forward the relay token to another destination.
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _read_json(response):
    raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise WorkerError("response_too_large")
    try:
        parsed = json.loads(raw.decode("utf-8"), parse_constant=lambda _value: None)
        if not isinstance(parsed, dict):
            raise ValueError
        return parsed
    except (ValueError, UnicodeError):
        raise WorkerError("invalid_response") from None


def _cloud_json(base_url, token, path, payload, timeout):
    base_url = validate_relay_url(base_url)
    if path not in {"/internal/pull", "/internal/complete"}:
        raise WorkerError("invalid_relay_path")
    raw = _json_bytes(payload)
    if len(raw) > MAX_RESPONSE:
        raise WorkerError("request_too_large")
    request = Request(base_url + path, data=raw, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json", "Accept": "application/json",
    })
    try:
        with _open_cloud(request, timeout=timeout) as response:
            if response.status != 200:
                raise WorkerError("relay_http_error")
            return _read_json(response)
    except HTTPError:
        # Do not read or expose cloud error bodies or redirected URLs.
        raise WorkerError("relay_http_error") from None
    except (URLError, TimeoutError, OSError):
        raise WorkerError("relay_transport_error", retryable=True) from None


def validate_job(value):
    if not isinstance(value, dict):
        raise WorkerError("invalid_job")
    job_id, payload = value.get("id"), value.get("payload")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
        raise WorkerError("invalid_job")
    if not isinstance(payload, dict) or len(_json_bytes(payload)) > MAX_BODY:
        raise WorkerError("invalid_job")
    return job_id, payload


def _safe_result(status, value):
    if status == 200 and isinstance(value, dict) and value.get("policy") == POLICY:
        reply = value.get("reply")
        if isinstance(reply, str) and reply.strip() and len(reply) <= 8000:
            usage = {}
            if isinstance(value.get("usage"), dict):
                for name in ("input_tokens", "output_tokens", "total_tokens"):
                    count = value["usage"].get(name)
                    if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 10_000_000:
                        usage[name] = count
            return 200, {"reply": reply.strip(), "policy": POLICY, "usage": usage}
    # Error text is fixed locally, so a malformed backend response cannot leak
    # credentials, headers, paths or upstream diagnostics into a public reply.
    messages = {
        400: "请求内容无效，请调整问题后重试。",
        413: "请求内容过大，请缩短问题后重试。",
        415: "请求格式无效，请刷新页面后重试。",
        429: "熊大爷正在回复或已达到调用限额，请稍后再试。",
        503: "对话服务暂未就绪，请稍后再试。",
    }
    if status not in messages:
        status = 502
    return status, {"error": messages.get(status, "暂时连接不上对话服务，请重试。")}


def call_backend(payload):
    raw = _json_bytes(payload)
    if not isinstance(payload, dict) or len(raw) > MAX_BODY:
        return _safe_result(400, None)
    request = Request(BACKEND_URL, data=raw, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Host": BACKEND_HOST, "Origin": BACKEND_ORIGIN,
    })
    try:
        with _open_backend(request, timeout=BACKEND_TIMEOUT) as response:
            return _safe_result(response.status, _read_json(response))
    except HTTPError as error:
        # Status is sufficient; never relay the upstream error body.
        return _safe_result(error.code, None)
    except (URLError, TimeoutError, OSError, WorkerError):
        return _safe_result(502, None)


class RelayWorker:
    def __init__(self, base_url, token, stop_event=None):
        self.base_url = validate_relay_url(base_url)
        self._token = token
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._results = OrderedDict()

    def pull(self):
        value = _cloud_json(self.base_url, self._token, "/internal/pull", {}, PULL_TIMEOUT)
        if "job" not in value:
            raise WorkerError("invalid_pull_response")
        job = value["job"]
        if job is not None:
            validate_job(job)
        return job

    def complete(self, job_id, status, body):
        payload = {"id": job_id, "status": status, "body": body}
        for attempt in range(COMPLETION_RETRIES + 1):
            try:
                value = _cloud_json(self.base_url, self._token, "/internal/complete", payload, COMPLETE_TIMEOUT)
                if value.get("accepted") is False:
                    # A timed-out job can be discarded by the relay. This is a
                    # final acknowledgment, not a reason to run the model again.
                    return False
                if value.get("accepted") is not True:
                    raise WorkerError("completion_not_acknowledged")
                return True
            except WorkerError as problem:
                if not problem.retryable or attempt == COMPLETION_RETRIES:
                    raise
                if self.stop_event.wait(0.5 * (attempt + 1)):
                    raise WorkerError("worker_stopped") from None

    def handle_job(self, job):
        job_id, payload = validate_job(job)
        now = time.monotonic()
        for previous_id, (created, _status, _body) in list(self._results.items()):
            if now - created >= RESULT_TTL:
                self._results.pop(previous_id, None)
        saved = self._results.get(job_id)
        if saved is None:
            status, body = call_backend(payload)
            self._results[job_id] = (now, status, body)
            while len(self._results) > MAX_CACHED_RESULTS:
                self._results.popitem(last=False)
        else:
            _created, status, body = saved
        # A failed completion retry never runs the model again. Cached results
        # also cover an uncertain acknowledgment followed by the same job ID.
        self.complete(job_id, status, body)


def run(stop_event=None):
    stop_event = stop_event if stop_event is not None else threading.Event()
    worker = None
    failures = 0
    last_code = None
    while not stop_event.is_set():
        delay = POLL_INTERVAL
        try:
            base_url, token = load_connection()
            if worker is None or worker.base_url != base_url or worker._token != token:
                worker = RelayWorker(base_url, token, stop_event)
            job = worker.pull()
            if job is not None:
                worker.handle_job(job)
            failures = 0
            code = "ready"
        except WorkerError as problem:
            failures += 1
            delay = min(MAX_BACKOFF, POLL_INTERVAL * (2 ** min(failures - 1, 3)))
            code = problem.code
        if code != last_code:
            print("operations cloud worker: " + code, flush=True)
            last_code = code
        stop_event.wait(delay)


def main():
    stop_event = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop_event.set())
    run(stop_event)


if __name__ == "__main__":
    main()
