import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlparse

from .http_client import PoliteClient, SchemaError

log = logging.getLogger("youtube")

SEARCH_URL = "https://www.youtube.com/results"
WATCH_URL = "https://www.youtube.com/watch"
CONSENT_COOKIES = {"SOCS": "CAI", "CONSENT": "YES+cb"}
SP_POPULAR_LONG = "CAMSAhgC"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TRAILER = re.compile(r"\b(trailer|трейлер|teaser|тизер)\b", re.I)
OFFICIAL = re.compile(r"\b(official|официальный)\b", re.I)
BLOCK_MARKERS = (
    ("consent.youtube.com", "YouTube вернул страницу согласия на cookies"),
    ("Sign in to confirm you", "YouTube просит подтвердить, что запрос не от бота"),
    ("g-recaptcha", "YouTube показал капчу"),
)


class YouTubeBlocked(SchemaError):
    pass

EXCLUDE = re.compile(
    r"\b(trailer|review|reviews|teaser|reveal|announcement|preview|обзор|трейлер|news|reaction)\b"
    r"|\b(no|without)\s+(commentary|talking)\b|\bnocommentary\b|без\s+(комментариев|комментария|слов)",
    re.I,
)
STOP = {"the", "a", "an", "of", "and", "edition", "remastered", "remaster", "complete", "definitive", "deluxe",
        "game", "goty", "collection", "hd", "for", "dlc"}
EDITION_WORDS = {
    "collection": "collection", "remaster": "remaster", "remastered": "remaster", "remake": "remake",
    "complete": "complete", "definitive": "definitive", "edition": "edition", "deluxe": "deluxe", "goty": "goty",
    "anniversary": "anniversary", "enhanced": "enhanced", "ultimate": "ultimate", "trilogy": "trilogy",
    "director": "director", "directors": "director",
}
PRE_RELEASE = re.compile(
    r"\b(demo|beta|alpha|playtest|early\s+access|early|preview|prologue)\b|демо|бета|альфа|ранн\w*\s+доступ",
    re.I,
)
AGE_UNITS = {"s": 1 / 86400, "sec": 1 / 86400, "second": 1 / 86400, "min": 1 / 1440, "minute": 1 / 1440,
             "h": 1 / 24, "hr": 1 / 24, "hour": 1 / 24, "d": 1, "day": 1, "w": 7, "wk": 7, "week": 7,
             "mo": 30, "month": 30, "y": 365, "yr": 365, "year": 365}
AGE = re.compile(r"(\d+)\s*(second|sec|minute|min|hour|hr|h|day|d|week|wk|w|month|mo|year|yr|y|s)s?\s+ago", re.I)
SUBTITLE_SPLIT = re.compile(r"\s*[:~\u2013\u2014]\s*|\s+-\s+|\s+\+\s+")
PLATFORM_EDITION = re.compile(
    r"\b(nintendo\s+switch\s*2|switch\s*2|ps5|playstation\s*5|xbox\s+series\s+x\s*\|?\s*s?)\s+edition\b", re.I)
SEGMENT_SPLIT = re.compile(r"\s+[-|/~•·]+\s+|[|:~\u2013\u2014()\[\]{}【】「」《》<>/\\•·!?#]|[^\w\s'’&.,+\-]")
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9",
         "x": "10", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16", "xvii": "17",
         "xviii": "18", "xix": "19", "xx": "20"}
DESCRIPTORS = {
    "official", "trailer", "trailers", "teaser", "launch", "reveal", "revealed", "announcement", "announce",
    "gameplay", "release", "date", "story", "cinematic", "extended", "premiere", "overview", "features", "feature",
    "first", "look", "new", "video", "english", "demo", "early", "access", "pre", "full", "final", "accolades",
    "showcase", "ps5", "ps4", "xbox", "series", "switch", "nintendo", "pc", "steam", "4k", "1080p", "60fps", "vr",
    "mobile", "ios", "android", "playtest", "update", "by", "out", "now", "coming", "soon", "available", "world",
    "debut", "opening", "intro", "remake", "anniversary", "enhanced", "ultimate", "трейлер", "тизер",
    "официальный", "игра", "геймплей", "анонс", "релиз",
}
LETSPLAY_LEAD = DESCRIPTORS | {
    "let", "s", "lets", "play", "plays", "playing", "played", "try", "trying", "tried", "walkthrough",
    "playthrough", "longplay", "part", "episode", "ep", "1", "we", "so", "this", "is", "it", "my", "live", "stream",
    "streamed", "blind", "прохождение", "полное", "играем", "стрим", "летсплей",
}
YEAR = re.compile(r"(19|20)\d\d")
NOT_A_GAME = re.compile(r"\b(wedding|full\s+movie|movie\s+trailer|film\s+trailer|official\s+film)\b"
                        r"|свадьб|фильм|кинофильм", re.I)
