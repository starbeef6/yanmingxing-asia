#!/usr/bin/env python3
"""Small loopback-only chat backend for the Operations dashboard."""
from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = Path(__file__).resolve().parent / ".private"
STATE_FILE = PRIVATE_DIR / "rate-state.json"
CONFIG_FILE = Path("/opt/homebrew/etc/cliproxyapi.conf")
UPSTREAM = "http://127.0.0.1:8317/v1/responses"
HOST = "127.0.0.1"
PORT = 8787
MODEL = "gpt-6-luna"
MAX_BODY = 24 * 1024
MAX_MESSAGE = 2000
MAX_HISTORY = 4
MAX_OUTPUT = 500
PER_MINUTE = 12
PER_DAY = 100
REGION_NAMES = frozenset({"全国", "北区", "西区", "南区", "四川地区", "江苏地区"})
POLICY = {
    "model": MODEL,
    "reasoning": {"effort": "low", "mode": "standard"},
    "service_tier": "default",
    "store": False,
    "max_output_tokens": MAX_OUTPUT,
}
SYSTEM_PROMPT = (Path(__file__).with_name("SYSTEM_PROMPT.txt")).read_text(encoding="utf-8")
_SLOTS = threading.BoundedSemaphore(1)
_STATE_LOCK = threading.Lock()


