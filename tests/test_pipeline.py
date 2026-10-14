from app.monitor import Monitor
from app.pipeline import Pipeline

from .conftest import FakeLLM, FakeMetacritic, FakeYouTube


def make(settings, db, mc=None, yt=None, llm=None):
    return Pipeline(settings, db, Monitor(), mc or FakeMetacritic(), yt if yt is not None else FakeYouTube(),
                    llm if llm is not None else FakeLLM())


def test_first_run_processes_new_releases_and_fills_cards(settings, db):
    settings.batch_size = 5
    p = make(settings, db)
    res = p.run("manual")
    assert res["status"] == "ok" and res["selected"] == 5 and res["ok"] == 5
    assert db.count_games() == 5
    day = settings.today()
    assert db.processed_count(day) == 5
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "llm"
    assert g["user_summary"]["method"] == "llm"
    assert g["letsplay"]["status"] == "ok" and g["letsplay"]["method"] == "llm-transcript"
    assert g["letsplay"]["video"]["video_id"] == "bGBXM6oxbag"
    assert len(g["platforms"]) == 3
    assert isinstance(g["similar"], list) and len(g["similar"]) >= 1
    state = db.get_day_state(day)
    assert state.new_releases_done is False


def test_runs_continue_into_browse_and_update_existing(settings, db):
    mc = FakeMetacritic()
    p = make(settings, db, mc=mc)
    first = p.run()
    assert first["selected"] == 20
    day = settings.today()
    assert db.get_day_state(day).new_releases_done is True
    second = p.run()
    assert second["selected"] > 0
    assert "browse" in second["sources"]
    assert db.processed_count(day) == 20 + second["selected"]
    third = p.run()
    assert not ({s for s in third["sources"].split(", ")} & {"new-releases"})


def test_one_failing_game_does_not_stop_the_run(settings, db):
    settings.batch_size = 4
    mc = FakeMetacritic(fail_slugs={"warhammer-40000-boltgun-2"})
    p = make(settings, db, mc=mc)
    res = p.run()
    assert res["failed"] == 1 and res["ok"] == 3 and res["status"] == "partial"
    assert db.error_count(settings.today()) >= 1


def test_llm_and_transcript_failures_degrade_gracefully(settings, db):
    settings.batch_size = 2
    p = make(settings, db, yt=FakeYouTube(transcript_ok=False), llm=FakeLLM(fail=True))
    res = p.run()
    assert res["ok"] == 2
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "extractive"
    assert g["letsplay"]["status"] == "no_transcript"
    assert g["letsplay"]["method"] in ("description", "none")
    assert g["letsplay"]["video"]["url"].startswith("https://www.youtube.com/watch?v=")


def test_summaries_and_letsplay_are_reused_when_unchanged(settings, db):
    settings.batch_size = 1
    llm, yt = FakeLLM(), FakeYouTube()
    p = make(settings, db, llm=llm, yt=yt)
    p.run()
    calls, searches = llm.calls, yt.searches
    db.mark_processed  # noqa: B018
    with db.write() as c:
        c.execute("DELETE FROM processed")
        c.execute("DELETE FROM day_state")
    p.run()
    assert llm.calls == calls
    assert yt.searches == searches
    assert db.get_game(slug="over-the-hill")["times_processed"] == 2


def test_parallel_run_is_rejected(settings, db):
    import threading

    from app.pipeline import AlreadyRunning

    p = make(settings, db)
    p._lock.acquire()
    try:
        assert p.start_background() is False
        try:
            p.run()
        except AlreadyRunning:
            pass
        else:
            raise AssertionError
    finally:
        p._lock.release()
    assert isinstance(threading.active_count(), int)


def test_backfill_upgrades_fallback_summaries_when_llm_returns(settings, db):
    settings.batch_size = 2
    settings.llm_backfill_limit = 0
    down = make(settings, db, llm=FakeLLM(fail=True))
    down.run()
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "extractive"
    assert g["letsplay"]["method"] == "extractive-transcript" and g["letsplay"]["transcript_excerpt"]
    up = make(settings, db, llm=FakeLLM())
    assert up.backfill_llm(set(), limit=10) >= 3
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "llm"
    assert g["letsplay"]["method"] == "llm-transcript"
