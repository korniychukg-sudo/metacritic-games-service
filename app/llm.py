import json
import logging
import re
import threading
import time

import httpx

log = logging.getLogger("llm")


class LLMError(Exception):
    pass


class LLMRejected(LLMError):
    pass


def extract_json(text: str) -> dict:
    if not text:
        raise LLMError("пустой ответ")
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
                    raise LLMError(f"некорректный JSON: {exc}") from exc
                if isinstance(value, dict):
                    return value
                break
    raise LLMError("в ответе нет JSON-объекта")


def is_anonymous_pollinations(base_url: str, api_key: str) -> bool:
    return "pollinations.ai" in base_url and not api_key


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 120.0,
                 min_gap: float | None = None, retries: int = 3, temperature: float | None = 0.2,
                 enabled: bool = True, on_call=None, max_prompt_chars: int | None = None,
                 min_prompt_chars: int = 900):
        self.url = base_url.rstrip("/") + "/chat/completions"
        anonymous = is_anonymous_pollinations(base_url, api_key)
        if min_gap is None:
            min_gap = 32.0 if anonymous else 3.0
        if not max_prompt_chars:
            max_prompt_chars = 6000 if anonymous else 9000
        self.max_prompt_chars = max_prompt_chars
        self.min_prompt_chars = min(min_prompt_chars, max_prompt_chars)
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.min_gap = min_gap
        self.retries = retries
        self.temperature = temperature
        self.enabled = enabled
        self.on_call = on_call
        self._lock = threading.Lock()
        self._last_start = -1e9
        self._client = httpx.Client(timeout=timeout)

    def _wait_turn(self) -> None:
        wait = self._last_start + self.min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_start = time.monotonic()

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> str:
        if not self.enabled:
            raise LLMError("LLM отключена настройкой LLM_ENABLED=0")
        body: dict = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if max_tokens:
            body["max_tokens"] = max_tokens
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_err: Exception | None = None
        with self._lock:
            for attempt in range(self.retries):
                self._wait_turn()
                started = time.monotonic()
                try:
                    resp = self._client.post(self.url, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    last_err = exc
                    self._report(False, time.monotonic() - started)
                    log.warning("LLM call failed (%s), attempt %d/%d", exc.__class__.__name__, attempt + 1,
                                self.retries)
                    continue
                latency = time.monotonic() - started
                if resp.status_code in (402, 413):
                    self._report(False, latency)
                    raise LLMRejected(f"HTTP {resp.status_code}: запрос отклонён (лимит частоты или размера)")
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = LLMError(f"HTTP {resp.status_code}")
                    self._report(False, latency)
                    log.warning("LLM call failed (HTTP %d), attempt %d/%d", resp.status_code, attempt + 1,
                                self.retries)
                    time.sleep(2 * (2 ** attempt))
                    continue
                if resp.status_code >= 400:
                    self._report(False, latency)
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                try:
                    data = resp.json()
                    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
                except ValueError:
                    content = None
                if not content or not content.strip():
                    self._report(False, latency)
                    raise LLMRejected("пустой ответ модели")
                self._report(True, latency)
                return content
        raise LLMError(str(last_err) if last_err else "LLM недоступна")

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
        raise LLMError(str(last))

    def shrink(self) -> bool:
        new = max(self.min_prompt_chars, int(self.max_prompt_chars * 0.6))
        changed = new < self.max_prompt_chars
        if changed:
            log.info("LLM rejected the prompt size, budget %d -> %d chars", self.max_prompt_chars, new)
        self.max_prompt_chars = new
        return changed

    def ask_json(self, build, validator=None, attempts: int = 4) -> dict:
        last: LLMRejected | None = None
        for attempt in range(attempts):
            try:
                return self.chat_json(build(self.max_prompt_chars), validator)
            except LLMRejected as exc:
                last = exc
                if attempt >= 1 and not self.shrink():
                    break
        raise last or LLMRejected("запрос отклонён")

    def _report(self, ok: bool, latency: float) -> None:
        if self.on_call:
            try:
                self.on_call(ok, latency)
            except Exception:
                pass
