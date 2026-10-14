from app.youtube import (
    VideoCandidate,
    parse_duration,
    parse_search_html,
    parse_views,
    parse_watch_html,
    rank_candidates,
    sample_transcript,
    transcript_to_text,
)

from .conftest import load_json, load_text


def test_search_parse():
    cands = parse_search_html(load_text("yt_search_star-wars-galactic-racer_lets-play_over20min.html"))
    assert len(cands) == 20
    rad = next(c for c in cands if c.video_id == "bGBXM6oxbag")
    assert rad.views == 465300 and rad.length_seconds == 6170 and rad.channel == "theRadBrad"


def test_most_popular_letsplay_is_selected():
    cands = parse_search_html(load_text("yt_search_star-wars-galactic-racer_lets-play_over20min.html"))
    ranked = rank_candidates("Star Wars: Galactic Racer", cands)
    assert [c.video_id for c in ranked[:3]] == ["nYThl8ODM8c", "bGBXM6oxbag", "WAKDTrzOaF8"]
    assert [c.views for c in ranked] == sorted((c.views for c in ranked), reverse=True)
    assert all("review" not in c.title.lower() for c in ranked)
    assert all(c.video_id != "YWlb1OMbmOQ" for c in ranked)


def test_rank_filters_short_live_and_trailers_and_orders_by_views():
    base = dict(views=10, length_seconds=3600, channel="c")
    cands = [
        VideoCandidate("a", "Game X Official Trailer", **base),
        VideoCandidate("b", "Game X gameplay", views=5, length_seconds=120, channel="c"),
        VideoCandidate("c", "Game X live", views=None, length_seconds=None, live=True),
        VideoCandidate("d", "Game X full playthrough", **{**base, "views": 50}),
        VideoCandidate("e", "Game X is great", **{**base, "views": 500}),
    ]
    ranked = rank_candidates("Game X", cands)
    assert [c.video_id for c in ranked] == ["e", "d"]


def test_demo_videos_rank_last_once_the_game_is_out():
    base = dict(length_seconds=3600, channel="c")
    cands = [
        VideoCandidate("demo", "Game X FULL DEMO Gameplay", views=9000, **base),
        VideoCandidate("early", "I Played Game X Early!", views=8000, **base),
        VideoCandidate("full", "Game X Let's Play Part 1", views=100, **base),
    ]
    assert [c.video_id for c in rank_candidates("Game X", cands)] == ["demo", "early", "full"]
    assert [c.video_id for c in rank_candidates("Game X", cands, released=True)] == ["full", "demo", "early"]
    from datetime import date

    from app.youtube import is_released

    assert is_released("2026-10-13", date(2026, 10, 15)) and not is_released("2026-11-01", date(2026, 10, 15))
    assert not is_released(None) and not is_released("soon")


def test_title_mismatches_are_counted():
    stats = {}
    cands = [VideoCandidate("a", "Dragon's Dogma 2 Let's Play", views=10, length_seconds=3600),
             VideoCandidate("b", "Dragon's Dogma 2 Dark Arisen walkthrough", views=5, length_seconds=3600),
             VideoCandidate("c", "Dragon's Dogma 2 Official Trailer", views=5, length_seconds=3600)]
    ranked = rank_candidates("Dragon's Dogma 2: Dark Arisen", cands, stats=stats)
    assert [c.video_id for c in ranked] == ["b"]
    assert stats == {"title_mismatch": 1, "found": 3, "eligible": 1}


def test_videos_without_commentary_are_not_lets_plays():
    base = dict(views=10, length_seconds=3600, channel="c")
    cands = [
        VideoCandidate("a", "Game X Walkthrough FULL GAME (4K 60FPS) No Commentary", **{**base, "views": 900}),
        VideoCandidate("b", "Game X прохождение без комментариев", **{**base, "views": 800}),
        VideoCandidate("c", "Game X gameplay #nocommentary", **{**base, "views": 700}),
        VideoCandidate("d", "Game X let's play part 1", **base),
    ]
    assert [c.video_id for c in rank_candidates("Game X", cands)] == ["d"]
    ru = parse_search_html(load_text("yt_search_star-wars-galactic-racer_prohozhdenie_over20min.html"))
    assert not any("commentary" in c.title.lower() for c in rank_candidates("Star Wars: Galactic Racer", ru))


