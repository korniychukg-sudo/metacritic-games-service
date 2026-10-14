import json
import logging
import re
import time
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import Database
from app.main import build_services, create_app
from app.monitor import Monitor
from app.pipeline import Pipeline

from .conftest import FakeClock, FakeLLM, FakeMetacritic, FakeYouTube, make_stt

EVIL_TITLE = '<img src=x onerror="alert(1)">'


def client_with_data(settings, run=True, batch=6, clock=None, start_scheduler=False, yt=None, stt=None):
    settings.batch_size = batch
    clock = clock or FakeClock()
    mon = Monitor()
    db = Database(settings.db_path)
    pipeline = Pipeline(settings, db, mon, FakeMetacritic(), yt or FakeYouTube(), FakeLLM(), clock=clock, stt=stt)
    if run:
        pipeline.run()
    app = create_app(settings, pipeline=pipeline, start_scheduler=start_scheduler, monitor=mon, clock=clock)
    return TestClient(app), pipeline, clock


def wait_idle(pipeline, timeout=10):
    deadline = time.time() + timeout
    while pipeline.running and time.time() < deadline:
        time.sleep(0.02)
    assert not pipeline.running


def test_list_page_search_and_sort(settings):
    client, _, _ = client_with_data(settings)
    with client:
        r = client.get("/")
        assert r.status_code == 200
        assert "over the hill" in r.text and "Все платформы" in r.text
        r = client.get("/", params={"q": "OVER the"})
        assert "over the hill" in r.text and "Planet Zoo 2" not in r.text
        data = client.get("/api/games", params={"sort": "title"}).json()
        titles = [g["title"].casefold() for g in data["items"]]
        assert titles == sorted(titles) and data["total"] == 6
        plats = client.get("/api/platforms").json()
        assert {p["name"] for p in plats} == {"PC", "PlayStation 5", "Xbox Series X"}
        only_xbox = client.get("/api/games", params={"platform": "Xbox Series X"}).json()
        assert only_xbox["total"] == 6
        assert client.get("/api/games", params={"platform": "Nintendo 64"}).json()["total"] == 0
        assert client.get("/api/games", params={"q": "%"}).json()["total"] == 0
        assert client.get("/api/games", params={"q": "_"}).json()["total"] == 0


def test_platform_filter_changes_which_scores_are_shown_and_sorted(settings):
    client, pipeline, _ = client_with_data(settings)
    db = pipeline.db
    slugs = [g["slug"] for g in client.get("/api/games").json()["items"]]
    low, high = slugs[0], slugs[1]
    with db.write() as c:
        for slug, score, user in ((low, 41, 3.2), (high, 97, 9.4)):
            gid = c.execute("SELECT id FROM games WHERE slug=?", (slug,)).fetchone()[0]
            c.execute("UPDATE platforms SET metascore=?, userscore=? WHERE game_id=? AND name='PC'", (score, user, gid))
    with client:
        unfiltered = client.get("/api/games", params={"sort": "metascore_desc"}).json()["items"]
        assert {g["score_meta"] for g in unfiltered} == {88}
        pc = client.get("/api/games", params={"platform": "PC", "sort": "metascore_desc"}).json()["items"]
        assert pc[0]["slug"] == high and pc[0]["score_meta"] == 97
        assert pc[-1]["slug"] == low and pc[-1]["score_meta"] == 41
        pc_user = client.get("/api/games", params={"platform": "PC", "sort": "userscore_asc"}).json()["items"]
        assert pc_user[0]["slug"] == low and pc_user[0]["score_user"] == 3.2
        ps5 = client.get("/api/games", params={"platform": "PlayStation 5", "sort": "metascore_asc"}).json()["items"]
        assert {g["score_meta"] for g in ps5} == {88}
        html = client.get("/", params={"platform": "PC", "sort": "metascore_desc"}).text
        assert "оценки показаны для платформы PC" in html
        assert html.index(f'href="/game/{high}"') < html.index(f'href="/game/{low}"')


