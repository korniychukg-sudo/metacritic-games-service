from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.llm import LLMClient, LLMError
from app.monitor import Monitor
from app.pipeline import AlreadyRunning, Pipeline

from app.stt import NoSpeech, SpeechUnavailable

from .conftest import (
    WHISPER_TEXT,
    FakeAudioSource,
    FakeClock,
    FakeLLM,
    FakeMetacritic,
    FakeTranscriber,
    FakeYouTube,
    make_stt,
)


TOP, SECOND, THIRD = "nYThl8ODM8c", "bGBXM6oxbag", "WAKDTrzOaF8"
DIALOGUE = ("Have you heard of the ancient keyblade war? Of course I have. Long ago, keyblade wielders waged a war "
            "over the ownership of light. Yeah, the master's favorite story. I wonder what they planned on doing. ") * 8


def make(settings, db, mc=None, yt=None, llm=None, clock=None, monitor=None, stt=None):
    return Pipeline(settings, db, monitor or Monitor(), mc or FakeMetacritic(),
                    yt if yt is not None else FakeYouTube(), llm if llm is not None else FakeLLM(),
                    clock=clock or FakeClock(), stt=stt)


def forget_today(db):
    with db.write() as c:
        c.execute("DELETE FROM processed")
        c.execute("DELETE FROM day_state")


class Killed(BaseException):
    pass


def iso_now(clock):
    from app.config import iso

    return iso(clock())


def test_first_run_processes_new_releases_and_fills_cards(settings, db):
    settings.batch_size = 5
    clock = FakeClock()
    mc = FakeMetacritic()
    seen = []
    mc.on_game = lambda slug: seen.append((db.last_run()["selected"], db.last_run()["sources"]))
    p = make(settings, db, mc=mc, clock=clock)
    res = p.run("manual")
    assert seen[0] == (5, "new-releases")
    assert res["status"] == "ok" and res["selected"] == 5 and res["ok"] == 5
    assert db.count_games() == 5
    day = settings.today(clock())
    assert db.processed_count(day) == 5
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "llm"
    assert g["user_summary"]["method"] == "llm"
    assert g["letsplay"]["status"] == "ok" and g["letsplay"]["method"] == "llm-transcript"
    assert g["letsplay"]["video"]["video_id"] == TOP and g["letsplay"]["passed_over"] == []
    assert g["letsplay"]["has_commentary"] is True and g["letsplay"]["evidence"]
    assert len(g["platforms"]) == 3
    assert g["similar"] and all(s["reasons"] for s in g["similar"])
    assert db.get_day_state(day).new_releases_done is False
    last = db.last_run()
    assert last["status"] == "ok" and last["finished_at"] and last["owner"] == p.owner
    assert not p.running and db.lease_holder("pipeline", clock()) is None


def test_runs_continue_into_browse_and_update_existing(settings, db):
    clock = FakeClock()
    p = make(settings, db, clock=clock)
    first = p.run()
    assert first["selected"] == 20 and first["sources"] == "new-releases"
    day = settings.today(clock())
    assert db.get_day_state(day).new_releases_done is True
    second = p.run()
    assert second["selected"] > 0 and "browse" in second["sources"] and "new-releases" not in second["sources"]
    assert db.processed_count(day) == 20 + second["selected"]
    third = p.run()
    assert "new-releases" not in third["sources"]
    assert db.processed_count(day) == 20 + second["selected"] + third["selected"]


def test_one_failing_game_does_not_stop_the_run(settings, db):
    settings.batch_size = 4
    clock = FakeClock()
    p = make(settings, db, mc=FakeMetacritic(fail_slugs={"warhammer-40000-boltgun-2"}), clock=clock)
    res = p.run()
    assert res["failed"] == 1 and res["ok"] == 3 and res["status"] == "partial"
    errors = db.recent_errors()
    assert errors[0]["slug"] == "warhammer-40000-boltgun-2" and "boom" in errors[0]["message"]


