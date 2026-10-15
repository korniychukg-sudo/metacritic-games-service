import hashlib
import logging
import re

from .config import iso, utcnow
from .llm import LLMClient, LLMDisabled, LLMError, LLMSkipped
from .metacritic import Review

log = logging.getLogger("summarize")

POSITIVE_WORDS = {
    "great", "excellent", "amazing", "fun", "love", "loved", "beautiful", "gorgeous", "stunning", "best",
    "brilliant", "fantastic", "enjoy", "enjoyed", "satisfying", "polished", "addictive", "masterpiece",
    "smooth", "charming", "impressive", "solid", "awesome", "incredible", "perfect", "superb", "delight",
    "отлично", "супер", "шедевр", "понравилось", "красив", "интересн",
}
NEGATIVE_WORDS = {
    "bad", "boring", "bug", "bugs", "buggy", "crash", "crashes", "repetitive", "frustrating", "worst",
    "broken", "lack", "lacks", "lacking", "short", "clunky", "disappointing", "disappointed", "tedious",
    "annoying", "poor", "unfortunately", "however", "but", "mess", "overpriced", "grind", "grindy",
    "performance", "stutter", "missing", "weak", "shallow", "плохо", "скучн", "баг", "вылет", "однообраз",
}
EVAL_WORDS = POSITIVE_WORDS | NEGATIVE_WORDS | {"game", "gameplay", "story", "graphics", "controls", "combat"}

KIND_RU = {"critic": "критиков", "user": "игроков"}


def reviews_hash(reviews: list[Review]) -> str:
    h = hashlib.sha1()
    for r in sorted(reviews, key=lambda r: r.quote):
        h.update(r.quote.encode("utf-8"))
        h.update(str(r.score).encode())
    return h.hexdigest()[:16]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", re.sub(r"\s+", " ", text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) > 15]


def _word_hits(sentence: str, words: set[str]) -> int:
    low = sentence.lower()
    tokens = set(re.findall(r"\w+", low))
    hits = len(tokens & words)
    hits += sum(1 for w in words if len(w) >= 5 and not w.isascii() and w in low)
    return hits


