import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass

from .config import iso, utcnow
from .metacritic import GameDetails

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    title_search TEXT NOT NULL,
    release_date TEXT,
    description TEXT,
    developers TEXT NOT NULL DEFAULT '[]',
    publishers TEXT NOT NULL DEFAULT '[]',
    genres TEXT NOT NULL DEFAULT '[]',
    franchises TEXT NOT NULL DEFAULT '[]',
    rating TEXT,
    cover_url TEXT,
    hero_url TEXT,
    video_embed_url TEXT,
    video_manifest_url TEXT,
    video_title TEXT,
    lead_platform TEXT,
    metascore INTEGER,
    userscore REAL,
    critic_total INTEGER NOT NULL DEFAULT 0,
    user_total INTEGER NOT NULL DEFAULT 0,
    critic_reviews TEXT NOT NULL DEFAULT '[]',
    user_reviews TEXT NOT NULL DEFAULT '[]',
    critic_summary TEXT,
    user_summary TEXT,
    letsplay TEXT,
    similar TEXT NOT NULL DEFAULT '[]',
    source TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    times_processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_games_metascore ON games(metascore);
CREATE INDEX IF NOT EXISTS idx_games_userscore ON games(userscore);
CREATE INDEX IF NOT EXISTS idx_games_release ON games(release_date);

