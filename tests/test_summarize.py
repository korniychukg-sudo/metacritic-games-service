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


def test_russian_check_runs_per_item():
    from app.summarize import is_russian

    try:
        validate_review_json({"likes": ["Красивые пейзажи", "Отличная музыка"],
                              "dislikes": ["botched remaster process", "use of AI slop"],
                              "verdict": "Игроки разочарованы переизданием, хотя графика и музыка хороши."})
    except LLMError as exc:
        assert "botched remaster process" in str(exc)
    else:
        raise AssertionError
    assert is_russian("Поддержка Steam Deck") and is_russian("Режим Photo Mode") and is_russian("5")
    assert not is_russian("Solid game") and not is_russian("use of AI slop и всё")


KH_EXCERPT = ("you know Have you heard of the ancient keyblade war? Huh? Of course I have. Long ago, keyblade "
              "wielders waged a war over the ownership of light. Yeah, the master's favorite story. ") * 6


def test_letsplay_validator_drops_items_about_the_video_and_unverified_quotes():
    from app.summarize import validate_letsplay_json

    value = {"has_commentary": True,
             "evidence": ["Of course I have. Long ago, keyblade wielders waged a war", "I think this remaster is amazing"],
             "summary": "Автор ролика проходит пролог.",
             "pros": ["Позитивный настрой и энтузиазм автора", "Красивые арены", "Подробный обзор в видео"],
             "cons": ["Не указаны конкретные проблемы", "Отсутствие подробного обзора", "Долгие загрузки"],
             "verdict": "Хорошая коллекция."}
    value["evidence"][0] = '"' + value["evidence"][0] + '"'
    out = validate_letsplay_json(value, KH_EXCERPT)
    assert out["pros"] == ["Красивые арены"] and out["cons"] == ["Долгие загрузки"]
    assert out["evidence"] == ["Of course I have. Long ago, keyblade wielders waged a war"]
    assert out["has_commentary"] is True


def test_letsplay_without_commentary_is_reported_not_invented():
    from app.summarize import validate_letsplay_json

    out = validate_letsplay_json({"has_commentary": False, "evidence": [], "summary": "", "pros": [], "cons": []},
                                 KH_EXCERPT)
    assert out["has_commentary"] is False and out["pros"] == [] and out["summary"] == ""
    made_up = validate_letsplay_json({"has_commentary": "true", "evidence": ["Автор хвалит производительность"],
                                      "summary": "Автор ценит производительность на PS5 Pro.",
                                      "pros": ["Производительность"]}, KH_EXCERPT)
    assert made_up["has_commentary"] is False and made_up["evidence_rejected"] == 1
    try:
        validate_letsplay_json({"has_commentary": True, "evidence": ["Of course I have. Long ago, keyblade wielders"],
                                "summary": "Great prologue", "pros": ["Nice"]}, KH_EXCERPT)
    except LLMError:
        pass
    else:
        raise AssertionError


def test_commentary_heuristic_rejects_music_and_noise_only():
    from app.summarize import commentary_density, looks_like_commentary
    from app.youtube import transcript_to_text

    music = transcript_to_text([{"text": "Heat. Heat. Heat. [bell] Heat. Heat. " * 50 + "wind rain " * 200}])
    assert not looks_like_commentary(music)
    talk = "Okay guys, I really love how this game feels, let's go and I think we should try the next race. " * 20
    assert looks_like_commentary(talk) and commentary_density(talk) > 100
    assert looks_like_commentary("short text")


def test_description_prompt_and_validator_never_attribute_opinions():
    from app.summarize import build_letsplay_prompt, validate_description_json

    msgs = build_letsplay_prompt("Game", "Video", "Chan", "Название: x\nОписание: Full demo walkthrough", "description")
    assert "Это описание ролика, а не речь его автора" in msgs[-1]["content"]
    assert "pros" not in msgs[-1]["content"] and "has_commentary" not in msgs[-1]["content"]
    assert "описания роликов" in msgs[0]["content"]
    out = validate_description_json({"summary": "Судя по описанию ролика, это полное демо.", "pros": ["x"]})
    assert out == {"about_this_game": True, "summary": "Судя по описанию ролика, это полное демо.", "pros": [],
                   "cons": [], "verdict": ""}
    transcript = build_letsplay_prompt("Game", "Video", "Chan", "I love it. " * 50, "transcript")
    assert '"has_commentary"' in transcript[-1]["content"] and "только о самой игре" in transcript[-1]["content"]


def test_llm_names_the_played_game_and_code_decides_whether_it_is_another_one():
    from app.summarize import build_letsplay_prompt, validate_description_json, validate_letsplay_json

    text = "welcome back to Monster Hunter, today we fight Basarios and I really love this hunt. " * 20
    for source in ("transcript", "whisper", "description"):
        body = build_letsplay_prompt("Rock Hunter", "Monster Hunter 4 Ultimate - Part 12", "c", text, source)
        task = body[-1]["content"]
        assert "played_game" in task and "{title}" not in task
    wrong = {"played_game": "Monster Hunter 4 Ultimate", "has_commentary": True,
             "evidence": ["welcome back to Monster Hunter"],
             "summary": "Игра «Rock Hunter» предлагает интересный геймплей.", "pros": ["бой"], "cons": []}
    out = validate_letsplay_json(wrong, text, "Rock Hunter")
    assert out["about_this_game"] is False and out["other_game"] == "Monster Hunter 4 Ultimate"
    assert out["summary"] == "" and out["pros"] == [] and not out["evidence"]
    same = [("Rock Hunter", "Rock Hunter"), ("ROCK HUNTER", "Rock Hunter"), ("", "Rock Hunter"),
            ("Hellraiser", "Clive Barker's Hellraiser: Revival"), ("Аллоглум", "Allogloom"),
            ("Kingdom Hearts 3", "Kingdom Hearts III"), ("Allogloom", "Allogloom")]
    for played, game in same:
        res = validate_letsplay_json({**wrong, "played_game": played}, text, game)
        assert res["about_this_game"] is True and res["summary"], (played, game)
    assert validate_letsplay_json(wrong, text)["about_this_game"] is True
    assert validate_description_json({"played_game": "SimCity", "summary": "Судя по описанию ролика, это SimCity."},
                                     "Alice Nation") == {
        "about_this_game": False, "other_game": "SimCity", "summary": "", "pros": [], "cons": [], "verdict": ""}


def test_untranslated_quotes_in_pros_are_dropped_not_fatal():
    from app.summarize import validate_letsplay_json

    text = "Phase one is super easy. Excellent blade work. I love the parry but the timing is hard. " * 20
    out = validate_letsplay_json({"played_game": "Valor Mortis", "has_commentary": True,
                                  "evidence": ["Phase one is super easy"],
                                  "summary": "Автор хвалит бои и парирование, но жалуется на тайминги.",
                                  "pros": ["Phase one is super easy", "Отзывчивое парирование"],
                                  "cons": ["Excellent blade work.", "Сложные тайминги"], "verdict": "Понравилось."},
                                 text, "Valor Mortis")
    assert out["pros"] == ["Отзывчивое парирование"] and out["cons"] == ["Сложные тайминги"]
    try:
        validate_letsplay_json({"has_commentary": True, "evidence": ["Phase one is super easy"],
                                "summary": "The author likes it.", "pros": [], "cons": []}, text)
    except LLMError:
        pass
    else:
        raise AssertionError
