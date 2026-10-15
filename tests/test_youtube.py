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
    assert stats == {"excluded": 1, "title_mismatch": 1, "near_miss": 1, "found": 3, "eligible": 1}


def test_only_videos_sharing_half_the_title_words_count_as_near_misses():
    from app.youtube import near_miss

    stats = {}
    cands = [VideoCandidate(str(i) * 11, t, views=10, length_seconds=3600) for i, t in enumerate([
        "Minecraft Hardcore Day 100", "GTA 5 Funny Moments", "KAEBON Thai Horror", "Sudoku for beginners"])]
    assert rank_candidates("KAEBON", cands[:2], stats=stats) == []
    assert stats.get("title_mismatch") == 2 and "near_miss" not in stats
    assert near_miss("Sudoku University", "Sudoku for beginners")
    assert not near_miss("College Hockey 27", "NHL 26 Be a Pro")
    assert near_miss("Rock Hunter", "Monster Hunter 4 Ultimate - Part 12 Let's ROCK Basarios")


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
    assert only_old == []

    html = ('<script>var ytInitialData = {"a": [{"videoRenderer": {"videoId": "dw3aaaaaaaa", "title": {"runs": '
            '[{"text": "Dynasty Warriors 3 - Official Trailer"}]}, "viewCountText": {"simpleText": "9 views"},'
            ' "lengthText": {"simpleText": "2:01"}, "publishedTimeText": {"simpleText": "15 years ago"}}}]};</script>')
    cand = YouTubeClient(StubSearchHttp([html]), today=lambda: date(2026, 10, 15)).find_trailer(
        "Dynasty Warriors 3 Complete Edition Remastered", "2026-10-01")
    assert cand.video_id == "dw3aaaaaaaa" and cand.base_game is True and cand.to_dict()["base_game"] is True
    old = html.replace("Dynasty Warriors 3", "Bathyscaphe")
    assert YouTubeClient(StubSearchHttp([old]), today=lambda: date(2026, 10, 15)).find_trailer(
        "Bathyscaphe", "2026-10-15") is None


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


def test_lets_plays_of_other_games_with_similar_titles_are_rejected():
    from app.youtube import title_matches

    wrong = [
        ("Rock Hunter", "Monster Hunter 4 Ultimate - Part 12 Let's ROCK Basarios (Stone Dragon)"),
        ("THE GUARDIANS", "GUARDIANS OF THE GALAXY PS5 Walkthrough Gameplay Part 1 - INTRO (FULL GAME)"),
        ("Hunt Me", "The Witcher 3 Wild Hunt Прохождение Серия 73"),
        ("MEAT AND FLOWERS", "Gaia Online Dev Meat 4/24/26: April Showers? May Flowers."),
        ("Fatal Train", "Fatal Frame 1: #1 (END) - Halloween Horror Train 19' [09/20/19]"),
        ("Melon Rally", "Pac Man World Rally Part 3 Multiplayer Melon Cup"),
        ("Reason and Rhyme", "Let's Play Pokemon Stadium 2 #31 Chatting without rhyme or reason"),
        ("Alice Nation", "Alice Plays :: SimCity :: Episode 2 :: Education for the Nation!"),
        ("Toys Co.", "New Toys On The Farm! | Bucks Co, PA | Let's Play FS19"),
        ("The Nightmare Box Vol III", "The Nightmare Box vol 2 Dagon ep 1"),
        ("Isle Forge", "Zelda Classic → Isle of Rebirth Walkthrough: 4 - Abandoned Forge"),
        ("Labyrinth A Way Out", "Kingdom Hearts 3 - Arendelle Walkthrough Part 8 - Find a Way Out of The Labyrinth"),
        ("Backrooms Adventure", "SUPER BEAR ADVENTURE İMKANSIZ BACKROOMS! GİZEMLİ YER BULDUM!"),
        ("House Flipper 2 + Sakura DLC", "We Got Paid to Renovate a Haunted House?! (House Flipper 2 Scooby Doo DLC)"),
        ("Fossil Digger", "Digger of a Fossil - Part 2"),
        ("Planet Zoo", "Planet Zoo 2 Let's Play Part 1"),
        ("Hades", "Hades II - Let's Play Part 1"),
        ("Qiang Gu: Ghost Festival", "Hungry Ghost Festival walkthrough"),
        ("HAUNT", "Kings Island Haunt 2026 FIRST LOOK - Maze POV Walkthroughs - JASON: Blood Reign"),
        ("The Screamer", "Magma Juega MINECRAFT, The SCREAMER"),
        ("HAUNT", "Super Mario 64 - Gameplay Walkthrough Part 5 - Big Boo's Haunt 100%"),
    ]
    right = [
        ("Crimson Desert: Charting the Unknown", "CRIMSON DESERT Enhanced - Charting the Unknown DLC Hype"),
        ("Folded Fables: Tidy Up The Dragon Hoard", "Sorting A Dragon's Horde ~ Folded Fables Tidy Up The Dragon Hoard"),
        ("Silent Wounds - The Doll", "UN JEU D'HORREUR GRATUIT SYMPA ? | SILENT WOUNDS : THE DOLL Gameplay FR"),
        ("Clive Barker's Hellraiser: Revival", "So... I Played Hellraiser: Revival"),
        ("Gears of War: E-Day", "Gears of War E-Day - Part 1 - WHERE IT ALL BEGAN.."),
        ("Kingdom Hearts III", "Kingdom Hearts 3 Let's Play Part 1"),
        ("The Nightmare Box Vol III", "The Nightmare Box Vol. 3 - Full Playthrough"),
        ("Grand Theft Auto V", "GTA V is still great - Grand Theft Auto 5 Let's Play"),
        ("Dynasty Warriors 3 Complete Edition Remastered", "【DYNASTY WARRIORS 3 REMASTERED】back to three kingdoms"),
        ("HAUNT", "HAUNT - Full Playthrough"),
        ("HAUNT", "Let's Play HAUNT - Part 1"),
        ("HAUNT", "I Played HAUNT and it's terrifying"),
        ("Retrospace", "Vinny - RetroSpace"),
        ("Panchofobia", "YO SOLO QUERIA UN PANCHO - Panchofobia"),
        ("Allogloom", "ALLOGLOOM - Полное прохождение"),
        ("The Screamer", "The Screamer - Full Walkthrough"),
    ]
    for game, video in wrong:
        assert not title_matches(game, video), (game, video)
    for game, video in right:
        assert title_matches(game, video), (game, video)
    assert title_matches("Grand Theft Auto V", "Grand Theft Auto 5 Let's Play")


