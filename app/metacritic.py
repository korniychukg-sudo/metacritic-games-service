import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .http_client import NotFound, PoliteClient, SchemaError

log = logging.getLogger("metacritic")

BACKEND = "https://backend.metacritic.com"
SITE = "https://www.metacritic.com"
IMG_PREFIX = f"{SITE}/a/img/catalog"

PLATFORM_SHORT = {
    "playstation-5": "PS5",
    "playstation-4": "PS4",
    "playstation-3": "PS3",
    "nintendo-switch-2": "NS2",
    "nintendo-switch": "NS",
    "xbox-series-x": "XSX",
    "xbox-one": "XONE",
    "xbox-360": "X360",
    "pc": "PC",
    "ios-iphoneipad": "iOS",
    "meta-quest": "Quest",
    "3ds": "3DS",
    "wii-u": "Wii U",
    "playstation-vita": "Vita",
}


def platform_short(slug: str | None, name: str | None) -> str:
    if slug and slug in PLATFORM_SHORT:
        return PLATFORM_SHORT[slug]
    return name or slug or "?"


def platform_slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


VIDEO_HOSTS = frozenset({"cdn.jwplayer.com"})


def cover_url(bucket_path: str | None) -> str | None:
    if not isinstance(bucket_path, str) or not bucket_path.startswith("/") or bucket_path.startswith("//"):
        return None
    if any(ch in bucket_path for ch in "\"'<> \\"):
        return None
    return IMG_PREFIX + bucket_path


def safe_url(url: Any, hosts: frozenset[str] | None = None, schemes: tuple[str, ...] = ("https",)) -> str | None:
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in schemes or not parsed.hostname:
        return None
    if hosts is not None and parsed.hostname not in hosts:
        return None
    return url


@dataclass
class ListItem:
    id: int
    slug: str
    title: str
    release_date: str | None = None
    metascore: int | None = None
    cover_url: str | None = None
    lead_platform: str | None = None
    genres: list[str] = field(default_factory=list)


@dataclass
class Review:
    kind: str
    sentiment: str
    quote: str
    score: float | None = None
    author: str | None = None
    url: str | None = None
    date: str | None = None
    platform: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class PlatformInfo:
    slug: str
    name: str
    short: str
    is_lead: bool = False
    metascore: int | None = None
    critic_count: int | None = None
    userscore: float | None = None
    user_count: int | None = None
    release_date: str | None = None


@dataclass
class GameDetails:
    id: int
    slug: str
    title: str
    description: str | None = None
    release_date: str | None = None
    rating: str | None = None
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    franchises: list[str] = field(default_factory=list)
    cover_url: str | None = None
    hero_url: str | None = None
    video_embed_url: str | None = None
    video_manifest_url: str | None = None
    video_title: str | None = None
    lead_platform: str | None = None
    platforms: list[PlatformInfo] = field(default_factory=list)
    critic_reviews: list[Review] = field(default_factory=list)
    user_reviews: list[Review] = field(default_factory=list)
    critic_total: int = 0
    user_total: int = 0
    lead_userscore: float | None = None
    lead_user_count: int | None = None
    critic_negative_count: int = 0
    user_negative_count: int = 0
    critic_reviews_known: bool = True
    user_reviews_known: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def metascore(self) -> int | None:
        lead = next((p for p in self.platforms if p.is_lead and p.metascore is not None), None)
        if lead:
            return lead.metascore
        scores = [p.metascore for p in self.platforms if p.metascore is not None]
        return max(scores) if scores else None

    @property
    def userscore(self) -> float | None:
        lead = next((p for p in self.platforms if p.is_lead and p.userscore is not None), None)
        if lead:
            return lead.userscore
        scores = [p.userscore for p in self.platforms if p.userscore is not None]
        return max(scores) if scores else None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_list(payload: dict, offset: int = 0) -> tuple[list[ListItem], int]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise SchemaError("в ответе списка игр Metacritic нет объекта data")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise SchemaError("в ответе списка игр Metacritic нет массива data.items")
    total = _int_or_none(data.get("totalResults"))
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict) or raw.get("id") is None or not raw.get("slug"):
            continue
        plats = raw.get("platforms") or []
        items.append(
            ListItem(
                id=int(raw["id"]),
                slug=raw["slug"],
                title=(raw.get("title") or raw["slug"]).strip(),
                release_date=raw.get("releaseDate"),
                metascore=_int_or_none((raw.get("criticScoreSummary") or {}).get("score")),
                cover_url=cover_url((raw.get("image") or {}).get("bucketPath")),
                lead_platform=plats[0].get("name") if plats else None,
                genres=[g.get("name") for g in raw.get("genres") or [] if g.get("name")],
            )
        )
    if raw_items and not items:
        raise SchemaError("у элементов списка игр Metacritic нет полей id и slug")
    if not raw_items and total and offset < total:
        raise SchemaError(f"список игр Metacritic пуст, хотя totalResults={total}")
    return items, total or 0