def test_watch_page_details():
    d = parse_watch_html(load_text("yt_watch_bGBXM6oxbag.html"))
    assert d["title"].startswith("STAR WARS: GALACTIC RACER Walkthrough")
    assert d["views"] == 465300 and d["length_seconds"] == 6169 and d["channel"] == "theRadBrad"


def test_transcript_cleanup_and_sampling():
    snippets = load_json("yt_transcript_api_bGBXM6oxbag_first200.json")["snippets"]
    text = transcript_to_text(snippets + [{"text": "[Music]"}, {"text": "♪ ♪"}, {"text": ">> whoa, it's beautiful"}])
    assert "[Music]" not in text and "\n" not in text and ">>" not in text and "whoa, it's beautiful" in text
    noisy = transcript_to_text([{"text": "you know Heat. Heat. Heat. Heat. [bell] Heat. Heat. [Applause]"},
                                {"text": "Have you heard of the keyblade war? Of course I have."}])
    assert noisy == "you know Have you heard of the keyblade war? Of course I have."
    long_text = (text + " ") * 20
    sample = sample_transcript(long_text, 3000)
    assert len(sample) <= 3200
    assert "[…середина…]" in sample
    assert sample_transcript("short text", 3000) == "short text"


def test_parsers_helpers():
    assert parse_duration("1:42:50") == 6170 and parse_duration("LIVE") is None
    assert parse_views("465,300 views") == 465300 and parse_views(None) is None


def test_title_matching_rules():
    from app.youtube import title_matches

    yes = [
        ("Star Wars: Galactic Racer", "STAR WARS: GALACTIC RACER Walkthrough Gameplay Part 1"),
        ("over the hill", "This New Offroad Game is Pure Chaos (Over The Hill)"),
        ("Clive Barker's Hellraiser: Revival", "Hellraiser Revival Full Walkthrough"),
        ("Total War: SHOGUN 2 Complete Edition", "Total War: Shogun 2 - Legendary Campaign Part 1"),
        ("Valor Mortis", "VALOR MORTIS ПРОХОЖДЕНИЕ [4K] На Русском"),
        ("Kingdom Hearts Collection I ~ III", "KINGDOM HEARTS COLLECTION I-III Gameplay Walkthrough Part 1"),
        ("Planet Zoo 2", "Planet Zoo 2 Let's Play Part 3"),
        ("Warhammer 40,000: Boltgun 2", "Warhammer 40,000: Boltgun 2 Gameplay Walkthrough Part 1 - FULL DEMO"),
    ]
    no = [
        ("Star Wars: Galactic Racer", "Star Wars Episode I Racer Playthrough"),
        ("Warhammer 40,000: Boltgun 2", "Twitch Livestream | Warhammer 40,000: Space Marine 2 Full Playthrough"),
        ("Planet Zoo 2", "Planet Coaster 2 Let's Play"),
        ("Neon Abyss 2", "Neon Abyss Full Game"),
        ("Planet Zoo 2", "PLANET ZOO Gameplay Walkthrough Part 2 - FIRST BABIES"),
    ]
    for game, video in yes:
        assert title_matches(game, video), (game, video)
    for game, video in no:
        assert not title_matches(game, video), (game, video)


def test_search_page_without_initial_data_raises():
    import pytest

    from app.http_client import SchemaError
    from app.youtube import YouTubeBlocked

    with pytest.raises(YouTubeBlocked):
        parse_search_html('<html><form action="https://consent.youtube.com/save"></form></html>')
    with pytest.raises(YouTubeBlocked):
        parse_search_html("<html>Sign in to confirm you’re not a bot</html>")
    with pytest.raises(SchemaError):
        parse_search_html("<html><body>new layout</body></html>")
    assert parse_search_html(load_text("yt_search_no_results.html")) == []
    lockups = ('<script>var ytInitialData = {"contents": {"itemSectionRenderer": {"contents": [{"lockupViewModel":'
               ' {"contentId": "bGBXM6oxbag", "metadata": {"title": "x"}}}]}}};</script>')
    with pytest.raises(SchemaError, match="videoRenderer"):
        parse_search_html(lockups)
    with pytest.raises(SchemaError):
        parse_search_html('<script>var ytInitialData = {"contents": {}};</script>')