def test_lets_plays_published_long_before_release_are_skipped():
    from datetime import date

    base = dict(views=10, length_seconds=3600, channel="c")
    cands = [
        VideoCandidate("sm64aaaaaaa", "Super Mario 64 - Big Boo's Haunt 100%", published="6 years ago",
                       **{**base, "views": 9000}),
        VideoCandidate("fibsaaaaaaa", "HAUNT Let's Play Part 1", published="1 year ago", **{**base, "views": 800}),
        VideoCandidate("hauntnewaaa", "HAUNT Let's Play Part 1", published="11 months ago", **base),
        VideoCandidate("hauntexact1", "HAUNT gameplay", published_date="2026-09-01", **base),
        VideoCandidate("hauntexact2", "HAUNT gameplay", published_date="2025-01-01", published="1 month ago", **base),
    ]
    stats = {}
    ranked = rank_candidates("HAUNT", cands, release_date="2026-10-15", today=date(2026, 10, 15), stats=stats)
    assert [c.video_id for c in ranked] == ["hauntnewaaa", "hauntexact1"]
    assert stats["too_old"] == 2 and stats["title_mismatch"] == 1
    unknown = rank_candidates("HAUNT", cands, today=date(2026, 10, 15))
    assert len(unknown) == 4


def test_original_titles_replace_machine_translations_before_the_final_choice():
    from datetime import date

    from app.youtube import YouTubeClient

    def renderer(vid, title, views, age="2 weeks ago"):
        return ('{"videoRenderer": {"videoId": "%s", "title": {"runs": [{"text": "%s"}]}, "viewCountText": '
                '{"simpleText": "%d views"}, "lengthText": {"simpleText": "40:00"}, "publishedTimeText": '
                '{"simpleText": "%s"}}}' % (vid, title, views, age))

    search = ('<script>var ytInitialData = {"a": [' + ",".join([
        renderer("gtalcsaaaaa", "Walkthrough of GTA Liberty City Stories on PC - Not My Son / Proposal / Part 4", 9000),
        renderer("earlyaaaaaa", "Я поиграл в Not My Son раньше всех!", 5000),
        renderer("okaaaaaaaaa", "Not My Son - Part 1", 100),
    ]) + ']};</script>')
    empty = '<script>var ytInitialData = {"a": [], "backgroundPromoRenderer": {}};</script>'
    originals = {"gtalcsaaaaa": "Прохождение GTA Liberty City Stories на ПК - Не Мой Сын / Предложение / Часть 4",
                 "earlyaaaaaa": "I Played Not My Son Early!", "okaaaaaaaaa": "Not My Son - Part 1"}

    class Http(StubSearchHttp):
        def get(self, url, params=None, headers=None, cookies=None):
            if "v" not in (params or {}):
                return super().get(url, params, headers, cookies)
            vid = params["v"]
            self.calls.append(params)

            class R:
                text = ('<script>var ytInitialPlayerResponse = {"videoDetails": {"videoId": "%s", "title": "%s", '
                        '"viewCount": "100", "lengthSeconds": "2400", "author": "a"}, "microformat": '
                        '{"playerMicroformatRenderer": {"publishDate": "2026-10-01T07:00:00-07:00"}}};</script>'
                        % (vid, originals[vid]))
                url = "https://www.youtube.com/watch"
            return R()

    yt = YouTubeClient(Http([search, empty]), today=lambda: date(2026, 10, 15))
    ranked = yt.find_letsplays("Not My Son", "2026-10-15")
    assert [c.video_id for c in ranked] == ["okaaaaaaaaa", "earlyaaaaaa"]
    early = ranked[1]
    assert early.title == "I Played Not My Son Early!" and early.search_title.startswith("Я поиграл")
    assert early.published_date == "2026-10-01" and early.refined
    dropped = yt.last_stats["dropped"]
    assert [d["video_id"] for d in dropped] == ["gtalcsaaaaa"] and "Прохождение GTA" in dropped[0]["reason"]
    assert yt.last_stats["eligible"] == 2