def components_by_name(payload: dict) -> dict[str, dict]:
    out = {}
    for comp in (payload or {}).get("components") or []:
        name = (comp.get("meta") or {}).get("componentName")
        if name:
            out[name] = comp
    return out


def clean_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _pick_image(images: list[dict], *types: str) -> str | None:
    for t in types:
        for img in images or []:
            if img.get("typeName") == t and img.get("bucketPath"):
                return cover_url(img["bucketPath"])
    return None


def parse_reviews(summary_component: dict | None, kind: str) -> tuple[list[Review], int, bool]:
    if not summary_component or not isinstance(summary_component.get("data"), dict):
        return [], 0, False
    data = summary_component["data"]
    item = data.get("item")
    if not isinstance(item, dict) or not any(k in item for k in ("positive", "neutral", "negative", "default")):
        return [], _int_or_none(data.get("totalResults")) or 0, False
    seen: set[str] = set()
    out: list[Review] = []
    for sentiment in ("positive", "neutral", "negative", "default"):
        for r in item.get(sentiment) or []:
            rev = _review_from_raw(r, kind, sentiment)
            if rev and rev.quote not in seen:
                seen.add(rev.quote)
                out.append(rev)
    return out, _int_or_none(data.get("totalResults")) or 0, True


def sentiment_for(kind: str, score: float | None) -> str:
    if score is None:
        return "neutral"
    if kind == "critic":
        return "positive" if score >= 75 else "negative" if score < 50 else "neutral"
    return "positive" if score >= 7.5 else "negative" if score < 5 else "neutral"


PLACEHOLDER_QUOTES = ("quotation forthcoming", "quote not yet available")


def _review_from_raw(r: dict, kind: str, sentiment: str | None) -> Review | None:
    quote = clean_text(r.get("quote"))
    if not quote or quote.lower().startswith(PLACEHOLDER_QUOTES):
        return None
    score = _float_or_none(r.get("score"))
    if sentiment in (None, "default"):
        sentiment = sentiment_for(kind, score)
    author = r.get("publicationName") if kind == "critic" else r.get("author")
    return Review(
        kind=kind,
        sentiment=sentiment,
        quote=quote,
        score=score,
        author=author or None,
        url=safe_url(r.get("url"), schemes=("https", "http")) if kind == "critic" else None,
        date=r.get("date"),
        platform=r.get("platform"),
    )


def parse_review_list(payload: dict, kind: str) -> tuple[list[Review], int]:
    data = (payload or {}).get("data") or {}
    out = []
    for r in data.get("items") or []:
        rev = _review_from_raw(r, kind, None)
        if rev:
            out.append(rev)
    return out, int(data.get("totalResults") or 0)


def parse_user_stats(payload: dict) -> tuple[float | None, int | None]:
    item = ((payload or {}).get("data") or {}).get("item") or {}
    count = _int_or_none(item.get("reviewCount")) or 0
    score = _float_or_none(item.get("score"))
    if not count or not score:
        return None, count or 0
    return score, count


def _not_found_errors(payload: Any) -> bool:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    return bool(errors) and any(isinstance(e, dict) and e.get("code") == 404 for e in errors)


