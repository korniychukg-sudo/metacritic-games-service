import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

STOPWORDS = set(
    """a about above after again against all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has have having he
    her here hers him his how i if in into is it its itself just me more most my no nor not now of off on once only
    or other our out over own same she should so some such than that the their them then there these they this
    those through to too under until up very was we were what when where which while who whom why will with you
    your yours game games player players new world every one get take make way through across own experience
    will can including features feature""".split()
)

WEIGHTS = {"desc": 0.45, "genre": 0.2, "franchise": 0.2, "developer": 0.1, "platform": 0.05}


@dataclass
class GameFeatures:
    id: int
    title: str
    description: str = ""
    genres: list[str] = field(default_factory=list)
    franchises: list[str] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zа-яё][a-zа-яё0-9'-]+", (text or "").lower())
    out = []
    for w in words:
        w = w.strip("'-")
        if len(w) < 3 or w in STOPWORDS:
            continue
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            w = w[:-1]
        out.append(w)
    return out


def tfidf_vectors(docs: dict[int, list[str]]) -> dict[int, dict[str, float]]:
    n = len(docs)
    df: Counter = Counter()
    for toks in docs.values():
        df.update(set(toks))
    vectors = {}
    for gid, toks in docs.items():
        tf = Counter(toks)
        vec = {}
        for term, cnt in tf.items():
            if df[term] > max(3, 0.5 * n):
                continue
            vec[term] = (1 + math.log(cnt)) * (math.log((1 + n) / (1 + df[term])) + 1)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[gid] = {t: v / norm for t, v in vec.items()}
    return vectors


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _norm_set(values: list[str]) -> set[str]:
    return {v.strip().lower() for v in values if v and v.strip()}


MIN_DESC = 0.06


def compute_similar(games: list[GameFeatures], top_n: int = 5, min_score: float = 0.05) -> dict[int, list[dict]]:
    if len(games) < 2:
        return {g.id: [] for g in games}
    docs = {g.id: tokenize(f"{g.title} {g.description}") for g in games}
    vectors = tfidf_vectors(docs)
    postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for gid, vec in vectors.items():
        for term, w in vec.items():
            postings[term].append((gid, w))

    meta = {
        g.id: {
            "genre": _norm_set(g.genres),
            "franchise": _norm_set(g.franchises),
            "developer": _norm_set(g.developers),
            "platform": _norm_set(g.platforms),
        }
        for g in games
    }
    by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for gid, m in meta.items():
        for kind in ("genre", "franchise", "developer"):
            for value in m[kind]:
                by_key[(kind, value)].append(gid)

    result: dict[int, list[dict]] = {}
    for g in games:
        cos: dict[int, float] = defaultdict(float)
        for term, w in vectors[g.id].items():
            for other, w2 in postings[term]:
                if other != g.id:
                    cos[other] += w * w2
        candidates = set(cos)
        m = meta[g.id]
        for kind in ("genre", "franchise", "developer"):
            for value in m[kind]:
                candidates.update(o for o in by_key[(kind, value)] if o != g.id)
        scored = []
        for other in candidates:
            om = meta[other]
            parts = {
                "desc": cos.get(other, 0.0),
                "genre": _jaccard(m["genre"], om["genre"]),
                "franchise": 1.0 if m["franchise"] & om["franchise"] else 0.0,
                "developer": 1.0 if m["developer"] & om["developer"] else 0.0,
                "platform": _jaccard(m["platform"], om["platform"]),
            }
            meaningful = parts["franchise"] or parts["developer"] or parts["genre"] or parts["desc"] >= MIN_DESC
            score = sum(WEIGHTS[k] * v for k, v in parts.items())
            if not meaningful or score < min_score:
                continue
            reasons = []
            if parts["franchise"]:
                reasons.append("та же франшиза")
            if parts["developer"]:
                reasons.append("тот же разработчик")
            if parts["genre"]:
                reasons.append("общий жанр")
            if parts["desc"] >= MIN_DESC:
                reasons.append("похожее описание")
            if parts["platform"] >= 0.5:
                reasons.append("те же платформы")
            scored.append((score, other, reasons))
        scored.sort(key=lambda x: (-x[0], x[1]))
        result[g.id] = [{"id": o, "score": round(s, 4), "reasons": r} for s, o, r in scored[:top_n]]
    return result
