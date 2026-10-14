import logging
import threading
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger("http")


class NotFound(Exception):
    pass


class HttpError(Exception):
    pass


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            ready_at = self._last.get(host, 0.0) + self.min_interval
            delay = max(0.0, ready_at - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)


class PoliteClient:
    def __init__(self, user_agent: str, min_interval: float = 1.1, timeout: float = 30.0,
                 retries: int = 3, backoff_base: float = 2.0, on_request=None):
        self.limiter = RateLimiter(min_interval)
        self.retries = retries
        self.backoff_base = backoff_base
        self.on_request = on_request
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
        )

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            cookies: dict | None = None) -> httpx.Response:
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait(host)
            try:
                resp = self.client.get(url, params=params, headers=headers, cookies=cookies)
            except httpx.HTTPError as exc:
                last_exc = exc
                self._report(host, False)
                log.warning("GET %s failed (%s), attempt %d", url, exc.__class__.__name__, attempt + 1)
            else:
                if resp.status_code == 404:
                    self._report(host, True)
                    raise NotFound(url)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = HttpError(f"HTTP {resp.status_code} for {url}")
                    self._report(host, False)
                    log.warning("GET %s -> %d, attempt %d", url, resp.status_code, attempt + 1)
                    retry_after = resp.headers.get("retry-after")
                    if retry_after and retry_after.isdigit() and attempt < self.retries:
                        time.sleep(min(int(retry_after), 60))
                        continue
                elif resp.status_code >= 400:
                    self._report(host, False)
                    raise HttpError(f"HTTP {resp.status_code} for {url}")
                else:
                    self._report(host, True)
                    return resp
            if attempt < self.retries:
                time.sleep(self.backoff_base * (2 ** attempt))
        raise HttpError(str(last_exc) if last_exc else f"GET {url} failed")

    def _report(self, host: str, ok: bool) -> None:
        if self.on_request:
            try:
                self.on_request(host, ok)
            except Exception:
                pass

    def close(self) -> None:
        self.client.close()