def test_game_card_and_similar_links(settings):
    client, _, _ = client_with_data(settings)
    with client:
        r = client.get("/game/over-the-hill")
        assert r.status_code == 200
        for text in ("Оценки по платформам", "Что говорят критики", "Что говорят игроки", "Летсплей на YouTube",
                     "Похожие игры", "Fuse Games", "bGBXM6oxbag", "красивые трассы", "cdn.jwplayer.com"):
            assert text in r.text, text
        g = client.get("/api/games/over-the-hill").json()
        assert g["video"]["source"] == "metacritic" and g["video_url"].startswith("https://cdn.jwplayer.com/")
        assert g["similar_games"]
        target = g["similar_games"][0]["slug"]
        assert f'href="/game/{target}"' in r.text
        assert client.get(f"/game/{target}").status_code == 200
        assert client.get("/game/nope").status_code == 404


def test_titles_and_links_are_escaped(settings):
    client, pipeline, _ = client_with_data(settings)
    with pipeline.db.write() as c:
        c.execute("UPDATE games SET title=?, video_embed_url=?, video_manifest_url=?, cover_url=?, critic_reviews=? "
                  "WHERE slug='over-the-hill'",
                  (EVIL_TITLE, "javascript:alert(document.domain)", "javascript:alert(2)", "javascript:alert(3)",
                   json.dumps([{"kind": "critic", "sentiment": "positive", "quote": "<b>bold</b>", "score": 90,
                                "author": "Evil", "url": "javascript:alert(4)", "date": None, "platform": None}])))
    with client:
        for path in ("/", "/game/over-the-hill"):
            html = client.get(path).text
            assert EVIL_TITLE not in html and "&lt;img src=x onerror=" in html
            assert "javascript:" not in html
        html = client.get("/game/over-the-hill").text
        assert "&lt;b&gt;bold&lt;/b&gt;" in html and ">Evil<" not in html.replace("\n", "")


def test_security_headers_and_no_inline_scripts_or_styles(settings):
    client, _, _ = client_with_data(settings)
    with client:
        for path in ("/", "/game/over-the-hill", "/monitor"):
            r = client.get(path)
            csp = r.headers["content-security-policy"]
            assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
            assert r.headers["x-content-type-options"] == "nosniff"
            assert not re.search(r"<script(?![^>]*\bsrc=)", r.text), path
            assert ' style="' not in r.text, path
        assert client.get("/static/app.js").headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" not in client.get("/docs").headers


def test_youtube_trailer_is_shown_when_metacritic_has_no_video(settings):
    client, pipeline, _ = client_with_data(settings)
    with pipeline.db.write() as c:
        c.execute("UPDATE games SET video_embed_url=NULL, video_manifest_url=NULL, trailer=? WHERE slug='over-the-hill'",
                  (json.dumps({"status": "ok", "video": {"video_id": "TrAiLeR0001", "title": "over the hill Trailer",
                                                         "channel": "Pub", "views": 1200}}),))
        c.execute("UPDATE games SET video_embed_url=NULL, trailer=? WHERE slug='planet-zoo-2'",
                  (json.dumps({"status": "ok", "video": {"video_id": "javascript:x"}}),))
    with client:
        html = client.get("/game/over-the-hill").text
        assert "https://www.youtube-nocookie.com/embed/TrAiLeR0001" in html
        assert "https://www.youtube.com/watch?v=TrAiLeR0001" in html and "трейлер с YouTube" in html
        assert client.get("/api/games/over-the-hill").json()["video"]["source"] == "youtube"
        assert client.get("/api/games/planet-zoo-2").json()["video"] is None


