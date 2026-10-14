import copy

import pytest

from app.http_client import NotFound, SchemaError
from app.metacritic import (
    MetacriticClient,
    cover_url,
    parse_composer,
    parse_list,
    parse_review_list,
    parse_reviews,
    parse_user_stats,
    platform_short,
    safe_url,
)

from .conftest import load_json, product_item


def test_new_releases_list():
    items, total = parse_list(load_json("finder_new_releases.json"))
    assert len(items) == 20 and total > 1000
    first = items[0]
    assert first.id == 1300667189 and first.slug == "over-the-hill"
    assert first.metascore == 68
    assert first.cover_url == "https://www.metacritic.com/a/img/catalog/provider/6/12/6-1-1087502-52.jpg"
    assert first.lead_platform == "PC"
    assert "star-wars-galactic-racer" in [i.slug for i in items]


def test_browse_list_has_24_items_and_unscored_games():
    items, total = parse_list(load_json("finder_browse_new_p1.json"))
    assert len(items) == 24 and total > 100000
    assert any(i.metascore is None for i in items)
    assert len({i.id for i in items}) == 24


def test_composer_full_card():
    g = parse_composer(load_json("composer_game_star-wars-galactic-racer.json"))
    assert g.id == 1300714334
    assert g.title == "Star Wars: Galactic Racer"
    assert g.developers == ["Fuse Games"] and g.publishers == ["Secret Mode"]
    assert g.description.startswith("Star Wars: Galactic Racer is a runs-based")
    assert "\r" not in g.description
    assert g.cover_url == "https://www.metacritic.com/a/img/catalog/provider/7/2/7-1782239544.jpg"
    assert g.video_embed_url == "https://cdn.jwplayer.com/players/FsPCrZeP.html"
    assert g.video_title == "Star Wars: Galactic Racer – Official Launch Trailer"
    assert g.franchises == ["Star Wars", "Star Wars: Racer"]
    by_slug = {p.slug: p for p in g.platforms}
    assert set(by_slug) == {"pc", "xbox-series-x", "playstation-5"}
    assert by_slug["playstation-5"].is_lead and by_slug["playstation-5"].metascore == 88
    assert by_slug["pc"].metascore == 84 and by_slug["xbox-series-x"].metascore == 86
    assert by_slug["playstation-5"].userscore == 8.3 and by_slug["playstation-5"].user_count == 172
    assert by_slug["pc"].userscore is None
    assert g.platforms[0].slug == "playstation-5"
    assert g.metascore == 88 and g.userscore == 8.3
    assert g.critic_total == 74 and g.user_total == 32
    assert 7 <= len(g.critic_reviews) <= 21
    assert {r.kind for r in g.critic_reviews} == {"critic"}
    assert any(r.sentiment == "negative" for r in g.user_reviews)
    assert len({r.quote for r in g.user_reviews}) == len(g.user_reviews)


def test_user_stats_zero_means_no_score():
    assert parse_user_stats(load_json("user_stats_niph_platform_pc.json")) == (None, 0)
    assert parse_user_stats(load_json("user_stats_star-wars-galactic-racer_platform_pc.json")) == (8.1, 18)


def test_empty_review_summary():
    reviews, total, known = parse_reviews(load_json("critic_summary_niph.json"), "critic")
    assert reviews == [] and total == 0 and known is True
    assert parse_reviews(None, "critic") == ([], 0, False)
    assert parse_reviews({"data": {"totalResults": 5}}, "user") == ([], 5, False)


def test_review_list_sentiment_from_score():
    reviews, total = parse_review_list(load_json("user_reviews_star-wars-galactic-racer_ps5.json"), "user")
    assert reviews and total >= len(reviews)
    for r in reviews:
        if r.score is not None and r.score >= 7.5:
            assert r.sentiment == "positive"
        if r.score is not None and r.score < 5:
            assert r.sentiment == "negative"


def test_platform_short_codes():
    assert platform_short("playstation-5", "PlayStation 5") == "PS5"
    assert platform_short("nintendo-switch-2", "Nintendo Switch 2") == "NS2"
    assert platform_short("something-new", "Something New") == "Something New"
    assert cover_url(None) is None


class StubHttp:
    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def get(self, url, params=None, headers=None, cookies=None):
        self.urls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if value is None:
                    raise NotFound(url)

                class R:
                    def json(self_inner):
                        return value
                return R()
        raise AssertionError(f"unexpected url {url}")


