import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
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
PREFER = re.compile(
    r"(let'?s\s*play|walkthrough|playthrough|gameplay|full\s+game|part\s*1|episode\s*1|прохождение|летсплей|стрим|stream)",
    re.I,
)
STOP = {"the", "a", "an", "of", "and", "edition", "remastered", "remaster", "complete", "definitive", "deluxe",
        "game", "goty", "collection", "hd", "for"}


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


def title_tokens(title: str) -> list[str]:
    toks = [t for t in normalize(title).split() if t not in STOP]
    return toks or normalize(title).split()


def title_overlap(game_title: str, video_title: str) -> float:
    gt = title_tokens(game_title)
    if not gt:
        return 0.0
    vt = set(normalize(video_title).split())
    return sum(1 for t in gt if t in vt) / len(gt)


def _split_words(title: str) -> tuple[set[str], set[str]]:
    words, nums = set(), set()
    for t in title_tokens(title):
        if t.isdigit():
            nums.add(t)
        elif len(t) >= 2:
            words.add(t)
    return words, nums


def _numbers_in_place(game_title: str, video_title: str) -> bool:
    gt = normalize(game_title).split()
    vt = f" {normalize(video_title)} "
    for i, tok in enumerate(gt):
        if not tok.isdigit():
            continue
        if i > 0:
            phrase = f" {gt[i - 1]} {tok} "
        elif i + 1 < len(gt):
            phrase = f" {tok} {gt[i + 1]} "
        else:
            phrase = f" {tok} "
        if phrase not in vt:
            return False
    return True


def title_matches(game_title: str, video_title: str) -> bool:
    words, nums = _split_words(game_title)
    vt = set(normalize(video_title).split())
    if not words and not nums:
        words = set(normalize(game_title).split())
    if not words:
        return bool(nums) and nums <= vt
    if not _numbers_in_place(game_title, video_title):
        return False
    overlap = len(words & vt) / len(words)
    parts = re.split(r"\s*[:\u2013\u2014]\s*|\s+-\s+", game_title, maxsplit=1)
    if len(parts) == 2:
        sub_words, _ = _split_words(parts[1])
        if sub_words and not sub_words <= vt:
            return False
        return overlap >= 0.5
    return overlap >= 0.75


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


def parse_search_html(html_text: str) -> list[VideoCandidate]:
    data = extract_initial_data(html_text)
    if data is None:
        reason = detect_block(html_text)
        if reason:
            raise YouTubeBlocked(reason)
        raise SchemaError("на странице поиска YouTube нет ytInitialData (сменился формат страницы)")
    out: list[VideoCandidate] = []
    seen = set()
    for vr in iter_video_renderers(data):
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
    return out


def parse_watch_html(html_text: str) -> dict | None:
    m = re.search(r"var ytInitialPlayerResponse = (\{.*?\});(?:var|</script>)", html_text, re.S)
    if not m:
        return None
    try:
        details = json.loads(m.group(1)).get("videoDetails") or {}
    except json.JSONDecodeError:
        return None
    return {
        "video_id": details.get("videoId"),
        "title": details.get("title"),
        "views": int(details["viewCount"]) if str(details.get("viewCount", "")).isdigit() else None,
        "length_seconds": int(details["lengthSeconds"]) if str(details.get("lengthSeconds", "")).isdigit() else None,
        "channel": details.get("author"),
        "description": details.get("shortDescription"),
        "live": bool(details.get("isLiveContent")),
    }


def rank_candidates(game_title: str, candidates: list[VideoCandidate],
                    min_seconds: int = 600) -> list[VideoCandidate]:
    eligible = []
    for c in candidates:
        if c.live or c.views is None:
            continue
        if c.length_seconds is not None and c.length_seconds < min_seconds:
            continue
        if EXCLUDE.search(c.title):
            continue
        if not title_matches(game_title, c.title):
            continue
        eligible.append(c)
    return sorted(eligible, key=lambda c: (not bool(PREFER.search(c.title)), -(c.views or 0)))


def rank_trailers(game_title: str, candidates: list[VideoCandidate], max_seconds: int = 600) -> list[VideoCandidate]:
    eligible = []
    for c in candidates:
        if c.live or c.views is None:
            continue
        if c.length_seconds is None or not 20 <= c.length_seconds <= max_seconds:
            continue
        if not TRAILER.search(c.title) or re.search(r"\breaction\b|реакция", c.title, re.I):
            continue
        if not title_matches(game_title, c.title):
            continue
        eligible.append(c)
    return sorted(eligible, key=lambda c: (not bool(OFFICIAL.search(c.title)), -(c.views or 0)))


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


NOISE = re.compile(r"\[(?:music|applause|laughter|музыка|аплодисменты|смех|__)\]|♪+|>>", re.I)


def transcript_to_text(snippets: list[dict]) -> str:
    text = " ".join((s.get("text") or "").replace("\n", " ") for s in snippets)
    text = NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


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
    pass


class YouTubeClient:
    def __init__(self, http: PoliteClient, proxy_url: str = ""):
        self.http = http
        self.proxy_url = proxy_url
        self._api = None

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

    def find_letsplays(self, game_title: str) -> list[VideoCandidate]:
        results = []
        last_exc: Exception | None = None
        for query in (f"{game_title} let's play", f"{game_title} прохождение"):
            try:
                results.append((query, self.search(query)))
            except Exception as exc:
                last_exc = exc
                log.warning("YouTube search failed for %r: %s", query, exc)
        if not results and last_exc is not None:
            raise last_exc
        return rank_candidates(game_title, merge_candidates(results))

    def find_trailer(self, game_title: str) -> VideoCandidate | None:
        ranked = rank_trailers(game_title, self.search(f"{game_title} official trailer", sp=None))
        return ranked[0] if ranked else None

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
            raise TranscriptUnavailable("некорректный id ролика")
        api = self._transcript_api()
        try:
            listing = api.list(video_id)
            try:
                tr = listing.find_transcript(["en", "ru"])
            except Exception:
                tr = next(iter(listing))
            raw = tr.fetch().to_raw_data()
        except StopIteration:
            raise TranscriptUnavailable("нет ни одной дорожки субтитров")
        except Exception as exc:
            raise TranscriptUnavailable(f"{exc.__class__.__name__}") from exc
        text = transcript_to_text(raw)
        if len(text) < 200:
            raise TranscriptUnavailable("расшифровка слишком короткая")
        return text, tr.language_code