def test_failed_game_is_retried_the_same_day(settings, db):
    settings.batch_size = 4
    clock = FakeClock()
    mc = FakeMetacritic(fail_once={"warhammer-40000-boltgun-2"})
    p = make(settings, db, mc=mc, clock=clock)
    assert p.run()["failed"] == 1
    day = settings.today(clock())
    second = p.run()
    assert second["sources"].startswith("retry")
    assert second["ok"] == 4 and second["failed"] == 0
    assert db.get_game(slug="warhammer-40000-boltgun-2") is not None
    with db.conn() as c:
        row = c.execute("SELECT status, attempts, source FROM processed WHERE day=? AND slug=?",
                        (day, "warhammer-40000-boltgun-2")).fetchone()
    assert tuple(row) == ("ok", 2, "new-releases")


def test_retries_are_capped(settings, db):
    settings.batch_size = 2
    settings.failed_game_max_attempts = 2
    clock = FakeClock()
    mc = FakeMetacritic(fail_slugs={"over-the-hill"})
    p = make(settings, db, mc=mc, clock=clock)
    p.run()
    p.run()
    attempts = [c for c in mc.calls if c == ("game", "over-the-hill")]
    assert len(attempts) == 2
    third = p.run()
    assert "retry" not in third["sources"]
    assert len([c for c in mc.calls if c == ("game", "over-the-hill")]) == 2


def test_interrupted_run_is_recorded_and_resumed(settings, db):
    settings.batch_size = 5
    clock = FakeClock()
    mon = Monitor()
    mc = FakeMetacritic()
    fourth = mc.new[3].slug
    mc.raise_for[fourth] = Killed()
    p = make(settings, db, mc=mc, clock=clock, monitor=mon)
    with pytest.raises(Killed):
        p.run()
    day = settings.today(clock())
    last = db.last_run()
    assert last["status"] == "failed" and last["finished_at"]
    assert db.processed_count(day) == 3
    assert db.get_day_state(day).new_releases_done is False
    assert mon.progress["run_id"] is None
    assert not p.running
    settings.batch_size = 20
    res = p.run()
    assert res["selected"] == 20 and res["sources"] == "new-releases, browse:p1"
    done_first = {it.slug for it in mc.new[:3]}
    later = [slug for kind, slug in mc.calls[5:] if kind == "game"]
    assert not done_first & set(later)
    assert db.get_day_state(day).new_releases_done is True


def test_bookkeeping_survives_an_exception_after_the_games(settings, db, monkeypatch):
    settings.batch_size = 2
    clock = FakeClock()
    mon = Monitor()
    p = make(settings, db, clock=clock, monitor=mon)

    def broken(state):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "save_day_state", broken)
    res = p.run()
    assert res["status"] == "failed"
    last = db.last_run()
    assert last["status"] == "failed" and last["finished_at"] and "disk full" in last["message"]
    assert mon.progress == {"run_id": None, "done": 0, "total": 0, "ok": 0, "failed": 0, "current": None,
                            "step": None, "started_at": None}
    assert mon.workers["pipeline"]["status"] == "error"
    assert not p.running
    assert any(e["step"] == "run" for e in db.recent_errors())


def test_selection_failure_is_a_failed_run_with_a_visible_error(settings, db):
    from app.http_client import SchemaError

    clock = FakeClock()
    mc = FakeMetacritic()
    mc.list_error = SchemaError("в ответе списка игр Metacritic нет массива data.items")
    p = make(settings, db, mc=mc, clock=clock)
    res = p.run()
    assert res["status"] == "failed" and res["selected"] == 0
    err = db.recent_errors()[0]
    assert err["step"] == "selection:schema" and "data.items" in err["message"]
    assert db.get_day_state(settings.today(clock())).new_releases_done is False


