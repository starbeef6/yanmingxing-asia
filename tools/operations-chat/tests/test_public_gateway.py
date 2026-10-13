import http.client
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from email.message import Message
from io import BytesIO


GATEWAY_PATH = Path(__file__).resolve().parents[1] / "public_gateway.py"
SPEC = importlib.util.spec_from_file_location("operations_public_gateway", GATEWAY_PATH)
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)
ORIGINAL_OPEN_BACKEND = gateway._open_backend


class MockResponse:
    def __init__(self, value, status=200, content_type="application/json"):
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


def backend_payload(reply="熊大爷在。"):
    return {
        "reply": reply,
        "policy": gateway.PUBLIC_POLICY,
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        "untrusted": "should not pass",
    }


class PublicGatewayHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = gateway.ThreadingHTTPServer(("127.0.0.1", 0), gateway.GatewayHandler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    def setUp(self):
        self.backend_patch = patch.object(gateway, "_open_backend", return_value=MockResponse(backend_payload()))
        self.backend_patch.start()
        self.addCleanup(self.backend_patch.stop)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def send(self, method, path, origin=None, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        request_headers = dict(headers or {})
        if origin is not None:
            request_headers["Origin"] = origin
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
            request_headers.setdefault("Content-Length", str(len(body)))
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def test_allowed_origin_gets_filtered_success_and_fixed_upstream_headers(self):
        seen = {}

        def open_backend(request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            seen["timeout"] = timeout
            return MockResponse(backend_payload())

        with patch.object(gateway, "_open_backend", side_effect=open_backend):
            status, headers, raw = self.send("POST", "/api/chat", "https://yanmingxing.asia", '{"message":"你好"}'.encode("utf-8"), {"Host": "attacker.example"})
        # The gateway's request runs on its server thread; patching is visible to it.
        self.assertEqual(status, 200)
        value = json.loads(raw)
        self.assertEqual(value["reply"], "熊大爷在。")
        self.assertNotIn("untrusted", value)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://yanmingxing.asia")
        self.assertEqual(headers["Vary"], "Origin")
        self.assertNotIn("Access-Control-Allow-Credentials", headers)
        self.assertEqual(seen["url"], gateway.BACKEND_URL)
        self.assertEqual(seen["headers"]["host"], gateway.BACKEND_HOST)
        self.assertEqual(seen["headers"]["origin"], gateway.BACKEND_ORIGIN)
        self.assertEqual(seen["timeout"], 50)

    def test_origin_allowlist_and_missing_origin(self):
        good = sorted(gateway.ALLOWED_ORIGINS)
        for origin in good:
            with self.subTest(origin=origin):
                status, headers, _ = self.send("POST", "/api/chat", origin, b'{"message":"x"}')
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        for origin in (None, "https://evil.example", "null", "https://yanmingxing.asia.evil"):
            with self.subTest(origin=origin):
                status, headers, _ = self.send("POST", "/api/chat", origin, b'{"message":"x"}')
                self.assertEqual(status, 403)
                self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight_allows_safari_headers_but_rejects_authorization(self):
        safari_headers = "content-type, cache-control, pragma, accept, accept-language, content-language"
        status, headers, raw = self.send("OPTIONS", "/api/chat", "https://starbeef6.github.io", headers={
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": safari_headers,
        })
        self.assertEqual(status, 204)
        self.assertEqual(raw, b"")
        self.assertEqual(headers["Access-Control-Allow-Methods"], "POST")
        self.assertEqual(headers["Access-Control-Allow-Headers"], "Content-Type, Cache-Control, Pragma, Accept, Accept-Language, Content-Language")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://starbeef6.github.io")
        self.assertEqual(headers["Vary"], "Origin")
        self.assertNotIn("Access-Control-Allow-Credentials", headers)

        for method, requested_headers in (("GET", "content-type"), ("POST", "authorization"), ("POST", "content-type, x-forwarded-host")):
            with self.subTest(method=method, requested_headers=requested_headers):
                status, headers, _ = self.send("OPTIONS", "/api/chat", "https://yanmingxing.asia", headers={
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": requested_headers,
                })
                self.assertEqual(status, 403)
                self.assertNotIn("Access-Control-Allow-Methods", headers)

    def test_local_preview_origins_can_preflight_and_chat(self):
        for origin in ("http://127.0.0.1:8786", "http://localhost:8786"):
            with self.subTest(origin=origin):
                status, headers, _ = self.send("OPTIONS", "/api/chat", origin, headers={
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                })
                self.assertEqual(status, 204)
                self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
                status, headers, raw = self.send("POST", "/api/chat", origin, b'{"message":"x"}')
                self.assertEqual(status, 200)
                self.assertEqual(headers["Access-Control-Allow-Origin"], origin)
                self.assertEqual(json.loads(raw)["reply"], "熊大爷在。")

    def test_local_preview_does_not_allow_other_ports_or_lookalike_hosts(self):
        for origin in ("http://127.0.0.1:8785", "http://localhost:8785",
                       "https://127.0.0.1:8786", "http://localhost.evil:8786",
                       "http://127.0.0.1:8786/operations/"):
            with self.subTest(origin=origin):
                status, headers, _ = self.send("OPTIONS", "/api/chat", origin, headers={
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                })
                self.assertEqual(status, 403)
                self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_only_chat_and_health_routes_exist(self):
        for method, path in (("GET", "/"), ("GET", "/tools/operations-chat/server.py"),
                             ("POST", "/v1/responses"), ("OPTIONS", "/static/x.js"),
                             ("HEAD", "/healthz"), ("PUT", "/api/chat")):
            with self.subTest(method=method, path=path):
                status, _, _ = self.send(method, path, "https://yanmingxing.asia")
                self.assertEqual(status, 404)

    def test_oversized_payload_is_rejected_before_backend(self):
        body = b" " * (gateway.MAX_BODY + 1)
        status, headers, _ = self.send("POST", "/api/chat", "https://yanmingxing.asia", body)
        self.assertEqual(status, 413)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://yanmingxing.asia")

    def test_errors_and_redirects_are_generic_and_never_followed(self):
        upstream_error = HTTPError(gateway.BACKEND_URL, 500, "secret upstream message", Message(), BytesIO(b"private upstream body"))
        with patch.object(gateway, "_open_backend", side_effect=upstream_error) as mocked:
            status, _, raw = self.send("POST", "/api/chat", "https://yanmingxing.asia", b'{"message":"x"}')
        self.assertEqual(status, 502)
        self.assertNotIn(b"secret", raw)
        self.assertNotIn(b"private upstream body", raw)
        self.assertEqual(mocked.call_count, 1)
        self.assertIsNone(gateway._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example"))

    def test_real_local_backend_redirect_is_not_followed(self):
        visited = []

        class RedirectHandler(gateway.BaseHTTPRequestHandler):
            def do_GET(self):
                visited.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/followed")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, _format, *args):
                return

        backend = gateway.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{backend.server_address[1]}/redirect")
            with self.assertRaises(HTTPError) as caught:
                ORIGINAL_OPEN_BACKEND(request, timeout=2)
            self.assertEqual(caught.exception.code, 302)
            self.assertEqual(visited, ["/redirect"])
        finally:
            backend.shutdown()
            backend.server_close()
            thread.join(timeout=2)

    def test_request_body_size_and_policy_response_are_bounded(self):
        oversized_answer = {"reply": "x" * 8001, "usage": {}}
        with patch.object(gateway, "_open_backend", return_value=MockResponse(oversized_answer)):
            status, _, raw = self.send("POST", "/api/chat", "https://yanmingxing.asia", b'{"message":"x"}')
        self.assertEqual(status, 502)
        self.assertNotIn(b"x" * 100, raw)

        too_large = MockResponse(b" " * (gateway.MAX_RESPONSE + 1))
        with patch.object(gateway, "_open_backend", return_value=too_large):
            status, _, _ = self.send("POST", "/api/chat", "https://yanmingxing.asia", b'{"message":"x"}')
        self.assertEqual(status, 502)

    def test_healthz_exposes_only_ready_and_fixed_policy(self):
        backend_health = {"ready": True, "policy": gateway.PUBLIC_POLICY, "api_key": "must-not-leak"}
        seen = {}

        def open_backend(request, timeout):
            seen["url"] = request.full_url
            seen["host"] = request.get_header("Host")
            seen["timeout"] = timeout
            return MockResponse(backend_health)

        with patch.object(gateway, "_open_backend", side_effect=open_backend):
            status, headers, raw = self.send("GET", "/healthz", "https://yanmingxing.asia")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ready": True, "policy": gateway.PUBLIC_POLICY})
        self.assertNotIn(b"must-not-leak", raw)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://yanmingxing.asia")
        self.assertEqual(seen["url"], gateway.BACKEND_HEALTH_URL)
        self.assertEqual(seen["host"], gateway.BACKEND_HOST)
        self.assertEqual(seen["timeout"], gateway.HEALTH_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
