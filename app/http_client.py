import logging
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

log = logging.getLogger("http")


class NotFound(Exception):
    pass


class HttpError(Exception):
    pass


class SchemaError(Exception):
    pass


class RateLimiter:
    def __init__(self, min_interval: float, sleep=time.sleep, clock=time.monotonic):
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            now = self._clock()
            ready_at = self._last.get(host, float("-inf")) + self.min_interval
            delay = max(0.0, ready_at - now)
            self._last[host] = now + delay
        if delay:
            self._sleep(delay)


def retry_after_seconds(value: str | None, cap: float = 60.0) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(float(value), cap)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    delta = when.timestamp() - time.time()
    return min(max(delta, 0.0), cap)


class PoliteClient:
    def __init__(self, user_agent: str, min_interval: float = 1.1, timeout: float = 30.0,
                 retries: int = 3, backoff_base: float = 2.0, on_request=None, transport=None,
                 sleep=time.sleep):
        self.limiter = RateLimiter(min_interval, sleep=sleep)
        self.retries = retries
        self.backoff_base = backoff_base
        self.on_request = on_request
        self._sleep = sleep
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
            transport=transport,
        )

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            cookies: dict | None = None) -> httpx.Response:
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait(host)
            wait = self.backoff_base * (2 ** attempt)
            try:
                resp = self.client.get(url, params=params, headers=headers, cookies=cookies)
            except httpx.HTTPError as exc:
                last_exc = HttpError(f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__)
                self.report(host, False)
                log.warning("GET %s failed (%s), attempt %d", url, exc.__class__.__name__, attempt + 1)
            else:
                if resp.status_code == 404:
                    self.report(host, True)
                    raise NotFound(url)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = HttpError(f"HTTP {resp.status_code} for {url}")
                    self.report(host, False)
                    log.warning("GET %s -> %d, attempt %d", url, resp.status_code, attempt + 1)
                    hinted = retry_after_seconds(resp.headers.get("retry-after"))
                    if hinted is not None:
                        wait = max(wait, hinted)
                elif resp.status_code >= 400:
                    self.report(host, False)
                    raise HttpError(f"HTTP {resp.status_code} for {url}")
                else:
                    self.report(host, True)
                    return resp
            if attempt < self.retries:
                self._sleep(wait)
        raise HttpError(str(last_exc) if last_exc else f"GET {url} failed")

    def report(self, host: str, ok: bool) -> None:
        if self.on_request:
            try:
                self.on_request(host, ok)
            except Exception:
                pass

    def close(self) -> None:
        self.client.close()