def test_run_crossing_midnight_keeps_its_day_and_next_run_starts_over(settings, db):
    settings.batch_size = 5
    clock = FakeClock(datetime(2026, 10, 14, 20, 50, tzinfo=timezone.utc))
    mc = FakeMetacritic()
    mc.on_game = lambda slug: clock.advance(minutes=3)
    p = make(settings, db, mc=mc, clock=clock)
    p.run()
    assert clock() > datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc)
    assert db.processed_count("2026-10-14") == 5
    assert db.processed_count("2026-10-15") == 0
    assert db.last_run()["day"] == "2026-10-14"
    second = p.run()
    assert second["sources"] == "new-releases"
    assert db.processed_count("2026-10-15") == 5
    assert db.get_game(slug="over-the-hill")["times_processed"] == 2


def test_time_limit_stops_taking_new_games(settings, db):
    settings.batch_size = 6
    settings.run_max_minutes = 50
    clock = FakeClock()
    mc = FakeMetacritic()
    mc.on_game = lambda slug: clock.advance(minutes=20)
    p = make(settings, db, mc=mc, clock=clock)
    res = p.run()
    assert res["status"] == "cut" and res["ok"] == 3
    assert "лимиту" in db.last_run()["message"]
    day = settings.today(clock())
    assert db.processed_count(day) == 3
    assert db.get_day_state(day).new_releases_done is False
    mc.on_game = None
    nxt = p.run()
    assert nxt["selected"] == 6 and nxt["status"] == "ok"
    assert db.processed_count(day) == 9


def test_llm_failures_trip_the_circuit_for_the_rest_of_the_run(settings, db):
    settings.batch_size = 4
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    llm = LLMClient("https://llm.example.test/v1", "m", api_key="k", min_gap=0, retries=1,
                    transport=httpx.MockTransport(handler), sleep=lambda s: None, failure_threshold=3)
    clock = FakeClock()
    p = make(settings, db, llm=llm, clock=clock)
    res = p.run()
    assert res["ok"] == 4
    assert len(calls) == 3
    g = db.get_game(slug=FakeMetacritic().new[3].slug)
    assert g["critic_summary"]["method"] == "extractive" and "пропущена" in g["critic_summary"]["note"]
    assert g["letsplay"]["method"] == "extractive-transcript"
    llm_errors = [e for e in db.recent_errors(50) if e["step"].startswith("llm")]
    assert len(llm_errors) <= 3
    assert p.llm.circuit_open
    p.run()
    assert len(calls) > 3


def test_summaries_and_letsplay_are_reused_when_unchanged(settings, db):
    settings.batch_size = 1
    llm, yt = FakeLLM(), FakeYouTube()
    clock = FakeClock()
    p = make(settings, db, llm=llm, yt=yt, clock=clock)
    p.run()
    calls, searches = llm.calls, yt.searches
    forget_today(db)
    clock.advance(hours=1)
    p.run()
    assert llm.calls == calls
    assert yt.searches == searches
    assert db.get_game(slug="over-the-hill")["times_processed"] == 2


def test_letsplay_refresh_periods_depend_on_the_result(settings, db):
    settings.batch_size = 1
    clock = FakeClock()
    yt = FakeYouTube(transcript_ok=False)
    p = make(settings, db, yt=yt, clock=clock)
    p.run()
    assert db.get_game(slug="over-the-hill")["letsplay"]["status"] == "no_transcript"
    assert yt.searches == 1
    forget_today(db)
    clock.advance(hours=5)
    p.run()
    assert yt.searches == 1
    forget_today(db)
    clock.advance(hours=20)
    yt.transcript_ok = True
    p.run()
    assert yt.searches == 2
    assert db.get_game(slug="over-the-hill")["letsplay"]["method"] == "llm-transcript"
    forget_today(db)
    clock.advance(days=3)
    p.run()
    assert yt.searches == 2
    forget_today(db)
    clock.advance(days=5)
    p.run()
    assert yt.searches == 3