def test_page_bounds(settings):
    client, _, _ = client_with_data(settings)
    with client:
        for bad in ("0", "abc", "-3", "99999999999"):
            r = client.get("/", params={"page": bad})
            assert r.status_code == 200 and 'class="current">1<' not in r.text and "over the hill" in r.text, bad
        past = client.get("/", params={"page": 2}).text
        assert "страницы 2 нет, показана 1 из 1" in past and "Ничего не найдено" not in past
        assert "Найдено: 6 игр" in past
        assert client.get("/api/games", params={"page": 10**20}).status_code == 422
        assert client.get("/api/games", params={"page": 10000}).json()["items"] == []
        assert client.get("/api/games", params={"q": "x" * 201}).status_code == 422
        r = client.get("/", params={"q": "x" * 201})
        assert r.status_code == 422 and "Некорректный адрес страницы" in r.text and "← Все игры" in r.text


def test_error_pages_are_html_for_people_and_json_for_the_api(settings):
    client, pipeline, _ = client_with_data(settings)
    with client:
        r = client.get("/game/nope")
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
        assert "Игра не найдена" in r.text and 'href="/"' in r.text and "← Все игры" in r.text
        r = client.get("/no/such/page")
        assert r.status_code == 404 and "Страница не найдена" in r.text
        r = client.get("/api/games/nope")
        assert r.status_code == 404 and r.json() == {"detail": "Игра не найдена"}
        assert client.get("/api/nothing").headers["content-type"].startswith("application/json")

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    pipeline.db.get_game = boom
    app = client.app
    with TestClient(app, raise_server_exceptions=False) as broken:
        r = broken.get("/game/over-the-hill")
        assert r.status_code == 500 and "Что-то пошло не так" in r.text and "disk on fire" not in r.text
        r = broken.get("/api/games/over-the-hill")
        assert r.status_code == 500 and r.json() == {"detail": "Внутренняя ошибка сервера"}


def test_monitor_status_and_live_stream(settings):
    client, pipeline, _ = client_with_data(settings)
    with client:
        r = client.get("/monitor")
        assert r.status_code == 200 and "Запустить обработку сейчас" in r.text
        st = client.get("/api/status").json()
        assert st["totals"]["games_total"] == 6 and st["totals"]["processed_today"] == 6
        assert {w["name"] for w in st["workers"]} >= {"scheduler", "pipeline", "llm", "metacritic", "youtube", "stt"}
        assert "Распознавание речи (Whisper)" in r.text
        assert st["manual"]["allowed"] is True and st["last_run"]["status"] == "ok"
        logging.getLogger("pipeline").info("hello from the live log")
        with client.stream("GET", "/api/events", params={"once": 1}) as resp:
            body = "".join(resp.iter_text())
        assert "event: status" in body and "event: log" in body
        status = json.loads(body.split("event: status\ndata: ", 1)[1].split("\n", 1)[0])
        assert status["totals"]["games_total"] == 6
        logs = json.loads(body.split("event: log\ndata: ", 1)[1].split("\n", 1)[0])
        assert any(e["message"] == "hello from the live log" for e in logs)
        last_id = logs[-1]["id"]
        with client.stream("GET", "/api/events", params={"once": 1, "last_log": last_id}) as resp:
            body = "".join(resp.iter_text())
        assert "hello from the live log" not in body


