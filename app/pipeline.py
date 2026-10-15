import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta

from .config import Settings, iso, parse_iso, utcnow
from .db import Database
from .http_client import SchemaError
from .llm import LLMClient, LLMDisabled, LLMError, LLMSkipped
from .metacritic import GameDetails, ListItem, MetacriticClient, PlatformInfo, Review
from .monitor import TRIGGER_LABELS, Monitor
from .selection import select_batch
from .similarity import GameFeatures, compute_similar
from .stt import SpeechResult, SpeechToText, SpeechUnavailable
from .summarize import (
    build_letsplay_prompt,
    extractive_transcript_summary,
    looks_like_commentary,
    reviews_hash,
    summarize_reviews,
    validate_description_json,
    validate_letsplay_json,
)
from .youtube import (
    TranscriptUnavailable,
    VideoCandidate,
    YouTubeClient,
    apply_watch_details,
    parse_day,
    recheck_letsplay,
    sample_transcript,
    stored_candidate,
    trailer_problem,
)

log = logging.getLogger("pipeline")

LEASE = "pipeline"
FULL_LETSPLAY = {"llm-transcript", "extractive-transcript", "llm-whisper", "extractive-whisper"}
TRANSCRIPT_EXTRACTIVE = {"extractive-transcript": "transcript", "extractive-whisper": "whisper"}
NO_COMMENTARY_TEXT = "в тексте почти нет речи автора (только диалоги игры, музыка или шум)"
NO_COMMENTARY_LLM = "LLM не нашла в тексте оценок игры от автора ролика (диалоги игры или геймплей без комментариев)"
NO_TEXT = "субтитров нет, речь не распознана"
WRONG_GAME_LLM = "по расшифровке ролик о другой игре (так ответила LLM)"
WRONG_GAME_DESCRIPTION = "по названию и описанию ролик о другой игре (так ответила LLM)"
NO_COMMENTARY = (NO_COMMENTARY_TEXT, NO_COMMENTARY_LLM)
WRONG_GAME = (WRONG_GAME_LLM, WRONG_GAME_DESCRIPTION)
SCALAR_KEEP = ("description", "cover_url", "hero_url", "video_embed_url", "video_manifest_url", "video_title",
               "release_date", "lead_platform", "rating")
LIST_KEEP = ("developers", "publishers", "genres", "franchises")


class AlreadyRunning(Exception):
    pass


def make_owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def ru_count(n: int, one: str, few: str, many: str) -> str:
    form = one if n % 10 == 1 and n % 100 != 11 else few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many
    return f"{n} {form}"


def grouped(reasons: list[str]) -> str:
    counts: dict[str, int] = {}
    for r in reasons:
        counts[r] = counts.get(r, 0) + 1
    return ", ".join(r if n == 1 else f"{r} ({ru_count(n, 'ролик', 'ролика', 'роликов')})" for r, n in counts.items())


def failure_note(checked: list[dict], stt_notes: list[dict], skipped: list[dict], captions: bool) -> str:
    parts = []
    if checked:
        parts.append("субтитры: " + grouped([c["reason"] for c in checked]))
    elif not captions:
        parts.append("субтитры YouTube не запрашивались (YOUTUBE_CAPTIONS=0)")
    if stt_notes:
        parts.append("Whisper: " + grouped([n["reason"] for n in stt_notes]))
    no_talk = [s for s in skipped if s["reason"] in NO_COMMENTARY]
    if no_talk:
        parts.append("без комментариев автора: " + ru_count(len(no_talk), "ролик", "ролика", "роликов"))
    other = [s for s in skipped if s["reason"] in WRONG_GAME]
    if other:
        parts.append("о другой игре: " + ru_count(len(other), "ролик", "ролика", "роликов"))
    return "Речь автора ролика не расшифрована" + (f" ({'; '.join(parts)})" if parts else "")


def release_anchor(release_date: str | None, platform_dates) -> str | None:
    days = [d for d in (parse_day(x) for x in [release_date, *(platform_dates or [])]) if d]
    return min(days).isoformat() if days else None


def letsplay_rank(lp: dict | None) -> int:
    if not lp:
        return 0
    verified = lp.get("has_commentary") is True or str(lp.get("method", "")).startswith("extractive")
    if lp.get("status") == "ok" and lp.get("method") in FULL_LETSPLAY and verified and not lp.get("llm_no_commentary"):
        return 3
    if lp.get("video"):
        return 2
    if lp.get("status") == "no_video":
        return 1
    return 0