FILM_CHANNEL = re.compile(r"\b(movies?|films?|cinema|movieclips|кино)\b", re.I)
REACTION = re.compile(r"\breaction\b|реакция", re.I)
LETSPLAY_WINDOW_DAYS = 365
TRAILER_WINDOW_DAYS = 3 * 365
TRAILER_AGGREGATORS = {
    "ign", "gamespot", "gametrailers", "game trailers games explorer", "indie game radar", "playstation",
    "xbox", "nintendo of america", "nintendo", "steam", "gamesradar", "pc gamer", "polygon", "the game awards",
    "rock paper shotgun", "gameranx", "gamespot trailers", "ign india", "kotaku", "igromania", "stopgame ru",
    "gamegrin", "indie games", "epic games store", "gog com", "upcoming games", "trailer park", "indiegame trailers",
    "indie games hub", "next indie", "ultimate game database", "gametikker", "games explorer",
}
STORE_LINK = re.compile(r"(store\.steampowered\.com|steamcommunity\.com|store\.epicgames\.com|gog\.com|"
                        r"store\.playstation\.com|playstation\.com|xbox\.com|nintendo\.com|itch\.io)", re.I)
OWNER_STOP = {"inc", "ltd", "llc", "games", "game", "studio", "studios", "entertainment", "interactive", "co",
              "corp", "corporation", "limited", "gmbh", "sa", "s", "a", "the", "digital", "publishing", "software"}


@dataclass
class VideoCandidate:
    video_id: str
    title: str
    views: int | None = None
    length_seconds: int | None = None
    channel: str | None = None
    published: str | None = None
    description: str | None = None
    live: bool = False
    queries: list[str] = field(default_factory=list)
    base_game: bool = False
    published_date: str | None = None
    search_title: str | None = None
    refined: bool = False

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["url"] = self.url
        return d


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^\w]+", " ", text).strip()


def phrase_words(text: str, keep_stop: bool = False) -> list[str]:
    toks = [ROMAN.get(t, t) for t in normalize(text).split()]
    return toks if keep_stop else [t for t in toks if t not in STOP]


def game_phrases(game_title: str) -> tuple[list[list[list[str]]], bool]:
    title = PLATFORM_EDITION.sub(" ", game_title or "")
    keep_stop = len(phrase_words(title)) <= 1
    parts = [w for w in (phrase_words(p, keep_stop) for p in SUBTITLE_SPLIT.split(title)) if w]
    if not parts:
        parts = [phrase_words(title, True)] if phrase_words(title, True) else []
    if not parts:
        return [], keep_stop
    variants = [parts]
    first = parts[0]
    owners = [i for i, t in enumerate(first[:-1]) if t == "s" and i > 0]
    if owners:
        rest = first[owners[-1] + 1:]
        if len(rest) + sum(len(p) for p in parts[1:]) >= 2:
            variants.append([rest] + parts[1:])
    return variants, keep_stop


def video_tokens(video_title: str, keep_stop: bool) -> list[tuple[str, int]]:
    out = []
    for seg_no, seg in enumerate(SEGMENT_SPLIT.split(video_title or "")):
        for t in phrase_words(seg, keep_stop):
            out.append((t, seg_no))
    return out


def _occurrences(tokens: list[tuple[str, int]], phrase: list[str]) -> list[tuple[int, int]]:
    n = len(phrase)
    words = [t for t, _ in tokens]
    return [(i, i + n) for i in range(len(words) - n + 1) if n and words[i:i + n] == phrase]


