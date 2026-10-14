import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.monitor import Monitor
from app.pipeline import Pipeline

from .conftest import FakeLLM, FakeMetacritic, FakeYouTube


def client_with_data(settings, run=True):
    from app.db import Database

    settings.batch_size = 6
    mon = Monitor()
    db = Database(settings.db_path)
    pipeline = Pipeline(settings, db, mon, FakeMetacritic(), FakeYouTube(), FakeLLM())
    if run:
        pipeline.run()
    app = create_app(settings, pipeline=pipeline, start_scheduler=False, monitor=mon)
    return TestClient(app), pipeline


def test_list_page_filters_and_sort(settings):
    client, _ = client_with_data(settings)
    with client:
        r = client.get("/")
        assert r.status_code == 200
        assert "Star Wars: Galactic Racer" in r.text or "over the hill" in r.text
        assert "Все платформы" in r.text
        r = client.get("/", params={"q": "OVER the"})
        assert "over the hill" in r.text and "Planet Zoo 2" not in r.text
        data = client.get("/api/games", params={"sort": "metascore_desc"}).json()
        scores = [g["score_meta"] for g in data["items"] if g["score_meta"] is not None]
        assert scores == sorted(scores, reverse=True)
        asc = client.get("/api/games", params={"sort": "userscore_asc"}).json()
        us = [g["score_user"] for g in asc["items"] if g["score_user"] is not None]
        assert us == sorted(us)
        plats = client.get("/api/platforms").json()
        assert any(p["name"] == "PC" for p in plats)
        only_pc = client.get("/api/games", params={"platform": "Xbox Series X"}).json()
        assert only_pc["total"] == 6
        assert client.get("/api/games", params={"platform": "Nintendo 64"}).json()["total"] == 0


def test_game_card_and_similar_links(settings):
    client, _ = client_with_data(settings)
    with client:
        r = client.get("/game/over-the-hill")
        assert r.status_code == 200
        for text in ("Оценки по платформам", "Что говорят критики", "Что говорят игроки", "Летсплей на YouTube",
                     "Похожие игры", "Fuse Games", "bGBXM6oxbag", "красивые трассы"):
            assert text in r.text, text
        g = client.get("/api/games/over-the-hill").json()
        assert g["similar_games"]
        target = g["similar_games"][0]["slug"]
        assert f'href="/game/{target}"' in r.text
        assert client.get("/game/nope").status_code == 404


def test_monitor_status_events_and_run_guard(settings):
    client, pipeline = client_with_data(settings)
    with client:
        r = client.get("/monitor")
        assert r.status_code == 200 and "Запустить обработку сейчас" in r.text
        st = client.get("/api/status").json()
        assert st["totals"]["games_total"] == 6
        assert st["totals"]["processed_today"] == 6
        assert {w["name"] for w in st["workers"]} >= {"scheduler", "pipeline", "llm"}
        with client.stream("GET", "/api/events", params={"once": 1}) as resp:
            body = "".join(resp.iter_text())
        assert "event: status" in body
        payload = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
        assert payload["totals"]["games_total"] == 6
        pipeline._lock.acquire()
        try:
            assert client.post("/api/run").status_code == 409
            r = client.post("/run", follow_redirects=False)
            assert r.status_code == 303 and "busy=1" in r.headers["location"]
        finally:
            pipeline._lock.release()
        assert client.get("/healthz").json()["status"] == "ok"


def test_empty_state(settings):
    client, _ = client_with_data(settings, run=False)
    with client:
        r = client.get("/")
        assert "Игр пока нет" in r.text
