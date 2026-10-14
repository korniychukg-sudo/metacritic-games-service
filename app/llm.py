import json
import logging
import re
import threading
import time

import httpx

from .http_client import retry_after_seconds

log = logging.getLogger("llm")


class LLMError(Exception):
    pass


class LLMDisabled(LLMError):
    pass


class LLMSkipped(LLMError):
    pass


class PromptTooLarge(LLMError):
    pass


class LLMBadOutput(LLMError):
    pass


class EmptyReply(LLMError):
    pass


def extract_json(text: str) -> dict:
    if not text:
        raise LLMBadOutput("пустой ответ")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    depth = 0
    in_str = False
    esc = False
    for i in range(start if start >= 0 else len(cleaned), len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(cleaned[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise LLMBadOutput(f"некорректный JSON: {exc}") from exc
                if isinstance(value, dict):
                    return value
                break
    raise LLMBadOutput("в ответе нет JSON-объекта")


def is_anonymous_pollinations(base_url: str, api_key: str) -> bool:
    return "pollinations.ai" in base_url and not api_key


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60.0,
                 min_gap: float | None = None, retries: int = 3, temperature: float | None = 0.2,
                 enabled: bool = True, on_call=None, max_prompt_chars: int | None = None,
                 min_prompt_chars: int = 900, failure_threshold: int = 3, backoff_base: float = 2.0,
                 transport=None, sleep=time.sleep, clock=time.monotonic, on_state=None,
                 reasoning_effort: str | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.anonymous = is_anonymous_pollinations(base_url, api_key)
        if min_gap is None:
            min_gap = 32.0 if self.anonymous else 3.0
        if not max_prompt_chars:
            max_prompt_chars = 6000 if self.anonymous else 9000
        effort = (reasoning_effort or "").strip().lower()
        if not effort:
            effort = "low" if self.anonymous else "none"
        self.reasoning_effort = "" if effort in ("none", "off", "0") else effort
        self.max_prompt_chars = max_prompt_chars
        self.min_prompt_chars = min(min_prompt_chars, max_prompt_chars)
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.min_gap = min_gap
        self.retries = max(1, retries)
        self.temperature = temperature
        self.enabled = enabled
        self.on_call = on_call
        self.on_state = on_state
        self.failure_threshold = max(1, failure_threshold)
        self.backoff_base = backoff_base
        self.consecutive_failures = 0
        self.circuit_open = False
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last_start = float("-inf")
        self._client = httpx.Client(timeout=timeout, transport=transport)

    @property
    def available(self) -> bool:
        return self.enabled and not self.circuit_open

    def begin_run(self) -> None:
        self.consecutive_failures = 0
        if self.circuit_open:
            log.info("LLM circuit closed for the new run")
        self.circuit_open = False

    def _wait_turn(self) -> None:
        wait = self._last_start + self.min_gap - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_start = self._clock()

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> str:
        if not self.enabled:
            raise LLMDisabled("LLM отключена настройкой LLM_ENABLED=0")
        if self.circuit_open:
            raise LLMSkipped("LLM пропущена до конца запуска после нескольких неудач подряд")
        body: dict = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if max_tokens:
            body["max_tokens"] = max_tokens
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        last_err: Exception | None = None
        with self._lock:
            for attempt in range(self.retries):
                if attempt:
                    self._sleep(self._backoff(attempt, last_err))
                self._wait_turn()
                started = self._clock()
                try:
                    resp = self._client.post(self.url, json=body, headers=self._headers())
                except httpx.HTTPError as exc:
                    last_err = LLMError(f"{exc.__class__.__name__}")
                    self._report(False, self._clock() - started)
                    log.warning("LLM call failed (%s), attempt %d/%d", exc.__class__.__name__, attempt + 1,
                                self.retries)
                    continue
                latency = self._clock() - started
                code = resp.status_code
                if code == 413:
                    self._report(False, latency)
                    raise PromptTooLarge("HTTP 413: промпт слишком большой")
                if code == 402 and not self.anonymous:
                    self._report(False, latency)
                    raise LLMError("HTTP 402: у ключа закончился баланс или нет доступа к модели")
                if code in (402, 429) or code >= 500:
                    last_err = RateLimited(code, retry_after_seconds(resp.headers.get("retry-after")))
                    self._report(False, latency)
                    log.warning("LLM call failed (HTTP %d%s), attempt %d/%d", code,
                                ", лимит частоты" if code in (402, 429) else "", attempt + 1, self.retries)
                    continue
                if code >= 400:
                    self._report(False, latency)
                    raise LLMError(f"HTTP {code}: {resp.text[:200]}")
                content, finish = None, None
                try:
                    data = resp.json()
                    choice = (data.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason")
                    content = (choice.get("message") or {}).get("content")
                except (ValueError, AttributeError, IndexError):
                    pass
                if not content or not str(content).strip():
                    self._report(False, latency)
                    log.warning("LLM returned an empty reply (finish_reason=%s, %.1f s)", finish, latency)
                    raise EmptyReply(f"пустой ответ модели (finish_reason={finish})")
                self._report(True, latency)
                return content
        raise LLMError(str(last_err) if last_err else "LLM недоступна")

    def _backoff(self, attempt: int, err: Exception | None) -> float:
        wait = self.backoff_base * (2 ** (attempt - 1))
        if isinstance(err, RateLimited) and err.retry_after is not None:
            wait = max(wait, err.retry_after)
        return wait

    def chat_json(self, messages: list[dict], validator=None) -> dict:
        last: Exception | None = None
        for _ in range(2):
            content = self.chat(messages)
            try:
                value = extract_json(content)
                if validator:
                    value = validator(value)
                return value
            except LLMError as exc:
                last = exc
                log.warning("LLM returned unusable JSON: %s", exc)
        raise LLMBadOutput(str(last))

    def ask_json(self, build, validator=None, attempts: int = 4) -> dict:
        if not self.enabled:
            raise LLMDisabled("LLM отключена настройкой LLM_ENABLED=0")
        if self.circuit_open:
            raise LLMSkipped("LLM пропущена до конца запуска после нескольких неудач подряд")
        budget = self.max_prompt_chars
        last: LLMError | None = None
        for _ in range(attempts):
            try:
                result = self.chat_json(build(budget), validator)
            except (PromptTooLarge, EmptyReply) as exc:
                last = exc
                smaller = max(self.min_prompt_chars, int(budget * 0.6))
                if smaller >= budget:
                    break
                log.info("LLM %s, budget %d -> %d chars for this call",
                         "returned empty replies" if isinstance(exc, EmptyReply) else "rejected the prompt size",
                         budget, smaller)
                budget = smaller
                continue
            except LLMError as exc:
                last = exc
                break
            self._succeeded()
            return result
        self._failed(last)
        raise last or LLMError("LLM недоступна")

    def _succeeded(self) -> None:
        self.consecutive_failures = 0

    def _failed(self, err: Exception | None) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold and not self.circuit_open:
            self.circuit_open = True
            log.warning("LLM failed %d times in a row (%s); quote-based summaries until the end of the run",
                        self.consecutive_failures, err)
            if self.on_state:
                try:
                    self.on_state("skipped", f"{self.consecutive_failures} неудачи подряд, до конца запуска "
                                             "резюме строятся без LLM")
                except Exception:
                    pass

    def _report(self, ok: bool, latency: float) -> None:
        if self.on_call:
            try:
                self.on_call(ok, latency)
            except Exception:
                pass


class RateLimited(LLMError):
    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"HTTP {status}: лимит частоты запросов" if status in (402, 429) else f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after