def _sequel_after(tokens: list[tuple[str, int]], end: int, game_words: set[str]) -> bool:
    if end >= len(tokens) or tokens[end][1] != tokens[end - 1][1]:
        return False
    nxt = tokens[end][0]
    return nxt.isdigit() and 1 <= int(nxt) <= 20 and nxt not in game_words


def _lead_ok(tokens: list[tuple[str, int]], start: int, allowed: set[str]) -> bool:
    seg = tokens[start][1]
    for t, s in reversed(tokens[:start]):
        if s != seg:
            break
        if t not in allowed and t not in STOP:
            return False
    return True


def _anchored(tokens: list[tuple[str, int]], start: int, end: int, next_ok: set[str]) -> bool:
    if not _lead_ok(tokens, start, DESCRIPTORS):
        return False
    if end < len(tokens) and tokens[end][1] == tokens[end - 1][1]:
        nxt = tokens[end][0]
        latin = re.search(r"[a-z0-9]", nxt)
        if latin and nxt not in DESCRIPTORS and nxt not in next_ok and not YEAR.fullmatch(nxt):
            return False
    return True


def title_matches(game_title: str, video_title: str, anchored: bool = False) -> bool:
    variants, keep_stop = game_phrases(game_title)
    if not variants:
        return False
    tokens = video_tokens(video_title, keep_stop)
    for parts in variants:
        game_words = {t for p in parts for t in p}
        joined = [t for p in parts for t in p]
        for start, end in _occurrences(tokens, joined):
            if _sequel_after(tokens, end, game_words):
                continue
            if anchored:
                if _anchored(tokens, start, end, set()):
                    return True
            elif not keep_stop or _lead_ok(tokens, start, LETSPLAY_LEAD):
                return True
        if len(parts) < 2:
            continue
        found = []
        for part in parts:
            occ = [o for o in _occurrences(tokens, part) if not _sequel_after(tokens, o[1], game_words)]
            if not occ:
                break
            found.append(occ)
        else:
            if not anchored:
                return True
            heads = {p[0] for p in parts[1:]}
            if any(_anchored(tokens, s, e, heads) for s, e in found[0]):
                return True
    return False


def near_miss(game_title: str, video_title: str) -> bool:
    variants, keep_stop = game_phrases(game_title)
    if not variants:
        return False
    words = {t for p in variants[0] for t in p if len(t) > 1 or t.isdigit()}
    if not words:
        return False
    have = {t for t, _ in video_tokens(video_title, keep_stop)}
    return len(words & have) * 2 >= len(words)


def edition_words(title: str) -> set[str]:
    return {EDITION_WORDS[t] for t in normalize(title).split() if t in EDITION_WORDS}


def edition_matches(game_title: str, video_title: str) -> bool:
    return edition_words(game_title) <= edition_words(video_title)


def parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def parse_age_days(text: str | None) -> float | None:
    m = AGE.search(text or "")
    if not m:
        return None
    return int(m.group(1)) * AGE_UNITS[m.group(2).lower()]


def is_released(release_date: str | None, today: date | None = None) -> bool:
    if not release_date:
        return False
    try:
        released = date.fromisoformat(str(release_date)[:10])
    except ValueError:
        return False
    return released <= (today or date.today())