def test_full_game_fetches_userscore_for_other_platforms_by_path_segment():
    http = StubHttp({
        "/composer/metacritic/pages/games/star-wars-galactic-racer/web": load_json(
            "composer_game_star-wars-galactic-racer.json"),
        "/platform/pc/stats/web": load_json("user_stats_star-wars-galactic-racer_platform_pc.json"),
        "/platform/xbox-series-x/stats/web": load_json("user_stats_niph_platform_pc.json"),
        "/reviews/metacritic/user/games/star-wars-galactic-racer/platform/playstation-5/web": load_json(
            "user_reviews_star-wars-galactic-racer_ps5.json"),
        "/reviews/metacritic/critic/games/star-wars-galactic-racer/platform/playstation-5/web": load_json(
            "critic_reviews_star-wars-galactic-racer_ps5.json"),
    })
    g = MetacriticClient(http).full_game("star-wars-galactic-racer")
    by_slug = {p.slug: p for p in g.platforms}
    assert by_slug["pc"].userscore == 8.1
    assert by_slug["xbox-series-x"].userscore is None
    assert by_slug["playstation-5"].userscore == 8.3
    assert not any("stats/web" in u and "playstation-5" in u for u in http.urls)
    assert all("?platform=" not in u for u in http.urls)


def test_unknown_slug_raises_not_found():
    http = StubHttp({"/composer/": None})
    with pytest.raises(NotFound):
        MetacriticClient(http).game("this-slug-does-not-exist-xyz")
    with pytest.raises(NotFound):
        parse_composer(load_json("product_404.json"))


def test_list_schema_drift_raises_instead_of_returning_nothing():
    good = load_json("finder_browse_new_p1.json")
    renamed = copy.deepcopy(good)
    renamed["data"]["results"] = renamed["data"].pop("items")
    with pytest.raises(SchemaError):
        parse_list(renamed)
    with pytest.raises(SchemaError):
        parse_list({"meta": {}})
    with pytest.raises(SchemaError):
        parse_list([])
    empty_but_total = copy.deepcopy(good)
    empty_but_total["data"]["items"] = []
    with pytest.raises(SchemaError):
        parse_list(empty_but_total, offset=0)
    items, total = parse_list(empty_but_total, offset=total_of(good) + 24)
    assert items == [] and total == total_of(good)
    no_ids = copy.deepcopy(good)
    for it in no_ids["data"]["items"]:
        it.pop("id")
    with pytest.raises(SchemaError):
        parse_list(no_ids)


def total_of(payload):
    return payload["data"]["totalResults"]


def test_composer_schema_drift():
    good = load_json("composer_game_star-wars-galactic-racer.json")
    no_product = copy.deepcopy(good)
    no_product["components"] = [c for c in no_product["components"] if c["meta"]["componentName"] != "product"]
    with pytest.raises(SchemaError):
        parse_composer(no_product)
    with pytest.raises(SchemaError):
        parse_composer({"components": []})
    no_id = copy.deepcopy(good)
    product_item(no_id).pop("id")
    with pytest.raises(SchemaError):
        parse_composer(no_id)
    partial = copy.deepcopy(good)
    product_item(partial).pop("platforms")
    partial["components"] = [c for c in partial["components"] if c["meta"]["componentName"] != "critic-reviews"]
    g = parse_composer(partial)
    assert g.platforms == [] and g.critic_reviews == [] and g.critic_reviews_known is False
    assert g.user_reviews_known is True
    assert any("платформ" in w for w in g.warnings) and any("критиков" in w for w in g.warnings)
    assert parse_composer(good).warnings == []


def test_unsafe_urls_are_dropped_by_the_parser():
    payload = load_json("composer_game_star-wars-galactic-racer.json")
    item = product_item(payload)
    item["video"]["embedUrl"] = "javascript:alert(document.domain)"
    item["video"]["manifestUrl"] = "https://evil.example/x.m3u8"
    for comp in payload["components"]:
        if comp["meta"]["componentName"] == "critic-reviews":
            for r in comp["data"]["item"]["positive"]:
                r["url"] = "javascript:alert(1)"
    g = parse_composer(payload)
    assert g.video_embed_url is None and g.video_manifest_url is None
    assert all(r.url is None or r.url.startswith(("https://", "http://")) for r in g.critic_reviews)
    assert any(r.url is None for r in g.critic_reviews if r.sentiment == "positive")
    assert safe_url("https://cdn.jwplayer.com/players/x.html", frozenset({"cdn.jwplayer.com"}))
    assert safe_url("https://cdn.jwplayer.com.evil.example/x", frozenset({"cdn.jwplayer.com"})) is None
    assert safe_url("http://site.example/review", schemes=("https", "http"))
    assert safe_url("data:text/html,hi") is None and safe_url(None) is None
    assert cover_url("/provider/7/2/a.jpg").startswith("https://www.metacritic.com/a/img/catalog/")
    assert cover_url("//evil.example/a.jpg") is None and cover_url('/a" onerror="x') is None