def keep_stored_data(game: GameDetails, stored: dict | None) -> list[str]:
    notes = list(game.warnings)
    if not stored:
        return notes
    if not game.platforms and stored.get("platforms"):
        game.platforms = [
            PlatformInfo(slug=p["slug"], name=p["name"], short=p["short"], is_lead=bool(p["is_lead"]),
                         metascore=p["metascore"], critic_count=p["critic_count"], userscore=p["userscore"],
                         user_count=p["user_count"], release_date=p["release_date"])
            for p in stored["platforms"]
        ]
        notes.append("Metacritic не вернул платформы, оставлены платформы и оценки из базы")
    for kind, label in (("critic", "критиков"), ("user", "игроков")):
        reviews = getattr(game, f"{kind}_reviews")
        known = getattr(game, f"{kind}_reviews_known")
        total = getattr(game, f"{kind}_total")
        stored_reviews = stored.get(f"{kind}_reviews") or []
        if stored_reviews and not reviews and (not known or total > 0):
            setattr(game, f"{kind}_reviews", [Review(**r) for r in stored_reviews])
            setattr(game, f"{kind}_total", max(total, stored.get(f"{kind}_total") or 0))
            notes.append(f"Metacritic не вернул отзывы {label}, оставлены отзывы и резюме из базы")
    for attr in SCALAR_KEEP:
        if not getattr(game, attr) and stored.get(attr):
            setattr(game, attr, stored[attr])
    for attr in LIST_KEEP:
        if not getattr(game, attr) and stored.get(attr):
            setattr(game, attr, list(stored[attr]))
    return notes