def test_blocked_youtube_keeps_the_previous_video(settings, db):
    settings.batch_size = 1
    clock = FakeClock()
    yt = FakeYouTube()
    p = make(settings, db, yt=yt, clock=clock)
    p.run()
    forget_today(db)
    clock.advance(days=8)
    yt.blocked = True
    p.run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["video"]["video_id"] == TOP and "капчу" in lp["last_error"] and lp["method"] == "llm-transcript"
    assert any(e["step"] == "youtube:schema" for e in db.recent_errors())
    forget_today(db)
    clock.advance(hours=2)
    p.run()
    assert yt.searches == 2


def test_partial_metacritic_data_does_not_wipe_stored_data(settings, db):
    settings.batch_size = 1
    clock = FakeClock()
    mc = FakeMetacritic()
    p = make(settings, db, mc=mc, clock=clock)
    p.run()
    before = db.get_game(slug="over-the-hill")
    assert before["platforms"] and before["critic_reviews"] and before["critic_summary"]["method"] == "llm"
    mc.drop["over-the-hill"] = {"platforms", "critic-reviews", "user-reviews"}
    forget_today(db)
    p.run()
    after = db.get_game(slug="over-the-hill")
    assert [x["name"] for x in after["platforms"]] == [x["name"] for x in before["platforms"]]
    assert after["critic_reviews"] == before["critic_reviews"]
    assert after["critic_summary"]["likes"] == before["critic_summary"]["likes"]
    assert after["critic_summary"]["method"] == "llm"
    assert after["metascore"] == before["metascore"]
    steps = [e["step"] for e in db.recent_errors(20)]
    assert steps.count("данные Metacritic:schema") >= 3


def test_same_slug_new_id_replaces_the_game(settings, db):
    settings.batch_size = 1
    clock = FakeClock()
    mc = FakeMetacritic()
    p = make(settings, db, mc=mc, clock=clock)
    p.run()
    old_id = db.get_game(slug="over-the-hill")["id"]
    mc.ids["over-the-hill"] = 777
    forget_today(db)
    p.run()
    assert db.get_game(slug="over-the-hill")["id"] == 777
    assert db.get_game(game_id=old_id) is None
    assert db.count_games() == 1


def test_youtube_trailer_fills_in_when_metacritic_has_no_video(settings, db):
    settings.batch_size = 2
    clock = FakeClock()
    yt = FakeYouTube()
    p = make(settings, db, mc=FakeMetacritic(no_video=True), yt=yt, clock=clock)
    p.run()
    g = db.get_game(slug="over-the-hill")
    assert g["video_embed_url"] is None
    assert g["trailer"]["status"] == "ok" and g["trailer"]["video"]["video_id"] == "TrAiLeR0001"
    assert yt.trailer_searches == 2
    forget_today(db)
    clock.advance(days=1)
    p.run()
    assert yt.trailer_searches == 2
    with_video = FakeYouTube()
    forget_today(db)
    make(settings, db, yt=with_video, clock=clock).run()
    assert with_video.trailer_searches == 0


def test_parallel_runs_are_rejected_in_process_and_across_processes(settings, db):
    clock = FakeClock()
    p = make(settings, db, clock=clock)
    p._lock.acquire()
    try:
        assert p.running
        assert p.start_background() is False
        with pytest.raises(AlreadyRunning):
            p.run()
    finally:
        p._lock.release()
    assert not p.running
    other = make(settings, db, clock=clock)
    assert db.acquire_lease("pipeline", other.owner, settings.lease_ttl_seconds, clock())
    assert p.running
    with pytest.raises(AlreadyRunning):
        p.run()
    assert db.count_games() == 0
    clock.advance(seconds=settings.lease_ttl_seconds + 1)
    assert not p.running
    settings.batch_size = 1
    assert p.run()["ok"] == 1


