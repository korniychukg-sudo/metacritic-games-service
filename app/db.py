import json
import logging
import os
import queue
import re
import shutil
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import iso, utcnow
from .metacritic import GameDetails

log = logging.getLogger("db")

BASE_SCHEMA = """
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

MIGRATIONS: list[list[str]] = [
    [
        "ALTER TABLE processed ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1",
        "CREATE INDEX IF NOT EXISTS idx_processed_day_status ON processed(day, status)",
        "CREATE INDEX IF NOT EXISTS idx_errors_day ON errors(day)",
        "CREATE INDEX IF NOT EXISTS idx_errors_at ON errors(at)",
    ],
    [
        "ALTER TABLE runs ADD COLUMN owner TEXT",
        "ALTER TABLE runs ADD COLUMN heartbeat_at TEXT",
        "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
        "CREATE INDEX IF NOT EXISTS idx_runs_day_trigger ON runs(day, trigger)",
        "CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL)",
    ],
    [
        "ALTER TABLE games ADD COLUMN backfill_at TEXT",
        "ALTER TABLE games ADD COLUMN backfill_attempts INTEGER NOT NULL DEFAULT 0",
    ],
    [
        "ALTER TABLE games ADD COLUMN trailer TEXT",
    ],
    [
        "fix_title_search",
        "fix_non_russian_summaries",
        "fix_description_letsplays",
        "UPDATE games SET letsplay = json_set(letsplay, '$.updated_at', '2000-01-01T00:00:00+00:00', "
        "'$.checked_at', '2000-01-01T00:00:00+00:00') "
        "WHERE json_extract(letsplay, '$.method') IN ('llm-transcript', 'llm-whisper') "
        "AND json_extract(letsplay, '$.has_commentary') IS NULL",
    ],
    [
        "fix_wrong_game_videos",
    ],
]

RESET_LETSPLAY_NOTE = ("Найденный раньше ролик не прошёл новую проверку: в его оригинальном названии нет названия игры "
                       "или он опубликован задолго до её выхода. Летсплей будет найден заново")


def fix_title_search(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, title FROM games").fetchall()
    conn.executemany("UPDATE games SET title_search=? WHERE id=?", [(search_key(r[1]), r[0]) for r in rows])


def fix_non_russian_summaries(conn: sqlite3.Connection) -> None:
    from .metacritic import Review
    from .summarize import extractive_review_summary, extractive_transcript_summary, is_russian

    def russian(summary: dict, fields: tuple[str, ...]) -> bool:
        for f in fields:
            value = summary.get(f)
            for item in (value if isinstance(value, list) else [value]):
                if item and not is_russian(item):
                    return False
        return True

    rows = conn.execute("SELECT id, critic_summary, user_summary, critic_reviews, user_reviews, letsplay "
                        "FROM games").fetchall()
    for row in rows:
        gid = row[0]
        for column, kind, raw, reviews_raw in (("critic_summary", "critic", row[1], row[3]),
                                               ("user_summary", "user", row[2], row[4])):
            summary = json.loads(raw) if raw else None
            if not summary or summary.get("method") != "llm" or russian(summary, ("likes", "dislikes", "verdict")):
                continue
            reviews = [Review(**r) for r in json.loads(reviews_raw or "[]")]
            if not reviews:
                continue
            fallback = extractive_review_summary(reviews, kind, summary.get("review_count") or len(reviews))
            fixed = {k: v for k, v in summary.items() if k not in ("model",)}
            fixed.update(fallback)
            fixed["note"] = "LLM ответила не на русском, показаны цитаты; резюме пересчитается через LLM"
            conn.execute(f"UPDATE games SET {column}=? WHERE id=?", (_dumps(fixed), gid))
        lp = json.loads(row[5]) if row[5] else None
        if (lp and lp.get("method") in ("llm-transcript", "llm-whisper") and lp.get("transcript_excerpt")
                and not russian(lp, ("summary", "pros", "cons", "verdict"))):
            source = lp["method"].split("-", 1)[1]
            lp.update(extractive_transcript_summary(lp["transcript_excerpt"]), method=f"extractive-{source}")
            conn.execute("UPDATE games SET letsplay=? WHERE id=?", (_dumps(lp), gid))


def fix_description_letsplays(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, letsplay FROM games WHERE json_extract(letsplay, '$.method') = 'llm-description'")
    for gid, raw in rows.fetchall():
        lp = json.loads(raw)
        description = ((lp.get("video") or {}).get("description") or "")[:600]
        lp.update(method="description", summary=description, pros=[], cons=[], verdict="")
        conn.execute("UPDATE games SET letsplay=? WHERE id=?", (_dumps(lp), gid))


def fix_wrong_game_videos(conn: sqlite3.Connection) -> None:
    from .youtube import parse_day, recheck_letsplay, stored_candidate, trailer_problem

    now = iso(utcnow())
    today = utcnow().date()
    dates: dict[int, list[str]] = {}
    for gid, day in conn.execute("SELECT game_id, release_date FROM platforms WHERE release_date IS NOT NULL"):
        dates.setdefault(gid, []).append(day)
    rows = conn.execute("SELECT id, title, release_date, letsplay, trailer FROM games").fetchall()
    for gid, title, release_date, lp_raw, tr_raw in rows:
        days = [d for d in (parse_day(x) for x in [release_date, *dates.get(gid, [])]) if d]
        anchor = min(days).isoformat() if days else None
        lp = json.loads(lp_raw) if lp_raw else None
        if lp and lp.get("video"):
            video = lp["video"]
            ref = parse_day(lp.get("updated_at")) or today
            reason = recheck_letsplay(title, stored_candidate(video), anchor, ref)
            if reason:
                lp = {"status": "no_video", "method": "none", "candidates": [], "updated_at": now, "checked_at": now,
                      "recheck": True, "note": RESET_LETSPLAY_NOTE,
                      "rejected": [{"video_id": video.get("video_id"), "title": video.get("title"), "reason": reason}]}
            elif "about_this_game" not in lp:
                lp["recheck"] = True
            conn.execute("UPDATE games SET letsplay=? WHERE id=?", (_dumps(lp), gid))
        elif lp and lp.get("status") == "no_video" and str(lp.get("note", "")).startswith("Летсплеи по игре на YouTube"):
            lp["recheck"] = True
            conn.execute("UPDATE games SET letsplay=? WHERE id=?", (_dumps(lp), gid))
        tr = json.loads(tr_raw) if tr_raw else None
        if tr and tr.get("status") == "ok" and tr.get("video"):
            video = tr["video"]
            ref = parse_day(tr.get("updated_at")) or today
            reason = trailer_problem(title, stored_candidate(video), anchor, ref,
                                     strict_edition=not video.get("base_game"))
            if reason:
                tr = {"status": "none", "updated_at": now, "checked_at": now, "recheck": True,
                      "rejected": {"video_id": video.get("video_id"), "title": video.get("title"), "reason": reason}}
                conn.execute("UPDATE games SET trailer=? WHERE id=?", (_dumps(tr), gid))


DATA_FIXES = {
    "fix_title_search": fix_title_search,
    "fix_non_russian_summaries": fix_non_russian_summaries,
    "fix_description_letsplays": fix_description_letsplays,
    "fix_wrong_game_videos": fix_wrong_game_videos,
}

SCHEMA_VERSION = len(MIGRATIONS)

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
               "critic_summary", "user_summary", "letsplay", "similar", "trailer")
JSON_COLUMNS = ("critic_summary", "user_summary", "letsplay", "similar", "trailer")


@dataclass
class DayState:
    day: str
    new_releases_done: bool = False
    browse_offset: int = 0


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def search_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"['’`.,]", "", text)
    text = re.sub(r"[^\w%+#&]+|_", " ", text)
    return " ".join(text.split())


def search_tokens(query: str) -> list[str]:
    return search_key(query).split()[:8]


def like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SchemaTooNew(RuntimeError):
    pass


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaTooNew(f"database schema v{version} is newer than this code (v{SCHEMA_VERSION})")
        if version == 0:
            for statement in BASE_SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
        for number in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[number - 1]:
                if statement in DATA_FIXES:
                    DATA_FIXES[statement](conn)
                else:
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            log.info("База обновлена до схемы v%d", number)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return conn.execute("PRAGMA user_version").fetchone()[0]


SEED_DB_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed", "games.db")


def seed_from_snapshot(path: str) -> bool:
    seed = os.environ.get("SEED_DB_PATH", SEED_DB_DEFAULT)
    if not seed or os.path.exists(path) or not os.path.isfile(seed):
        return False
    if os.path.abspath(seed) == os.path.abspath(path):
        return False
    shutil.copyfile(seed, path)
    log.info("Новая база создана из снимка %s", seed)
    return True


class Database:
    def __init__(self, path: str, pool_size: int = 8):
        self.path = path
        self.memory = path == ":memory:"
        if not self.memory:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            seed_from_snapshot(path)
        self.pool_size = 1 if self.memory else max(1, pool_size)
        self._idle: queue.LifoQueue = queue.LifoQueue()
        self._slots = threading.BoundedSemaphore(self.pool_size)
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closed = False
        boot = self._connect(autocommit=True)
        try:
            self.schema_version = migrate(boot)
        finally:
            if self.memory:
                boot.isolation_level = ""
                self._register(boot)
                self._idle.put(boot)
            else:
                boot.close()

    def _connect(self, autocommit: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False,
                               isolation_level=None if autocommit else "")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if not self.memory:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _register(self, conn: sqlite3.Connection) -> None:
        with self._all_lock:
            self._all.append(conn)

    @property
    def open_connections(self) -> int:
        with self._all_lock:
            return len(self._all)

    @contextmanager
    def conn(self):
        if self._closed:
            raise RuntimeError("database is closed")
        self._slots.acquire()
        try:
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                conn = self._connect()
                self._register(conn)
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                self._idle.put(conn)
        finally:
            self._slots.release()

    @contextmanager
    def write(self):
        with self._write_lock, self.conn() as c:
            yield c

    def close(self) -> None:
        self._closed = True
        with self._all_lock:
            conns, self._all = self._all, []
        for c in conns:
            try:
                c.close()
            except Exception:
                pass

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

    def processed_ids(self, day: str, max_attempts: int = 1) -> set[int]:
        with self.conn() as c:
            return {r[0] for r in c.execute(
                "SELECT game_id FROM processed WHERE day=? AND (status != 'error' OR attempts >= ?)",
                (day, max_attempts))}

    def retry_candidates(self, day: str, max_attempts: int) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT game_id, slug, title, source, attempts FROM processed "
                "WHERE day=? AND status='error' AND attempts < ? ORDER BY processed_at", (day, max_attempts))]

    def mark_processed(self, day: str, game_id: int, slug: str, title: str, run_id: int | None, status: str,
                       source: str) -> int:
        with self.write() as c:
            c.execute(
                "INSERT INTO processed(day, game_id, slug, title, run_id, status, source, processed_at, attempts) "
                "VALUES (?,?,?,?,?,?,?,?,1) ON CONFLICT(day, game_id) DO UPDATE SET status=excluded.status, "
                "run_id=excluded.run_id, processed_at=excluded.processed_at, attempts=processed.attempts+1",
                (day, game_id, slug, title, run_id, status, source, iso(utcnow())),
            )
            return c.execute("SELECT attempts FROM processed WHERE day=? AND game_id=?", (day, game_id)).fetchone()[0]

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
                    log.info("Адрес %s перешёл от игры %s к игре %s, старая запись заменена", g.slug, clash[0], g.id)
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
        if column not in JSON_COLUMNS:
            raise ValueError(column)
        with self.write() as c:
            c.execute(f"UPDATE games SET {column}=? WHERE id=?", (_dumps(value), game_id))

    def mark_backfill_attempt(self, game_id: int) -> None:
        with self.write() as c:
            c.execute("UPDATE games SET backfill_at=?, backfill_attempts=backfill_attempts+1 WHERE id=?",
                      (iso(utcnow()), game_id))

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

    def games_with_fallbacks(self, limit: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, slug, title, critic_summary, user_summary, letsplay, critic_reviews, user_reviews, "
                "backfill_at, backfill_attempts FROM games "
                "WHERE json_extract(critic_summary, '$.method') = 'extractive' "
                "OR json_extract(user_summary, '$.method') = 'extractive' "
                "OR (json_extract(letsplay, '$.method') IN ('extractive-transcript', 'extractive-whisper') "
                "    AND json_extract(letsplay, '$.transcript_excerpt') IS NOT NULL "
                "    AND json_extract(letsplay, '$.llm_no_commentary') IS NULL) "
                "OR (json_extract(letsplay, '$.method') = 'description' "
                "    AND json_extract(letsplay, '$.video.description') IS NOT NULL) "
                "ORDER BY backfill_at IS NOT NULL, backfill_at, updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_game(r) for r in rows]

    def games_needing_video_recheck(self, limit: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, slug, title, release_date, video_embed_url, letsplay, trailer FROM games "
                "WHERE json_extract(letsplay, '$.recheck') = 1 OR json_extract(trailer, '$.recheck') = 1 "
                "ORDER BY coalesce(json_extract(letsplay, '$.checked_at'), json_extract(trailer, '$.checked_at'), ''), "
                "id LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                g = dict(r)
                for f in ("letsplay", "trailer"):
                    g[f] = json.loads(g[f]) if g[f] else None
                g["platform_dates"] = [d[0] for d in c.execute(
                    "SELECT release_date FROM platforms WHERE game_id=? AND release_date IS NOT NULL", (g["id"],))]
                out.append(g)
        return out

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
        tokens = search_tokens(q or "")
        if tokens:
            where = "WHERE " + " AND ".join("g.title_search LIKE ? ESCAPE '\\'" for _ in tokens)
            params.extend(f"%{like_escape(t)}%" for t in tokens)
        elif q and q.strip():
            where = "WHERE 0"
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

    def create_run(self, trigger: str, day: str, owner: str | None = None, now: datetime | None = None) -> int:
        stamp = iso(now or utcnow())
        with self.write() as c:
            cur = c.execute(
                "INSERT INTO runs(trigger, day, started_at, status, owner, heartbeat_at) VALUES (?,?,?,?,?,?)",
                (trigger, day, stamp, "running", owner, stamp))
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str, selected: int, ok: int, failed: int, sources: str,
                   message: str = "", now: datetime | None = None) -> None:
        with self.write() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, status=?, selected=?, ok=?, failed=?, sources=?, message=? WHERE id=?",
                (iso(now or utcnow()), status, selected, ok, failed, sources, message[:500], run_id),
            )

    def update_run_progress(self, run_id: int, selected: int, ok: int, failed: int, sources: str) -> None:
        with self.write() as c:
            c.execute("UPDATE runs SET selected=?, ok=?, failed=?, sources=?, heartbeat_at=? WHERE id=?",
                      (selected, ok, failed, sources, iso(utcnow()), run_id))

    def heartbeat_run(self, run_id: int, now: datetime | None = None) -> None:
        with self.write() as c:
            c.execute("UPDATE runs SET heartbeat_at=? WHERE id=? AND status='running'", (iso(now or utcnow()), run_id))

    def close_stale_runs(self, now: datetime | None = None, ttl_seconds: int = 90) -> int:
        now = now or utcnow()
        threshold = iso(now - timedelta(seconds=ttl_seconds))
        with self.write() as c:
            cur = c.execute(
                "UPDATE runs SET status='interrupted', finished_at=?, "
                "message=COALESCE(NULLIF(message, ''), 'Процесс остановился посреди запуска') "
                "WHERE status='running' AND COALESCE(heartbeat_at, started_at) < ?",
                (iso(now), threshold))
            return cur.rowcount

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def public_runs(self, limit: int = 10) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, trigger, day, started_at, finished_at, status, selected, ok, failed, sources, message "
                "FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def last_run(self) -> dict | None:
        runs = self.recent_runs(1)
        return runs[0] if runs else None

    def last_finished_at(self) -> str | None:
        with self.conn() as c:
            row = c.execute("SELECT MAX(finished_at) FROM runs WHERE finished_at IS NOT NULL").fetchone()
        return row[0] if row else None

    def count_runs(self, day: str, trigger: str) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM runs WHERE day=? AND trigger=?", (day, trigger)).fetchone()[0]

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

    def prune(self, now: datetime, errors_keep_days: int, processed_keep_days: int) -> dict:
        errors_before = iso(now - timedelta(days=errors_keep_days))
        processed_before = (now - timedelta(days=processed_keep_days)).date().isoformat()
        with self.write() as c:
            e = c.execute("DELETE FROM errors WHERE at < ?", (errors_before,)).rowcount
            p = c.execute("DELETE FROM processed WHERE day < ?", (processed_before,)).rowcount
            d = c.execute("DELETE FROM day_state WHERE day < ?", (processed_before,)).rowcount
        return {"errors": e, "processed": p, "day_state": d}

    def acquire_lease(self, name: str, owner: str, ttl_seconds: int, now: datetime | None = None) -> bool:
        now = now or utcnow()
        with self.write() as c:
            c.execute(
                "INSERT INTO leases(name, owner, acquired_at, expires_at) VALUES (?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, acquired_at=excluded.acquired_at, "
                "expires_at=excluded.expires_at WHERE leases.expires_at < ? OR leases.owner = excluded.owner",
                (name, owner, iso(now), iso(now + timedelta(seconds=ttl_seconds)), iso(now)))
            row = c.execute("SELECT owner FROM leases WHERE name=?", (name,)).fetchone()
        return bool(row) and row[0] == owner

    def renew_lease(self, name: str, owner: str, ttl_seconds: int, now: datetime | None = None) -> bool:
        now = now or utcnow()
        with self.write() as c:
            cur = c.execute("UPDATE leases SET expires_at=? WHERE name=? AND owner=?",
                            (iso(now + timedelta(seconds=ttl_seconds)), name, owner))
            return cur.rowcount == 1

    def release_lease(self, name: str, owner: str) -> None:
        with self.write() as c:
            c.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))

    def lease_holder(self, name: str, now: datetime | None = None) -> str | None:
        now = now or utcnow()
        with self.conn() as c:
            row = c.execute("SELECT owner FROM leases WHERE name=? AND expires_at >= ?", (name, iso(now))).fetchone()
        return row[0] if row else None