def parse_composer(payload: dict) -> GameDetails:
    if _not_found_errors(payload):
        raise NotFound("game not found")
    comps = components_by_name(payload)
    product = comps.get("product")
    if not product:
        raise SchemaError("в ответе карточки Metacritic нет компонента product")
    if _not_found_errors(product):
        raise NotFound("game not found")
    item = (product.get("data") or {}).get("item") if isinstance(product.get("data"), dict) else None
    if not isinstance(item, dict) or item.get("id") is None or not item.get("slug"):
        raise SchemaError("в компоненте product нет id или slug игры")
    warnings: list[str] = []
    if not isinstance(item.get("platforms"), list) or not item.get("platforms"):
        warnings.append("в карточке нет списка платформ")
    companies = (item.get("production") or {}).get("companies") or []
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    lead_name = item.get("platform")

    platforms: list[PlatformInfo] = []
    for p in item.get("platforms") or []:
        name = p.get("name") or ""
        slug = p.get("slug") or platform_slugify(name)
        css = p.get("criticScoreSummary") or {}
        platforms.append(
            PlatformInfo(
                slug=slug,
                name=name,
                short=platform_short(slug, name),
                is_lead=bool(p.get("isLeadPlatform")) or (name == lead_name and not any(
                    q.get("isLeadPlatform") for q in item.get("platforms") or [])),
                metascore=_int_or_none(css.get("score")),
                critic_count=_int_or_none(css.get("reviewCount")),
                release_date=p.get("releaseDate"),
            )
        )
    platforms.sort(key=lambda p: (not p.is_lead, p.name))

    lead_userscore, lead_user_count = None, None
    user_negative = 0
    uss = comps.get("user-score-summary")
    if uss and "data" in uss:
        lead_userscore, lead_user_count = parse_user_stats(uss)
        user_negative = _int_or_none(((uss.get("data") or {}).get("item") or {}).get("negativeCount")) or 0
    critic_negative = 0
    css_comp = comps.get("critic-score-summary")
    if css_comp and "data" in css_comp:
        critic_negative = _int_or_none(((css_comp.get("data") or {}).get("item") or {}).get("negativeCount")) or 0
        for p in platforms:
            if p.is_lead:
                p.userscore, p.user_count = lead_userscore, lead_user_count

    critic_reviews, critic_total, critic_known = parse_reviews(comps.get("critic-reviews"), "critic")
    user_reviews, user_total, user_known = parse_reviews(comps.get("user-reviews"), "user")
    if not critic_known:
        warnings.append("в карточке нет блока отзывов критиков")
    if not user_known:
        warnings.append("в карточке нет блока отзывов игроков")

    return GameDetails(
        id=int(item.get("id")),
        slug=item.get("slug"),
        title=clean_text(item.get("title")) or item.get("slug"),
        description=clean_text(item.get("description")),
        release_date=item.get("releaseDate"),
        rating=item.get("rating"),
        developers=[c["name"] for c in companies if c.get("typeName") == "Developer" and c.get("name")],
        publishers=[c["name"] for c in companies if c.get("typeName") == "Publisher" and c.get("name")],
        genres=[g["name"] for g in item.get("genres") or [] if g.get("name")],
        franchises=[f["name"] for f in (item.get("gameTaxonomy") or {}).get("franchises") or [] if f.get("name")],
        cover_url=_pick_image(item.get("images"), "cardImage", "mainImage"),
        hero_url=_pick_image(item.get("images"), "mainImage", "cardImage"),
        video_embed_url=safe_url(video.get("embedUrl"), VIDEO_HOSTS),
        video_manifest_url=safe_url(video.get("manifestUrl"), VIDEO_HOSTS),
        video_title=clean_text(video.get("title") or video.get("videoTitle")),
        lead_platform=lead_name or next((p.name for p in platforms if p.is_lead), None),
        platforms=platforms,
        critic_reviews=critic_reviews,
        user_reviews=user_reviews,
        critic_total=critic_total,
        user_total=user_total,
        lead_userscore=lead_userscore,
        lead_user_count=lead_user_count,
        critic_negative_count=critic_negative,
        user_negative_count=user_negative,
        critic_reviews_known=critic_known,
        user_reviews_known=user_known,
        warnings=warnings,
    )