def test_backfill_upgrades_fallback_summaries_when_llm_returns(settings, db):
    settings.batch_size = 2
    settings.llm_backfill_limit = 0
    clock = FakeClock()
    make(settings, db, llm=FakeLLM(fail=True), clock=clock).run()
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "extractive"
    assert g["letsplay"]["method"] == "extractive-transcript" and g["letsplay"]["transcript_excerpt"]
    up = make(settings, db, llm=FakeLLM(), clock=clock)
    assert up.backfill_llm(set(), limit=10) >= 3
    g = db.get_game(slug="over-the-hill")
    assert g["critic_summary"]["method"] == "llm"
    assert g["letsplay"]["method"] == "llm-transcript"


def test_backfill_upgrades_description_based_letsplays(settings, db):
    settings.batch_size = 1
    settings.llm_backfill_limit = 0
    clock = FakeClock()
    make(settings, db, yt=FakeYouTube(transcript_ok=False), llm=FakeLLM(fail=True), clock=clock).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["method"] == "description" and lp["video"]["description"]
    make(settings, db, llm=FakeLLM(), clock=clock).backfill_llm(set(), limit=10)
    assert db.get_game(slug="over-the-hill")["letsplay"]["method"] == "llm-description"


class PickyLLM(FakeLLM):
    def __init__(self, bad_title):
        super().__init__()
        self.bad_title = bad_title

    def chat_json(self, messages, validator=None):
        if self.bad_title in messages[-1]["content"]:
            self.calls += 1
            raise LLMError("модель отвечает мусором на эту игру")
        return super().chat_json(messages, validator)


def test_one_game_cannot_block_the_backfill(settings, db):
    settings.batch_size = 3
    settings.llm_backfill_limit = 0
    clock = FakeClock()
    make(settings, db, llm=FakeLLM(fail=True), yt=FakeYouTube(transcript_ok=False), clock=clock).run()
    slugs = [it.slug for it in FakeMetacritic().new[:3]]
    titles = {s: db.get_game(slug=s)["title"] for s in slugs}
    bad = slugs[0]
    picky = PickyLLM(titles[bad])
    p = make(settings, db, llm=picky, clock=clock)
    p.backfill_llm(set(), limit=10)
    for s in slugs[1:]:
        assert db.get_game(slug=s)["critic_summary"]["method"] == "llm", s
    assert db.get_game(slug=bad)["critic_summary"]["method"] == "extractive"
    with db.conn() as c:
        attempts = c.execute("SELECT backfill_attempts FROM games WHERE slug=?", (bad,)).fetchone()[0]
    assert attempts == 1
    queue = [g["slug"] for g in db.games_with_fallbacks(10)]
    assert queue[0] == bad


def test_backfill_respects_skip_ids_limit_and_deadline(settings, db):
    settings.batch_size = 3
    settings.llm_backfill_limit = 0
    clock = FakeClock()
    make(settings, db, llm=FakeLLM(fail=True), clock=clock).run()
    up = make(settings, db, llm=FakeLLM(), clock=clock)
    assert up.backfill_llm(set(), limit=2, deadline=clock() - timedelta(seconds=1)) == 0
    assert up.backfill_llm(set(), limit=2) == 2
    skip = {db.get_game(slug=s)["id"] for s in [it.slug for it in FakeMetacritic().new[:3]]}
    assert up.backfill_llm(skip, limit=5) == 0


def test_whisper_transcribes_the_most_popular_video_when_it_has_no_captions(settings, db, tmp_path):
    settings.batch_size = 1
    yt = FakeYouTube(transcript_ok=False)
    source, tr, llm = FakeAudioSource(seconds=60), FakeTranscriber(), FakeLLM()
    stt = make_stt(settings, source, tr, tmp_path)
    result = make(settings, db, yt=yt, llm=llm, stt=stt).run()
    assert result["status"] == "ok"
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["status"] == "ok" and lp["method"] == "llm-whisper" and lp["transcript_method"] == "whisper"
    assert lp["video"]["video_id"] == TOP and [c[0] for c in source.calls] == [TOP]
    assert lp["transcript_chars"] == len(" ".join(WHISPER_TEXT.split())) and lp["transcript_lang"] == "en"
    assert lp["stt"]["model"] == "base" and lp["stt"]["audio_seconds"] == 60 and lp["stt"]["method"] == "whisper"
    assert lp["transcript_excerpt"] and lp["summary"] and lp["checked"][0]["code"] == "TranscriptsDisabled"
    assert yt.transcript_calls == 1 and lp["passed_over"] == []
    assert any("Whisper" in prompt for prompt in llm.prompts)
    assert tr.released == 1


