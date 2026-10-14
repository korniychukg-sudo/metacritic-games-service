import json

import httpx
import pytest

from app.llm import EmptyReply, LLMClient, LLMDisabled, LLMError, LLMSkipped, PromptTooLarge
from app.metacritic import parse_composer
from app.summarize import build_review_prompt, summarize_reviews, validate_review_json

from .conftest import load_json

GOOD = '```json\n{"likes": ["плюс"], "dislikes": [], "verdict": "вывод"}\n```'


class Time:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 2))
        self.now += seconds


def ok(content=GOOD):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make(handler, base="https://text.pollinations.ai/openai", **kw):
    t = Time()
    states = []
    llm = LLMClient(base, "openai", transport=httpx.MockTransport(handler), sleep=t.sleep, clock=t.clock,
                    on_state=lambda status, detail: states.append(status), **kw)
    return llm, t, states


def prompt_size(request):
    return sum(len(m["content"]) for m in json.loads(request.content)["messages"])


def game():
    return parse_composer(load_json("composer_game_star-wars-galactic-racer.json"))


def ask(llm):
    g = game()
    return llm.ask_json(lambda budget: build_review_prompt(g.title, "critic", g.critic_reviews, budget),
                        validate_review_json)


def test_anonymous_402_is_a_rate_limit_backs_off_and_never_shrinks():
    sizes = []

    def handler(request):
        sizes.append(prompt_size(request))
        return httpx.Response(402, json={}) if len(sizes) < 3 else ok()

    llm, t, _ = make(handler, max_prompt_chars=2500)
    assert ask(llm)["likes"] == ["плюс"]
    assert len(sizes) == 3 and len(set(sizes)) == 1
    assert llm.max_prompt_chars == 2500
    assert sum(t.sleeps) >= 2 * 32
    assert llm.consecutive_failures == 0


def test_keyed_402_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(402, json={"error": "no credit"})

    llm, _, _ = make(handler, base="https://api.example.test/v1", api_key="k")
    with pytest.raises(LLMError, match="402"):
        llm.chat([{"role": "user", "content": "x"}])
    assert len(calls) == 1


def test_413_shrinks_only_that_call_and_the_budget_is_restored():
    sizes = []

    def handler(request):
        size = prompt_size(request)
        sizes.append(size)
        return httpx.Response(413) if size > 1500 else ok()

    llm, _, _ = make(handler, max_prompt_chars=2500)
    ask(llm)
    first_call = list(sizes)
    assert first_call[0] > 1500 and first_call[-1] <= 1500 and len(first_call) >= 2
    assert llm.max_prompt_chars == 2500
    sizes.clear()
    ask(llm)
    assert sizes[0] == first_call[0]


def test_413_at_minimum_budget_gives_up():
    def handler(request):
        return httpx.Response(413)

    llm, _, _ = make(handler, max_prompt_chars=1000, min_prompt_chars=900)
    with pytest.raises(PromptTooLarge):
        ask(llm)


def test_429_and_5xx_are_retried_with_retry_after():
    codes = iter([httpx.Response(429, headers={"Retry-After": "40"}), httpx.Response(502), ok("готово")])

    def handler(request):
        return next(codes)

    llm, t, _ = make(handler, base="https://api.example.test/v1", api_key="k", min_gap=0)
    assert llm.chat([{"role": "user", "content": "x"}]) == "готово"
    assert t.sleeps[0] == 40 and t.sleeps[1] == 4


def test_network_errors_are_retried():
    replies = iter(["timeout", "good"])

    def handler(request):
        if next(replies) == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return ok("ответ")

    llm, _, _ = make(handler, min_gap=0)
    assert llm.chat([{"role": "user", "content": "x"}]) == "ответ"


def test_empty_reply_shrinks_only_that_call():
    sizes = []

    def handler(request):
        sizes.append(prompt_size(request))
        if len(sizes) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}]})
        return ok()

    llm, _, _ = make(handler, max_prompt_chars=3000)
    assert ask(llm)["likes"] == ["плюс"]
    assert len(sizes) == 2 and sizes[1] < sizes[0]
    assert llm.max_prompt_chars == 3000 and llm.consecutive_failures == 0
    sizes.clear()
    with pytest.raises(EmptyReply, match="length"):
        llm.chat([{"role": "user", "content": "x"}])