def test_force_run_starts_and_then_cools_down(settings):
    settings.manual_cooldown_seconds = 300
    client, pipeline, clock = client_with_data(settings, run=False, batch=2)
    with client:
        r = client.post("/run", headers={"Accept": "application/json"})
        assert r.status_code == 202 and r.json()["started"] is True
        wait_idle(pipeline)
        assert pipeline.db.last_run()["trigger"] == "manual" and pipeline.db.count_games() == 2
        r = client.post("/api/run")
        assert r.status_code == 429 and r.json()["code"] == "cooldown"
        assert 290 <= int(r.headers["retry-after"]) <= 300
        page = client.get("/monitor").text
        assert "Запуск через 5:00" in page and 'data-code="cooldown"' in page
        r = client.post("/run", follow_redirects=False)
        assert r.status_code == 303 and "wait=300" in r.headers["location"]
        assert "Попробуйте через 5:00" in client.get(r.headers["location"]).text
        clock.advance(seconds=301)
        r = client.post("/run", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("started=1")
        wait_idle(pipeline)


def test_busy_runs_are_rejected(settings):
    client, pipeline, _ = client_with_data(settings)
    with client:
        pipeline._lock.acquire()
        try:
            r = client.post("/api/run")
            assert r.status_code == 409 and r.json()["code"] == "busy"
            r = client.post("/run", follow_redirects=False)
            assert r.status_code == 303 and "busy=1" in r.headers["location"]
            assert "Обработка идёт" in client.get("/monitor").text
        finally:
            pipeline._lock.release()


def test_cross_site_run_requests_are_refused(settings):
    client, pipeline, _ = client_with_data(settings, run=False)
    with client:
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "same-site"}):
            assert client.post("/api/run", headers=headers).status_code == 403
            r = client.post("/run", headers=headers, follow_redirects=False)
            assert r.status_code == 303 and "forbidden=1" in r.headers["location"]
        assert pipeline.db.last_run() is None
        r = client.post("/run", headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin",
                                         "Accept": "application/json"})
        assert r.status_code == 202
        wait_idle(pipeline)