def test_a_less_popular_video_with_captions_does_not_beat_whisper_on_the_top_one(settings, db, tmp_path):
    settings.batch_size = 1

    class TopHasNoCaptions(FakeYouTube):
        def transcript(self, video_id):
            if video_id == TOP:
                self.transcript_calls += 1
                from app.youtube import TranscriptUnavailable

                raise TranscriptUnavailable("автор отключил субтитры", "TranscriptsDisabled")
            return super().transcript(video_id)

    source = FakeAudioSource()
    make(settings, db, yt=TopHasNoCaptions(), stt=make_stt(settings, source, tmp_root=tmp_path)).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["video"]["video_id"] == TOP and lp["transcript_method"] == "whisper"
    assert [c[0] for c in source.calls] == [TOP]


def test_videos_without_the_authors_commentary_are_skipped(settings, db, tmp_path):
    settings.batch_size = 1
    yt = FakeYouTube()
    yt.transcript_texts[TOP] = DIALOGUE
    llm = FakeLLM(commentary=lambda prompt: "keyblade" not in prompt)
    make(settings, db, yt=yt, llm=llm).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["status"] == "ok" and lp["video"]["video_id"] == SECOND and lp["method"] == "llm-transcript"
    assert [p["video_id"] for p in lp["passed_over"]] == [TOP]
    assert "оценок игры от автора" in lp["passed_over"][0]["reason"]
    assert lp["passed_over"][0]["views"] == 693184

    forget_today(db)
    yt.transcript_texts = {v: "Heat. Heat. Heat. Heat. Heat. Heat. " * 60 + " ".join(["wind"] * 400)
                           for v in (TOP, SECOND, THIRD)}
    other = FakeLLM()
    p = make(settings, db, yt=yt, llm=other, clock=FakeClock(datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)))
    p.run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["video"]["video_id"] == SECOND and lp["method"] == "llm-transcript"
    assert "last_error" in lp and "без комментариев автора: 3 ролика" in lp["last_error"]
    assert not any('"has_commentary"' in prompt for prompt in other.prompts)


def test_description_fallback_never_invents_the_authors_opinion(settings, db):
    settings.batch_size = 1
    llm = FakeLLM()
    make(settings, db, yt=FakeYouTube(transcript_ok=False), llm=llm).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["status"] == "no_transcript" and lp["method"] == "llm-description"
    assert lp["summary"].startswith("Судя по описанию ролика") and lp["pros"] == [] and lp["cons"] == []
    assert "Это описание ролика, а не речь его автора" in llm.prompts[-1]
    assert lp["note"] == "Речь автора ролика не расшифрована (субтитры: автор отключил субтитры (3 ролика))"


def test_a_worse_refresh_never_replaces_a_stored_letsplay_or_trailer(settings, db):
    settings.batch_size = 1
    clock = FakeClock()
    yt = FakeYouTube()
    p = make(settings, db, mc=FakeMetacritic(no_video=True), yt=yt, clock=clock)
    p.run()
    first = db.get_game(slug="over-the-hill")
    assert first["letsplay"]["method"] == "llm-transcript" and first["trailer"]["status"] == "ok"
    forget_today(db)
    clock.advance(days=8)
    yt.transcript_ok = False
    yt.no_trailer = True
    p.run()
    g = db.get_game(slug="over-the-hill")
    lp = g["letsplay"]
    assert lp["method"] == "llm-transcript" and lp["transcript_excerpt"] == first["letsplay"]["transcript_excerpt"]
    assert "Речь автора ролика не расшифрована" in lp["last_error"] and lp["checked_at"] == iso_now(clock)
    assert g["trailer"]["status"] == "ok" and g["trailer"]["video"]["video_id"] == "TrAiLeR0001"
    assert g["trailer"]["checked_at"] == iso_now(clock) and "оставлен прежний" in g["trailer"]["last_error"]
    forget_today(db)
    clock.advance(days=8)
    yt.empty = True
    p.run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["video"]["video_id"] == TOP and lp["method"] == "llm-transcript" and "20 минут" in lp["last_error"]


