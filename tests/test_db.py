import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.db import BASE_SCHEMA, SCHEMA_VERSION, Database, SchemaTooNew
from app.metacritic import parse_composer

from .conftest import load_json, product_item

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def make_game(game_id=1300714334, slug="star-wars-galactic-racer", title=None):
    payload = load_json("composer_game_star-wars-galactic-racer.json")
    item = product_item(payload)
    item["id"], item["slug"] = game_id, slug
    if title:
        item["title"] = title
    return parse_composer(payload)


def columns(db, table):
    with db.conn() as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def test_old_schema_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(BASE_SCHEMA)
    raw.execute("INSERT INTO games(id, slug, title, title_search, first_seen_at, updated_at) "
                "VALUES (1, 'old', 'Old Game', 'old game', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')")
    raw.execute("INSERT INTO processed(day, game_id, slug, title, run_id, status, source, processed_at) "
                "VALUES ('2026-10-01', 1, 'old', 'Old Game', 1, 'error', 'new-releases', '2026-10-01T00:00:00+00:00')")
    raw.execute("INSERT INTO runs(trigger, day, started_at, status) VALUES ('schedule', '2026-10-01', "
                "'2026-10-01T00:00:00+00:00', 'ok')")
    raw.commit()
    raw.close()

    db = Database(str(path))
    assert db.schema_version == SCHEMA_VERSION
    assert {"attempts"} <= columns(db, "processed")
    assert {"owner", "heartbeat_at"} <= columns(db, "runs")
    assert {"backfill_at", "backfill_attempts", "trailer"} <= columns(db, "games")
    with db.conn() as c:
        indexes = {r[1] for r in c.execute("PRAGMA index_list(errors)")}
    assert "idx_errors_day" in indexes
    assert db.get_game(slug="old")["title"] == "Old Game"
    assert db.retry_candidates("2026-10-01", 2)[0]["attempts"] == 1
    db.upsert_game(make_game(), "new-releases")
    assert db.count_games() == 2
    reopened = Database(str(path))
    assert reopened.schema_version == SCHEMA_VERSION


def test_fresh_database_gets_latest_schema_and_newer_schema_is_refused(tmp_path):
    db = Database(str(tmp_path / "fresh.db"))
    assert db.schema_version == SCHEMA_VERSION
    raw = sqlite3.connect(tmp_path / "fresh.db")
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()
    with pytest.raises(SchemaTooNew):
        Database(str(tmp_path / "fresh.db"))


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    from app import db as dbmod

    path = tmp_path / "half.db"
    raw = sqlite3.connect(path)
    raw.executescript(BASE_SCHEMA)
    raw.commit()
    raw.close()
    monkeypatch.setattr(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:1] + [["ALTER TABLE nope ADD COLUMN x"]])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 2)
    with pytest.raises(sqlite3.OperationalError):
        Database(str(path))
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "attempts" not in {r[1] for r in raw.execute("PRAGMA table_info(processed)")}
    raw.close()


def test_like_wildcards_are_escaped(db):
    db.upsert_game(make_game(1, "a", "100% Orange Juice"), "x")
    db.upsert_game(make_game(2, "b", "Snake_Pass"), "x")
    db.upsert_game(make_game(3, "c", "Plain Game"), "x")
    assert db.list_games(q="%")[1] == 1
    assert db.list_games(q="100%")[0][0]["slug"] == "a"
    assert db.list_games(q="_")[1] == 0
    assert [g["slug"] for g in db.list_games(q="snake_pass")[0]] == ["b"]
    assert db.list_games(q="   ")[1] == 3
    assert db.list_games(q="\\")[1] == 0


def test_title_search_ignores_punctuation_and_matches_every_word(db):
    db.upsert_game(make_game(1, "dd2", "Dragon's Dogma 2: Dark Arisen"), "x")
    db.upsert_game(make_game(2, "bolt", "Warhammer 40,000: Boltgun 2"), "x")
    db.upsert_game(make_game(3, "kh", "Kingdom Hearts Collection I ~ III"), "x")
    db.upsert_game(make_game(4, "kh3", "Kingdom Hearts III"), "x")
    for query, slugs in (("dragons dogma", ["dd2"]), ("dragon's", ["dd2"]), ("DOGMA dragons", ["dd2"]),
                         ("warhammer 40000", ["bolt"]), ("40,000", ["bolt"]), ("boltgun 2", ["bolt"]),
                         ("kingdom hearts collection i-iii", ["kh"]), ("hearts collection", ["kh"]),
                         ("dogma boltgun", [])):
        assert sorted(g["slug"] for g in db.list_games(q=query)[0]) == slugs, query


