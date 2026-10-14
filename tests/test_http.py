import email.utils
import time

import httpx
import pytest

from app.http_client import HttpError, NotFound, PoliteClient, RateLimiter, retry_after_seconds


def make(handler, retries=3):
    sleeps = []
    events = []
    client = PoliteClient("UA", min_interval=0, retries=retries, transport=httpx.MockTransport(handler),
                          sleep=sleeps.append, on_request=lambda host, ok: events.append(ok))
    return client, sleeps, events


def test_429_honours_retry_after_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request.url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    client, sleeps, events = make(handler)
    assert client.get("https://backend.example/x").json() == {"ok": True}
    assert len(calls) == 2 and sleeps == [7.0]
    assert events == [False, True]


def test_retry_after_is_capped_and_accepts_http_dates():
    assert retry_after_seconds("3600") == 60.0
    future = email.utils.formatdate(time.time() + 20, usegmt=True)
    assert 15 <= retry_after_seconds(future) <= 21
    assert retry_after_seconds("soon") is None and retry_after_seconds(None) is None


def test_5xx_backs_off_exponentially_and_gives_up():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    client, sleeps, events = make(handler, retries=3)
    with pytest.raises(HttpError, match="503"):
        client.get("https://backend.example/x")
    assert len(calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]
    assert events == [False] * 4


@pytest.mark.parametrize("status,exc", [(404, NotFound), (403, HttpError), (400, HttpError)])
def test_client_errors_are_not_retried(status, exc):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)

    client, sleeps, _ = make(handler)
    with pytest.raises(exc):
        client.get("https://backend.example/x")
    assert len(calls) == 1 and sleeps == []


def test_timeouts_are_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text="ok")

    client, sleeps, events = make(handler)
    assert client.get("https://backend.example/x").text == "ok"
    assert sleeps == [2.0, 4.0] and events == [False, False, True]


def test_timeouts_exhausted_raise_http_error():
    def handler(request):
        raise httpx.ConnectTimeout("down", request=request)

    client, sleeps, _ = make(handler, retries=1)
    with pytest.raises(HttpError, match="ConnectTimeout"):
        client.get("https://backend.example/x")
    assert sleeps == [2.0]


def test_rate_limiter_spaces_requests_per_host():
    now = [100.0]
    slept = []

    def sleep(d):
        slept.append(round(d, 3))
        now[0] += d

    limiter = RateLimiter(1.1, sleep=sleep, clock=lambda: now[0])
    limiter.wait("a")
    limiter.wait("a")
    limiter.wait("b")
    now[0] += 5
    limiter.wait("a")
    assert slept == [1.1]