def test_no_video_note_explains_the_strict_title_check(settings, db):
    settings.batch_size = 1

    class OtherSeries(FakeYouTube):
        def find_letsplays(self, title, release_date=None):
            self.searches += 1
            self.last_stats = {"found": 12, "eligible": 0, "title_mismatch": 7}
            return []

    yt = OtherSeries()
    make(settings, db, yt=yt).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["status"] == "no_video" and "ни в одном названии нет полного названия «over the hill»" in lp["note"]
    assert lp["search"]["title_mismatch"] == 7


def test_release_date_reaches_the_youtube_search(settings, db):
    settings.batch_size = 1
    yt = FakeYouTube()
    make(settings, db, yt=yt).run()
    assert yt.release_dates == [db.get_game(slug="over-the-hill")["release_date"]] and yt.release_dates[0]


def test_captions_can_be_switched_off_to_go_straight_to_whisper(settings, db, tmp_path):
    settings.batch_size = 1
    settings.youtube_captions = False
    yt = FakeYouTube()
    make(settings, db, yt=yt, stt=make_stt(settings, tmp_root=tmp_path)).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert yt.transcript_calls == 0 and lp["transcript_method"] == "whisper" and lp["checked"] == []


def test_whisper_failure_falls_back_to_the_video_description(settings, db, tmp_path):
    settings.batch_size = 1
    source = FakeAudioSource(error=SpeechUnavailable("yt-dlp не получил данные ролика: Sign in to confirm"))
    result = make(settings, db, yt=FakeYouTube(transcript_ok=False),
                  stt=make_stt(settings, source, tmp_root=tmp_path)).run()
    assert result["status"] == "ok" and result["failed"] == 0
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["status"] == "no_transcript" and lp["method"] == "llm-description"
    assert lp["video"]["video_id"] == TOP
    assert "субтитры: автор отключил субтитры (3 ролика)" in lp["note"]
    assert "Whisper: yt-dlp не получил данные ролика: Sign in to confirm (2 ролика)" in lp["note"]
    assert "пропускается до конца запуска" in lp["note"] and len(source.calls) == 2
    assert [n.get("video_id") for n in lp["stt_checked"]] == [TOP, SECOND, None]
    assert "stt" not in lp


class ScriptedTranscriber(FakeTranscriber):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    def transcribe(self, audio, budget):
        if self.errors:
            self.calls.append((len(audio), budget))
            raise self.errors.pop(0)
        return super().transcribe(audio, budget)


def test_whisper_moves_on_when_the_top_video_has_no_speech(settings, db):
    settings.batch_size = 1
    source = FakeAudioSource()
    tr = ScriptedTranscriber([NoSpeech("в аудио почти нет речи: детектор речи нашёл 38 с из 900")])
    make(settings, db, yt=FakeYouTube(transcript_ok=False), stt=make_stt(settings, source, tr)).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    first, second = [c[0] for c in source.calls]
    assert lp["method"] == "llm-whisper" and (first, second) == (TOP, SECOND) and lp["video"]["video_id"] == SECOND
    reason = "в аудио почти нет речи: детектор речи нашёл 38 с из 900"
    assert lp["stt_checked"] == [{"video_id": first, "reason": reason}]
    assert lp["passed_over"] == [{"video_id": TOP, "title": lp["candidates"][0]["title"], "views": 693184,
                                  "reason": "субтитров нет, речь не распознана"}]