def test_search_skips_malformed_video_ids():
    html = ('<script>var ytInitialData = {"a": [{"videoRenderer": {"videoId": "javascript:x", "title": {"runs": '
            '[{"text": "x"}]}}}, {"videoRenderer": {"videoId": "bGBXM6oxbag", "title": {"runs": [{"text": "ok"}]},'
            ' "viewCountText": {"simpleText": "10 views"}}}]};</script>')
    assert [c.video_id for c in parse_search_html(html)] == ["bGBXM6oxbag"]


class StubSearchHttp:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params=None, headers=None, cookies=None):
        self.calls.append(params)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page

        class R:
            text = page
            url = "https://www.youtube.com/results"
        return R()


def test_find_letsplays_raises_when_every_search_fails_and_survives_one_failure():
    import pytest

    from app.http_client import HttpError
    from app.youtube import YouTubeClient

    good = load_text("yt_search_star-wars-galactic-racer_lets-play_over20min.html")
    yt = YouTubeClient(StubSearchHttp([HttpError("HTTP 503"), HttpError("HTTP 503")]))
    with pytest.raises(HttpError):
        yt.find_letsplays("Star Wars: Galactic Racer")
    yt = YouTubeClient(StubSearchHttp(["<html>captcha g-recaptcha</html>", good]))
    ranked = yt.find_letsplays("Star Wars: Galactic Racer")
    assert ranked and ranked[0].video_id == "nYThl8ODM8c"
    assert yt.last_stats["found"] == 20 and yt.last_stats["eligible"] == len(ranked)


def test_consent_redirect_is_reported_as_blocked():
    import pytest

    from app.youtube import YouTubeBlocked, YouTubeClient

    class ConsentHttp(StubSearchHttp):
        def get(self, url, params=None, headers=None, cookies=None):
            class R:
                text = "<html></html>"
                url = "https://consent.youtube.com/m?continue=x"
            return R()

    with pytest.raises(YouTubeBlocked):
        YouTubeClient(ConsentHttp([])).search("x")


def test_trailers_of_re_releases_need_the_edition_words_and_a_recent_date():
    from datetime import date

    from app.youtube import YouTubeClient, rank_trailers

    base = dict(length_seconds=150, channel="c")
    cands = [
        VideoCandidate("kh3aaaaaaaa", "Kingdom Hearts III - Official Extended Trailer | E3 2018", views=9_000_000,
                       published="8y ago", **base),
        VideoCandidate("khcoll00001", "KINGDOM HEARTS Collection I-III - Official Announce Trailer", views=40_000,
                       published="3mo ago", **base),
    ]
    assert [c.video_id for c in rank_trailers("Kingdom Hearts Collection I ~ III", cands)] == ["khcoll00001"]
    old_and_new = [
        VideoCandidate("old00000000", "Game Z Official Trailer", views=5_000_000, published="6y ago", **base),
        VideoCandidate("new00000000", "Game Z Official Trailer", views=1_000, published="2mo ago", **base),
    ]
    ranked = rank_trailers("Game Z", old_and_new, release_date="2026-10-10", today=date(2026, 10, 15))
    assert [c.video_id for c in ranked] == ["new00000000"]
    only_old = rank_trailers("Game Z", old_and_new[:1], release_date="2026-10-10", today=date(2026, 10, 15))
    assert [c.video_id for c in only_old] == ["old00000000"]

    html = ('<script>var ytInitialData = {"a": [{"videoRenderer": {"videoId": "kh3aaaaaaaa", "title": {"runs": '
            '[{"text": "Kingdom Hearts III - Official Trailer"}]}, "viewCountText": {"simpleText": "9 views"},'
            ' "lengthText": {"simpleText": "2:01"}}}]};</script>')
    cand = YouTubeClient(StubSearchHttp([html])).find_trailer("Kingdom Hearts Collection I ~ III")
    assert cand.video_id == "kh3aaaaaaaa" and cand.base_game is True and cand.to_dict()["base_game"] is True