def test_reasoning_effort_is_low_for_anonymous_pollinations_only():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return ok("ok")

    for base, key, effort, expected in (
        ("https://text.pollinations.ai/openai", "", None, "low"),
        ("https://api.example.test/v1", "k", None, None),
        ("https://api.example.test/v1", "k", "medium", "medium"),
        ("https://text.pollinations.ai/openai", "", "none", None),
        ("https://text.pollinations.ai/openai", "", "", "low"),
    ):
        llm, _, _ = make(handler, base=base, api_key=key, reasoning_effort=effort, min_gap=0)
        llm.chat([{"role": "user", "content": "x"}])
        assert bodies[-1].get("reasoning_effort") == expected, (base, effort)


def test_retries_exhausted_raise():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    llm, _, _ = make(handler, retries=3, min_gap=0)
    with pytest.raises(LLMError, match="503"):
        llm.chat([{"role": "user", "content": "x"}])
    assert len(calls) == 3


def test_three_failed_calls_open_the_circuit_for_the_rest_of_the_run():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    llm, _, states = make(handler, retries=1, min_gap=0, failure_threshold=3)
    for _ in range(3):
        with pytest.raises(LLMError):
            ask(llm)
    assert llm.circuit_open and not llm.available and states == ["skipped"]
    before = len(calls)
    with pytest.raises(LLMSkipped):
        ask(llm)
    g = game()
    summary = summarize_reviews(llm, g.title, "critic", g.critic_reviews, g.critic_total)
    assert summary["method"] == "extractive" and "error" not in summary and "пропущена" in summary["note"]
    assert len(calls) == before
    llm.begin_run()
    assert llm.available and llm.consecutive_failures == 0


def test_success_resets_the_failure_streak():
    replies = iter([httpx.Response(503), httpx.Response(503), ok(), httpx.Response(503)])

    def handler(request):
        return next(replies)

    llm, _, _ = make(handler, retries=1, min_gap=0, failure_threshold=2)
    with pytest.raises(LLMError):
        ask(llm)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "x"}])
    ask(llm)
    assert llm.consecutive_failures == 0
    with pytest.raises(LLMError):
        ask(llm)
    assert not llm.circuit_open


def test_disabled_llm_raises_without_calls():
    def handler(request):
        raise AssertionError("no calls expected")

    llm, _, _ = make(handler, enabled=False)
    with pytest.raises(LLMDisabled):
        ask(llm)
    g = game()
    s = summarize_reviews(llm, g.title, "user", g.user_reviews, g.user_total)
    assert s["method"] == "extractive" and "отключена" in s["note"]


def test_calls_are_paced_start_to_start():
    starts = []
    t_holder = {}

    def handler(request):
        starts.append(t_holder["t"].now)
        return ok("ok")

    llm, t, _ = make(handler, min_gap=32)
    t_holder["t"] = t
    for _ in range(3):
        llm.chat([{"role": "user", "content": "x"}])
    assert [round(b - a) for a, b in zip(starts, starts[1:])] == [32, 32]


def test_defaults_depend_on_the_endpoint():
    anon = LLMClient("https://text.pollinations.ai/openai", "openai")
    assert anon.max_prompt_chars == 6000 and anon.min_gap == 32.0 and anon.anonymous
    keyed = LLMClient("https://api.openai.com/v1", "gpt", api_key="x")
    assert keyed.max_prompt_chars == 9000 and keyed.min_gap == 3.0 and not keyed.anonymous


def test_authorization_header_only_with_key():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return ok("ok")

    llm, _, _ = make(handler, min_gap=0)
    llm.chat([{"role": "user", "content": "x"}])
    keyed, _, _ = make(handler, base="https://api.example.test/v1", api_key="secret", min_gap=0)
    keyed.chat([{"role": "user", "content": "x"}])
    assert seen == [None, "Bearer secret"]
