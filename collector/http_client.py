"""HTTP for the data.go.kr APIs (copied from trail-walk-data, GPX part removed).

Safety rules:
- every URL is checked against an origin allow-list before a request;
- redirects are refused and proxy environment variables are ignored;
- bodies are capped; errors carry only the operation name, HTTP status and
  result code, never a URL.
"""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, FrozenSet, Optional, Protocol, Sequence, Tuple, TypeVar

from .config import API_ORIGINS, BACKOFF_BASE_SECONDS, MAX_API_BYTES, RETRIES, TIMEOUT_SECONDS, USER_AGENT
from .errors import CallFailed

T = TypeVar("T")
Params = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class RawResponse:
    status: int
    body: bytes


class Transport(Protocol):
    def get(self, url: str, *, operation: str, timeout: float, max_bytes: int) -> RawResponse: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the key in a query never reaches another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def check_url(url: str, origins: FrozenSet[Tuple[str, str]]) -> None:
    """Allow only known scheme/host pairs, no credentials, query or fragment."""
    parts = urllib.parse.urlsplit(url)
    bad_chars = any(ch <= " " or ch == "\x7f" for ch in url)
    if bad_chars or (parts.scheme, parts.netloc) not in origins or parts.query or parts.fragment or "@" in parts.netloc:
        raise CallFailed("endpoint", "URL refused", retryable=False)


class UrllibTransport:
    """Real network transport (urllib, no proxy, no redirects)."""

    def __init__(self, extra_handlers: Sequence[urllib.request.BaseHandler] = ()) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), *extra_handlers)

    def get(self, url: str, *, operation: str, timeout: float, max_bytes: int) -> RawResponse:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status = response.status
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                body = error.read(max_bytes + 1)
            except (OSError, http.client.HTTPException):
                body = b""
            error.close()
            if 300 <= status < 400:
                raise CallFailed(operation, "redirect refused", status=status, retryable=False) from None
            # 504 bodies carry the gateway reason code; the decoder reads it.
            if len(body) > max_bytes:
                raise CallFailed(operation, "response too large", status=status, retryable=False) from None
            return RawResponse(status=status, body=body)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
            raise CallFailed(operation, f"request failed ({type(error).__name__})") from None
        except (http.client.HTTPException, OSError, ValueError) as error:
            raise CallFailed(operation, f"request failed ({type(error).__name__})") from None
        if len(body) > max_bytes:
            raise CallFailed(operation, "response too large", retryable=False)
        return RawResponse(status=status, body=body)


def _with_retries(operation: Callable[[], T], sleep: Callable[[float], None], retries: int) -> T:
    last: Optional[CallFailed] = None
    for attempt in range(retries + 1):
        if attempt:
            sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        try:
            return operation()
        except CallFailed as error:
            if not error.retryable:
                raise
            last = error
    assert last is not None
    raise last


class ApiClient:
    """Calls one data.go.kr operation with retries; the key is added last.

    `decode` gets the HTTP status and body: the gateway answers some errors
    (504 service timeout) with a status other than 200 and a body that names
    the reason, so the decoder decides what the failure is."""

    def __init__(
        self,
        transport: Transport,
        key: str,
        sleep: Callable[[float], None],
        timeout: float = TIMEOUT_SECONDS,
        retries: int = RETRIES,
    ) -> None:
        self._transport = transport
        self._key = key
        self._sleep = sleep
        self._timeout = timeout
        self._retries = retries

    def call(
        self,
        base_url: str,
        operation: str,
        params: Params,
        key_param: str,
        decode: Callable[[int, bytes], T],
    ) -> T:
        check_url(base_url, API_ORIGINS)
        url = base_url + "?" + urllib.parse.urlencode(list(params) + [(key_param, self._key)])

        def attempt() -> T:
            response = self._transport.get(url, operation=operation, timeout=self._timeout, max_bytes=MAX_API_BYTES)
            if len(response.body) > MAX_API_BYTES:
                raise CallFailed(operation, "response too large", retryable=False)
            if 300 <= response.status < 400:
                raise CallFailed(operation, "redirect refused", status=response.status, retryable=False)
            return decode(response.status, response.body)

        return _with_retries(attempt, self._sleep, self._retries)
