from app.llm import LLMError, extract_json
from app.metacritic import Review, parse_composer
from app.summarize import (
    build_review_prompt,
    extractive_review_summary,
    extractive_transcript_summary,
    reviews_hash,
    summarize_reviews,
    validate_review_json,
)

from .conftest import FakeLLM, load_json


def game():
    return parse_composer(load_json("composer_game_star-wars-galactic-racer.json"))


def test_llm_summary_is_used_when_available():
    g = game()
    llm = FakeLLM()
    s = summarize_reviews(llm, g.title, "critic", g.critic_reviews, g.critic_total)
    assert s["method"] == "llm" and s["likes"] == ["красивые трассы"]
    assert s["review_count"] == 74 and s["sample_size"] == len(g.critic_reviews)
    assert s["reviews_hash"] == reviews_hash(g.critic_reviews)


def test_fallback_to_extractive_when_llm_fails():
    g = game()
    s = summarize_reviews(FakeLLM(fail=True), g.title, "user", g.user_reviews, g.user_total)
    assert s["method"] == "extractive"
    assert s["likes"] and s["dislikes"]
    assert "503" in s["error"]
    assert s["verdict"]


def test_no_reviews_means_no_llm_call():
    llm = FakeLLM()
    s = summarize_reviews(llm, "Niph", "critic", [], 0)
    assert s["method"] == "none" and llm.calls == 0


def test_extractive_without_llm_object():
    g = game()
    s = summarize_reviews(None, g.title, "critic", g.critic_reviews, g.critic_total)
    assert s["method"] == "extractive" and len(s["likes"]) <= 4


def test_extractive_handles_only_positive():
    reviews = [Review("critic", "positive", "A great and fun racer. Gorgeous tracks everywhere.", 90)]
    s = extractive_review_summary(reviews, "critic", 1)
    assert s["likes"] and s["dislikes"] == []


def test_prompt_mixes_positive_and_negative_and_is_bounded():
    g = game()
    msgs = build_review_prompt(g.title, "user", g.user_reviews * 10, max_chars=3000)
    body = msgs[-1]["content"]
    assert "[-]" in body and "[+]" in body
    assert len(body) < 4200


def test_extract_json_strips_fences_and_noise():
    raw = load_json("llm_pollinations_response.json")["response"]["choices"][0]["message"]["content"]
    assert extract_json(raw)["likes"] == ["Отличная физика и скорость"]
    assert extract_json('Вот ответ: {"a": {"b": "}"}} спасибо') == {"a": {"b": "}"}}
    for bad in ("", "no json here", "[1, 2]"):
        try:
            extract_json(bad)
        except LLMError:
            pass
        else:
            raise AssertionError(bad)


def test_validator_coerces_and_rejects():
    v = validate_review_json({"likes": "одно", "dislikes": [{"text": "второе"}], "verdict": 5})
    assert v == {"likes": ["одно"], "dislikes": ["второе"], "verdict": "5"}
    try:
        validate_review_json({"likes": [], "dislikes": []})
    except LLMError:
        pass
    else:
        raise AssertionError


def test_extractive_transcript_summary():
    text = ("This game is amazing and the tracks are beautiful. " * 3 + "Honestly the controls feel clunky and frustrating. " * 3
            + "We drive to the next checkpoint now. " * 10)
    s = extractive_transcript_summary(text)
    assert s["pros"] or s["cons"]
    assert "без LLM" in s["summary"]


def test_prompts_respect_small_budget():
    from app.summarize import build_letsplay_prompt, condense_transcript

    g = game()
    msgs = build_review_prompt(g.title, "critic", g.critic_reviews, max_chars=1600)
    assert sum(len(m["content"]) for m in msgs) <= 1700
    assert msgs[-1]["content"].count("[") >= 4
    text = ("I really love the drifting here, it feels amazing. " + "We go left now and pick up the boost. " * 40
            + "Honestly the menus are clunky and the AI is frustrating. " + "Okay next race. " * 40
            + "Overall I would recommend it, worth the price.") * 3
    short = condense_transcript(text, 900)
    assert len(short) <= 900 and "love" in short
    lp = build_letsplay_prompt("Game", "Video", "Chan", text, "transcript", 1600)
    assert sum(len(m["content"]) for m in lp) <= 1700


def test_validator_rejects_english_answers():
    try:
        validate_review_json({"likes": ["Fun combat and gorgeous visuals"], "dislikes": [], "verdict": "Solid game"})
    except LLMError:
        pass
    else:
        raise AssertionError
    ok = validate_review_json({"likes": ["Отличная боёвка в духе Sekiro"], "dislikes": [], "verdict": "Хорошо"})
    assert ok["likes"]
