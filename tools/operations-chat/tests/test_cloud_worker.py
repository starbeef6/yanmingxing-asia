import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from io import BytesIO


WORKER_PATH = Path(__file__).resolve().parents[1] / "cloud_worker.py"
SPEC = importlib.util.spec_from_file_location("operations_cloud_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
RELAY = "https://operations-relay-example.a.run.app"
TEST_TOKEN = "fictional_token_for_unit_tests_only"


class Response:
    def __init__(self, value, status=200):
        self.status = status
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.raw if size < 0 else self.raw[:size]


def receipt(**overrides):
    value = {
        "reply": "熊大爷在。", "policy": worker.POLICY,
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        "secret": "discard this extra field",
    }
    value.update(overrides)
    return value


def job():
    return {"id": "random-uuid-123", "payload": {"message": "你好", "context": {}}}


class URLAndConfigurationTests(unittest.TestCase):
    def test_cloud_url_requires_https_service_origin(self):
        self.assertEqual(worker.validate_relay_url(RELAY + "/"), RELAY)
        self.assertEqual(worker.validate_relay_url(RELAY + ":443"), RELAY)
        for value in (None, "", "http://operations-relay-example.a.run.app", RELAY + "/api/chat",
                      RELAY + "/?key=x", RELAY + "#x", RELAY + ":8443", "https://evil.example",
                      "https://a.run.app", "https://valid.a.run.app.evil.example", "https://127.0.0.1",
                      "https://name@operations-relay-example.a.run.app", RELAY + "\n", "https://x_y.a.run.app"):
            with self.subTest(value=value):
                with self.assertRaises(worker.WorkerError) as caught:
                    worker.validate_relay_url(value)
                self.assertEqual(str(caught.exception), "invalid_relay_url")

    def test_runtime_inputs_are_read_only_from_fictional_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            token = Path(directory) / "token"
            config.write_text(json.dumps({"url": RELAY}))
            token.write_text(TEST_TOKEN + "\n")
            token.chmod(0o600)
            self.assertEqual(worker.load_connection(config, token), (RELAY, TEST_TOKEN))
            token.chmod(0o644)
            with self.assertRaises(worker.WorkerError) as caught:
                worker.load_connection(config, token)
            self.assertEqual(caught.exception.code, "unsafe_token_permissions")

    def test_missing_or_invalid_config_has_sanitized_error(self):
        with tempfile.TemporaryDirectory() as directory:
            config, token = Path(directory) / "config", Path(directory) / "token"
            with self.assertRaises(worker.WorkerError) as caught:
                worker.load_connection(config, token)
            self.assertEqual(str(caught.exception), "private_config_unavailable")
            config.write_text("not json")
            with self.assertRaises(worker.WorkerError) as caught:
                worker.load_connection(config, token)
            self.assertEqual(str(caught.exception), "invalid_private_config")

    def test_job_bounds_and_identifiers(self):
        self.assertEqual(worker.validate_job(job()), ("random-uuid-123", job()["payload"]))
        for value in (None, [], {}, {"id": "../x", "payload": {}}, {"id": "x" * 101, "payload": {}},
                      {"id": "x", "payload": []}, {"id": "x", "payload": {"message": "x" * worker.MAX_BODY}}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(worker.WorkerError):
                    worker.validate_job(value)


class TransportAndReceiptTests(unittest.TestCase):
    def test_local_forwarding_uses_only_fixed_loopback_host_origin(self):
        seen = {}

        def capture(request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            seen["timeout"] = timeout
            seen["payload"] = json.loads(request.data)
            return Response(receipt())

        payload = {"message": "你好", "upstream_url": "https://evil.example", "headers": {"Authorization": "ignored"}}
        with patch.object(worker, "_open_backend", side_effect=capture):
            status, body = worker.call_backend(payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], "熊大爷在。")
        self.assertNotIn("secret", body)
        self.assertEqual(seen["url"], "http://127.0.0.1:8787/api/chat")
        self.assertEqual(seen["headers"]["host"], "127.0.0.1:8787")
        self.assertEqual(seen["headers"]["origin"], "http://127.0.0.1:8787")
        self.assertNotIn("authorization", seen["headers"])
        self.assertEqual(seen["timeout"], 45)

    def test_local_transport_explicitly_bypasses_proxy_and_refuses_redirect(self):
        opener = Mock()
        with patch.object(worker, "build_opener", return_value=opener) as factory:
            worker._open_backend(Mock(), 45)
        proxy_handler, redirect_handler = factory.call_args.args
        self.assertEqual(proxy_handler.proxies, {})
        self.assertIsInstance(redirect_handler, worker._NoRedirect)
        self.assertIsNone(redirect_handler.redirect_request(None, None, 302, "", None, "https://evil.example"))

    def test_cloud_auth_only_sent_to_fixed_paths_without_browser_origin(self):
        seen = {}

        def capture(request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            seen["timeout"] = timeout
            return Response({"job": None})

        with patch.object(worker, "_open_cloud", side_effect=capture):
            self.assertEqual(worker._cloud_json(RELAY, TEST_TOKEN, "/internal/pull", {}, 10), {"job": None})
        self.assertEqual(seen["url"], RELAY + "/internal/pull")
        self.assertEqual(seen["headers"]["authorization"], "Bearer " + TEST_TOKEN)
        self.assertNotIn("origin", seen["headers"])
        self.assertEqual(seen["timeout"], 10)
        with patch.object(worker, "_open_cloud") as transport:
            with self.assertRaises(worker.WorkerError):
                worker._cloud_json(RELAY, TEST_TOKEN, "/other", {}, 10)
            transport.assert_not_called()

    def test_cloud_redirect_and_error_details_are_never_returned(self):
        error = HTTPError("https://secret.example?token=private", 302, "private", {}, BytesIO(b"private body"))
        with patch.object(worker, "_open_cloud", side_effect=error):
            with self.assertRaises(worker.WorkerError) as caught:
                worker._cloud_json(RELAY, TEST_TOKEN, "/internal/pull", {}, 10)
        self.assertEqual(str(caught.exception), "relay_http_error")
        self.assertFalse(caught.exception.retryable)

    def test_cloud_response_must_be_bounded_json_object(self):
        for raw in (b"[]", b"not json", b"x" * (worker.MAX_RESPONSE + 1)):
            with self.subTest(raw_length=len(raw)), patch.object(worker, "_open_cloud", return_value=Response(raw)):
                with self.assertRaises(worker.WorkerError):
                    worker._cloud_json(RELAY, TEST_TOKEN, "/internal/pull", {}, 10)

    def test_only_fixed_luna_policy_can_produce_success(self):
        for bad in (receipt(policy={}), receipt(policy={**worker.POLICY, "model": "other"}), receipt(reply=""),
                    receipt(reply="x" * 8001), None):
            with self.subTest(bad_type=type(bad).__name__):
                status, body = worker._safe_result(200, bad)
                self.assertEqual(status, 502)
                self.assertNotIn("reply", body)
        status, body = worker._safe_result(200, receipt(usage={"input_tokens": True, "output_tokens": -2, "total_tokens": 5}))
        self.assertEqual(status, 200)
        self.assertEqual(body["usage"], {"total_tokens": 5})

    def test_backend_failures_have_generic_messages_and_no_body_leak(self):
        for status in (400, 403, 413, 415, 429, 500, 503):
            with self.subTest(status=status):
                error = HTTPError(worker.BACKEND_URL, status, "private detail", {}, BytesIO(b"private body"))
                with patch.object(worker, "_open_backend", side_effect=error):
                    actual, body = worker.call_backend({"message": "x"})
                self.assertEqual(actual, status if status in (400, 413, 415, 429, 503) else 502)
                self.assertNotIn("private", json.dumps(body))
        for failure in (URLError("private detail"), TimeoutError(), worker.WorkerError("invalid_response")):
            with patch.object(worker, "_open_backend", side_effect=failure):
                self.assertEqual(worker.call_backend({"message": "x"})[0], 502)


class WorkerFlowTests(unittest.TestCase):
    def setUp(self):
        self.stop = Mock(spec=threading.Event)
        self.stop.wait.return_value = False
        self.client = worker.RelayWorker(RELAY, TEST_TOKEN, self.stop)

    def test_pull_handles_idle_and_validates_job(self):
        with patch.object(worker, "_cloud_json", return_value={"job": None}):
            self.assertIsNone(self.client.pull())
        with patch.object(worker, "_cloud_json", return_value={"job": job()}):
            self.assertEqual(self.client.pull(), job())
        for response in ({}, {"job": "invalid"}):
            with patch.object(worker, "_cloud_json", return_value=response), self.assertRaises(worker.WorkerError):
                self.client.pull()

    def test_completion_transport_retry_never_repeats_model_call(self):
        with patch.object(worker, "call_backend", return_value=(200, receipt())) as backend, patch.object(worker, "_cloud_json", side_effect=[
            worker.WorkerError("relay_transport_error", True),
            worker.WorkerError("relay_transport_error", True), {"accepted": True},
        ]) as cloud:
            self.client.handle_job(job())
        backend.assert_called_once_with(job()["payload"])
        self.assertEqual(cloud.call_count, 3)
        for call in cloud.call_args_list:
            self.assertEqual(call.args[2], "/internal/complete")
            self.assertEqual(call.args[3]["id"], job()["id"])
            self.assertEqual(call.args[4], 10)

    def test_uncertain_completion_then_duplicate_job_reuses_cached_reply(self):
        failure = worker.WorkerError("relay_transport_error", True)
        with patch.object(worker, "call_backend", return_value=(200, receipt())) as backend, patch.object(worker, "_cloud_json", side_effect=[
            failure, failure, failure, {"accepted": True},
        ]) as cloud:
            with self.assertRaises(worker.WorkerError):
                self.client.handle_job(job())
            self.client.handle_job(job())
        backend.assert_called_once()
        self.assertEqual(cloud.call_count, 4)

    def test_stale_job_rejection_does_not_retry_or_repeat_backend(self):
        with patch.object(worker, "call_backend", return_value=(200, receipt())) as backend, patch.object(worker, "_cloud_json", return_value={"accepted": False}) as cloud:
            self.client.handle_job(job())
            self.client.handle_job(job())
        self.assertEqual(backend.call_count, 1)
        self.assertEqual(cloud.call_count, 2)
        self.stop.wait.assert_not_called()

    def test_permanent_completion_failure_does_not_retry(self):
        with patch.object(worker, "_cloud_json", side_effect=worker.WorkerError("relay_http_error")) as cloud:
            with self.assertRaises(worker.WorkerError):
                self.client.complete("job-id", 502, {"error": "generic"})
        cloud.assert_called_once()

    def test_stop_during_completion_retry_does_not_repeat_model(self):
        self.stop.wait.return_value = True
        with patch.object(worker, "call_backend", return_value=(502, {"error": "generic"})) as backend, patch.object(worker, "_cloud_json", side_effect=worker.WorkerError("relay_transport_error", True)) as cloud:
            with self.assertRaises(worker.WorkerError) as caught:
                self.client.handle_job(job())
        self.assertEqual(caught.exception.code, "worker_stopped")
        backend.assert_called_once()
        cloud.assert_called_once()

    def test_missing_config_waits_with_capped_backoff_and_redacted_log(self):
        stop = Mock(spec=threading.Event)
        stop.is_set.side_effect = [False] * 5 + [True]
        with patch.object(worker, "load_connection", side_effect=worker.WorkerError("private_config_unavailable")), patch("builtins.print") as log:
            worker.run(stop)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [5, 10, 20, 30, 30])
        log.assert_called_once_with("operations cloud worker: private_config_unavailable", flush=True)

    def test_idle_worker_polls_every_five_seconds(self):
        stop = Mock(spec=threading.Event)
        stop.is_set.side_effect = [False, False, True]
        with patch.object(worker, "load_connection", return_value=(RELAY, TEST_TOKEN)), patch.object(worker, "_cloud_json", return_value={"job": None}), patch("builtins.print") as log:
            worker.run(stop)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [5, 5])
        log.assert_called_once_with("operations cloud worker: ready", flush=True)


if __name__ == "__main__":
    unittest.main()