def test_migration_recomputes_search_keys_and_flags_bad_summaries(tmp_path):
    from app import db as dbmod

    path = tmp_path / "old.db"
    old = Database(str(path))
    old.upsert_game(make_game(1, "dd2", "Dragon's Dogma 2"), "x")
    review = {"kind": "user", "sentiment": "negative", "quote": "Botched remaster and AI slop everywhere, sadly.",
              "score": 2.0, "author": "u", "url": None, "date": None, "platform": None}
    english = {"likes": ["Хорошая графика"], "dislikes": ["botched remaster process"], "verdict": "Игра спорная.",
               "method": "llm", "model": "m", "review_count": 1, "sample_size": 1, "reviews_hash": "h"}
    russian = {"likes": ["Отличная боёвка в духе Sekiro"], "dislikes": [], "verdict": "Хорошо.", "method": "llm"}
    unverified = {"status": "ok", "method": "llm-transcript", "summary": "Автор ценит производительность",
                  "updated_at": "2026-10-15T00:00:00+00:00", "checked_at": "2026-10-15T00:00:00+00:00",
                  "video": {"video_id": "zzzzzzzzzzz", "title": "KINGDOM HEARTS COLLECTION I-III Walkthrough Part 1"}}
    old2 = Database(str(path))
    old2.upsert_game(make_game(2, "kh", "Kingdom Hearts Collection I ~ III"), "x")
    old2.set_json(2, "letsplay", unverified)
    old2.close()
    letsplay = {"status": "no_transcript", "method": "llm-description", "summary": "Автор ролика отмечает…",
                "pros": ["выдумано"], "cons": [], "verdict": "x",
                "video": {"video_id": "bGBXM6oxbag", "title": "Dragon's Dogma 2 Walkthrough",
                          "description": "Full walkthrough of the game."}}
    with old.write() as c:
        c.execute("UPDATE games SET title_search='dragon''s dogma 2', user_summary=?, critic_summary=?, "
                  "user_reviews=?, letsplay=? WHERE id=1",
                  (json.dumps(english), json.dumps(russian), json.dumps([review]), json.dumps(letsplay)))
    old.close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 4")
    raw.commit()
    raw.close()
    db = Database(str(path))
    assert db.schema_version == dbmod.SCHEMA_VERSION
    g = db.get_game(slug="dd2")
    assert g["title_search"] == "dragons dogma 2"
    assert db.list_games(q="dragons dogma")[1] == 1
    assert g["user_summary"]["method"] == "extractive" and "botched remaster process" not in g["user_summary"]["dislikes"]
    assert g["critic_summary"]["method"] == "llm"
    assert g["letsplay"]["method"] == "description" and g["letsplay"]["pros"] == []
    assert g["letsplay"]["summary"] == "Full walkthrough of the game."
    assert [x["slug"] for x in db.games_with_fallbacks(5)] == ["dd2"]
    kh = db.get_game(slug="kh")["letsplay"]
    assert kh["updated_at"].startswith("2000-") and kh["checked_at"].startswith("2000-") and kh["summary"]
    assert kh["recheck"] is True and g["letsplay"]["recheck"] is True


