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
from .monitor import Monitor
from .selection import select_batch
from .similarity import GameFeatures, compute_similar
from .stt import SpeechResult, SpeechToText, SpeechUnavailable
from .summarize import (
    build_letsplay_prompt,
    extractive_transcript_summary,
    reviews_hash,
    summarize_reviews,
    validate_letsplay_json,
)
from .youtube import TranscriptUnavailable, VideoCandidate, YouTubeClient, sample_transcript

log = logging.getLogger("pipeline")

LEASE = "pipeline"
FULL_LETSPLAY = {"llm-transcript", "extractive-transcript", "llm-whisper", "extractive-whisper"}
TRANSCRIPT_EXTRACTIVE = {"extractive-transcript": "transcript", "extractive-whisper": "whisper"}
SCALAR_KEEP = ("description", "cover_url", "hero_url", "video_embed_url", "video_manifest_url", "video_title",
               "release_date", "lead_platform", "rating")
LIST_KEEP = ("developers", "publishers", "genres", "franchises")


class AlreadyRunning(Exception):
    pass


def make_owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


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
                log.warning("Heartbeat for run #%d failed: %s", self.run_id, exc)


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
            log.info("Another process holds the run lease, skipping")
        except Exception:
            log.exception("Could not take the run lease")
        self._lock.release()
        return False

    def _release(self) -> None:
        try:
            self.db.release_lease(LEASE, self.owner)
        except Exception:
            log.exception("Could not release the run lease")
        finally:
            self._lock.release()

    def start_background(self, trigger: str = "manual", limit: int | None = None) -> bool:
        if not self._acquire():
            return False

        def target():
            try:
                self._run(trigger, limit)
            except Exception:
                log.exception("Background run crashed")
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
            log.exception("Could not record an error")

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
        self.monitor.set_worker("pipeline", "running", f"запуск #{run_id} ({trigger}): выбор игр")
        log.info("Run #%d started (%s), day %s, batch %d", run_id, trigger, day, batch)
        try:
            message = self._run_body(run_id, day, batch, started, result)
        except Exception as exc:
            log.exception("Run #%d crashed", run_id)
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
                log.exception("Run #%d: could not record the finish", run_id)
            worker_status = "error" if result["status"] == "failed" else "idle"
            self.monitor.set_worker("pipeline", worker_status, f"последний запуск #{run_id}: {message.lower()}")
            self.monitor.reset_progress()
            log.info("Run #%d finished (%s): %s in %d s", run_id, result["status"], message, result["seconds"])
        return result

    def _run_body(self, run_id: int, day: str, batch: int, started: datetime, result: dict) -> str:
        s = self.settings
        deadline = started + timedelta(minutes=s.run_max_minutes)
        self._deadline = deadline
        try:
            pruned = self.db.prune(started, s.errors_keep_days, s.processed_keep_days)
            if any(pruned.values()):
                log.info("Pruned old rows: %s", pruned)
        except Exception:
            log.exception("Pruning failed")

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
            log.exception("Run #%d: selection failed", run_id)
            step = "selection:schema" if isinstance(exc, SchemaError) else "selection"
            self._error(run_id, day, None, step, f"{exc.__class__.__name__}: {exc}")
            self.monitor.set_worker("metacritic", "error", f"список игр недоступен: {exc}")
            return f"Не удалось получить список игр: {exc}"
        for where, exc in sel.errors:
            step = "selection:schema" if isinstance(exc, SchemaError) else "selection"
            self._error(run_id, day, None, step, f"{where}: {exc.__class__.__name__}: {exc}")

        total = len(sel.items)
        result.update(selected=total, sources=sel.sources)
        log.info("Run #%d: selected %d games (%s), browse cursor %d -> %d", run_id, total, sel.sources or "—",
                 state.browse_offset, sel.state.browse_offset)
        self.monitor.set_progress(total=total)
        self.db.update_run_progress(run_id, total, 0, 0, sel.sources)
        ok = failed = 0
        attempted = 0
        cut = False
        for i, (item, source) in enumerate(sel.items):
            if self.clock() >= deadline:
                cut = True
                log.warning("Run #%d: time limit of %d min reached, %d games left for the next run", run_id,
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
                log.exception("Run #%d: game %s failed", run_id, item.slug)
                step = self.monitor.progress.get("step") or "game"
                if isinstance(exc, SchemaError):
                    step = f"{step}:schema"
                self._error(run_id, day, item.slug, step, f"{exc.__class__.__name__}: {exc}")
            attempts = self.db.mark_processed(day, item.id, item.slug, item.title, run_id, status, source)
            if status == "error" and attempts < s.failed_game_max_attempts:
                log.info("Run #%d: %s will be retried later today (attempt %d of %d)", run_id, item.slug,
                         attempts, s.failed_game_max_attempts)
            attempted += 1
            result.update(ok=ok, failed=failed)
            self.db.update_run_progress(run_id, total, ok, failed, sel.sources)
            self.monitor.set_progress(done=i + 1, ok=ok, failed=failed)

        if attempted == total:
            self.db.save_day_state(sel.state)
        else:
            log.info("Run #%d: day cursor kept at its previous position so unfinished games are picked up", run_id)

        if s.llm_backfill_limit and self.llm is not None and self.llm.available and self.clock() < deadline:
            try:
                self._step("LLM: досчёт резюме без LLM")
                upgraded = self.backfill_llm({it.id for it, _ in sel.items}, s.llm_backfill_limit, deadline)
                if upgraded:
                    log.info("Run #%d: upgraded %d fallback summaries to LLM", run_id, upgraded)
            except Exception:
                log.exception("Run #%d: LLM backfill failed", run_id)

        try:
            self._step("похожие игры")
            self.recompute_similar()
        except Exception as exc:
            log.exception("Run #%d: similarity failed", run_id)
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
        log.info("%s %s: %d platforms, metascore %s, userscore %s, %d+%d reviews",
                 "Added" if is_new else "Updated", game.slug, len(game.platforms), game.metascore,
                 game.userscore, len(game.critic_reviews), len(game.user_reviews))

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
        self.ensure_trailer(game, prev.get("trailer"), run_id, day)
        old_lp = prev.get("letsplay")
        if not self.letsplay_due(old_lp):
            return
        self._step("YouTube: поиск летсплея", game.title)
        now = iso(self.clock())
        try:
            letsplay = self.find_letsplay(game.title)
        except Exception as exc:
            log.warning("Let's play for %s failed: %s", game.slug, exc)
            step = "youtube:schema" if isinstance(exc, SchemaError) else "youtube"
            self._error(run_id, day, game.slug, step, f"{exc.__class__.__name__}: {exc}")
            note = f"Ошибка при поиске летсплея: {exc}"
            if old_lp and old_lp.get("video"):
                letsplay = {**old_lp, "last_error": note, "checked_at": now}
            else:
                letsplay = {"status": "error", "method": "none", "note": note, "updated_at": now, "checked_at": now}
        self.db.set_json(game.id, "letsplay", letsplay)

    def _due(self, record: dict | None, period: timedelta) -> bool:
        if not record:
            return True
        stamps = [parse_iso(record.get(k)) for k in ("updated_at", "checked_at")]
        stamps = [t for t in stamps if t]
        if not stamps:
            return True
        return self.clock() - max(stamps) >= period

    def letsplay_due(self, old: dict | None) -> bool:
        s = self.settings
        if old and old.get("status") == "ok" and old.get("method") in FULL_LETSPLAY:
            return self._due(old, timedelta(days=s.letsplay_refresh_days))
        return self._due(old, timedelta(hours=s.letsplay_retry_hours))

    def trailer_due(self, old: dict | None) -> bool:
        s = self.settings
        if old and old.get("status") == "error":
            return self._due(old, timedelta(hours=s.letsplay_retry_hours))
        return self._due(old, timedelta(days=s.trailer_refresh_days))

    def ensure_trailer(self, game: GameDetails, old: dict | None, run_id: int, day: str) -> None:
        if game.video_embed_url or not self.settings.trailer_fallback or not self.trailer_due(old):
            return
        finder = getattr(self.yt, "find_trailer", None)
        if finder is None:
            return
        self._step("YouTube: трейлер", game.title)
        now = iso(self.clock())
        try:
            cand = finder(game.title)
        except Exception as exc:
            log.warning("Trailer search for %s failed: %s", game.slug, exc)
            if old and old.get("status") == "ok":
                trailer = {**old, "last_error": str(exc)[:200], "checked_at": now}
            else:
                trailer = {"status": "error", "error": str(exc)[:200], "checked_at": now, "updated_at": now}
        else:
            if cand is not None:
                trailer = {"status": "ok", "source": "youtube", "video": cand.to_dict(), "updated_at": now,
                           "checked_at": now}
            else:
                trailer = {"status": "none", "updated_at": now, "checked_at": now}
        self.db.set_json(game.id, "trailer", trailer)

    def find_letsplay(self, title: str) -> dict:
        now = iso(self.clock())
        candidates = self.yt.find_letsplays(title)
        top = [c.to_dict() for c in candidates[:5]]
        if not candidates:
            return {"status": "no_video", "method": "none", "candidates": [], "updated_at": now, "checked_at": now,
                    "note": "На YouTube не нашлось подходящих летсплеев длиннее 10 минут"}
        checked = []
        if self.settings.youtube_captions:
            for cand in candidates[: self.settings.youtube_max_candidates]:
                self._step("YouTube: расшифровка", f"{title} / {cand.video_id}")
                try:
                    text, lang = self.yt.transcript(cand.video_id)
                except TranscriptUnavailable as exc:
                    checked.append({"video_id": cand.video_id, "reason": str(exc)})
                    log.info("No transcript for %s (%s): %s", cand.video_id, title, exc)
                    continue
                self._refine(cand)
                self._step("LLM: заключение по летсплею", title)
                result, method, error = self._conclude(title, cand, text, "transcript")
                if result is None:
                    result, method = extractive_transcript_summary(text), "extractive-transcript"
                return {"status": "ok", "method": method, "video": cand.to_dict(), "transcript_method": "captions",
                        "transcript_chars": len(text), "transcript_lang": lang,
                        "transcript_excerpt": sample_transcript(text, 6000), "checked": checked, "candidates": top,
                        "updated_at": now, "checked_at": now, "error": error, **result}

        stt_notes = []
        for cand in candidates[: self.settings.youtube_max_candidates]:
            speech, reason, attempted = self._speech(title, cand)
            if speech is not None:
                self._refine(cand)
                self._step("LLM: заключение по летсплею", title)
                result, method, error = self._conclude(title, cand, speech.text, "whisper")
                if result is None:
                    result, method = extractive_transcript_summary(speech.text), "extractive-whisper"
                return {"status": "ok", "method": method, "video": cand.to_dict(), "transcript_method": "whisper",
                        "transcript_chars": len(speech.text), "transcript_lang": speech.language,
                        "transcript_excerpt": sample_transcript(speech.text, 6000), "stt": speech.meta(),
                        "checked": checked, "stt_checked": stt_notes, "candidates": top, "updated_at": now,
                        "checked_at": now, "error": error, **result}
            if reason:
                stt_notes.append({"video_id": cand.video_id, "reason": reason} if attempted else {"reason": reason})
            if not attempted:
                break

        best = candidates[0]
        self._refine(best)
        stt_note = "; ".join(f"{n['video_id']}: {n['reason']}" if "video_id" in n else n["reason"] for n in stt_notes)
        parts = []
        if checked:
            parts.append("субтитры: " + "; ".join(f"{c['video_id']}: {c['reason']}" for c in checked))
        elif not self.settings.youtube_captions:
            parts.append("субтитры YouTube не запрашивались (YOUTUBE_CAPTIONS=0)")
        if stt_note:
            parts.append(f"Whisper: {stt_note}")
        note = f"Расшифровка недоступна ({'; '.join(parts)})" if parts else "Расшифровка недоступна"
        if best.description:
            text = f"Название: {best.title}\nОписание: {best.description[:3000]}"
            result, method, error = self._conclude(title, best, text, "description")
            if result is None:
                result = {"summary": best.description[:600], "pros": [], "cons": [], "verdict": ""}
                method = "description"
            return {"status": "no_transcript", "method": method, "video": best.to_dict(), "checked": checked,
                    "candidates": top, "updated_at": now, "checked_at": now, "note": note, "error": error,
                    **result}
        return {"status": "no_transcript", "method": "none", "video": best.to_dict(), "checked": checked,
                "candidates": top, "updated_at": now, "checked_at": now, "note": note}

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
            log.warning("Whisper for %s (%s) gave no text: %s", title, cand.video_id, exc)
            return None, str(exc)[:200], True
        except Exception as exc:
            log.exception("Whisper for %s (%s) crashed", title, cand.video_id)
            return None, exc.__class__.__name__, True

    def _refine(self, cand) -> None:
        try:
            details = self.yt.watch_details(cand.video_id)
        except Exception as exc:
            log.info("Watch page for %s unavailable: %s", cand.video_id, exc)
            return
        if not details:
            return
        cand.title = details.get("title") or cand.title
        cand.views = details.get("views") or cand.views
        cand.length_seconds = details.get("length_seconds") or cand.length_seconds
        cand.channel = details.get("channel") or cand.channel
        cand.description = details.get("description") or cand.description

    def _conclude(self, title: str, cand, text: str, source: str):
        if self.llm is None:
            return None, None, "LLM отключена"
        try:
            result = self.llm.ask_json(
                lambda budget: build_letsplay_prompt(title, cand.title, cand.channel, text, source, budget),
                validate_letsplay_json)
            return result, f"llm-{source}", None
        except (LLMSkipped, LLMDisabled) as exc:
            return None, None, str(exc)[:200]
        except LLMError as exc:
            log.warning("LLM let's play conclusion for %s failed: %s", title, exc)
            return None, None, str(exc)[:200]

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
                    log.info("Backfill of %s summary for %s failed, moving on", kind, g["slug"])
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
                text = f"Название: {cand.title}\nОписание: {v['description'][:3000]}"
                result, method, _ = self._conclude(g["title"], cand, text, "description")
            else:
                continue
            if result is None:
                log.info("Backfill of the let's play conclusion for %s failed, moving on", g["slug"])
                continue
            lp.update(result, method=method, error=None, updated_at=iso(self.clock()))
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