class _Heartbeat:
    def __init__(self, pipeline: "Pipeline", run_id: int):
        self.pipeline = pipeline
        self.run_id = run_id
        self.interval = max(5.0, pipeline.settings.lease_ttl_seconds / 3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pipeline-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        p = self.pipeline
        while not self._stop.wait(self.interval):
            try:
                now = p.clock()
                p.db.renew_lease(LEASE, p.owner, p.settings.lease_ttl_seconds, now)
                p.db.heartbeat_run(self.run_id, now)
            except Exception as exc:
                log.warning("Запуск #%d: не удалось продлить блокировку: %s", self.run_id, exc)


class Pipeline:
    def __init__(self, settings: Settings, db: Database, monitor: Monitor, metacritic: MetacriticClient,
                 youtube: YouTubeClient | None, llm: LLMClient | None, clock=utcnow,
                 stt: SpeechToText | None = None):
        self.settings = settings
        self.db = db
        self.monitor = monitor
        self.mc = metacritic
        self.yt = youtube
        self.llm = llm
        self.stt = stt
        self.clock = clock
        self._deadline: datetime | None = None
        self.owner = make_owner_id()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        if self._lock.locked():
            return True
        try:
            return self.db.lease_holder(LEASE, self.clock()) is not None
        except Exception:
            return False

    def _acquire(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self.db.acquire_lease(LEASE, self.owner, self.settings.lease_ttl_seconds, self.clock()):
                return True
            log.info("Обработку уже ведёт другой процесс, запуск пропущен")
        except Exception:
            log.exception("Не удалось взять блокировку запуска")
        self._lock.release()
        return False

    def _release(self) -> None:
        try:
            self.db.release_lease(LEASE, self.owner)
        except Exception:
            log.exception("Не удалось снять блокировку запуска")
        finally:
            self._lock.release()

    def start_background(self, trigger: str = "manual", limit: int | None = None) -> bool:
        if not self._acquire():
            return False

        def target():
            try:
                self._run(trigger, limit)
            except Exception:
                log.exception("Фоновый запуск упал")
            finally:
                self._release()

        threading.Thread(target=target, name=f"pipeline-{trigger}", daemon=True).start()
        return True

    def run(self, trigger: str = "manual", limit: int | None = None) -> dict:
        if not self._acquire():
            raise AlreadyRunning()
        try:
            return self._run(trigger, limit)
        finally:
            self._release()

    def _step(self, step: str, title: str | None = None) -> None:
        detail = step if not title else f"{step}: {title}"
        self.monitor.set_worker("pipeline", "running", detail)
        self.monitor.set_progress(step=step, current=title)

    def _error(self, run_id: int | None, day: str, slug: str | None, step: str, message: str) -> None:
        try:
            self.db.add_error(run_id, day, slug, step, message)
        except Exception:
            log.exception("Не удалось записать ошибку в журнал")

    def _run(self, trigger: str, limit: int | None) -> dict:
        s = self.settings
        started = self.clock()
        day = s.today(started)
        batch = max(1, min(limit or s.batch_size, s.batch_size))
        run_id = self.db.create_run(trigger, day, self.owner, started)
        result = {"run_id": run_id, "status": "failed", "selected": 0, "ok": 0, "failed": 0, "sources": "",
                  "seconds": 0}
        message = ""
        if self.llm is not None:
            was_skipped = self.llm.circuit_open
            self.llm.begin_run()
            if was_skipped:
                self.monitor.set_worker("llm", "idle", "новый запуск: LLM снова вызывается")
        if self.stt is not None:
            self.stt.begin_run()
        heartbeat = _Heartbeat(self, run_id)
        heartbeat.start()
        self.monitor.set_progress(run_id=run_id, done=0, total=0, ok=0, failed=0, current=None,
                                  step="выбор игр", started_at=iso(started))
        trigger_label = TRIGGER_LABELS.get(trigger, trigger)
        self.monitor.set_worker("pipeline", "running", f"запуск #{run_id} ({trigger_label}): выбор игр")
        log.info("Запуск #%d начат (%s), день %s, до %d игр", run_id, trigger_label, day, batch)
        try:
            message = self._run_body(run_id, day, batch, started, result)
        except Exception as exc:
            log.exception("Запуск #%d прерван ошибкой", run_id)
            result["status"] = "failed"
            message = f"Запуск прерван ошибкой: {exc.__class__.__name__}: {exc}"
            self._error(run_id, day, None, "run", message)
        finally:
            heartbeat.stop()
            self._deadline = None
            if self.stt is not None:
                self.stt.end_run()
            result["seconds"] = round((self.clock() - started).total_seconds())
            try:
                self.db.finish_run(run_id, result["status"], result["selected"], result["ok"], result["failed"],
                                   result["sources"], message, self.clock())
            except Exception:
                log.exception("Запуск #%d: не удалось записать итог", run_id)
            worker_status = "error" if result["status"] == "failed" else "idle"
            self.monitor.set_worker("pipeline", worker_status, f"последний запуск #{run_id}: {message}")
            self.monitor.reset_progress()
            log.info("Запуск #%d завершён за %d с: %s", run_id, result["seconds"], message)
        return result

    def _run_body(self, run_id: int, day: str, batch: int, started: datetime, result: dict) -> str:
        s = self.settings
        deadline = started + timedelta(minutes=s.run_max_minutes)
        self._deadline = deadline
        try:
            pruned = self.db.prune(started, s.errors_keep_days, s.processed_keep_days)
            if any(pruned.values()):
                log.info("Удалены старые записи: %s", pruned)
        except Exception:
            log.exception("Не удалось удалить старые записи")

        state = self.db.get_day_state(day)
        processed = self.db.processed_ids(day, s.failed_game_max_attempts)
        retries = [ListItem(id=r["game_id"], slug=r["slug"], title=r["title"] or r["slug"])
                   for r in self.db.retry_candidates(day, s.failed_game_max_attempts)]
        try:
            sel = select_batch(
                state,
                processed,
                lambda: self.mc.new_releases(20),
                lambda offset, size: self.mc.browse_new(offset, size),
                batch_size=batch,
                page_size=s.browse_page_size,
                max_pages=s.browse_max_pages,
                retries=retries,
            )
        except Exception as exc:
            log.exception("Запуск #%d: не удалось выбрать игры", run_id)
            step = "selection:schema" if isinstance(exc, SchemaError) else "selection"
            self._error(run_id, day, None, step, f"{exc.__class__.__name__}: {exc}")
            self.monitor.set_worker("metacritic", "error", f"список игр недоступен: {exc}")
            return f"Не удалось получить список игр: {exc}"
        for where, exc in sel.errors:
            step = "selection:schema" if isinstance(exc, SchemaError) else "selection"
            self._error(run_id, day, None, step, f"{where}: {exc.__class__.__name__}: {exc}")

        total = len(sel.items)
        result.update(selected=total, sources=sel.sources)
        log.info("Запуск #%d: выбрано %d игр (%s), курсор SEE ALL %d -> %d", run_id, total, sel.sources or "—",
                 state.browse_offset, sel.state.browse_offset)
        self.monitor.set_progress(total=total)
        self.db.update_run_progress(run_id, total, 0, 0, sel.sources)
        ok = failed = 0
        attempted = 0
        cut = False
        for i, (item, source) in enumerate(sel.items):
            if self.clock() >= deadline:
                cut = True
                log.warning("Запуск #%d: достигнут лимит %d мин, %d игр останутся следующему запуску", run_id,
                            s.run_max_minutes, total - i)
                break
            self.monitor.set_progress(done=i, current=item.title)
            status = "ok"
            try:
                self.process_game(item, source, run_id, day)
                ok += 1
            except Exception as exc:
                status = "error"
                failed += 1
                log.exception("Запуск #%d: ошибка на игре %s", run_id, item.slug)
                step = self.monitor.progress.get("step") or "game"
                if isinstance(exc, SchemaError):
                    step = f"{step}:schema"
                self._error(run_id, day, item.slug, step, f"{exc.__class__.__name__}: {exc}")
            attempts = self.db.mark_processed(day, item.id, item.slug, item.title, run_id, status, source)
            if status == "error" and attempts < s.failed_game_max_attempts:
                log.info("Запуск #%d: %s повторим сегодня позже (попытка %d из %d)", run_id, item.slug,
                         attempts, s.failed_game_max_attempts)
            attempted += 1
            result.update(ok=ok, failed=failed)
            self.db.update_run_progress(run_id, total, ok, failed, sel.sources)
            self.monitor.set_progress(done=i + 1, ok=ok, failed=failed)

        if attempted == total:
            self.db.save_day_state(sel.state)
        else:
            log.info("Запуск #%d: курсор дня не сдвинут, необработанные игры возьмёт следующий запуск", run_id)

        if s.llm_backfill_limit and self.llm is not None and self.llm.available and self.clock() < deadline:
            try:
                self._step("LLM: досчёт резюме без LLM")
                upgraded = self.backfill_llm({it.id for it, _ in sel.items}, s.llm_backfill_limit, deadline)
                if upgraded:
                    log.info("Запуск #%d: через LLM пересчитано резюме из запасного режима: %d", run_id, upgraded)
            except Exception:
                log.exception("Запуск #%d: досчёт через LLM упал", run_id)

        if s.video_recheck_limit and self.yt is not None and self.clock() < deadline:
            try:
                self._step("YouTube: перепроверка роликов")
                rechecked = self.recheck_videos(s.video_recheck_limit, deadline, {it.id for it, _ in sel.items})
                if rechecked:
                    log.info("Запуск #%d: перепроверены ролики у %d игр", run_id, rechecked)
            except Exception:
                log.exception("Запуск #%d: перепроверка роликов упала", run_id)

        try:
            self._step("похожие игры")
            self.recompute_similar()
        except Exception as exc:
            log.exception("Запуск #%d: не удалось пересчитать похожие игры", run_id)
            self._error(run_id, day, None, "similar", str(exc))

        if total == 0:
            result["status"] = "empty"
            return "Новых игр для обработки нет"
        if cut:
            result["status"] = "cut"
            return (f"Остановлен по лимиту {s.run_max_minutes} мин: обработано {ok} из {total}, ошибок {failed}; "
                    "остальные игры возьмёт следующий запуск")
        result["status"] = "ok" if failed == 0 else ("partial" if ok else "failed")
        return f"Обработано {ok} из {total}, ошибок {failed}"

    def process_game(self, item: ListItem, source: str, run_id: int, day: str) -> None:
        self._step("Metacritic: карточка и оценки", item.title)
        game = self.mc.full_game(item.slug)
        if not game.cover_url:
            game.cover_url = item.cover_url
        stored = self.db.get_game(game_id=game.id)
        for note in keep_stored_data(game, stored):
            log.warning("%s: %s", game.slug, note)
            self._error(run_id, day, game.slug, "данные Metacritic:schema", note)
        prev = stored or {}
        is_new = self.db.upsert_game(game, source)
        log.info("%s %s: платформ %d, Metascore %s, Userscore %s, отзывов %d+%d",
                 "Добавлена" if is_new else "Обновлена", game.slug, len(game.platforms),
                 "нет" if game.metascore is None else game.metascore,
                 "нет" if game.userscore is None else game.userscore, len(game.critic_reviews), len(game.user_reviews))

        for kind, reviews, total, column in (
            ("critic", game.critic_reviews, game.critic_total, "critic_summary"),
            ("user", game.user_reviews, game.user_total, "user_summary"),
        ):
            old = prev.get(column)
            if (old and old.get("reviews_hash") == reviews_hash(reviews)
                    and old.get("method") in ("llm", "none")):
                old["review_count"] = total
                self.db.set_json(game.id, column, old)
                continue
            self._step("резюме критиков" if kind == "critic" else "резюме игроков", game.title)
            summary = summarize_reviews(self.llm, game.title, kind, reviews, total)
            self.db.set_json(game.id, column, summary)
            if summary.get("error"):
                self._error(run_id, day, game.slug, f"llm:{kind}", summary["error"])

        if self.yt is None:
            return
        anchor = release_anchor(game.release_date, [pl.release_date for pl in game.platforms])
        self.ensure_trailer(game.id, game.slug, game.title, anchor, bool(game.video_embed_url), prev.get("trailer"),
                            run_id, day)
        self.refresh_letsplay(game.id, game.slug, game.title, anchor, prev.get("letsplay"), run_id, day)

    def today(self):
        return datetime.strptime(self.settings.today(self.clock()), "%Y-%m-%d").date()

    def refresh_letsplay(self, game_id: int, slug: str, title: str, anchor: str | None, old_lp: dict | None,
                         run_id: int | None, day: str) -> None:
        if not self.letsplay_due(old_lp):
            return
        self._step("YouTube: поиск летсплея", title)
        now = iso(self.clock())
        try:
            letsplay = self.find_letsplay(title, anchor)
        except Exception as exc:
            log.warning("Летсплей для %s: ошибка поиска: %s", slug, exc)
            step = "youtube:schema" if isinstance(exc, SchemaError) else "youtube"
            self._error(run_id, day, slug, step, f"{exc.__class__.__name__}: {exc}")
            note = f"Ошибка при поиске летсплея: {exc}"
            letsplay = {"status": "error", "method": "none", "note": note, "updated_at": now, "checked_at": now}
        if self.keep_old_letsplay(title, anchor, old_lp, letsplay):
            log.info("Летсплей для %s: новый результат хуже сохранённого, оставлен прежний ролик", slug)
            kept = {**old_lp, "last_error": letsplay.get("note") or "новый поиск не дал результата",
                    "checked_at": now}
            if letsplay.get("status") != "error":
                kept.pop("recheck", None)
            letsplay = kept
        self.db.set_json(game_id, "letsplay", letsplay)

    def keep_old_letsplay(self, title: str, anchor: str | None, old: dict | None, new: dict) -> bool:
        if not old or not old.get("video") or letsplay_rank(old) <= letsplay_rank(new):
            return False
        if old.get("recheck") and new.get("status") != "error":
            return False
        video = old["video"]
        if video.get("video_id") in {r.get("video_id") for r in new.get("rejected") or []}:
            return False
        ref = parse_day(old.get("updated_at")) or self.today()
        return recheck_letsplay(title, stored_candidate(video), anchor, ref) is None

    def _due(self, record: dict | None, period: timedelta) -> bool:
        if not record:
            return True
        if record.get("recheck"):
            return True
        stamps = [parse_iso(record.get(k)) for k in ("updated_at", "checked_at")]
        stamps = [t for t in stamps if t]
        if not stamps:
            return True
        return self.clock() - max(stamps) >= period

    def letsplay_due(self, old: dict | None) -> bool:
        s = self.settings
        if (old and old.get("status") == "ok" and old.get("method") in FULL_LETSPLAY
                and not old.get("llm_no_commentary")):
            return self._due(old, timedelta(days=s.letsplay_refresh_days))
        return self._due(old, timedelta(hours=s.letsplay_retry_hours))

    def trailer_due(self, old: dict | None) -> bool:
        s = self.settings
        if old and old.get("status") == "error":
            return self._due(old, timedelta(hours=s.letsplay_retry_hours))
        return self._due(old, timedelta(days=s.trailer_refresh_days))

    def trailer_still_ok(self, title: str, old: dict | None, anchor: str | None) -> bool:
        if not old or old.get("status") != "ok" or not old.get("video"):
            return False
        video = old["video"]
        ref = parse_day(old.get("updated_at")) or self.today()
        return trailer_problem(title, stored_candidate(video), anchor, ref,
                               strict_edition=not video.get("base_game")) is None

    def ensure_trailer(self, game_id: int, slug: str, title: str, anchor: str | None, has_video: bool,
                       old: dict | None, run_id: int | None, day: str) -> None:
        if has_video or not self.settings.trailer_fallback:
            if old and old.get("recheck"):
                self.db.set_json(game_id, "trailer", {k: v for k, v in old.items() if k != "recheck"})
            return
        if not self.trailer_due(old):
            return
        finder = getattr(self.yt, "find_trailer", None)
        if finder is None:
            return
        self._step("YouTube: трейлер", title)
        now = iso(self.clock())
        keep = self.trailer_still_ok(title, old, anchor)
        try:
            cand = finder(title, anchor)
        except Exception as exc:
            log.warning("Трейлер для %s: ошибка поиска: %s", slug, exc)
            if keep:
                trailer = {**old, "last_error": str(exc)[:200], "checked_at": now}
            else:
                trailer = {"status": "error", "error": str(exc)[:200], "checked_at": now, "updated_at": now}
        else:
            if cand is not None:
                trailer = {"status": "ok", "source": "youtube", "video": cand.to_dict(), "updated_at": now,
                           "checked_at": now}
            elif keep:
                trailer = {**old, "last_error": "новый поиск не нашёл трейлер, оставлен прежний", "checked_at": now}
                trailer.pop("recheck", None)
            else:
                trailer = {"status": "none", "updated_at": now, "checked_at": now}
        self.db.set_json(game_id, "trailer", trailer)

    def no_video_note(self, title: str, stats: dict, rejected: list[dict]) -> str:
        again = "Проверим снова через сутки"
        if rejected:
            return (f"Ролики с похожими названиями нашлись, но проверка показала, что они о другой игре или вышли "
                    f"задолго до неё ({ru_count(len(rejected), 'ролик', 'ролика', 'роликов')}). {again}")
        if stats.get("near_miss"):
            return (f"На YouTube есть похожие ролики ({stats['near_miss']}), но ни в одном названии нет полного "
                    f"названия «{title}». Проверка строгая, чтобы не взять ролик другой игры или другой части серии. "
                    f"{again}")
        if stats.get("too_old"):
            return (f"Ролики с «{title}» в названии есть, но все опубликованы больше чем за год до выхода игры: "
                    f"скорее всего, это другая игра с тем же названием. {again}")
        if stats.get("title_mismatch"):
            found = stats.get("found") or stats["title_mismatch"]
            return (f"YouTube вернул {ru_count(found, 'ролик', 'ролика', 'роликов')}, но ни в одном названии нет "
                    f"«{title}»; проверим снова через сутки")
        return f"На YouTube не нашлось летсплеев длиннее 20 минут с названием игры в заголовке. {again}"

    def find_letsplay(self, title: str, anchor: str | None = None) -> dict:
        now = iso(self.clock())
        today = self.today()
        candidates = self.yt.find_letsplays(title, anchor)
        stats = dict(getattr(self.yt, "last_stats", None) or {})
        rejected = list(stats.get("dropped") or [])
        top = [c.to_dict() for c in candidates[:5]]
        if not candidates:
            return {"status": "no_video", "method": "none", "candidates": [], "updated_at": now, "checked_at": now,
                    "note": self.no_video_note(title, stats, rejected), "search": stats, "rejected": rejected}
        captions = self.settings.youtube_captions
        checked, stt_notes, skipped, tried = [], [], [], []
        stt_reasons_seen = set()
        for cand in candidates:
            if len(tried) >= self.settings.youtube_max_candidates:
                break
            if not cand.refined:
                self._refine(cand)
                reason = recheck_letsplay(title, cand, anchor, today)
                if reason:
                    rejected.append({"video_id": cand.video_id, "title": cand.title,
                                     "search_title": cand.search_title, "reason": reason})
                    continue
            tried.append(cand)
            text = lang = speech = None
            if captions:
                self._step("YouTube: субтитры", f"{title} / {cand.video_id}")
                try:
                    text, lang = self.yt.transcript(cand.video_id)
                except TranscriptUnavailable as exc:
                    checked.append({"video_id": cand.video_id, "reason": str(exc), "code": exc.code})
                    log.info("Субтитров нет у %s (%s): %s", cand.video_id, title, exc)
            if text is None:
                speech, reason, attempted = self._speech(title, cand)
                if speech is not None:
                    text, lang = speech.text, speech.language
                elif reason and (attempted or reason not in stt_reasons_seen):
                    stt_reasons_seen.add(reason)
                    stt_notes.append({"video_id": cand.video_id, "reason": reason} if attempted else {"reason": reason})
            if text is None:
                skipped.append({"video_id": cand.video_id, "reason": NO_TEXT})
                continue
            source = "whisper" if speech is not None else "transcript"
            if not looks_like_commentary(text):
                skipped.append({"video_id": cand.video_id, "reason": NO_COMMENTARY_TEXT})
                log.info("Ролик %s (%s): в тексте почти нет речи автора, берём следующий", cand.video_id, title)
                continue
            self._step("LLM: заключение по летсплею", title)
            result, method, error = self._conclude(title, cand, text, source)
            if result is not None and result.get("about_this_game") is False:
                skipped.append({"video_id": cand.video_id, "reason": WRONG_GAME_LLM})
                rejected.append({"video_id": cand.video_id, "title": cand.title, "reason": WRONG_GAME_LLM,
                                 "other_game": result.get("other_game")})
                log.info("Ролик %s (%s): по ответу LLM он о другой игре (%s), берём следующий", cand.video_id, title,
                         result.get("other_game"))
                continue
            if result is not None and not result.get("has_commentary"):
                skipped.append({"video_id": cand.video_id, "reason": NO_COMMENTARY_LLM})
                log.info("Ролик %s (%s): LLM не нашла комментариев автора, берём следующий", cand.video_id, title)
                continue
            unverified = result is None
            if result is None:
                result, method = extractive_transcript_summary(text), f"extractive-{source}"
            record = {"status": "ok", "method": method, "video": cand.to_dict(),
                      "transcript_method": "whisper" if speech is not None else "captions",
                      "transcript_chars": len(text), "transcript_lang": lang,
                      "transcript_excerpt": sample_transcript(text, 6000), "checked": checked,
                      "stt_checked": stt_notes, "skipped": skipped, "passed_over": self._passed_over(top, skipped),
                      "candidates": top, "rejected": rejected, "updated_at": now, "checked_at": now, "error": error,
                      **result}
            if unverified:
                record["about_unverified"] = True
            if speech is not None:
                record["stt"] = speech.meta()
            return record
        return self._described(title, tried, top, rejected, checked, stt_notes, skipped, stats, now)

    def _described(self, title: str, tried: list[VideoCandidate], top: list[dict], rejected: list[dict],
                   checked: list[dict], stt_notes: list[dict], skipped: list[dict], stats: dict, now: str) -> dict:
        captions = self.settings.youtube_captions
        wrong = {r["video_id"] for r in rejected}
        pool = [c for c in tried if c.video_id not in wrong]
        for cand in pool:
            if not cand.description:
                continue
            text = f"Название: {cand.title}\nОписание: {cand.description[:3000]}"
            result, method, error = self._conclude(title, cand, text, "description")
            if result is not None and result.get("about_this_game") is False:
                skipped.append({"video_id": cand.video_id, "reason": WRONG_GAME_DESCRIPTION})
                rejected.append({"video_id": cand.video_id, "title": cand.title, "reason": WRONG_GAME_DESCRIPTION,
                                 "other_game": result.get("other_game")})
                wrong.add(cand.video_id)
                continue
            if result is None:
                result = {"summary": cand.description[:600], "pros": [], "cons": [], "verdict": ""}
                method = "description"
            return {"status": "no_transcript", "video": cand.to_dict(), "checked": checked, "stt_checked": stt_notes,
                    "skipped": skipped, "candidates": top, "rejected": rejected, "updated_at": now,
                    "checked_at": now, "note": failure_note(checked, stt_notes, skipped, captions),
                    "method": method, "error": error, **result}
        rest = [c for c in pool if c.video_id not in wrong]
        if rest:
            return {"status": "no_transcript", "video": rest[0].to_dict(), "checked": checked,
                    "stt_checked": stt_notes, "skipped": skipped, "candidates": top, "rejected": rejected,
                    "updated_at": now, "checked_at": now, "note": failure_note(checked, stt_notes, skipped, captions),
                    "method": "none"}
        return {"status": "no_video", "method": "none", "candidates": top, "updated_at": now, "checked_at": now,
                "note": self.no_video_note(title, stats, rejected), "search": stats, "rejected": rejected,
                "skipped": skipped}

    @staticmethod
    def _passed_over(top: list[dict], skipped: list[dict]) -> list[dict]:
        info = {c["video_id"]: c for c in top}
        out = []
        for s in skipped:
            c = info.get(s["video_id"], {})
            out.append({"video_id": s["video_id"], "title": c.get("title"), "views": c.get("views"),
                        "reason": s["reason"]})
        return out

    def _speech(self, title: str, cand: VideoCandidate) -> tuple[SpeechResult | None, str | None, bool]:
        if self.stt is None:
            return None, None, False
        reason = self.stt.unavailable_reason()
        if reason:
            return None, reason, False
        if self._deadline is not None:
            left = (self._deadline - self.clock()).total_seconds()
            if left < self.settings.stt_timeout_seconds + 60:
                return None, f"до конца запуска {max(0, int(left // 60))} мин, распознавание отложено", False
        self._step("Whisper: распознавание речи", f"{title} / {cand.video_id}")
        try:
            return self.stt.transcribe_video(cand.video_id), None, True
        except SpeechUnavailable as exc:
            log.warning("Whisper для %s (%s) не дал текста: %s", title, cand.video_id, exc)
            return None, str(exc)[:200], True
        except Exception as exc:
            log.exception("Whisper для %s (%s) упал", title, cand.video_id)
            return None, f"внутренняя ошибка распознавания ({exc.__class__.__name__})", True

    def _refine(self, cand) -> None:
        refine = getattr(self.yt, "refine", None)
        if refine is not None:
            refine(cand)
            return
        try:
            details = self.yt.watch_details(cand.video_id)
        except Exception as exc:
            log.info("Страница ролика %s недоступна: %s", cand.video_id, exc)
            cand.refined = True
            return
        apply_watch_details(cand, details)

    def _conclude(self, title: str, cand, text: str, source: str):
        if self.llm is None:
            return None, None, "LLM отключена"
        if source == "description":
            def validator(value):
                return validate_description_json(value, title)
        else:
            def validator(value):
                return validate_letsplay_json(value, text, title)
        try:
            result = self.llm.ask_json(
                lambda budget: build_letsplay_prompt(title, cand.title, cand.channel, text, source, budget),
                validator)
            return result, f"llm-{source}", None
        except (LLMSkipped, LLMDisabled) as exc:
            return None, None, str(exc)[:200]
        except LLMError as exc:
            log.warning("LLM не написала заключение по летсплею для %s: %s", title, exc)
            return None, None, str(exc)[:200]

    def recheck_videos(self, limit: int, deadline: datetime | None = None, skip_ids: set[int] | None = None) -> int:
        done = 0
        if self.yt is None or limit <= 0:
            return 0
        day = self.settings.today(self.clock())
        for g in self.db.games_needing_video_recheck(limit + len(skip_ids or ())):
            if done >= limit or (deadline is not None and self.clock() >= deadline):
                break
            if skip_ids and g["id"] in skip_ids:
                continue
            anchor = release_anchor(g["release_date"], g["platform_dates"])
            if (g["trailer"] or {}).get("recheck"):
                self.ensure_trailer(g["id"], g["slug"], g["title"], anchor, bool(g["video_embed_url"]), g["trailer"],
                                    None, day)
            if (g["letsplay"] or {}).get("recheck"):
                self.refresh_letsplay(g["id"], g["slug"], g["title"], anchor, g["letsplay"], None, day)
            done += 1
        return done

    def backfill_llm(self, skip_ids: set[int], limit: int, deadline: datetime | None = None) -> int:
        upgraded = 0
        for g in self.db.games_with_fallbacks(max(limit * 3, limit + 5)):
            if self.llm is None or not self.llm.available:
                break
            if deadline is not None and self.clock() >= deadline:
                break
            if g["id"] in skip_ids:
                continue
            self.db.mark_backfill_attempt(g["id"])
            for kind, column in (("critic", "critic_summary"), ("user", "user_summary")):
                old = g[column]
                if not old or old.get("method") != "extractive" or not self.llm.available:
                    continue
                reviews = [Review(**r) for r in g[f"{kind}_reviews"] or []]
                if not reviews:
                    continue
                new = summarize_reviews(self.llm, g["title"], kind, reviews, old.get("review_count") or len(reviews))
                if new.get("method") != "llm":
                    log.info("Досчёт резюме (%s) для %s не удался, идём дальше", kind, g["slug"])
                    continue
                self.db.set_json(g["id"], column, new)
                upgraded += 1
                if upgraded >= limit:
                    return upgraded
            lp = g["letsplay"]
            if not lp or not lp.get("video") or not self.llm.available:
                continue
            v = lp["video"]
            cand = VideoCandidate(video_id=v["video_id"], title=v.get("title") or "", channel=v.get("channel"))
            if lp.get("method") in TRANSCRIPT_EXTRACTIVE and lp.get("transcript_excerpt"):
                source = TRANSCRIPT_EXTRACTIVE[lp["method"]]
                result, method, _ = self._conclude(g["title"], cand, lp["transcript_excerpt"], source)
            elif lp.get("method") == "description" and v.get("description"):
                source = "description"
                text = f"Название: {cand.title}\nОписание: {v['description'][:3000]}"
                result, method, _ = self._conclude(g["title"], cand, text, source)
            else:
                continue
            if result is None:
                log.info("Досчёт заключения по летсплею для %s не удался, идём дальше", g["slug"])
                continue
            if result.get("about_this_game") is False:
                log.info("Досчёт для %s: по ответу LLM ролик о другой игре, летсплей найдём заново", g["slug"])
                reason = WRONG_GAME_DESCRIPTION if source == "description" else WRONG_GAME_LLM
                now = iso(self.clock())
                self.db.set_json(g["id"], "letsplay", {
                    "status": "no_video", "method": "none", "candidates": [], "updated_at": now, "checked_at": now,
                    "recheck": True, "rejected": [{"video_id": v["video_id"], "title": v.get("title"), "reason": reason,
                                                   "other_game": result.get("other_game")}],
                    "note": "Найденный раньше ролик оказался о другой игре; летсплей будет найден заново"})
                continue
            if source != "description" and not result.get("has_commentary"):
                log.info("Досчёт для %s: LLM не нашла комментариев автора, летсплей перепроверим поиском", g["slug"])
                lp.update(llm_no_commentary=True, checked_at=iso(self.clock()))
                self.db.set_json(g["id"], "letsplay", lp)
                continue
            lp.update(result, method=method, error=None, updated_at=iso(self.clock()))
            lp.pop("about_unverified", None)
            self.db.set_json(g["id"], "letsplay", lp)
            upgraded += 1
            if upgraded >= limit:
                return upgraded
        return upgraded

    def recompute_similar(self) -> int:
        feats = [GameFeatures(**f) for f in self.db.all_features()]
        mapping = compute_similar(feats, top_n=self.settings.similar_top_n)
        self.db.set_similar_bulk(mapping)
        return len(mapping)
