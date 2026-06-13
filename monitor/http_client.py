"""HTTP client with rate limiting, conditional GET, and jitter."""

import hashlib
import random
import threading
import time
import urllib.error
import urllib.request

from monitor.config import USER_AGENT, FETCH_TIMEOUT, HEAD_TIMEOUT, BASE_URL
from monitor.logger import log


class FetchResult:
    __slots__ = ("url", "status", "etag", "last_modified", "content", "headers", "error")

    def __init__(self, url: str, status: int = 0, etag: str = None,
                 last_modified: str = None, content: bytes = None,
                 headers: dict = None, error: str = None):
        self.url = url
        self.status = status
        self.etag = etag
        self.last_modified = last_modified
        self.content = content
        self.headers = headers or {}
        self.error = error

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400 and self.content is not None

    @property
    def failed(self) -> bool:
        return self.status == 0 or self.status >= 400

    @property
    def hash(self) -> str:
        if self.content is None:
            return None
        return hashlib.md5(self.content).hexdigest()

    @property
    def text(self) -> str:
        if self.content is None:
            return ""
        return self.content.decode("utf-8", errors="replace")


_rate_limited_until = 0
_rate_limit_lock = threading.Lock()


def _check_rate_limited() -> bool:
    global _rate_limited_until
    with _rate_limit_lock:
        return time.time() < _rate_limited_until


def _mark_rate_limited(retry_after: int = 60):
    global _rate_limited_until
    with _rate_limit_lock:
        _rate_limited_until = time.time() + max(retry_after, 30)
        log(f"Rate limited -- backing off for {max(retry_after, 30)}s", "WARN")


def jitter(base: float = 0.1, spread: float = 0.15):
    time.sleep(base + random.uniform(0, spread))


def fetch(url: str, etag: str = None, last_modified: str = None,
          timeout: int = FETCH_TIMEOUT, headers_extra: dict = None,
          method: str = "GET", data: bytes = None) -> FetchResult:
    if _check_rate_limited():
        return FetchResult(url=url, status=0, error="rate_limited")

    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        **(headers_extra or {}),
    }
    if etag:
        req_headers["If-None-Match"] = etag
    if last_modified:
        req_headers["If-Modified-Since"] = last_modified

    # Bypass WordPress Batcache with a 10s rolling cache buster
    final_url = url
    if BASE_URL in url and "wp-json" in url:
        sep = "&" if "?" in url else "?"
        final_url = f"{url}{sep}_cb={int(time.time() / 10)}"

    req = urllib.request.Request(final_url, headers=req_headers, method=method, data=data)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        content = resp.read()
        result = FetchResult(
            url=url,
            status=resp.status,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            content=content,
            headers=dict(resp.headers.items()),
        )
        if result.status == 304:
            result.content = None
        return result
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return FetchResult(url=url, status=304,
                               etag=e.headers.get("ETag"),
                               last_modified=e.headers.get("Last-Modified"))
        if e.code == 429:
            retry_after = int(e.headers.get("Retry-After", 60))
            _mark_rate_limited(retry_after)
        try:
            err_content = e.read()
        except Exception:
            err_content = b""
        return FetchResult(url=url, status=e.code, content=err_content,
                           headers=dict(e.headers.items()) if e.headers else {},
                           error=str(e))
    except Exception as e:
        return FetchResult(url=url, status=0, error=str(e))


def head_url(url: str, timeout: int = HEAD_TIMEOUT) -> FetchResult:
    if _check_rate_limited():
        return FetchResult(url=url, status=0, error="rate_limited")

    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": USER_AGENT,
    })
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return FetchResult(
            url=url, status=resp.status,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            headers=dict(resp.headers.items()),
        )
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = int(e.headers.get("Retry-After", 60))
            _mark_rate_limited(retry_after)
        return FetchResult(url=url, status=e.code, error=str(e))
    except Exception as e:
        return FetchResult(url=url, status=0, error=str(e))