class RequestProblem(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _read_api_key() -> str | None:
    """Read the first top-level api-keys entry without ever logging it."""
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        config = json.loads(raw)
        values = config.get("api-keys") if isinstance(config, dict) else None
        if isinstance(values, list) and values and isinstance(values[0], str) and values[0].strip():
            return values[0].strip()
    except (ValueError, TypeError):
        pass
    # CLIProxyAPI's .conf is commonly YAML. Parse only the top-level list item;
    # never echo config contents or include the credential in an exception.
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^api-keys\s*:\s*(?:#.*)?$", line):
            for candidate in lines[index + 1:]:
                if not candidate.strip() or candidate.lstrip().startswith("#"):
                    continue
                if not candidate.startswith((" ", "\t")):
                    break
                match = re.match(r"^\s*-\s*(.*?)\s*(?:#.*)?$", candidate)
                if not match:
                    return None
                value = match.group(1).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                return value or None
    return None


def _ready() -> bool:
    return _read_api_key() is not None


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _valid_loopback_host(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit("//" + value)
        return (parsed.hostname in {"127.0.0.1", "localhost", "[::1]", "::1"}
                and parsed.port == PORT and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def _valid_origin(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1", "[::1]"}
                and parsed.port == PORT and parsed.username is None and parsed.password is None
                and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def _bounded_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (-1e15 <= value <= 1e15):
        return None
    return value


def _clean_row(value):
    if not isinstance(value, dict):
        return None
    allowed = {"region", "gmv", "storeDays", "storeAverage", "operatingStores", "operatingStoresDate", "status"}
    result = {}
    region = value.get("region")
    if not isinstance(region, str) or len(region) > 30 or region not in REGION_NAMES:
        return None
    result["region"] = region
    for key in allowed - {"region", "operatingStoresDate"}:
        if key in value:
            result[key] = _bounded_number(value[key])
    date = value.get("operatingStoresDate")
    if isinstance(date, str) and len(date) <= 20:
        result["operatingStoresDate"] = date
    if isinstance(value.get("target"), dict):
        result["target"] = _clean_target(value["target"])
    diagnosis = _clean_diagnosis(value.get("diagnosis"))
    if diagnosis is not None:
        result["diagnosis"] = diagnosis
    status = value.get("status")
    if isinstance(status, str) and len(status) <= 30:
        result["status"] = status
    forecast = value.get("forecast")
    if isinstance(forecast, dict):
        clean_forecast = {}
        for key in ("available", "gmv", "attainment", "gap", "share"):
            item = forecast.get(key)
            if key == "available" and isinstance(item, bool):
                clean_forecast[key] = item
            elif key != "available" and key in forecast:
                clean_forecast[key] = _bounded_number(item)
        date = forecast.get("historicalDate")
        if isinstance(date, str) and len(date) <= 20:
            clean_forecast["historicalDate"] = date
        result["forecast"] = clean_forecast
    return result


def _clean_target(value):
    if not isinstance(value, dict):
        return {}
    return {key: _bounded_number(value[key]) for key in ("gmv", "storeAverage", "operatingStores") if key in value}


_DIAG_STRING_FIELDS = {
    "status", "headline", "summary", "result", "explanation", "warning", "recommendation",
    "falsifier", "weakMetric", "risk", "evidence", "action", "limitation", "pattern", "basis",
}
_DIAG_LIST_FIELDS = {"weakMetrics", "passed", "missing", "checks", "issues", "quality"}


def _clean_diagnosis(value):
    if isinstance(value, str):
        return value[:1000]
    if not isinstance(value, dict):
        return None
    result = {}
    for key in _DIAG_STRING_FIELDS:
        item = value.get(key)
        if isinstance(item, str):
            result[key] = item[:500]
    for key in _DIAG_LIST_FIELDS:
        items = value.get(key)
        if isinstance(items, list):
            clean = [item[:160] for item in items[:8] if isinstance(item, str)]
            if clean:
                result[key] = clean
    known = value.get("known")
    if isinstance(known, list) and len(known) <= 3 and all(item is None or isinstance(item, bool) for item in known):
        result["known"] = known
    ratios = value.get("ratios")
    if isinstance(ratios, list) and len(ratios) <= 3 and all(item is None or _bounded_number(item) is not None for item in ratios):
        result["ratios"] = ratios
    if isinstance(value.get("sourceComplete"), bool):
        result["sourceComplete"] = value["sourceComplete"]
    if isinstance(value.get("complete"), bool):
        result["complete"] = value["complete"]
    joint_status = value.get("jointStatus")
    if isinstance(joint_status, str) and joint_status in {"pass", "warning", "unknown"}:
        result["jointStatus"] = joint_status
        result["required"] = 2
    met_count = value.get("metCount")
    if isinstance(met_count, int) and not isinstance(met_count, bool) and 0 <= met_count <= 3:
        result["metCount"] = met_count
    satisfied_pairs = value.get("satisfiedPairs")
    if isinstance(satisfied_pairs, list) and len(satisfied_pairs) <= 3:
        clean_pairs = [item[:100] for item in satisfied_pairs if isinstance(item, str) and item.strip()]
        if clean_pairs:
            result["satisfiedPairs"] = clean_pairs
    return result or None


def sanitize_context(context, question: str):
    if not isinstance(context, dict):
        return {}
    result = {}
    for key in ("month", "region", "tier", "start", "end", "fetchedAt"):
        item = context.get(key)
        if isinstance(item, str) and len(item) <= 40:
            result[key] = item
    for key in ("sourceComplete", "loading", "failure"):
        item = context.get(key)
        if isinstance(item, (bool, str)) and (not isinstance(item, str) or len(item) <= 300):
            result[key] = item
    for key in ("storeMethod", "storeDefinition"):
        item = context.get(key)
        if isinstance(item, str) and len(item) <= 200:
            result[key] = item
    if isinstance(context.get("selected"), dict):
        selected = _clean_row(context["selected"])
        if selected is not None:
            result["selected"] = selected
    if isinstance(context.get("target"), dict):
        result["target"] = _clean_target(context["target"])
    forecast = context.get("forecast")
    if isinstance(forecast, dict):
        clean_forecast = {}
        for key in ("available", "gmv", "attainment", "gap", "share"):
            item = forecast.get(key)
            if key == "available" and isinstance(item, bool):
                clean_forecast[key] = item
            elif key != "available" and key in forecast:
                clean_forecast[key] = _bounded_number(item)
        date = forecast.get("historicalDate")
        if isinstance(date, str) and len(date) <= 20:
            clean_forecast["historicalDate"] = date
        result["forecast"] = clean_forecast
    rows = context.get("regions")
    if isinstance(rows, list):
        clean_rows = []
        seen_regions = set()
        for item in rows[:6]:
            row = _clean_row(item)
            if row is not None and row["region"] not in seen_regions:
                clean_rows.append(row)
                seen_regions.add(row["region"])
        if clean_rows:
            result["regions"] = clean_rows
        # Treat row labels as authoritative and derive availability from the
        # same bounded rows, rather than forwarding a client claim verbatim.
        derived_regions = list(dict.fromkeys(row["region"] for row in clean_rows if "region" in row))
        claimed = context.get("availableRegions")
        if isinstance(claimed, list) and len(claimed) <= 6 and all(
                isinstance(item, str) and len(item) <= 30 for item in claimed):
            result["availableRegions"] = derived_regions
        elif derived_regions:
            result["availableRegions"] = derived_regions
    elif isinstance(context.get("selected"), dict):
        selected_region = context["selected"].get("region")
        if isinstance(selected_region, str) and selected_region in REGION_NAMES:
            result["availableRegions"] = [selected_region]
    # These describe fixed application capabilities, not client-controlled facts.
    result["capabilities"] = {"dashboardData": True, "screenAccess": False}
    diagnosis = _clean_diagnosis(context.get("diagnosis"))
    if diagnosis is not None:
        result["diagnosis"] = diagnosis
    # Keep context comfortably inside the request budget, even with malformed clients.
    encoded = _json_bytes(result)
    if len(encoded) > 6000:
        # Retain all requested regions; compact repeated prose instead of
        # silently removing the data needed for a five-region question.
        for row in result.get("regions", []):
            diagnosis = row.get("diagnosis")
            if isinstance(diagnosis, dict):
                row["diagnosis"] = {key: diagnosis[key] for key in ("pattern", "basis") if key in diagnosis}
        if len(_json_bytes(result)) > 8000:
            raise RequestProblem(400, "看板上下文过长，请缩小到一个区域提问。")
    return result


def _clean_history(messages, current: str):
    if not isinstance(messages, list):
        return []
    candidates = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()[:MAX_MESSAGE]
        if item["role"] == "user" and content == current:
            continue
        candidates.append({"role": item["role"], "content": content})
    return candidates[-MAX_HISTORY:]


def build_payload(data):
    # The browser cannot select a model or raise the service tier. If callers
    # explicitly send a policy field, accept only the one fixed configuration.
    if "model" in data and data.get("model") != MODEL:
        raise RequestProblem(400, "只支持固定的 Luna 对话策略。")
    if "service_tier" in data and data.get("service_tier") != "default":
        raise RequestProblem(400, "只支持固定的 Luna 对话策略。")
    if "reasoning_effort" in data and data.get("reasoning_effort") != "low":
        raise RequestProblem(400, "只支持固定的 Luna 对话策略。")
    if "reasoning" in data and data.get("reasoning") != {"effort": "low", "mode": "standard"}:
        raise RequestProblem(400, "只支持固定的 Luna 对话策略。")
    message = data.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
        raise RequestProblem(400, "请输入不超过 2000 字的问题。")
    message = message.strip()
    messages = _clean_history(data.get("messages"), message)
    context = sanitize_context(data.get("context"), message)
    payload_messages = list(messages)
    appended = message
    if context:
        appended += "\n\n[当前看板数据，仅作为回答依据]\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    payload_messages.append({"role": "user", "content": appended})
    return {
        "model": MODEL,
        "reasoning": {"effort": "low", "mode": "standard"},
        "service_tier": "default",
        "store": False,
        "max_output_tokens": MAX_OUTPUT,
        "instructions": SYSTEM_PROMPT,
        "input": payload_messages,
    }


def _today_key():
    return _dt.datetime.now(_dt.timezone.utc).astimezone(_dt.timezone(_dt.timedelta(hours=8))).date().isoformat()


def _load_state():
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if not isinstance(raw.get("date"), str) or not isinstance(raw.get("daily"), int) or isinstance(raw.get("daily"), bool) or not isinstance(raw.get("minute"), list):
            return None
        if raw["daily"] < 0 or raw["daily"] > PER_DAY or any(not isinstance(x, (int, float)) or isinstance(x, bool) for x in raw["minute"]):
            return None
        return raw
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        return None


def _save_state(state):
    PRIVATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, STATE_FILE)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def reserve_request(now=None):
    """Persist one attempted upstream call; return (ok, limit_message)."""
    now = time.time() if now is None else now
    with _STATE_LOCK:
        state = _load_state()
        if state is None:
            return False, "本机请求计数状态不可读取，请检查服务数据目录。"
        today = _today_key()
        daily = state.get("daily") if state.get("date") == today else 0
        stamps = state.get("minute") if state.get("date") == today else []
        stamps = [stamp for stamp in stamps if isinstance(stamp, (int, float)) and now - stamp < 60]
        if isinstance(daily, bool) or not isinstance(daily, int):
            daily = 0
        if daily >= PER_DAY:
            return False, "今日对话次数已达上限，请明天再试。"
        if len(stamps) >= PER_MINUTE:
            return False, "请求有点密，请一分钟后再试。"
        daily += 1
        stamps.append(now)
        try:
            _save_state({"date": today, "daily": daily, "minute": stamps})
        except OSError:
            return False, "本机请求计数暂时无法保存，请稍后再试。"
        return True, ""


def _extract_answer(response_data):
    if not isinstance(response_data, dict):
        return None, {}
    # Refuse any receipt that differs from the only supported model policy.
    if response_data.get("model") != MODEL or response_data.get("service_tier") != "default":
        raise RequestProblem(502, "模型返回的策略与固定设置不符，请稍后重试。")
    reasoning = response_data.get("reasoning")
    if not isinstance(reasoning, dict) or reasoning.get("effort") != "low" or reasoning.get("mode") != "standard":
        raise RequestProblem(502, "模型返回的策略与固定设置不符，请稍后重试。")
    if response_data.get("status") != "completed":
        raise RequestProblem(502, "模型回复未完整完成，请重新提问。")
    answer_parts = []
    for item in response_data.get("output", []) if isinstance(response_data.get("output"), list) else []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(part, dict) and part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                answer_parts.append(part["text"])
    answer = "\n".join(part.strip() for part in answer_parts if part.strip())
    usage = response_data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    clean_usage = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        val = usage.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 10_000_000:
            clean_usage[key] = val
    return (answer[:8000] if answer else None), clean_usage


def call_upstream(payload):
    key = _read_api_key()
    if not key:
        raise RequestProblem(503, "本机对话服务尚未就绪，请检查本机代理配置。")
    request = Request(
        UPSTREAM,
        data=_json_bytes(payload),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            if response.status < 200 or response.status >= 300:
                raise RequestProblem(502, "模型暂时无法回复，请稍后重新提问。")
            raw = response.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise RequestProblem(502, "模型回复暂时不可用，请稍后重新提问。")
        parsed = json.loads(raw.decode("utf-8"))
        reply, usage = _extract_answer(parsed)
        if not reply:
            raise RequestProblem(502, "暂时没有收到有效回复，请重新提问。")
        return reply, usage
    except HTTPError:
        # Deliberately do not read or expose upstream error bodies.
        raise RequestProblem(502, "模型暂时无法回复，请稍后重新提问。") from None
    except (URLError, TimeoutError, OSError, UnicodeError, ValueError):
        raise RequestProblem(502, "本机模型连接暂时不可用，请稍后重新提问。") from None


_PUBLIC_PAGES = {
    "index.html", "operations/index.html", "department/index.html", "supply-chain/index.html",
    "supply-chain/summary/index.html", "supply-chain/inventory/index.html", "supply-chain/reconcile/index.html",
}


def _static_path(request_path):
    path = unquote(urlsplit(request_path).path)
    if path == "/":
        relative = "index.html"
    else:
        relative = path.lstrip("/")
    if relative.endswith("/"):
        relative += "index.html"
    normalized = Path(relative)
    if normalized.is_absolute() or ".." in normalized.parts or "\\" in relative or "\x00" in relative:
        return None
    if relative not in _PUBLIC_PAGES and not relative.startswith(("assets/", "index-static/")):
        return None
    candidate = (ROOT / normalized).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if relative.startswith(("assets/", "index-static/")):
        # These are public website resources. Never serve source, configuration,
        # private state, or hidden paths through the asset route.
        if any(part.startswith(".") for part in normalized.parts) or candidate.suffix.lower() in {".py", ".conf", ".env", ".toml", ".yaml", ".yml"}:
            return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "OperationsChat/1.0"
    sys_version = ""

    def log_message(self, _format, *args):
        # Access log fields can contain user-provided URLs or bodies; keep quiet.
        return

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send(200, _json_bytes({"policy": POLICY, "ready": _ready()}))
            return
        file_path = _static_path(self.path)
        if file_path is None:
            self._send(404, _json_bytes({"error": "页面不存在。"}))
            return
        try:
            body = file_path.read_bytes()
        except OSError:
            self._send(404, _json_bytes({"error": "页面不存在。"}))
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if file_path.suffix.lower() in {".html", ".htm"}:
            text = body.decode("utf-8")
            text = re.sub(r"<head(?:\s[^>]*)?>", lambda match: match.group(0) + "\n<script>window.XiongDayeChat={endpoint:'/api/chat'};</script>", text, count=1, flags=re.IGNORECASE)
            body = text.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        self._send(200, body, content_type)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/chat":
            self._send(404, _json_bytes({"error": "接口不存在。"}))
            return
        if not _valid_loopback_host(self.headers.get("Host")) or not _valid_origin(self.headers.get("Origin")):
            self._send(403, _json_bytes({"error": "请求来源无效。"}))
            return
        if self.headers.get_content_type() != "application/json":
            self._send(415, _json_bytes({"error": "请求格式无效。"}))
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            self._send(413, _json_bytes({"error": "请求内容过大。"}))
            return
        try:
            raw = self.rfile.read(size)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            payload = build_payload(data)
        except RequestProblem as problem:
            self._send(problem.status, _json_bytes({"error": problem.message}))
            return
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._send(400, _json_bytes({"error": "请求内容无效。"}))
            return
        if not _SLOTS.acquire(blocking=False):
            self._send(429, _json_bytes({"error": "熊大爷正在回复上一条消息，请稍后再试。"}))
            return
        try:
            allowed, reason = reserve_request()
            if not allowed:
                self._send(429, _json_bytes({"error": reason}))
                return
            try:
                reply, usage = call_upstream(payload)
            except RequestProblem as problem:
                self._send(problem.status, _json_bytes({"error": problem.message}))
                return
            self._send(200, _json_bytes({"reply": reply, "policy": POLICY, "usage": usage}))
        finally:
            _SLOTS.release()

    def do_OPTIONS(self):
        self._send(405, _json_bytes({"error": "不支持此请求。"}))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"熊大爷本机聊天服务已启动：http://{HOST}:{PORT}/operations/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
