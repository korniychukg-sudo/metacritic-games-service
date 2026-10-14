import pytest

from app.metacritic import (
    MetacriticClient,
    cover_url,
    parse_composer,
    parse_list,
    parse_review_list,
    parse_reviews,
    parse_user_stats,
    platform_short,
)
from app.http_client import NotFound

from .conftest import load_json


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
    reviews, total = parse_reviews(load_json("critic_summary_niph.json"), "critic")
    assert reviews == [] and total == 0


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