def test_migration_resets_videos_of_other_games_and_flags_unverified_ones(tmp_path):
    from app import db as dbmod

    path = tmp_path / "v5.db"
    old = Database(str(path))
    stamp = "2026-10-15T04:00:00+00:00"
    games = [
        (1, "rock-hunter", "Rock Hunter",
         {"video_id": "mh4aaaaaaaa", "title": "Monster Hunter 4 Ultimate - Part 12 Let's ROCK Basarios",
          "published": "11y ago"}, None),
        (2, "haunt", "HAUNT",
         {"video_id": "hauntoldaaa", "title": "HAUNT - Full Playthrough", "published": "6y ago"},
         {"video_id": "eternalhaun", "title": "Eternal Haunt - Official Gameplay Trailer", "views": 10,
          "length_seconds": 90, "published": "11mo ago"}),
        (3, "the-screamer", "The Screamer",
         {"video_id": "magmaaaaaaa", "title": "Magma Juega MINECRAFT, The SCREAMER", "published": "11mo ago"},
         {"video_id": "screameraaa", "title": "Screamer - Official Gameplay Mechanics Overview Trailer",
          "views": 10, "length_seconds": 90, "published": "11mo ago"}),
        (4, "valor-mortis", "Valor Mortis",
         {"video_id": "valoraaaaaa", "title": "Valor Mortis is Great", "published": "4mo ago"},
         {"video_id": "valortraile", "title": "Valor Mortis - Official Story Trailer | gamescom 2026", "views": 10,
          "length_seconds": 90, "published": "1mo ago"}),
        (6, "kaebon", "KAEBON", None, None),
        (5, "bathyscaphe", "Bathyscaphe", None,
         {"video_id": "bathytraile", "title": "Bathyscaphe Trailer", "views": 10, "length_seconds": 90,
          "published": "17y ago"}),
    ]
    for gid, slug, title, lp_video, tr_video in games:
        game = make_game(gid, slug, title)
        game.release_date = "2026-10-15"
        old.upsert_game(game, "x")
        if gid == 6:
            old.set_json(gid, "letsplay", {"status": "no_video", "method": "none", "updated_at": stamp,
                                           "note": "Летсплеи по игре на YouTube есть, но ни в одном названии нет "
                                                   "полного названия «KAEBON»."})
        if lp_video:
            old.set_json(gid, "letsplay", {"status": "ok", "method": "llm-transcript", "has_commentary": True,
                                           "summary": "Игра «X» предлагает интересный геймплей.", "video": lp_video,
                                           "updated_at": stamp, "checked_at": stamp})
        if tr_video:
            old.set_json(gid, "trailer", {"status": "ok", "source": "youtube", "video": tr_video,
                                          "updated_at": stamp, "checked_at": stamp})
    old.close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {dbmod.SCHEMA_VERSION - 1}")
    raw.commit()
    raw.close()
    db = Database(str(path))
    rock = db.get_game(slug="rock-hunter")["letsplay"]
    assert rock["status"] == "no_video" and "video" not in rock and "summary" not in rock and rock["recheck"] is True
    assert rock["rejected"][0]["video_id"] == "mh4aaaaaaaa" and "нет названия игры" in rock["rejected"][0]["reason"]
    haunt = db.get_game(slug="haunt")
    assert haunt["letsplay"]["status"] == "no_video" and "за год" in haunt["letsplay"]["rejected"][0]["reason"]
    assert haunt["trailer"]["status"] == "none" and haunt["trailer"]["recheck"] is True
    screamer = db.get_game(slug="the-screamer")
    assert screamer["letsplay"]["status"] == "no_video" and screamer["letsplay"]["recheck"] is True
    assert screamer["trailer"]["status"] == "none"
    valor = db.get_game(slug="valor-mortis")
    assert valor["letsplay"]["status"] == "ok" and valor["letsplay"]["recheck"] is True
    assert valor["trailer"]["status"] == "ok" and "recheck" not in valor["trailer"]
    assert db.get_game(slug="bathyscaphe")["trailer"]["status"] == "none"
    assert db.get_game(slug="kaebon")["letsplay"]["recheck"] is True
    assert sorted(g["slug"] for g in db.games_needing_video_recheck(10)) == [
        "bathyscaphe", "haunt", "kaebon", "rock-hunter", "the-screamer", "valor-mortis"]


def test_failed_games_are_retryable_until_the_cap(db):
    day = "2026-10-14"
    assert db.mark_processed(day, 1, "a", "A", 1, "error", "new-releases") == 1
    db.mark_processed(day, 2, "b", "B", 1, "ok", "new-releases")
    assert db.processed_ids(day, max_attempts=2) == {2}
    assert [r["game_id"] for r in db.retry_candidates(day, 2)] == [1]
    assert db.mark_processed(day, 1, "a", "A", 2, "error", "retry") == 2
    assert db.processed_ids(day, max_attempts=2) == {1, 2}
    assert db.retry_candidates(day, 2) == []
    db.mark_processed(day, 3, "c", "C", 2, "error", "browse:p1")
    db.mark_processed(day, 3, "c", "C", 3, "ok", "retry")
    assert 3 in db.processed_ids(day, 2)
    with db.conn() as c:
        assert c.execute("SELECT source FROM processed WHERE game_id=3").fetchone()[0] == "browse:p1"


def test_same_slug_with_new_id_replaces_old_row(db):
    db.upsert_game(make_game(111, "remake"), "x")
    assert db.upsert_game(make_game(222, "remake"), "x") is True
    assert db.get_game(slug="remake")["id"] == 222
    assert db.get_game(game_id=111) is None
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM platforms WHERE game_id=111").fetchone()[0] == 0
    assert db.count_games() == 1