def test_run_token_is_needed_everywhere_except_the_monitor_button(settings):
    settings.run_token = "s3cret"
    client, pipeline, _ = client_with_data(settings, run=False, batch=1)
    with client:
        assert client.post("/api/run").status_code == 401
        assert client.post("/api/run", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = client.post("/run", headers={"Accept": "application/json"})
        assert r.status_code == 401 and r.json()["code"] == "unauthorized"
        r = client.post("/run", follow_redirects=False)
        assert r.status_code == 303 and "forbidden=1" in r.headers["location"]
        assert pipeline.db.last_run() is None
        r = client.post("/api/run?limit=1", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 202
        wait_idle(pipeline)
        r = client.post("/run", headers={"Accept": "application/json", "Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 202
        wait_idle(pipeline)
        r = client.post("/run", headers={"Accept": "application/json", "Authorization": "Bearer s3cret"})
        assert r.status_code == 202
        wait_idle(pipeline)
        assert client.get("/api/status").json()["manual"]["token_required_for_api"] is True


def test_daily_cap_on_manual_runs(settings):
    settings.manual_daily_cap = 2
    client, pipeline, clock = client_with_data(settings, run=False)
    day = settings.today(clock())
    for _ in range(2):
        rid = pipeline.db.create_run("manual", day, "x", clock() - timedelta(hours=2))
        pipeline.db.finish_run(rid, "ok", 0, 0, 0, "", "", clock() - timedelta(hours=2))
    with client:
        r = client.post("/api/run")
        assert r.status_code == 429 and r.json()["code"] == "cap"
        assert int(r.headers["retry-after"]) == int((settings.next_midnight(clock()) - clock()).total_seconds())
        assert "Лимит ручных запусков на сегодня" in client.get("/monitor").text
        clock.now = settings.next_midnight(clock()) + timedelta(minutes=1)
        assert client.post("/api/run").status_code == 202
        wait_idle(pipeline)


def test_healthz_reports_scheduler_liveness(settings):
    client, _, _ = client_with_data(settings)
    with client:
        r = client.get("/healthz")
        assert r.status_code == 200 and r.json()["status"] == "ok"
    settings.scheduler_enabled = True
    client, _, _ = client_with_data(settings, run=False)
    with client:
        r = client.get("/healthz")
        assert r.status_code == 503 and r.json()["scheduler"]["alive"] is False


def test_empty_state(settings):
    client, _, _ = client_with_data(settings, run=False)
    with client:
        r = client.get("/")
        assert "Игр пока нет" in r.text


def test_live_log_timestamps_use_the_configured_zone():
    from datetime import datetime, timezone

    from app.config import MSK

    mon = Monitor(tz=MSK)
    record = logging.LogRecord("pipeline", logging.INFO, __file__, 1, "x", None, None)
    record.created = datetime(2026, 10, 14, 12, 0, 5, tzinfo=timezone.utc).timestamp()
    mon.add_log(record, "x")
    assert mon.logs_since(0)[0]["ts"] == "15:00:05"


def test_head_requests_for_uptime_checks(settings):
    client, _, _ = client_with_data(settings, run=False)
    with client:
        assert client.head("/").status_code == 200
        assert client.head("/healthz").status_code == 200


def test_russian_plurals():
    from app.main import ru_plural

    forms = ("оценка", "оценки", "оценок")
    assert [ru_plural(n, *forms) for n in (1, 2, 5, 11, 12, 21, 22, 25, 101, 111)] == [
        "оценка", "оценки", "оценок", "оценок", "оценок", "оценка", "оценки", "оценок", "оценка", "оценок"]
    assert ru_plural(None, *forms) == "оценок"


def test_card_says_when_the_text_comes_from_whisper(settings):
    stt = make_stt(settings)
    client, pipeline, _ = client_with_data(settings, batch=1, yt=FakeYouTube(transcript_ok=False), stt=stt)
    with client:
        html = client.get("/game/over-the-hill").text
        assert "LLM по речи, распознанной Whisper" in html
        assert "Субтитров у ролика нет, поэтому речь блогера распознана локально моделью Whisper (base" in html
        assert "по первым 1 мин аудио" in html and "Фрагменты речи, распознанной Whisper" in html
        assert "Это самый популярный подходящий ролик в поиске YouTube" in html
        assert "Слова блогера, на которых основано заключение" in html
        lp = client.get("/api/games/over-the-hill").json()["letsplay"]
        assert lp["transcript_method"] == "whisper" and lp["stt"]["model"] == "base"
        lp["passed_over"] = [{"video_id": "oiVbvxSoNZI", "title": "Big Video", "views": 2050,
                              "reason": "в ролике нет комментариев блогера"}]
        pipeline.db.set_json(pipeline.db.get_game(slug="over-the-hill")["id"], "letsplay", lp)
        html = client.get("/game/over-the-hill").text
        assert "Более популярные пропущены:" in html
        assert "«Big Video» (2 050 просмотров): в ролике нет комментариев блогера" in html


def test_monitor_shows_the_speech_worker_state(settings, monkeypatch):
    mon = Monitor()
    db = Database(settings.db_path)
    pipeline = build_services(settings, db, mon)
    assert pipeline.stt is not None
    worker = next(w for w in mon.snapshot()["workers"] if w["name"] == "stt")
    assert worker["status"] == "idle" and "Whisper «base»" in worker["detail"]
    settings.stt_enabled = False
    off = Monitor()
    assert build_services(settings, db, off).stt is None
    worker = next(w for w in off.snapshot()["workers"] if w["name"] == "stt")
    assert worker["status"] == "off" and "STT_ENABLED=0" in worker["detail"]


def test_description_based_letsplay_is_not_presented_as_the_bloggers_opinion(settings):
    client, pipeline, _ = client_with_data(settings, batch=1, yt=FakeYouTube(transcript_ok=False))
    with client:
        html = client.get("/game/over-the-hill").text
        assert "По описанию ролика" in html and "речь блогера не расшифрована" in html
        assert "Понравилось блогеру" not in html and "Не понравилось блогеру" not in html
        lp = client.get("/api/games/over-the-hill").json()["letsplay"]
        lp.update(pros=["выдуманный плюс"], cons=["выдуманный минус"])
        pipeline.db.set_json(pipeline.db.get_game(slug="over-the-hill")["id"], "letsplay", lp)
        html = client.get("/game/over-the-hill").text
        assert "выдуманный плюс" not in html and "выдуманный минус" not in html


def test_monitor_shows_sources_durations_and_hides_internal_fields(settings):
    client, pipeline, clock = client_with_data(settings, batch=3)
    with client:
        html = client.get("/monitor").text
        assert "<th>Источник</th>" in html and "<th>Длительность</th>" in html
        assert "New Releases" in html and "new-releases" not in html.replace('data-', '')
        assert "сегодня ещё не пройден блок New Releases" in html
        st = client.get("/api/status").json()
        assert "owner" not in st["last_run"] and all("owner" not in r for r in st["runs"])
        assert st["last_run"]["source_label"] == "New Releases" and st["last_run"]["seconds"] == 0
        runs = client.get("/api/runs").json()
        assert runs and "owner" not in runs[0] and "heartbeat_at" not in runs[0]
        pipeline.db.add_error(1, st["day"], "x", "youtube:schema", "boom")
        client.app.state.status_cache.invalidate()
        st = client.get("/api/status").json()
        assert st["errors"][0]["step_label"] == "YouTube: поиск летсплея (формат ответа изменился)"
        pipeline.db.save_day_state(type(pipeline.db.get_day_state("d"))(st["day"], True, 48))
        assert "дальше SEE ALL со страницы 3" in client.get("/monitor").text


def test_source_and_step_labels():
    from app.main import fmt_wait, source_label, step_label

    assert source_label("new-releases, browse:p2") == "New Releases, SEE ALL, стр. 2"
    assert source_label("retry, browse:p1") == "повтор упавших, SEE ALL, стр. 1"
    assert source_label("") == "—"
    assert step_label("selection:schema") == "Список игр Metacritic (формат ответа изменился)"
    assert step_label("Metacritic: карточка и оценки") == "Metacritic: карточка и оценки"
    assert fmt_wait(299) == "4:59" and fmt_wait(3600 * 22 + 61) == "22:01:01"


def test_cooldown_hint_counts_down_instead_of_freezing(settings):
    settings.manual_cooldown_seconds = 300
    client, pipeline, clock = client_with_data(settings, batch=1)
    with client:
        html = client.get("/monitor").text
        assert "Можно запустить через 5:00." in html
        st = client.get("/api/status").json()["manual"]
        assert st["code"] == "cooldown" and "5:00" not in st["reason"] and "через" not in st["reason"]


def test_empty_states_follow_the_scheduler_setting(settings):
    settings.scheduler_enabled = False
    client, _, _ = client_with_data(settings, run=False)
    with client:
        html = client.get("/").text
        assert "Автозапуск выключен" in html and "запускается автоматически" not in html
        mon = client.get("/monitor").text
        assert "Автозапуск выключен (SCHEDULER_ENABLED=0)" in mon
        assert "сегодня первым будет обработан блок New Releases" in mon
        assert "запусков ещё не было" in mon
        assert "с момента старта сервиса запросов не было" in mon


def test_similar_games_show_weak_matches_separately(settings):
    client, pipeline, _ = client_with_data(settings)
    db = pipeline.db
    games = client.get("/api/games").json()["items"]
    first, second, third = games[0], games[1], games[2]
    db.set_similar_bulk({first["id"]: [{"id": second["id"], "score": 0.4, "reasons": ["общий жанр"]},
                                       {"id": third["id"], "score": 0.05, "reasons": ["близкий жанр"],
                                        "weak": True}]})
    with client:
        html = client.get(f"/game/{first['slug']}").text
        strong_at = html.index(f'href="/game/{second["slug"]}"')
        weak_at = html.index(f'href="/game/{third["slug"]}"')
        assert strong_at < html.index("Ближе всего из базы (слабое сходство)") < weak_at
        data = client.get(f"/api/games/{first['slug']}").json()["similar_games"]
        assert [s["weak"] for s in data] == [False, True]


def test_event_stream_ends_after_its_lifetime(settings, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "SSE_MAX_SECONDS", 0)
    client, _, _ = client_with_data(settings, run=False)
    with client:
        with client.stream("GET", "/api/events") as resp:
            body = "".join(resp.iter_text())
        assert resp.status_code == 200 and body == ""
