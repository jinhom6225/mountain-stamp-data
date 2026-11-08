import email.message
import io
import os
import socket
import unittest
import urllib.error
import urllib.request
import urllib.response
from unittest import mock

from collector.errors import CallFailed
from collector.http_client import ApiClient, NoRedirect, RawResponse, UrllibTransport, check_url
from collector.tests.support import FAKE_KEY, FakeClock, real_bytes

OK = real_bytes("ktc_top100_list.json")
BASE = "https://apis.data.go.kr/B553662/top100FamtListBasiInfoService/getTop100FamtListBasiInfoList"


class SequenceTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def get(self, url, *, operation, timeout, max_bytes):
        self.urls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def api(*outcomes):
    transport, clock = SequenceTransport(*outcomes), FakeClock()
    return ApiClient(transport, FAKE_KEY, clock.sleep), transport, clock


def body_of(status, body):
    if status != 200:
        raise CallFailed("ktc", "HTTP error", status=status, retryable=status >= 500)
    return body


class ApiClientTest(unittest.TestCase):
    def test_success_adds_the_named_key_last(self):
        client, transport, _ = api(RawResponse(200, OK))
        self.assertEqual(client.call(BASE, "ktc", (("pageNo", "1"),), "serviceKey", body_of), OK)
        self.assertTrue(transport.urls[0].startswith(BASE + "?pageNo=1&serviceKey="))
        client, transport, _ = api(RawResponse(200, OK))
        client.call(BASE, "kfsMnt", (), "ServiceKey", body_of)
        self.assertIn("?ServiceKey=", transport.urls[0])

    def test_retries_with_backoff_then_succeeds(self):
        client, transport, clock = api(RawResponse(500, b""), CallFailed("ktc", "request failed (timeout)"), RawResponse(200, OK))
        client.call(BASE, "ktc", (), "serviceKey", body_of)
        self.assertEqual((len(transport.urls), clock.sleeps), (3, [5.0, 10.0]))

    def test_gives_up_after_retries(self):
        client, transport, _ = api(*[RawResponse(503, b"")] * 3)
        with self.assertRaises(CallFailed) as caught:
            client.call(BASE, "ktc", (), "serviceKey", body_of)
        self.assertEqual((caught.exception.status, len(transport.urls)), (503, 3))

    def test_redirect_oversize_and_refused_base_are_not_retried(self):
        for outcome in (RawResponse(302, b""), RawResponse(200, b"x" * (8 * 1024 * 1024 + 1))):
            client, transport, _ = api(outcome, RawResponse(200, OK))
            with self.assertRaises(CallFailed):
                client.call(BASE, "ktc", (), "serviceKey", body_of)
            self.assertEqual(len(transport.urls), 1)
        for base in ("http://apis.data.go.kr/x", "https://evil.example/x", "https://apis.data.go.kr/x?a=1",
                     "https://u@apis.data.go.kr/x", "https://apis.data.go.kr/x y", "https://apis.data.go.kr/x#f"):
            client, transport, _ = api()
            with self.assertRaises(CallFailed) as caught:
                client.call(base, "ktc", (), "serviceKey", body_of)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(transport.urls, [])

    def test_check_url(self):
        check_url(BASE, frozenset({("https", "apis.data.go.kr")}))


def _response(req, status, body=b"", location=None):
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    response = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, status)
    response.msg = "OK" if status == 200 else "Error"
    return response


class FakeHttpHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Answers http and https without a socket; records what reached the
    HTTP layer (replaces both default handlers in build_opener)."""

    def __init__(self, status=200, body=OK, location=None, error=None):
        super().__init__()
        self.status, self.body, self.location, self.error = status, body, location, error
        self.seen = []

    def http_open(self, req):
        self.seen.append((req.host, req.has_proxy(), req.full_url))
        if self.error:
            raise self.error
        return _response(req, self.status, self.body, self.location)

    https_open = http_open


class UrllibTransportTest(unittest.TestCase):
    def get(self, handler, max_bytes=1 << 20):
        return UrllibTransport([handler]).get(BASE + "?a=1", operation="ktc", timeout=30, max_bytes=max_bytes)

    def test_success(self):
        handler = FakeHttpHandler()
        self.assertEqual(self.get(handler), RawResponse(200, OK))
        self.assertEqual(handler.seen[0][:2], ("apis.data.go.kr", False))

    def test_redirect_is_refused_and_not_followed(self):
        handler = FakeHttpHandler(status=302, location="https://evil.example/collect")
        with self.assertRaises(CallFailed) as caught:
            self.get(handler)
        self.assertEqual((caught.exception.status, caught.exception.retryable), (302, False))
        self.assertEqual(len(handler.seen), 1)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "http://x"))

    def test_http_error_body_is_returned_for_the_decoder(self):
        body = real_bytes("ktc_plus_list_504.json")
        self.assertEqual(self.get(FakeHttpHandler(status=504, body=body)), RawResponse(504, body))
        with self.assertRaises(CallFailed):
            self.get(FakeHttpHandler(status=504, body=b"x" * 101), max_bytes=100)

    def test_network_errors(self):
        for error in (urllib.error.URLError("x"), socket.timeout("x"), TimeoutError("x"), ConnectionResetError("x"), ValueError("x")):
            with self.assertRaises(CallFailed) as caught:
                self.get(FakeHttpHandler(error=error))
            self.assertIn(type(error).__name__, caught.exception.describe())

    def test_body_over_the_cap_is_refused(self):
        with self.assertRaises(CallFailed) as caught:
            self.get(FakeHttpHandler(body=b"x" * 101), max_bytes=100)
        self.assertFalse(caught.exception.retryable)
        self.assertIn("too large", caught.exception.describe())

    def test_proxy_environment_is_ignored(self):
        proxies = {"HTTP_PROXY": "http://proxy.invalid:3128", "HTTPS_PROXY": "http://proxy.invalid:3128",
                   "https_proxy": "http://proxy.invalid:3128", "ALL_PROXY": "http://proxy.invalid:3128"}
        with mock.patch.dict(os.environ, proxies):
            handler = FakeHttpHandler()
            self.get(handler)
        self.assertFalse(handler.seen[0][1])


if __name__ == "__main__":
    unittest.main()