def test_caption_errors_become_plain_russian_reasons():
    from app.youtube import transcript_reason

    class RequestBlocked(Exception):
        pass

    class TranscriptsDisabled(Exception):
        pass

    class Strange(Exception):
        pass

    assert transcript_reason(RequestBlocked()) == "YouTube не отдаёт субтитры этому серверу"
    assert transcript_reason(TranscriptsDisabled()) == "автор отключил субтитры"
    assert transcript_reason(Strange()) == "не удалось получить субтитры"


def test_trailer_ranking_prefers_official_trailers_of_the_right_game():
    from app.youtube import rank_trailers

    base = dict(length_seconds=150, channel="c")
    cands = [
        VideoCandidate("aaaaaaaaaaa", "Planet Zoo 2 - Official Announcement Trailer", views=50_000, **base),
        VideoCandidate("bbbbbbbbbbb", "Planet Zoo 2 Trailer Reaction", views=900_000, **base),
        VideoCandidate("ccccccccccc", "Planet Zoo 2 gameplay trailer", views=200_000, **base),
        VideoCandidate("ddddddddddd", "Planet Coaster 2 Official Trailer", views=5_000_000, **base),
        VideoCandidate("eeeeeeeeeee", "Planet Zoo 2 Official Trailer", views=10, length_seconds=3600, channel="c"),
        VideoCandidate("fffffffffff", "Planet Zoo 2 full playthrough", views=10_000_000, **base),
    ]
    ranked = [c.video_id for c in rank_trailers("Planet Zoo 2", cands)]
    assert ranked == ["aaaaaaaaaaa", "ccccccccccc"]


def test_find_trailer_uses_relevance_search():
    from app.youtube import YouTubeClient

    html = ('<script>var ytInitialData = {"a": [{"videoRenderer": {"videoId": "TrAiLeR0001", "title": {"runs": '
            '[{"text": "Valor Mortis - Official Launch Trailer"}]}, "viewCountText": {"simpleText": "1,234 views"},'
            ' "lengthText": {"simpleText": "2:01"}, "ownerText": {"runs": [{"text": "Pub"}]}}}]};</script>')
    http = StubSearchHttp([html])
    cand = YouTubeClient(http).find_trailer("Valor Mortis")
    assert cand.video_id == "TrAiLeR0001" and cand.views == 1234
    assert "sp" not in http.calls[0] and "trailer" in http.calls[0]["search_query"]


def test_caption_requests_go_through_the_rate_limiter():
    import requests

    from app.youtube import TranscriptUnavailable, YouTubeClient

    waits, reports = [], []

    class Limiter:
        def wait(self, host):
            waits.append(host)

    class Http:
        limiter = Limiter()

        class client:
            headers = {"User-Agent": "UA"}

        def report(self, host, ok):
            reports.append((host, ok))

    yt = YouTubeClient(Http())
    api = yt._transcript_api()
    session = api._fetcher._http_client if hasattr(api, "_fetcher") else None
    assert session is not None and isinstance(session, requests.Session)

    class Resp:
        status_code = 200

    original = requests.Session.request
    requests.Session.request = lambda self, method, url, *a, **kw: Resp()
    try:
        session.get("https://www.youtube.com/watch?v=bGBXM6oxbag")
        session.post("https://www.youtube.com/youtubei/v1/player")
    finally:
        requests.Session.request = original
    assert waits == ["www.youtube.com", "www.youtube.com"]
    assert reports == [("www.youtube.com", True), ("www.youtube.com", True)]
    try:
        yt.transcript("not-an-id")
    except TranscriptUnavailable:
        pass
    else:
        raise AssertionError