def test_watch_page_gives_the_exact_publish_date():
    d = parse_watch_html(load_text("yt_watch_bGBXM6oxbag.html"))
    assert d["published_date"] == "2026-10-10"


def test_trailers_must_name_the_game_on_its_own_and_be_recent():
    from datetime import date

    from app.youtube import trailer_problem

    def cand(title, published="2 months ago", channel="Publisher"):
        return VideoCandidate("x" * 11, title, views=1000, length_seconds=120, channel=channel, published=published)

    today = date(2026, 10, 15)
    wrong = [
        ("Vanisha", cand("ETHAN & VANISHA/ OUR WEDDING TRAILER", "11 months ago", "Ethan Gets His Van")),
        ("THE GUARDIANS", cand("BLADES OF THE GUARDIANS Official Trailer (2026) Jet Li", "7 months ago",
                               "Movie Trailers Source")),
        ("THE GUARDIANS", cand("BLADES OF THE GUARDIANS Official Trailer (2026) Jet Li", "7 months ago")),
        ("Qiang Gu: Ghost Festival", cand("Ghost Month 2023 (Official Trailer) Hungry Ghost Festival", "4 years ago")),
        ("Bathyscaphe", cand("Bathyscaphe Trailer", "17 years ago")),
        ("HAUNT", cand("Eternal Haunt - Official Gameplay Trailer | Indie Horror Showcase 2025", "11 months ago")),
        ("Pestilence", cand("Plague Doctor: Life in Pestilence - Official Announcement Trailer", "10 months ago")),
        ("The Screamer", cand("Screamer - Official Gameplay Mechanics Overview Trailer", "11 months ago")),
        ("The Nightmare Box Vol III", cand("Nightmare Box Vol I: Remorse: The List - PlayStation 5 - Trailer")),
        ("House Flipper 2 + Sakura DLC", cand("House Flipper 2: Pets - Official DLC Announcement Trailer")),
        ("FIBS", cand("FIBS   Trailer", "7 years ago")),
        ("Thalia", cand("Official Trailer of Thalia & Leo.", "7 months ago")),
    ]
    right = [
        ("Kings of Antikva", cand("Trailer Kings Of Antikva", "1 year ago")),
        ("Alice Nation", cand("Alice Nation 丨 Trailer")),
        ("KAEBON", cand("KAEBON แก้บน | Official Teaser Trailer | Thai Horror Game")),
        ("Wolfsschanze - Operation Gotterdammerung", cand("Wolfsschanze – Operation Götterdämmerung | Trailer")),
        ("Kingdom Hearts Collection I ~ III", cand("Kingdom Hearts Collection [I ~ III] - Official Launch Trailer")),
        ("SpudFall", cand("A Beautiful Potato Game: Spudfall Trailer")),
        ("GO DUB YOURSELF!", cand("GO DUB YOURSELF! - Official Trailer | Movie Dubbing Party Game")),
        ("Fossil Digger", cand("Fossil Digger by PIJI - Official Trailer | PIJI App Game", "1 year ago")),
        ("The Screamer", cand("The Screamer - Official Launch Trailer")),
        ("Hunt Me", cand("Hunt Me - Horror Game - Official Trailer - Release 6 Dec 24", "1 year ago")),
        ("Tribal Trouble: Resurrected", cand("Tribal Trouble Resurrected - Trailer")),
    ]
    for game, c in wrong:
        assert trailer_problem(game, c, "2026-10-15", today) is not None, (game, c.title)
    for game, c in right:
        assert trailer_problem(game, c, "2026-10-15", today) is None, (game, c.title)
