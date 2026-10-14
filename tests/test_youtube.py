from app.youtube import (
    VideoCandidate,
    parse_duration,
    parse_search_html,
    parse_views,
    parse_watch_html,
    rank_candidates,
    sample_transcript,
    title_overlap,
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
    assert ranked[0].video_id == "bGBXM6oxbag"
    assert all("review" not in c.title.lower() for c in ranked)
    assert all(c.video_id != "YWlb1OMbmOQ" for c in ranked)


def test_title_overlap_rejects_other_games():
    assert title_overlap("Star Wars: Galactic Racer", "Star Wars Episode I Racer Playthrough") < 0.8
    assert title_overlap("Star Wars: Galactic Racer", "STAR WARS: GALACTIC RACER Walkthrough Part 1") == 1.0


def test_rank_filters_short_live_and_trailers():
    base = dict(views=10, length_seconds=3600, channel="c")
    cands = [
        VideoCandidate("a", "Game X Official Trailer", **base),
        VideoCandidate("b", "Game X gameplay", views=5, length_seconds=120, channel="c"),
        VideoCandidate("c", "Game X live", views=None, length_seconds=None, live=True),
        VideoCandidate("d", "Game X full playthrough", **{**base, "views": 50}),
        VideoCandidate("e", "Game X is great", **{**base, "views": 500}),
    ]
    ranked = rank_candidates("Game X", cands)
    assert [c.video_id for c in ranked] == ["d", "e"]


def test_watch_page_details():
    d = parse_watch_html(load_text("yt_watch_bGBXM6oxbag.html"))
    assert d["title"].startswith("STAR WARS: GALACTIC RACER Walkthrough")
    assert d["views"] == 465300 and d["length_seconds"] == 6169 and d["channel"] == "theRadBrad"


def test_transcript_cleanup_and_sampling():
    snippets = load_json("yt_transcript_api_bGBXM6oxbag_first200.json")["snippets"]
    text = transcript_to_text(snippets + [{"text": "[Music]"}, {"text": "♪ ♪"}])
    assert "[Music]" not in text and "\n" not in text
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