class MetacriticClient:
    def __init__(self, http: PoliteClient):
        self.http = http

    def _json(self, path: str, params: dict | None = None) -> dict:
        resp = self.http.get(BACKEND + path, params=params, headers={"Accept": "application/json"})
        return resp.json()

    def new_releases(self, limit: int = 20) -> list[ListItem]:
        payload = self._json(
            "/finder/metacritic/web",
            {
                "componentName": "new-releases-carousel",
                "componentDisplayName": "Newly Released",
                "componentType": "ProductList",
                "sortBy": "-releaseDate",
                "metaScoreMin": 1,
                "offset": 0,
                "limit": limit,
                "mcoTypeId": 13,
            },
        )
        return parse_list(payload, 0)[0]

    def browse_new(self, offset: int, limit: int = 24) -> list[ListItem]:
        payload = self._json(
            "/finder/metacritic/web",
            {"sortBy": "-releaseDate", "productType": "games", "offset": offset, "limit": limit, "mcoTypeId": 13},
        )
        return parse_list(payload, offset)[0]

    def game(self, slug: str) -> GameDetails:
        return parse_composer(self._json(f"/composer/metacritic/pages/games/{slug}/web"))

    def user_stats(self, slug: str, platform_slug: str) -> tuple[float | None, int | None]:
        payload = self._json(
            f"/reviews/metacritic/user/games/{slug}/platform/{platform_slug}/stats/web",
            {
                "componentName": "user-score-summary",
                "componentDisplayName": "user Score Summary",
                "componentType": "MetaScoreSummary",
            },
        )
        return parse_user_stats(payload)

    def review_list(self, kind: str, slug: str, platform_slug: str, sentiment: str = "all",
                    limit: int | None = None, offset: int = 0) -> list[Review]:
        limit = limit or (10 if kind == "critic" else 20)
        payload = self._json(
            f"/reviews/metacritic/{kind}/games/{slug}/platform/{platform_slug}/web",
            {
                "offset": offset,
                "limit": limit,
                "filterBySentiment": sentiment,
                "sort": "date",
                "componentName": f"{kind}-reviews",
                "componentDisplayName": f"{kind.title()} Reviews",
                "componentType": "ReviewList",
            },
        )
        return parse_review_list(payload, kind)[0]

    def full_game(self, slug: str, top_up_reviews: bool = True) -> GameDetails:
        game = self.game(slug)
        for p in game.platforms:
            if p.is_lead and p.userscore is not None:
                continue
            if p.is_lead and game.lead_user_count == 0:
                continue
            try:
                p.userscore, p.user_count = self.user_stats(slug, p.slug)
            except NotFound:
                p.userscore, p.user_count = None, 0
            except Exception as exc:
                log.warning("Userscore %s/%s не получен: %s", slug, p.slug, exc)
        if top_up_reviews:
            lead = next((p for p in game.platforms if p.is_lead), None)
            if lead:
                self._top_up(game, lead.slug, "critic")
                self._top_up(game, lead.slug, "user")
        return game

    def _top_up(self, game: GameDetails, platform_slug: str, kind: str) -> None:
        reviews = game.critic_reviews if kind == "critic" else game.user_reviews
        available = game.critic_negative_count if kind == "critic" else game.user_negative_count
        have = sum(1 for r in reviews if r.sentiment == "negative")
        if have >= 5 or available <= have:
            return
        try:
            extra = self.review_list(kind, game.slug, platform_slug, sentiment="negative")
        except Exception as exc:
            log.info("Дополнительные отзывы (%s) для %s недоступны: %s", kind, game.slug, exc)
            return
        known = {r.quote for r in reviews}
        for r in extra:
            if r.quote not in known:
                r.sentiment = "negative"
                reviews.append(r)
                known.add(r.quote)
