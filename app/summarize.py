import hashlib
import logging
import re

from .config import iso, utcnow
from .llm import LLMClient, LLMError
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
    "\n\nЧто нравится и что не нравится? Все пункты и вывод пиши на русском языке, даже если отзывы "
    "на английском. Ответь только JSON: "
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


def ensure_russian(*parts) -> None:
    text = " ".join(" ".join(p) if isinstance(p, list) else str(p or "") for p in parts)
    cyr = len(re.findall(r"[а-яё]", text, re.I))
    lat = len(re.findall(r"[a-z]", text, re.I))
    if lat > cyr:
        raise LLMError("ответ не на русском языке")


def validate_review_json(value: dict) -> dict:
    likes = _str_list(value.get("likes") or value.get("pros"))
    dislikes = _str_list(value.get("dislikes") or value.get("cons"))
    verdict = str(value.get("verdict") or value.get("summary") or "").strip()
    if not likes and not dislikes:
        raise LLMError("в JSON нет пунктов likes/dislikes")
    ensure_russian(likes, dislikes, verdict)
    return {"likes": likes, "dislikes": dislikes, "verdict": verdict}


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
        except LLMError as exc:
            log.warning("LLM summary (%s) for %s failed, using extractive: %s", kind, title, exc)
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


LETSPLAY_SYSTEM = (
    "Ты пишешь заключения об играх по летсплеям. По-русски, в третьем лице («автор ролика»). "
    "Игнорируй рекламу, спонсоров, просьбы подписаться. Не выдумывай."
)
LETSPLAY_TASK = (
    "\n\nКак блогер оценивает игру, что понравилось и что нет? Пиши только на русском языке, даже если "
    "речь блогера на английском. Ответь только JSON: "
    '{"summary": "2-4 предложения", "pros": ["..."], "cons": ["..."], "verdict": "одно предложение"}'
)


def build_letsplay_prompt(game_title: str, video_title: str, channel: str | None, text: str,
                          source: str = "transcript", max_chars: int = 9000) -> list[dict]:
    by = f", канал {channel}" if channel else ""
    if source == "transcript":
        head = (f"Игра «{game_title}». Ролик «{video_title}»{by}. "
                "Ключевые фрагменты автоматической расшифровки речи блогера:\n")
    else:
        head = f"Игра «{game_title}». Ролик «{video_title}»{by}. Расшифровки нет, есть только описание ролика:\n"
    available = max(300, max_chars - len(LETSPLAY_SYSTEM) - len(head) - len(LETSPLAY_TASK))
    body = condense_transcript(text, available) if source == "transcript" else _clip(text, available)
    return [{"role": "system", "content": LETSPLAY_SYSTEM}, {"role": "user", "content": head + body + LETSPLAY_TASK}]


def validate_letsplay_json(value: dict) -> dict:
    summary = str(value.get("summary") or "").strip()
    pros = _str_list(value.get("pros") or value.get("likes"))
    cons = _str_list(value.get("cons") or value.get("dislikes"))
    verdict = str(value.get("verdict") or "").strip()
    if not summary and not pros and not cons:
        raise LLMError("пустое заключение")
    ensure_russian(summary, pros, cons, verdict)
    return {"summary": summary, "pros": pros, "cons": cons, "verdict": verdict}