def parse_duration(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.strip().split(":")
    if not all(p.isdigit() for p in parts):
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs


def parse_views(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _runs_text(obj: dict | None) -> str:
    if not obj:
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return "".join(r.get("text", "") for r in obj.get("runs") or [])


def extract_initial_data(html_text: str) -> dict | None:
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html_text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def iter_video_renderers(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            vr = cur.get("videoRenderer")
            if isinstance(vr, dict):
                yield vr
            stack.extend(v for k, v in cur.items() if k != "videoRenderer")
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def detect_block(html_text: str) -> str | None:
    for marker, reason in BLOCK_MARKERS:
        if marker in html_text:
            return reason
    return None


def has_no_results_marker(data: dict) -> bool:
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "backgroundPromoRenderer" in cur:
                return True
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def parse_search_html(html_text: str) -> list[VideoCandidate]:
    data = extract_initial_data(html_text)
    if data is None:
        reason = detect_block(html_text)
        if reason:
            raise YouTubeBlocked(reason)
        raise SchemaError("на странице поиска YouTube нет ytInitialData (сменился формат страницы)")
    out: list[VideoCandidate] = []
    seen = set()
    renderers = 0
    for vr in iter_video_renderers(data):
        renderers += 1
        vid = vr.get("videoId")
        if not isinstance(vid, str) or not VIDEO_ID.match(vid) or vid in seen:
            continue
        seen.add(vid)
        view_obj = vr.get("viewCountText") or {}
        live = "runs" in view_obj and "simpleText" not in view_obj
        snippet = ""
        for sn in vr.get("detailedMetadataSnippets") or []:
            snippet = _runs_text(sn.get("snippetText"))
            break
        out.append(
            VideoCandidate(
                video_id=vid,
                title=_runs_text(vr.get("title")),
                views=None if live else parse_views(view_obj.get("simpleText")),
                length_seconds=parse_duration((vr.get("lengthText") or {}).get("simpleText")),
                channel=_runs_text(vr.get("ownerText")) or None,
                published=(vr.get("publishedTimeText") or {}).get("simpleText"),
                description=snippet or None,
                live=live,
            )
        )
    if renderers == 0 and not has_no_results_marker(data) and estimated_results(data) != 0:
        raise SchemaError("на странице поиска YouTube нет videoRenderer (сменился формат выдачи)")
    return out


def estimated_results(data: dict) -> int | None:
    try:
        return int(str(data.get("estimatedResults")))
    except (TypeError, ValueError):
        return None


def parse_watch_html(html_text: str) -> dict | None:
    m = re.search(r"var ytInitialPlayerResponse = (\{.*?\});(?:var|</script>)", html_text, re.S)
    if not m:
        return None
    try:
        player = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    details = player.get("videoDetails") or {}
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    published = parse_day(micro.get("publishDate") or micro.get("uploadDate"))
    return {
        "video_id": details.get("videoId"),
        "title": details.get("title"),
        "views": int(details["viewCount"]) if str(details.get("viewCount", "")).isdigit() else None,
        "length_seconds": int(details["lengthSeconds"]) if str(details.get("lengthSeconds", "")).isdigit() else None,
        "channel": details.get("author"),
        "description": details.get("shortDescription"),
        "live": bool(details.get("isLiveContent")),
        "published_date": published.isoformat() if published else None,
    }


def published_too_early(cand: VideoCandidate, release_date: str | None, today: date, window_days: int) -> bool:
    released = parse_day(release_date)
    if released is None:
        return False
    limit = released - timedelta(days=window_days)
    exact = parse_day(cand.published_date)
    if exact is not None:
        return exact < limit
    age = parse_age_days(cand.published)
    if age is None:
        return False
    return today - timedelta(days=age) <= limit


def _count(stats: dict | None, key: str) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def rank_candidates(game_title: str, candidates: list[VideoCandidate], min_seconds: int = 600,
                    released: bool = False, stats: dict | None = None, release_date: str | None = None,
                    today: date | None = None) -> list[VideoCandidate]:
    today = today or date.today()
    eligible = []
    for c in candidates:
        if c.live or c.views is None:
            _count(stats, "short_or_live")
            continue
        if c.length_seconds is not None and c.length_seconds < min_seconds:
            _count(stats, "short_or_live")
            continue
        if EXCLUDE.search(c.title):
            _count(stats, "excluded")
            continue
        if not title_matches(game_title, c.title):
            _count(stats, "title_mismatch")
            if near_miss(game_title, c.title):
                _count(stats, "near_miss")
            continue
        if published_too_early(c, release_date, today, LETSPLAY_WINDOW_DAYS):
            _count(stats, "too_old")
            continue
        eligible.append(c)
    if stats is not None:
        stats["found"] = len(candidates)
        stats["eligible"] = len(eligible)
    return sort_letsplays(eligible, released)


def sort_letsplays(candidates: list[VideoCandidate], released: bool) -> list[VideoCandidate]:
    return sorted(candidates, key=lambda c: (released and bool(PRE_RELEASE.search(c.title)), -(c.views or 0)))


def recheck_letsplay(game_title: str, cand: VideoCandidate, release_date: str | None, today: date) -> str | None:
    if EXCLUDE.search(cand.title):
        return f"по оригинальному названию («{cand.title}») это трейлер, обзор или ролик без комментариев"
    if not title_matches(game_title, cand.title):
        return f"в оригинальном названии ролика («{cand.title}») нет названия игры"
    if published_too_early(cand, release_date, today, LETSPLAY_WINDOW_DAYS):
        when = cand.published_date or cand.published or "давно"
        return f"ролик опубликован {when}, больше чем за год до выхода игры"
    return None


def trailer_problem(game_title: str, cand: VideoCandidate, release_date: str | None, today: date,
                    strict_edition: bool = True, max_seconds: int = 600,
                    owners: list[str] | None = None) -> str | None:
    if cand.live or cand.views is None:
        return "трансляция или нет просмотров"
    if cand.length_seconds is None or not 20 <= cand.length_seconds <= max_seconds:
        return "не похоже на трейлер по длительности"
    if not TRAILER.search(cand.title) or REACTION.search(cand.title):
        return "в названии нет слова «трейлер» или это реакция"
    if NOT_A_GAME.search(cand.title) or (cand.channel and FILM_CHANNEL.search(cand.channel)):
        return "похоже на фильм или личное видео"
    if not title_matches(game_title, cand.title, anchored=True):
        return "название игры не стоит отдельно в начале названия ролика"
    if strict_edition:
        if not edition_matches(game_title, cand.title):
            return "в названии нет слов издания"
        if published_too_early(cand, release_date, today, TRAILER_WINDOW_DAYS):
            return "трейлер опубликован больше чем за три года до выхода игры"
    if owners is not None and not trailer_owned(cand, owners, game_title):
        return "ролик выложил не разработчик, не издатель и не игровой канал"
    return None


def owner_words(name: str) -> set[str]:
    return {w for w in normalize(name).split() if w not in OWNER_STOP and len(w) > 1}


def trailer_owned(cand: VideoCandidate, owners: list[str], game_title: str = "") -> bool:
    channel = normalize(cand.channel or "")
    if not channel:
        return bool(cand.description and STORE_LINK.search(cand.description))
    if channel in TRAILER_AGGREGATORS or re.search(r"\btrailers?\b", channel):
        return True
    channel_words = set(channel.split())
    squashed = channel.replace(" ", "")
    title_words = set(phrase_words(game_title))
    if title_words and channel_words - OWNER_STOP - {"official"} and \
            (channel_words - OWNER_STOP - {"official"}) <= title_words:
        return True
    desc_words = set(normalize(cand.description or "").split())
    for owner in owners or []:
        words = owner_words(owner)
        if not words:
            continue
        if words <= channel_words or "".join(sorted(words, key=normalize(owner).find)) in squashed:
            return True
        if words <= desc_words:
            return True
    return bool(cand.description and STORE_LINK.search(cand.description))


def rank_trailers(game_title: str, candidates: list[VideoCandidate], max_seconds: int = 600,
                  release_date: str | None = None, today: date | None = None,
                  strict_edition: bool = True, owners: list[str] | None = None) -> list[VideoCandidate]:
    today = today or date.today()
    eligible = [c for c in candidates
                if trailer_problem(game_title, c, release_date, today, strict_edition, max_seconds,
                                   owners=owners) is None]
    return sorted(eligible, key=lambda c: (not bool(OFFICIAL.search(c.title)), -(c.views or 0)))


CANDIDATE_FIELDS = ("video_id", "title", "views", "length_seconds", "channel", "published", "description", "live",
                    "base_game", "published_date", "search_title")


def stored_candidate(video: dict) -> VideoCandidate:
    data = {k: video[k] for k in CANDIDATE_FIELDS if video.get(k) is not None}
    data.setdefault("video_id", "")
    data.setdefault("title", "")
    return VideoCandidate(**data)


def apply_watch_details(cand: VideoCandidate, details: dict | None) -> None:
    cand.refined = True
    if not details:
        return
    title = details.get("title")
    if title and title != cand.title:
        cand.search_title = cand.search_title or cand.title
        cand.title = title
    cand.views = details.get("views") or cand.views
    cand.length_seconds = details.get("length_seconds") or cand.length_seconds
    cand.channel = details.get("channel") or cand.channel
    cand.description = details.get("description") or cand.description
    cand.published_date = details.get("published_date") or cand.published_date


def merge_candidates(lists: list[tuple[str, list[VideoCandidate]]]) -> list[VideoCandidate]:
    merged: dict[str, VideoCandidate] = {}
    for query, items in lists:
        for c in items:
            if c.video_id in merged:
                merged[c.video_id].queries.append(query)
            else:
                c.queries = [query]
                merged[c.video_id] = c
    return list(merged.values())


NOISE = re.compile(r"\[[^\[\]]{1,30}\]|♪+|>>", re.I)
REPEATS = re.compile(r"(?<!\w)(\w{1,12})[.!?,]*(?:\s+\1[.!?,]*(?!\w)){2,}", re.I)


def transcript_to_text(snippets: list[dict]) -> str:
    text = " ".join((s.get("text") or "").replace("\n", " ") for s in snippets)
    text = NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    text = REPEATS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


TRANSCRIPT_REASONS = {
    "TranscriptsDisabled": "автор отключил субтитры",
    "NoTranscriptFound": "субтитров нет",
    "NoTranscriptAvailable": "субтитров нет",
    "RequestBlocked": "YouTube не отдаёт субтитры этому серверу",
    "IpBlocked": "YouTube не отдаёт субтитры этому серверу",
    "PoTokenRequired": "YouTube требует подтверждение браузера для субтитров",
    "VideoUnavailable": "ролик недоступен",
    "VideoUnplayable": "ролик недоступен",
    "InvalidVideoId": "некорректный id ролика",
    "AgeRestricted": "у ролика возрастное ограничение",
    "YouTubeRequestFailed": "YouTube не ответил на запрос субтитров",
    "FailedToCreateConsentCookie": "YouTube запросил согласие на cookies",
    "TooManyRequests": "YouTube ограничил частоту запросов",
}


def transcript_reason(exc: BaseException) -> str:
    for cls in type(exc).__mro__:
        if cls.__name__ in TRANSCRIPT_REASONS:
            return TRANSCRIPT_REASONS[cls.__name__]
    return "не удалось получить субтитры"


def sample_transcript(text: str, max_chars: int = 9000) -> str:
    if len(text) <= max_chars:
        return text
    part = max_chars // 3
    mid = len(text) // 2
    chunks = [text[:part], text[mid - part // 2: mid + part // 2], text[-part:]]

    def trim(chunk: str) -> str:
        chunk = chunk.strip()
        first = chunk.find(" ")
        last = chunk.rfind(" ")
        return chunk[first + 1:last] if 0 <= first < last else chunk

    return "\n[…начало…]\n" + trim(chunks[0]) + "\n[…середина…]\n" + trim(chunks[1]) + "\n[…конец…]\n" + trim(chunks[2])


class TranscriptUnavailable(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class YouTubeClient:
    def __init__(self, http: PoliteClient, proxy_url: str = "", refine_top: int = 5, today=None):
        self.http = http
        self.proxy_url = proxy_url
        self.refine_top = refine_top
        self.today = today or date.today
        self._api = None
        self.last_stats: dict = {}

    def search(self, query: str, sp: str | None = SP_POPULAR_LONG) -> list[VideoCandidate]:
        params = {"search_query": query, "hl": "en", "gl": "US"}
        if sp:
            params["sp"] = sp
        resp = self.http.get(SEARCH_URL, params=params, cookies=CONSENT_COOKIES, headers={"Accept": "text/html"})
        if urlparse(str(resp.url)).hostname == "consent.youtube.com":
            raise YouTubeBlocked("YouTube перенаправил на страницу согласия на cookies")
        return parse_search_html(resp.text)

    def watch_details(self, video_id: str) -> dict | None:
        resp = self.http.get(WATCH_URL, params={"v": video_id, "hl": "en"}, cookies=CONSENT_COOKIES,
                             headers={"Accept": "text/html"})
        return parse_watch_html(resp.text)

    def refine(self, cand: VideoCandidate) -> bool:
        if cand.refined:
            return True
        try:
            details = self.watch_details(cand.video_id)
        except Exception as exc:
            log.info("Страница ролика %s недоступна: %s", cand.video_id, exc)
            return False
        apply_watch_details(cand, details)
        return details is not None

    def find_letsplays(self, game_title: str, release_date: str | None = None) -> list[VideoCandidate]:
        results = []
        last_exc: Exception | None = None
        for query in (f"{game_title} let's play", f"{game_title} прохождение"):
            try:
                results.append((query, self.search(query)))
            except Exception as exc:
                last_exc = exc
                log.warning("Поиск YouTube «%s» не удался: %s", query, exc)
        if not results and last_exc is not None:
            raise last_exc
        stats: dict = {}
        today = self.today()
        released = is_released(release_date, today)
        ranked = rank_candidates(game_title, merge_candidates(results), released=released, stats=stats,
                                 release_date=release_date, today=today)
        head, tail = ranked[: self.refine_top], ranked[self.refine_top:]
        kept, dropped = [], []
        for cand in head:
            if self.refine(cand):
                reason = recheck_letsplay(game_title, cand, release_date, today)
                if reason:
                    dropped.append({"video_id": cand.video_id, "title": cand.title,
                                    "search_title": cand.search_title, "reason": reason})
                    continue
            kept.append(cand)
        if dropped:
            stats["dropped"] = dropped
            stats["eligible"] = stats.get("eligible", 0) - len(dropped)
        self.last_stats = stats
        return sort_letsplays(kept, released) + tail

    def find_trailer(self, game_title: str, release_date: str | None = None,
                     owners: list[str] | None = None) -> VideoCandidate | None:
        found = self.search(f"{game_title} official trailer", sp=None)
        today = self.today()
        ranked = rank_trailers(game_title, found, release_date=release_date, today=today, owners=owners)
        if ranked:
            return ranked[0]
        if not edition_words(game_title):
            return None
        relaxed = rank_trailers(game_title, found, release_date=release_date, today=today, strict_edition=False,
                                owners=owners)
        if relaxed:
            relaxed[0].base_game = True
            return relaxed[0]
        return None

    def _transcript_api(self):
        if self._api is None:
            import requests
            from youtube_transcript_api import YouTubeTranscriptApi

            http = self.http

            class PoliteSession(requests.Session):
                def request(self, method, url, *args, **kw):
                    kw.setdefault("timeout", 30)
                    host = urlparse(str(url)).netloc
                    http.limiter.wait(host)
                    try:
                        resp = super().request(method, url, *args, **kw)
                    except requests.RequestException:
                        http.report(host, False)
                        raise
                    http.report(host, resp.status_code < 400)
                    return resp

            session = PoliteSession()
            session.headers.update({"User-Agent": self.http.client.headers.get("User-Agent", ""),
                                    "Accept-Language": "en-US,en;q=0.9"})
            kwargs = {"http_client": session}
            if self.proxy_url:
                from youtube_transcript_api.proxies import GenericProxyConfig

                kwargs["proxy_config"] = GenericProxyConfig(http_url=self.proxy_url, https_url=self.proxy_url)
            self._api = YouTubeTranscriptApi(**kwargs)
        return self._api

    def transcript(self, video_id: str) -> tuple[str, str]:
        if not VIDEO_ID.match(video_id or ""):
            raise TranscriptUnavailable("некорректный id ролика", "InvalidVideoId")
        api = self._transcript_api()
        try:
            listing = api.list(video_id)
            try:
                tr = listing.find_transcript(["en", "ru"])
            except Exception:
                tr = next(iter(listing))
            raw = tr.fetch().to_raw_data()
        except StopIteration:
            raise TranscriptUnavailable("субтитров нет", "NoTranscriptFound")
        except Exception as exc:
            raise TranscriptUnavailable(transcript_reason(exc), exc.__class__.__name__) from exc
        text = transcript_to_text(raw)
        if len(text) < 200:
            raise TranscriptUnavailable("в субтитрах почти нет текста", "TooShort")
        return text, tr.language_code