def test_connection_pool_is_bounded(tmp_path):
    db = Database(str(tmp_path / "pool.db"), pool_size=4)
    barrier = threading.Barrier(8, timeout=5)

    def work(i):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        for _ in range(20):
            db.count_games()
            db.add_error(None, "2026-10-14", None, "t", f"e{i}")
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        assert all(pool.map(work, range(32)))
    assert db.open_connections <= 4
    assert db.error_count("2026-10-14") == 32 * 20
    db.close()
    with pytest.raises(RuntimeError):
        db.count_games()


def test_memory_database_works():
    db = Database(":memory:")
    db.upsert_game(make_game(), "x")
    assert db.count_games() == 1 and db.open_connections == 1


def test_pruning_removes_old_errors_and_processed_rows(db):
    with db.write() as c:
        c.execute("INSERT INTO errors(run_id, day, slug, step, message, at) VALUES (1, '2026-09-01', NULL, 's', 'old', ?)",
                  ((NOW - timedelta(days=40)).isoformat(),))
        c.execute("INSERT INTO processed(day, game_id, slug, status, processed_at) VALUES ('2026-08-01', 1, 'a', 'ok', 'x')")
    db.add_error(1, "2026-10-14", None, "s", "fresh")
    db.mark_processed("2026-10-14", 2, "b", "B", 1, "ok", "x")
    removed = db.prune(NOW, errors_keep_days=14, processed_keep_days=30)
    assert removed["errors"] == 1 and removed["processed"] == 1
    assert db.error_count() == 1 and db.processed_count("2026-10-14") == 1


def test_lease_is_exclusive_until_it_expires(db):
    assert db.acquire_lease("pipeline", "A", 90, NOW)
    assert not db.acquire_lease("pipeline", "B", 90, NOW + timedelta(seconds=30))
    assert db.lease_holder("pipeline", NOW + timedelta(seconds=30)) == "A"
    assert db.renew_lease("pipeline", "A", 90, NOW + timedelta(seconds=60))
    assert not db.acquire_lease("pipeline", "B", 90, NOW + timedelta(seconds=120))
    assert db.acquire_lease("pipeline", "B", 90, NOW + timedelta(seconds=200))
    assert not db.renew_lease("pipeline", "A", 90, NOW + timedelta(seconds=201))
    db.release_lease("pipeline", "A")
    assert db.lease_holder("pipeline", NOW + timedelta(seconds=201)) == "B"
    db.release_lease("pipeline", "B")
    assert db.lease_holder("pipeline", NOW + timedelta(seconds=201)) is None


def test_only_runs_without_a_recent_heartbeat_are_closed(db):
    alive = db.create_run("manual", "2026-10-14", "A", NOW)
    stale = db.create_run("schedule", "2026-10-14", "B", NOW - timedelta(minutes=10))
    assert db.close_stale_runs(NOW + timedelta(seconds=30), ttl_seconds=90) == 1
    runs = {r["id"]: r for r in db.recent_runs()}
    assert runs[alive]["status"] == "running"
    assert runs[stale]["status"] == "interrupted" and runs[stale]["message"]
    db.heartbeat_run(alive, NOW + timedelta(seconds=80))
    assert db.close_stale_runs(NOW + timedelta(seconds=150), ttl_seconds=90) == 0
    assert db.close_stale_runs(NOW + timedelta(seconds=200), ttl_seconds=90) == 1


def test_backfill_candidates_rotate_by_last_attempt(db):
    for gid, slug in ((1, "a"), (2, "b"), (3, "c")):
        db.upsert_game(make_game(gid, slug), "x")
        db.set_json(gid, "critic_summary", {"method": "extractive"})
    db.mark_backfill_attempt(1)
    order = [g["id"] for g in db.games_with_fallbacks(10)]
    assert order[-1] == 1 and set(order) == {1, 2, 3}


def test_new_database_starts_from_the_seed_snapshot(tmp_path, monkeypatch):
    from app.db import Database
    seed = tmp_path / "seed.db"
    src = Database(str(seed))
    src.close()
    monkeypatch.setenv("SEED_DB_PATH", str(seed))
    target = tmp_path / "volume" / "games.db"
    Database(str(target)).close()
    assert target.exists() and target.read_bytes()[:16] == b"SQLite format 3\x00"
    import sqlite3
    con = sqlite3.connect(target)
    con.execute("CREATE TABLE marker (x INTEGER)")
    con.commit()
    con.close()
    Database(str(target)).close()
    con = sqlite3.connect(target)
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='marker'").fetchone()[0] == 1
    con.close()
