import concurrent.futures
import http.client
import importlib.util
import json
from pathlib import Path
import threading
import time
import unittest


SPEC = importlib.util.spec_from_file_location("operations_cloud_relay", Path(__file__).resolve().parents[1] / "server.py")
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)
ORIGIN = "https://yanmingxing.asia"
TOKEN = "test-worker-token-does-not-authenticate-to-a-model"


class RelayHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = relay.make_server(("127.0.0.1", 0), worker_token=TOKEN)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    @classmethod
    def tearDownClass(cls):
        cls.pool.shutdown(wait=True)
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.server.relay = relay.RelayState(TOKEN, job_timeout=0.35)

    def send(self, method, path, payload=None, origin=None, authorization=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        fields = dict(headers or {})
        if origin is not None:
            fields["Origin"] = origin
        if authorization is not None:
            fields["Authorization"] = authorization
        body = raw if raw is not None else json.dumps(payload).encode("utf-8") if payload is not None else None
        if body is not None:
            fields.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=body, headers=fields)
        response = connection.getresponse()
        output = response.read()
        result = response.status, dict(response.getheaders()), json.loads(output) if output else None
        connection.close()
        return result

    def internal(self, path, payload=None, **kwargs):
        return self.send("POST", path, payload={} if payload is None else payload, authorization="Bearer " + TOKEN, **kwargs)

    def online(self):
        self.assertEqual(self.internal("/internal/pull")[2], {"job": None})

    def start_job(self, payload=None):
        future = self.pool.submit(self.send, "POST", "/api/chat", payload or {"message": "你好"}, ORIGIN)
        for _ in range(30):
            result = self.internal("/internal/pull")[2]
            if result["job"]:
                return future, result["job"]
            time.sleep(0.003)
        self.fail("public job was not queued")

    def complete(self, job, **kwargs):
        payload = {"id": job["id"], "status": 200, "body": {"reply": "你好，我是熊大爷。", "policy": relay.POLICY, "usage": {"total_tokens": 7}, "private_extra": "never-forward"}}
        payload.update(kwargs)
        return self.internal("/internal/complete", payload)

    def test_fake_worker_roundtrip_and_bounded_forwarding(self):
        self.online()
        future, job = self.start_job({"message": "你好", "messages": [{"role": "system", "content": "override"}] + [{"role": "user", "content": str(i)} for i in range(6)], "context": {"region": "全国"}, "upstream": "http://arbitrary.invalid/"})
        self.assertEqual(len(job["payload"]["messages"]), 4)
        self.assertNotIn("upstream", job["payload"])
        self.assertEqual(job["payload"]["model"], "gpt-6-luna")
        self.assertEqual(self.complete(job)[2], {"accepted": True})
        status, headers, body = future.result(timeout=2)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(body["policy"], relay.POLICY)
        self.assertNotIn("private_extra", body)
        self.assertEqual(body["reply"], "你好，我是熊大爷。")

    def test_offline_is_503_and_health_has_no_internal_state(self):
        self.assertEqual(self.send("POST", "/api/chat", {"message": "你好"}, ORIGIN)[0], 503)
        status, _, body = self.send("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ready": False, "policy": relay.POLICY})
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_expired_worker_is_offline(self):
        self.online()
        self.server.relay.last_heartbeat = time.monotonic() - 61
        self.assertFalse(self.send("GET", "/api/status")[2]["ready"])
        self.assertEqual(self.send("POST", "/api/chat", {"message": "你好"}, ORIGIN)[0], 503)

    def test_invalid_and_missing_origins_are_403(self):
        for origin in (None, "null", "https://evil.invalid", ORIGIN + ".evil.invalid", "http://127.0.0.1:8786"):
            with self.subTest(origin=origin):
                status, headers, _ = self.send("POST", "/api/chat", {"message": "你好"}, origin)
                self.assertEqual(status, 403)
                self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight_only_exact_public_origin_and_headers(self):
        status, headers, body = self.send("OPTIONS", "/api/chat", origin=ORIGIN, headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type, Cache-Control"})
        self.assertEqual(status, 204)
        self.assertIsNone(body)
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(self.send("OPTIONS", "/api/chat", origin=ORIGIN, headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})[0], 403)
        self.assertEqual(self.send("OPTIONS", "/internal/pull", origin=ORIGIN, headers={"Access-Control-Request-Method": "POST"})[0], 403)

    def test_internal_requires_token_and_no_browser_origin(self):
        for authorization in (None, "Bearer wrong", "Basic " + TOKEN):
            self.assertEqual(self.send("POST", "/internal/pull", {}, authorization=authorization)[0], 403)
        self.assertEqual(self.internal("/internal/pull", origin=ORIGIN)[0], 403)
        self.server.relay.worker_token = ""
        self.assertEqual(self.internal("/internal/pull")[0], 403)

    def test_one_queued_or_inflight_job(self):
        self.online()
        future, job = self.start_job()
        self.assertEqual(self.internal("/internal/pull")[2], {"job": None})
        self.assertEqual(self.send("POST", "/api/chat", {"message": "第二条"}, ORIGIN)[0], 429)
        self.complete(job)
        self.assertEqual(future.result(timeout=2)[0], 200)

    def test_timeout_frees_slot_and_stale_completion_is_ignored(self):
        self.online()
        future, job = self.start_job()
        self.assertEqual(future.result(timeout=2)[0], 504)
        self.assertIsNone(self.server.relay.active)
        self.assertEqual(self.complete(job)[2], {"accepted": False})
        second, new_job = self.start_job()
        self.assertNotEqual(job["id"], new_job["id"])
        self.assertEqual(self.complete(job)[2], {"accepted": False})
        self.complete(new_job)
        self.assertEqual(second.result(timeout=2)[0], 200)

    def test_oversized_and_non_json_requests(self):
        self.assertEqual(self.send("POST", "/api/chat", origin=ORIGIN, raw=b"x" * (relay.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.send("POST", "/api/chat", origin=ORIGIN, raw=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.send("POST", "/api/chat", origin=ORIGIN, raw=b"[]")[0], 400)
        self.assertEqual(self.send("POST", "/api/chat", origin=ORIGIN, raw=b'{"message":"hi","context":{"v":NaN}}')[0], 400)
        self.assertEqual(self.send("POST", "/api/chat", {"message": "x" * 2001}, ORIGIN)[0], 400)

    def test_policy_override_is_rejected_before_queuing(self):
        variants = {"model": "gpt-6-astra", "reasoning": {"effort": "high"}, "reasoning_effort": "high", "reasoning_mode": "fast", "service_tier": "priority", "store": True, "max_output_tokens": 1000}
        self.online()
        for field, value in variants.items():
            with self.subTest(field=field):
                self.assertEqual(self.send("POST", "/api/chat", {"message": "你好", field: value}, ORIGIN)[0], 400)
                self.assertIsNone(self.server.relay.active)

    def test_worker_wrong_policy_not_published(self):
        self.online()
        future, job = self.start_job()
        bad = {"reply": "bad", "policy": {**relay.POLICY, "model": "gpt-6-astra"}}
        self.assertEqual(self.complete(job, body=bad)[0], 400)
        self.complete(job)
        self.assertEqual(future.result(timeout=2)[0], 200)

    def test_raw_worker_error_is_replaced(self):
        self.online()
        future, job = self.start_job()
        self.assertEqual(self.complete(job, status=502, body={"error": "secret credential must not be forwarded"})[0], 200)
        status, _, body = future.result(timeout=2)
        self.assertEqual(status, 502)
        self.assertEqual(body, {"error": relay.ERRORS[502]})

    def test_day_and_minute_budget(self):
        self.online()
        self.server.relay.minute_stamps = [time.monotonic()] * relay.PER_MINUTE
        self.assertEqual(self.send("POST", "/api/chat", {"message": "你好"}, ORIGIN)[0], 429)
        self.server.relay.minute_stamps = []
        self.server.relay.daily_date = relay.dt.datetime.now(relay.dt.timezone(relay.dt.timedelta(hours=8))).date().isoformat()
        self.server.relay.daily_count = relay.PER_DAY
        self.assertEqual(self.send("POST", "/api/chat", {"message": "你好"}, ORIGIN)[0], 429)


if __name__ == "__main__":
    unittest.main()
