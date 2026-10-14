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
    assert db.list_games(q="_")[1] == 1
    assert db.list_games(q="100%")[0][0]["slug"] == "a"
    assert db.list_games(q="e_p")[1] == 1
    assert db.list_games(q="   ")[1] == 3
    assert db.list_games(q="\\")[1] == 0


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