def best_sentence(text: str, words: set[str], max_len: int = 240) -> str:
    sentences = split_sentences(text) or [text.strip()]
    best = max(sentences, key=lambda s: (_word_hits(s, words), -abs(len(s) - 120)))
    if len(best) > max_len:
        best = best[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return best


def _score_key(r: Review) -> float:
    return r.score if r.score is not None else -1


def extractive_review_summary(reviews: list[Review], kind: str, total: int) -> dict:
    positive = sorted([r for r in reviews if r.sentiment == "positive"], key=_score_key, reverse=True)
    critical = sorted([r for r in reviews if r.sentiment == "negative"], key=_score_key)
    critical += sorted([r for r in reviews if r.sentiment == "neutral"], key=_score_key)
    likes, dislikes = [], []
    for r in positive[:4]:
        s = best_sentence(r.quote, POSITIVE_WORDS)
        if s not in likes:
            likes.append(s)
    for r in critical[:4]:
        s = best_sentence(r.quote, NEGATIVE_WORDS)
        if s not in dislikes and s not in likes:
            dislikes.append(s)
    pos, neg = len(positive), len([r for r in reviews if r.sentiment == "negative"])
    if pos and not neg:
        verdict = f"В выборке {KIND_RU[kind]} преобладают положительные отзывы ({pos} из {len(reviews)})."
    elif neg > pos:
        verdict = f"В выборке {KIND_RU[kind]} больше негативных отзывов ({neg} из {len(reviews)})."
    else:
        verdict = f"Мнения {KIND_RU[kind]} разделились: {pos} положительных и {neg} негативных в выборке."
    return {
        "likes": likes,
        "dislikes": dislikes,
        "verdict": verdict,
        "method": "extractive",
        "note": "LLM недоступна, показаны ключевые цитаты из отзывов (на языке оригинала)",
    }


def _clip(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rsplit(" ", 1)[0] + "…"


def _review_line(r: Review, max_chars: int = 380) -> str:
    sign = {"positive": "+", "negative": "-", "neutral": "~"}.get(r.sentiment, "~")
    score = "" if r.score is None else (f"{int(r.score)}" if r.kind == "critic" else f"{r.score:g}")
    return f"[{sign}{score}] {_clip(r.quote, max_chars)}"


REVIEW_SYSTEM = "Ты аналитик игровых отзывов. Пиши по-русски, кратко, в третьем лице, только по тексту отзывов."
REVIEW_TASK = (
    "\n\nЧто нравится и что не нравится? Пункты — только свойства самой игры (геймплей, сюжет, графика, "
    "техническое состояние, контент, цена), а не то, кто и как её оценил. Все пункты и вывод пиши на русском "
    "языке, даже если отзывы на английском. Ответь только JSON: "
    '{"likes": ["3-5 коротких пунктов"], "dislikes": ["0-5 пунктов"], "verdict": "одно предложение"}'
)


def build_review_prompt(title: str, kind: str, reviews: list[Review], max_chars: int = 6500) -> list[dict]:
    positive = [r for r in reviews if r.sentiment == "positive"]
    critical = [r for r in reviews if r.sentiment != "positive"]
    ordered = []
    for i in range(max(len(positive), len(critical))):
        if i < len(critical):
            ordered.append(critical[i])
        if i < len(positive):
            ordered.append(positive[i])
    who = "критиков" if kind == "critic" else "игроков"
    head = f"Игра «{title}», отзывы {who} ([+] хвалят, [-] ругают, [~] смешанно; число = оценка):\n"
    available = max(300, max_chars - len(REVIEW_SYSTEM) - len(head) - len(REVIEW_TASK))
    per_review = max(110, min(380, available // max(1, len(ordered)) - 8))
    lines, used = [], 0
    for r in ordered:
        line = _review_line(r, per_review)
        if used + len(line) + 1 > available:
            break
        lines.append(line)
        used += len(line) + 1
    user = head + "\n".join(lines) + REVIEW_TASK
    return [{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": user}]


def _str_list(value, limit: int = 6) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, dict):
            v = v.get("text") or v.get("point") or next(iter(v.values()), "")
        s = str(v).strip().strip("•- ").strip()
        if s:
            out.append(s)
    return out[:limit]


def is_russian(text: str) -> bool:
    text = str(text or "")
    cyr = len(re.findall(r"[а-яё]", text, re.I))
    lat = len(re.findall(r"[a-z]", text, re.I))
    if not cyr:
        return not re.search(r"[a-z]{2,}", text, re.I)
    return lat <= 2 * cyr


def ensure_russian(*parts) -> None:
    for part in parts:
        for item in (part if isinstance(part, list) else [part]):
            if not is_russian(item):
                raise LLMError(f"ответ не на русском языке: «{str(item)[:80]}»")


def validate_review_json(value: dict) -> dict:
    likes = _str_list(value.get("likes") or value.get("pros"))
    dislikes = _str_list(value.get("dislikes") or value.get("cons"))
    verdict = str(value.get("verdict") or value.get("summary") or "").strip()
    if not likes and not dislikes:
        raise LLMError("в JSON нет пунктов likes/dislikes")
    ensure_russian(likes, dislikes, verdict)
    return {"likes": likes, "dislikes": dislikes, "verdict": verdict}


SKIP_NOTES = {
    LLMSkipped: "LLM временно пропущена после нескольких неудач подряд, показаны ключевые цитаты из отзывов "
                "(на языке оригинала). Резюме пересчитается через LLM на следующих запусках",
    LLMDisabled: "LLM отключена настройкой, показаны ключевые цитаты из отзывов (на языке оригинала)",
}


def summarize_reviews(llm: LLMClient | None, title: str, kind: str, reviews: list[Review], total: int) -> dict:
    base = {"review_count": total, "sample_size": len(reviews), "reviews_hash": reviews_hash(reviews),
            "updated_at": iso(utcnow())}
    if not reviews:
        return {**base, "likes": [], "dislikes": [], "verdict": "Отзывов пока нет.", "method": "none"}
    if llm is not None:
        try:
            result = llm.ask_json(lambda budget: build_review_prompt(title, kind, reviews, budget),
                                  validate_review_json)
            return {**base, **result, "method": "llm", "model": llm.model}
        except (LLMSkipped, LLMDisabled) as exc:
            fallback = extractive_review_summary(reviews, kind, total)
            fallback["note"] = SKIP_NOTES[type(exc)]
            return {**base, **fallback}
        except LLMError as exc:
            log.warning("Резюме (%s) для %s без LLM, показаны цитаты: %s", kind, title, exc)
            fallback = extractive_review_summary(reviews, kind, total)
            fallback["error"] = str(exc)[:200]
            return {**base, **fallback}
    return {**base, **extractive_review_summary(reviews, kind, total)}


def extractive_transcript_summary(text: str, max_items: int = 5) -> dict:
    sentences = split_sentences(text)
    if not sentences:
        return {"summary": "", "pros": [], "cons": [], "verdict": ""}
    n = len(sentences)
    buckets = 5
    picked = []
    for b in range(buckets):
        seg = sentences[b * n // buckets:(b + 1) * n // buckets] or []
        if not seg:
            continue
        best = max(seg, key=lambda s: (_word_hits(s, POSITIVE_WORDS | NEGATIVE_WORDS), len(s) < 220))
        if _word_hits(best, POSITIVE_WORDS | NEGATIVE_WORDS):
            picked.append(best[:240])
    pros = [s for s in picked if _word_hits(s, POSITIVE_WORDS) >= _word_hits(s, NEGATIVE_WORDS)][:max_items]
    cons = [s for s in picked if s not in pros][:max_items]
    return {
        "summary": "Автоматическая выборка оценочных фраз блогера из расшифровки (без LLM, язык оригинала).",
        "pros": pros,
        "cons": cons,
        "verdict": "",
    }


OPINION_WORDS = POSITIVE_WORDS | NEGATIVE_WORDS | {
    "like", "liked", "hate", "recommend", "worth", "favorite", "favourite", "wow", "cool", "nice", "honestly",
    "feel", "feels", "difficult", "hard", "easy", "price", "graphics", "story", "controls", "music", "opinion",
}


def condense_transcript(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars >= 6000:
        from .youtube import sample_transcript

        return sample_transcript(text, max_chars)
    sentences = split_sentences(text)
    if len(sentences) < 4:
        return _clip(text, max_chars)
    n = len(sentences)
    keep = {0, 1, n - 2, n - 1}
    ranked = sorted(range(2, n - 2), key=lambda i: (-_word_hits(sentences[i], OPINION_WORDS), i))
    used = sum(len(sentences[i]) + 1 for i in keep)
    for i in ranked:
        if _word_hits(sentences[i], OPINION_WORDS) == 0:
            break
        cost = min(len(sentences[i]), 220) + 1
        if used + cost > max_chars:
            continue
        keep.add(i)
        used += cost
    out, prev = [], -2
    for i in sorted(keep):
        if i != prev + 1 and out:
            out.append("…")
        out.append(_clip(sentences[i], 220))
        prev = i
    return _clip(" ".join(out), max_chars)


COMMENTARY_MARKERS = {
    "i", "i'm", "im", "i've", "i'll", "i'd", "me", "my", "we", "we're", "let's", "lets", "guys", "chat",
    "subscribe", "episode", "video", "channel", "honestly", "gonna", "okay",
    "я", "мне", "меня", "мой", "моя", "мы", "нам", "ребята", "давайте", "друзья", "народ", "короче", "видео",
    "канал", "стрим", "чат", "щас",
}
MIN_COMMENTARY_PER_1000 = 10


def commentary_density(text: str) -> float:
    words = re.findall(r"[\w']+", (text or "").lower())
    if not words:
        return 0.0
    return 1000 * sum(1 for w in words if w in COMMENTARY_MARKERS) / len(words)


def looks_like_commentary(text: str) -> bool:
    words = re.findall(r"[\w']+", text or "")
    if len(words) < 150:
        return True
    return commentary_density(text) >= MIN_COMMENTARY_PER_1000


LETSPLAY_SYSTEM = (
    "Ты пишешь заключения об играх по речи автора летсплея. По-русски, в третьем лице («автор ролика»). "
    "Опирайся только на слова самого автора: диалоги персонажей игры, музыка и звуки не являются его мнением. "
    "Игнорируй рекламу, спонсоров, просьбы подписаться. Не выдумывай."
)
LETSPLAY_TASK = (
    "\n\nСначала определи: played_game — какую игру автор проходит в ролике, судя по тексту: название, как оно "
    "звучит у автора или персонажей (сказал «welcome to Minecraft» — значит Minecraft), или пустая строка, если "
    "название не звучит; сравнения с другими играми не в счёт. has_commentary — звучит ли голос "
    "самого автора: он обращается к зрителям, комментирует игру (реплики персонажей, музыка и шум не в счёт). "
    "Если голос есть, в evidence дай 2-3 короткие дословные цитаты автора на языке оригинала, лучше с оценкой игры, "
    "и только по его словам: summary — как он оценивает игру и что показывает (2-4 предложения); pros и cons — "
    "только о самой игре (геймплей, сюжет, графика, техническое состояние, контент) и только сказанное автором, "
    "иначе пустой список; не про автора или ролик. Иначе верни пустые поля. Все поля, кроме evidence и "
    "played_game, на русском. Ответь только JSON: "
    '{"played_game": "", "has_commentary": true, "evidence": ["..."], "summary": "...", '
    '"pros": ["..."], "cons": ["..."], "verdict": "одно предложение"}'
)
DESCRIPTION_SYSTEM = (
    "Ты кратко пересказываешь описания роликов YouTube об играх. По-русски, только по тексту описания. "
    "Не выдумывай."
)
DESCRIPTION_TASK = (
    "\n\nЭто описание ролика, а не речь его автора. Сначала определи played_game — какой игре посвящён ролик "
    "судя по названию и описанию, или пустая строка, если это неясно; упоминания других игр для сравнения не в "
    "счёт. В summary в 1-3 предложениях перескажи, что показано в ролике и что "
    "известно об игре из описания. Не приписывай автору мнений об игре и не придумывай плюсы и минусы, пиши "
    "«судя по описанию ролика». Ссылки, рекламу и просьбы подписаться пропусти. Ответь только JSON на русском: "
    '{"played_game": "", "summary": "1-3 предложения"}'
)
META_ITEM = re.compile(
    r"(?<![а-яё])(автор[а-яё]*|ролик[а-яё]*|видео(?![а-яё])|блогер[а-яё]*|ведущ(ий|его|ему|им|ем)(?![а-яё])"
    r"|стример[а-яё]*|канал[а-яё]*|обзор[а-яё]*|зрител[а-яё]*|летспле[а-яё]*)"
    r"|не\s+(указан|упомина|упомян|отмеч|назван|рассказ|описан)",
    re.I,
)


def build_letsplay_prompt(game_title: str, video_title: str, channel: str | None, text: str,
                          source: str = "transcript", max_chars: int = 9000) -> list[dict]:
    by = f", канал {channel}" if channel else ""
    system, task = LETSPLAY_SYSTEM, LETSPLAY_TASK
    if source == "transcript":
        head = (f"Игра «{game_title}». Ролик «{video_title}»{by}. "
                "Ключевые фрагменты автоматической расшифровки звуковой дорожки ролика:\n")
    elif source == "whisper":
        head = (f"Игра «{game_title}». Ролик «{video_title}»{by}. Ключевые фрагменты речи из начала ролика, "
                "распознанной моделью Whisper (возможны ошибки распознавания в именах и терминах):\n")
    else:
        system, task = DESCRIPTION_SYSTEM, DESCRIPTION_TASK
        head = f"Игра «{game_title}». Ролик «{video_title}»{by}. Описание ролика на YouTube:\n"
    task = task.replace("{title}", game_title)
    available = max(300, max_chars - len(system) - len(head) - len(task))
    body = condense_transcript(text, available) if source in ("transcript", "whisper") else _clip(text, available)
    return [{"role": "system", "content": system}, {"role": "user", "content": head + body + task}]


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", (text or "").lower()) if len(w) >= 3]


def verified_quotes(quotes: list[str], text: str | None) -> list[str]:
    if text is None:
        return quotes
    vocab = set(_words(text))
    out = []
    for q in quotes:
        words = _words(q)
        if len(words) >= 3 and sum(1 for w in words if w in vocab) >= 0.6 * len(words):
            out.append(q)
    return out


def _flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "да", "1")
    return bool(value)


def other_game(value: dict, game_title: str | None) -> str | None:
    played = str(value.get("played_game") or value.get("other_game") or "").strip().strip('"«»“” ')
    if not played or not game_title:
        return None
    from .youtube import normalize, phrase_words, title_matches

    if not re.search(r"[a-z]", normalize(played)):
        return None
    if normalize(played) == normalize(game_title) or title_matches(game_title, played):
        return None
    played_words, title_words = set(phrase_words(played)), set(phrase_words(game_title))
    if not played_words or not title_words or played_words <= title_words:
        return None
    return played[:120]


def about_the_game(items: list[str]) -> list[str]:
    return [x for x in items if not META_ITEM.search(x)]


def validate_letsplay_json(value: dict, text: str | None = None, game_title: str | None = None) -> dict:
    other = other_game(value, game_title)
    if other:
        return {"about_this_game": False, "other_game": other, "has_commentary": False, "evidence": [],
                "summary": "", "pros": [], "cons": [], "verdict": ""}
    summary = str(value.get("summary") or "").strip()
    pros = about_the_game(_str_list(value.get("pros") or value.get("likes")))
    cons = about_the_game(_str_list(value.get("cons") or value.get("dislikes")))
    verdict = str(value.get("verdict") or "").strip()
    raw_evidence = [q.strip('"«»“”\' ').strip() for q in _str_list(value.get("evidence") or value.get("quotes"), limit=4)]
    raw_evidence = [q for q in raw_evidence if q]
    evidence = verified_quotes(raw_evidence, text)
    has_commentary = _flag(value.get("has_commentary", True)) and bool(evidence)
    if not has_commentary:
        return {"about_this_game": True, "has_commentary": False, "evidence": [], "summary": "", "pros": [],
                "cons": [], "verdict": "", "evidence_rejected": len(raw_evidence) - len(evidence)}
    pros = [x for x in pros if is_russian(x)]
    cons = [x for x in cons if is_russian(x)]
    if not summary and not pros and not cons:
        raise LLMError("пустое заключение")
    ensure_russian(summary, verdict)
    return {"about_this_game": True, "has_commentary": True, "evidence": evidence, "summary": summary, "pros": pros,
            "cons": cons, "verdict": verdict}


def validate_description_json(value: dict, game_title: str | None = None) -> dict:
    other = other_game(value, game_title)
    if other:
        return {"about_this_game": False, "other_game": other, "summary": "", "pros": [], "cons": [], "verdict": ""}
    summary = str(value.get("summary") or "").strip()
    if not summary:
        raise LLMError("пустой пересказ описания")
    ensure_russian(summary)
    return {"about_this_game": True, "summary": summary, "pros": [], "cons": [], "verdict": ""}