def test_a_crashing_whisper_never_breaks_the_run(settings, db, tmp_path):
    settings.batch_size = 2
    tr = FakeTranscriber(error=MemoryError("out of memory"))
    result = make(settings, db, yt=FakeYouTube(transcript_ok=False), stt=make_stt(settings, transcriber=tr)).run()
    assert result["status"] == "ok" and result["ok"] == 2
    for item in FakeMetacritic().new[:2]:
        assert db.get_game(slug=item.slug)["letsplay"]["method"] == "llm-description"
    assert len(tr.calls) == 2


def test_whisper_is_capped_per_run_and_skipped_near_the_time_limit(settings, db):
    settings.batch_size = 3
    settings.stt_max_per_run = 1
    source = FakeAudioSource()
    make(settings, db, yt=FakeYouTube(transcript_ok=False), stt=make_stt(settings, source)).run()
    methods = [db.get_game(slug=i.slug)["letsplay"] for i in FakeMetacritic().new[:3]]
    assert [lp["method"] for lp in methods] == ["llm-whisper", "llm-description", "llm-description"]
    assert "лимит" in methods[1]["note"] and len(source.calls) == 1

    forget_today(db)
    settings.run_max_minutes = 5
    settings.stt_max_per_run = 5
    clock = FakeClock()
    clock.advance(days=2)
    late = FakeAudioSource()
    make(settings, db, yt=FakeYouTube(transcript_ok=False), stt=make_stt(settings, late), clock=clock).run()
    assert late.calls == []
    assert "отложено" in db.get_game(slug=FakeMetacritic().new[1].slug)["letsplay"]["note"]


def test_whisper_letsplays_refresh_weekly_and_get_llm_upgrades(settings, db):
    settings.batch_size = 1
    settings.llm_backfill_limit = 0
    clock = FakeClock()
    yt = FakeYouTube(transcript_ok=False)
    source = FakeAudioSource()
    make(settings, db, yt=yt, llm=FakeLLM(fail=True), clock=clock, stt=make_stt(settings, source)).run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["method"] == "extractive-whisper" and lp["transcript_excerpt"]
    up = make(settings, db, llm=FakeLLM(), clock=clock)
    assert up.backfill_llm(set(), limit=10) >= 1
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["method"] == "llm-whisper" and lp["transcript_method"] == "whisper"
    p = make(settings, db, yt=yt, clock=clock, stt=make_stt(settings, source))
    forget_today(db)
    clock.advance(days=3)
    p.run()
    assert yt.searches == 1
    forget_today(db)
    clock.advance(days=5)
    p.run()
    assert yt.searches == 2 and len(source.calls) == 2


def test_an_unverified_old_conclusion_is_replaced_by_an_honest_result(settings, db):
    from app.pipeline import letsplay_rank

    settings.batch_size = 1
    clock = FakeClock()
    p = make(settings, db, yt=FakeYouTube(transcript_ok=False), clock=clock)
    p.run()
    gid = db.get_game(slug="over-the-hill")["id"]
    db.set_json(gid, "letsplay", {"status": "ok", "method": "llm-transcript", "summary": "Автор ценит PS5 Pro",
                                  "pros": ["выдумано"], "video": {"video_id": "zzzzzzzzzzz"},
                                  "updated_at": "2000-01-01T00:00:00+00:00", "checked_at": "2000-01-01T00:00:00+00:00"})
    forget_today(db)
    p.run()
    lp = db.get_game(slug="over-the-hill")["letsplay"]
    assert lp["status"] == "no_transcript" and lp["video"]["video_id"] == TOP and "last_error" not in lp
    assert letsplay_rank({"status": "ok", "method": "llm-transcript", "has_commentary": True, "video": {"video_id": "x"}}) == 3
    assert letsplay_rank({"status": "ok", "method": "extractive-whisper", "video": {"video_id": "x"}}) == 3
    assert letsplay_rank({"status": "ok", "method": "llm-whisper", "video": {"video_id": "x"}}) == 2