CREATE TABLE IF NOT EXISTS platforms (
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    short TEXT NOT NULL,
    is_lead INTEGER NOT NULL DEFAULT 0,
    metascore INTEGER,
    critic_count INTEGER,
    userscore REAL,
    user_count INTEGER,
    release_date TEXT,
    PRIMARY KEY (game_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_platforms_name ON platforms(name);

CREATE TABLE IF NOT EXISTS day_state (
    day TEXT PRIMARY KEY,
    new_releases_done INTEGER NOT NULL DEFAULT 0,
    browse_offset INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed (
    day TEXT NOT NULL,
    game_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    title TEXT,
    run_id INTEGER,
    status TEXT NOT NULL,
    source TEXT,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (day, game_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    day TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    sources TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    day TEXT NOT NULL,
    slug TEXT,
    step TEXT,
    message TEXT NOT NULL,
    at TEXT NOT NULL
);
"""

SORTS = {
    "metascore_desc": ("score_meta", "DESC"),
    "metascore_asc": ("score_meta", "ASC"),
    "userscore_desc": ("score_user", "DESC"),
    "userscore_asc": ("score_user", "ASC"),
    "date_desc": ("g.release_date", "DESC"),
    "date_asc": ("g.release_date", "ASC"),
    "title": ("g.title_search", "ASC"),
    "updated": ("g.updated_at", "DESC"),
}

JSON_FIELDS = ("developers", "publishers", "genres", "franchises", "critic_reviews", "user_reviews",
               "critic_summary", "user_summary", "letsplay", "similar")


@dataclass
class DayState:
    day: str
    new_releases_done: bool = False
    browse_offset: int = 0


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def search_key(text: str) -> str:
    return " ".join((text or "").casefold().split())


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def write(self):
        with self._write_lock, self.conn() as c:
            yield c

    def get_day_state(self, day: str) -> DayState:
        with self.conn() as c:
            row = c.execute("SELECT * FROM day_state WHERE day=?", (day,)).fetchone()
        if not row:
            return DayState(day=day)
        return DayState(day=day, new_releases_done=bool(row["new_releases_done"]), browse_offset=row["browse_offset"])

    def save_day_state(self, state: DayState) -> None:
        with self.write() as c:
            c.execute(
                "INSERT INTO day_state(day, new_releases_done, browse_offset, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET new_releases_done=excluded.new_releases_done, "
                "browse_offset=excluded.browse_offset, updated_at=excluded.updated_at",
                (state.day, int(state.new_releases_done), state.browse_offset, iso(utcnow())),
            )

    def processed_ids(self, day: str) -> set[int]:
        with self.conn() as c:
            return {r[0] for r in c.execute("SELECT game_id FROM processed WHERE day=?", (day,))}

    def mark_processed(self, day: str, game_id: int, slug: str, title: str, run_id: int | None, status: str,
                       source: str) -> None:
        with self.write() as c:
            c.execute(
                "INSERT INTO processed(day, game_id, slug, title, run_id, status, source, processed_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(day, game_id) DO UPDATE SET status=excluded.status, "
                "run_id=excluded.run_id, processed_at=excluded.processed_at",
                (day, game_id, slug, title, run_id, status, source, iso(utcnow())),
            )

    def processed_count(self, day: str, status: str | None = None) -> int:
        with self.conn() as c:
            if status:
                return c.execute("SELECT COUNT(*) FROM processed WHERE day=? AND status=?", (day, status)).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM processed WHERE day=?", (day,)).fetchone()[0]

    def upsert_game(self, g: GameDetails, source: str) -> bool:
        now = iso(utcnow())
        with self.write() as c:
            exists = c.execute("SELECT 1 FROM games WHERE id=?", (g.id,)).fetchone() is not None
            if not exists:
                clash = c.execute("SELECT id FROM games WHERE slug=?", (g.slug,)).fetchone()
                if clash:
                    c.execute("DELETE FROM games WHERE id=?", (clash[0],))
            values = {
                "id": g.id,
                "slug": g.slug,
                "title": g.title,
                "title_search": search_key(g.title),
                "release_date": g.release_date,
                "description": g.description,
                "developers": _dumps(g.developers),
                "publishers": _dumps(g.publishers),
                "genres": _dumps(g.genres),
                "franchises": _dumps(g.franchises),
                "rating": g.rating,
                "cover_url": g.cover_url,
                "hero_url": g.hero_url,
                "video_embed_url": g.video_embed_url,
                "video_manifest_url": g.video_manifest_url,
                "video_title": g.video_title,
                "lead_platform": g.lead_platform,
                "metascore": g.metascore,
                "userscore": g.userscore,
                "critic_total": g.critic_total,
                "user_total": g.user_total,
                "critic_reviews": _dumps([r.to_dict() for r in g.critic_reviews]),
                "user_reviews": _dumps([r.to_dict() for r in g.user_reviews]),
                "source": source,
                "updated_at": now,
            }
            if exists:
                sets = ", ".join(f"{k}=:{k}" for k in values if k != "id")
                c.execute(f"UPDATE games SET {sets}, times_processed=times_processed+1 WHERE id=:id", values)
            else:
                values["first_seen_at"] = now
                values["times_processed"] = 1
                cols = ", ".join(values)
                c.execute(f"INSERT INTO games({cols}) VALUES ({', '.join(':' + k for k in values)})", values)
            c.execute("DELETE FROM platforms WHERE game_id=?", (g.id,))
            c.executemany(
                "INSERT INTO platforms(game_id, slug, name, short, is_lead, metascore, critic_count, userscore, "
                "user_count, release_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (g.id, p.slug, p.name, p.short, int(p.is_lead), p.metascore, p.critic_count, p.userscore,
                     p.user_count, p.release_date)
                    for p in g.platforms
                ],
            )
        return not exists

    def set_json(self, game_id: int, column: str, value) -> None:
        if column not in ("critic_summary", "user_summary", "letsplay", "similar"):
            raise ValueError(column)
        with self.write() as c:
            c.execute(f"UPDATE games SET {column}=? WHERE id=?", (_dumps(value), game_id))

    def set_similar_bulk(self, mapping: dict[int, list[dict]]) -> None:
        with self.write() as c:
            c.executemany("UPDATE games SET similar=? WHERE id=?", [(_dumps(v), k) for k, v in mapping.items()])

    @staticmethod
    def _row_to_game(row: sqlite3.Row) -> dict:
        g = dict(row)
        for f in JSON_FIELDS:
            if f in g:
                g[f] = json.loads(g[f]) if g[f] else ([] if f in ("similar",) else None)
        return g

    def get_game(self, slug: str | None = None, game_id: int | None = None) -> dict | None:
        with self.conn() as c:
            if slug is not None:
                row = c.execute("SELECT * FROM games WHERE slug=?", (slug,)).fetchone()
            else:
                row = c.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
            if not row:
                return None
            g = self._row_to_game(row)
            g["platforms"] = [dict(p) for p in c.execute(
                "SELECT * FROM platforms WHERE game_id=? ORDER BY is_lead DESC, name", (g["id"],))]
        return g

    def get_previous(self, game_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT critic_summary, user_summary, letsplay FROM games WHERE id=?", (game_id,)).fetchone()
        if not row:
            return None
        return {k: (json.loads(row[k]) if row[k] else None) for k in row.keys()}

    def games_with_fallbacks(self, limit: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, slug, title, critic_summary, user_summary, letsplay, critic_reviews, user_reviews "
                "FROM games WHERE json_extract(critic_summary, '$.method') = 'extractive' "
                "OR json_extract(user_summary, '$.method') = 'extractive' "
                "OR json_extract(letsplay, '$.method') = 'extractive-transcript' "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_game(r) for r in rows]

    def all_features(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT id, title, description, genres, franchises, developers FROM games").fetchall()
            plats: dict[int, list[str]] = {}
            for p in c.execute("SELECT game_id, name FROM platforms"):
                plats.setdefault(p[0], []).append(p[1])
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"] or "",
                "genres": json.loads(r["genres"]),
                "franchises": json.loads(r["franchises"]),
                "developers": json.loads(r["developers"]),
                "platforms": plats.get(r["id"], []),
            }
            for r in rows
        ]

    def brief_games(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        with self.conn() as c:
            q = ",".join("?" * len(ids))
            rows = c.execute(
                f"SELECT id, slug, title, cover_url, metascore, userscore, release_date FROM games WHERE id IN ({q})",
                ids,
            ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def list_games(self, q: str | None = None, platform: str | None = None, sort: str = "date_desc",
                   limit: int = 24, offset: int = 0) -> tuple[list[dict], int]:
        sort_col, direction = SORTS.get(sort, SORTS["date_desc"])
        params: list = []
        join = ""
        meta_expr, user_expr = "g.metascore", "g.userscore"
        if platform:
            join = "JOIN platforms p ON p.game_id = g.id AND p.name = ?"
            params.append(platform)
            meta_expr, user_expr = "p.metascore", "p.userscore"
        where = ""
        if q:
            where = "WHERE g.title_search LIKE ?"
            params.append(f"%{search_key(q)}%")
        base = f"FROM games g {join} {where}"
        order = f"{sort_col} IS NULL, {sort_col} {direction}, g.release_date DESC, g.id DESC"
        sql = (
            f"SELECT g.id, g.slug, g.title, g.cover_url, g.release_date, g.developers, g.genres, g.lead_platform, "
            f"g.updated_at, {meta_expr} AS score_meta, {user_expr} AS score_user {base} ORDER BY {order} LIMIT ? OFFSET ?"
        )
        with self.conn() as c:
            total = c.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
            rows = [dict(r) for r in c.execute(sql, params + [limit, offset])]
            ids = [r["id"] for r in rows]
            plats: dict[int, list[dict]] = {}
            if ids:
                qmarks = ",".join("?" * len(ids))
                for p in c.execute(
                    f"SELECT game_id, name, short, metascore, userscore, is_lead FROM platforms "
                    f"WHERE game_id IN ({qmarks}) ORDER BY is_lead DESC, name", ids):
                    plats.setdefault(p["game_id"], []).append(dict(p))
        for r in rows:
            r["developers"] = json.loads(r["developers"])
            r["genres"] = json.loads(r["genres"])
            r["platforms"] = plats.get(r["id"], [])
        return rows, total

    def platform_options(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT name, MIN(short) AS short, COUNT(*) AS games FROM platforms GROUP BY name "
                "ORDER BY games DESC, name")]

    def count_games(self) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM games").fetchone()[0]

    def create_run(self, trigger: str, day: str) -> int:
        with self.write() as c:
            cur = c.execute("INSERT INTO runs(trigger, day, started_at, status) VALUES (?,?,?,?)",
                            (trigger, day, iso(utcnow()), "running"))
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str, selected: int, ok: int, failed: int, sources: str,
                   message: str = "") -> None:
        with self.write() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, status=?, selected=?, ok=?, failed=?, sources=?, message=? WHERE id=?",
                (iso(utcnow()), status, selected, ok, failed, sources, message[:500], run_id),
            )

    def update_run_progress(self, run_id: int, selected: int, ok: int, failed: int, sources: str) -> None:
        with self.write() as c:
            c.execute("UPDATE runs SET selected=?, ok=?, failed=?, sources=? WHERE id=?",
                      (selected, ok, failed, sources, run_id))

    def close_stale_runs(self) -> None:
        with self.write() as c:
            c.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (iso(utcnow()),))

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def last_run(self) -> dict | None:
        runs = self.recent_runs(1)
        return runs[0] if runs else None

    def add_error(self, run_id: int | None, day: str, slug: str | None, step: str, message: str) -> None:
        with self.write() as c:
            c.execute("INSERT INTO errors(run_id, day, slug, step, message, at) VALUES (?,?,?,?,?,?)",
                      (run_id, day, slug, step, message[:500], iso(utcnow())))

    def error_count(self, day: str | None = None) -> int:
        with self.conn() as c:
            if day:
                return c.execute("SELECT COUNT(*) FROM errors WHERE day=?", (day,)).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM errors").fetchone()[0]

    def recent_errors(self, limit: int = 20) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,))]
