import logging
import threading
from datetime import datetime, timedelta

from .config import Settings, iso, utcnow
from .db import Database
from .llm import LLMClient, LLMError
from .metacritic import ListItem, MetacriticClient, Review
from .monitor import Monitor
from .selection import select_batch
from .similarity import GameFeatures, compute_similar
from .summarize import (
    build_letsplay_prompt,
    extractive_transcript_summary,
    reviews_hash,
    summarize_reviews,
    validate_letsplay_json,
)
from .youtube import TranscriptUnavailable, YouTubeClient, sample_transcript

log = logging.getLogger("pipeline")


class AlreadyRunning(Exception):
    pass


class Pipeline:
    def __init__(self, settings: Settings, db: Database, monitor: Monitor, metacritic: MetacriticClient,
                 youtube: YouTubeClient | None, llm: LLMClient | None):
        self.settings = settings
        self.db = db
        self.monitor = monitor
        self.mc = metacritic
        self.yt = youtube
        self.llm = llm
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start_background(self, trigger: str = "manual", limit: int | None = None) -> bool:
        if not self._lock.acquire(blocking=False):
            return False

        def target():
            try:
                self._run(trigger, limit)
            finally:
                self._lock.release()

        threading.Thread(target=target, name=f"pipeline-{trigger}", daemon=True).start()
        return True

    def run(self, trigger: str = "manual", limit: int | None = None) -> dict:
        if not self._lock.acquire(blocking=False):
            raise AlreadyRunning()
        try:
            return self._run(trigger, limit)
        finally:
            self._lock.release()

    def _step(self, step: str, title: str | None = None) -> None:
        detail = step if not title else f"{step}: {title}"
        self.monitor.set_worker("pipeline", "running", detail)
        self.monitor.set_progress(step=step, current=title)

    def _run(self, trigger: str, limit: int | None) -> dict:
        s = self.settings
        day = s.today()
        batch = max(1, min(limit or s.batch_size, s.batch_size))
        run_id = self.db.create_run(trigger, day)
        started = utcnow()
        self.monitor.set_progress(run_id=run_id, done=0, total=0, ok=0, failed=0, current=None,
                                  step="выбор игр", started_at=iso(started))
        self.monitor.set_worker("pipeline", "running", f"запуск #{run_id} ({trigger}): выбор игр")
        log.info("Run #%d started (%s), day %s, batch %d", run_id, trigger, day, batch)

        try:
            state = self.db.get_day_state(day)
            processed = self.db.processed_ids(day)
            sel = select_batch(
                state,
                processed,
                lambda: self.mc.new_releases(20),
                lambda offset, size: self.mc.browse_new(offset, size),
                batch_size=batch,
                page_size=s.browse_page_size,
                max_pages=s.browse_max_pages,
            )
        except Exception as exc:
            log.exception("Run #%d: selection failed", run_id)
            self.db.add_error(run_id, day, None, "selection", f"{exc.__class__.__name__}: {exc}")
            self.db.finish_run(run_id, "failed", 0, 0, 0, "", f"Не удалось получить список игр: {exc}")
            self.monitor.set_worker("pipeline", "error", f"запуск #{run_id}: не удалось получить список игр")
            self.monitor.reset_progress()
            return {"run_id": run_id, "status": "failed", "selected": 0, "ok": 0, "failed": 0}

        total = len(sel.items)
        log.info("Run #%d: selected %d games (%s), browse cursor %d -> %d", run_id, total, sel.sources or "—",
                 state.browse_offset, sel.state.browse_offset)
        self.monitor.set_progress(total=total)
        ok = failed = 0
        for i, (item, source) in enumerate(sel.items):
            self.monitor.set_progress(done=i, current=item.title)
            status = "ok"
            try:
                self.process_game(item, source, run_id, day)
                ok += 1
            except Exception as exc:
                status = "error"
                failed += 1
                log.exception("Run #%d: game %s failed", run_id, item.slug)
                self.db.add_error(run_id, day, item.slug, self.monitor.progress.get("step") or "game",
                                  f"{exc.__class__.__name__}: {exc}")
            self.db.mark_processed(day, item.id, item.slug, item.title, run_id, status, source)
            self.db.update_run_progress(run_id, total, ok, failed, sel.sources)
            self.monitor.set_progress(done=i + 1, ok=ok, failed=failed)

        self.db.save_day_state(sel.state)

        if self.settings.llm_backfill_limit and self.llm is not None:
            try:
                self._step("LLM: досчёт резюме без LLM")
                upgraded = self.backfill_llm({i.id for i, _ in sel.items}, self.settings.llm_backfill_limit)
                if upgraded:
                    log.info("Run #%d: upgraded %d fallback summaries to LLM", run_id, upgraded)
            except Exception:
                log.exception("Run #%d: LLM backfill failed", run_id)

        try:
            self._step("похожие игры")
            self.recompute_similar()
        except Exception as exc:
            log.exception("Run #%d: similarity failed", run_id)
            self.db.add_error(run_id, day, None, "similar", str(exc))

        status = "ok" if failed == 0 else ("partial" if ok else "failed")
        if total == 0:
            status = "empty"
        message = f"Обработано {ok} из {total}, ошибок {failed}"
        self.db.finish_run(run_id, status, total, ok, failed, sel.sources, message)
        elapsed = (utcnow() - started).total_seconds()
        log.info("Run #%d finished: %s in %.0f s", run_id, message, elapsed)
        self.monitor.set_worker("pipeline", "idle", f"последний запуск #{run_id}: {message.lower()}")
        self.monitor.reset_progress()
        return {"run_id": run_id, "status": status, "selected": total, "ok": ok, "failed": failed,
                "sources": sel.sources, "seconds": round(elapsed)}

    def process_game(self, item: ListItem, source: str, run_id: int, day: str) -> None:
        self._step("Metacritic: карточка и оценки", item.title)
        game = self.mc.full_game(item.slug)
        if not game.cover_url:
            game.cover_url = item.cover_url
        prev = self.db.get_previous(game.id) or {}
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
                self.db.add_error(run_id, day, game.slug, f"llm:{kind}", summary["error"])

        if self.yt is None:
            return
        old_lp = prev.get("letsplay")
        if self._letsplay_fresh(old_lp):
            return
        self._step("YouTube: поиск летсплея", game.title)
        try:
            letsplay = self.find_letsplay(game.title)
        except Exception as exc:
            log.warning("Let's play for %s failed: %s", game.slug, exc)
            self.db.add_error(run_id, day, game.slug, "youtube", f"{exc.__class__.__name__}: {exc}")
            letsplay = {"status": "error", "method": "none", "note": f"Ошибка при поиске летсплея: {exc}",
                        "updated_at": iso(utcnow())}
            if old_lp and old_lp.get("video"):
                old_lp["last_error"] = letsplay["note"]
                letsplay = old_lp
        self.db.set_json(game.id, "letsplay", letsplay)

    def _letsplay_fresh(self, old: dict | None) -> bool:
        if not old or old.get("method") != "llm-transcript":
            return False
        try:
            updated = datetime.fromisoformat(old["updated_at"])
        except (KeyError, ValueError, TypeError):
            return False
        return utcnow() - updated < timedelta(days=self.settings.letsplay_refresh_days)

    def find_letsplay(self, title: str) -> dict:
        now = iso(utcnow())
        candidates = self.yt.find_letsplays(title)
        top = [c.to_dict() for c in candidates[:5]]
        if not candidates:
            return {"status": "no_video", "method": "none", "candidates": [], "updated_at": now,
                    "note": "На YouTube не нашлось подходящих летсплеев длиннее 10 минут"}
        checked = []
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
            return {"status": "ok", "method": method, "video": cand.to_dict(), "transcript_chars": len(text),
                    "transcript_lang": lang, "transcript_excerpt": sample_transcript(text, 6000),
                    "checked": checked, "candidates": top, "updated_at": now, "error": error, **result}

        best = candidates[0]
        self._refine(best)
        reasons = "; ".join(f"{c['video_id']}: {c['reason']}" for c in checked)
        note = f"Расшифровка недоступна ({reasons})" if reasons else "Расшифровка недоступна"
        if best.description:
            text = f"Название: {best.title}\nОписание: {best.description[:3000]}"
            result, method, error = self._conclude(title, best, text, "description")
            if result is None:
                result = {"summary": best.description[:600], "pros": [], "cons": [], "verdict": ""}
                method = "description"
            return {"status": "no_transcript", "method": method, "video": best.to_dict(), "checked": checked,
                    "candidates": top, "updated_at": now, "note": note, "error": error, **result}
        return {"status": "no_transcript", "method": "none", "video": best.to_dict(), "checked": checked,
                "candidates": top, "updated_at": now, "note": note}

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
        except LLMError as exc:
            log.warning("LLM let's play conclusion for %s failed: %s", title, exc)
            return None, None, str(exc)[:200]

    def backfill_llm(self, skip_ids: set[int], limit: int) -> int:
        upgraded = 0
        for g in self.db.games_with_fallbacks(limit * 3):
            if g["id"] in skip_ids:
                continue
            for kind, column in (("critic", "critic_summary"), ("user", "user_summary")):
                old = g[column]
                if not old or old.get("method") != "extractive":
                    continue
                reviews = [Review(**r) for r in g[f"{kind}_reviews"]]
                new = summarize_reviews(self.llm, g["title"], kind, reviews, old.get("review_count") or len(reviews))
                if new.get("method") != "llm":
                    return upgraded
                self.db.set_json(g["id"], column, new)
                upgraded += 1
                if upgraded >= limit:
                    return upgraded
            lp = g["letsplay"]
            if lp and lp.get("method") == "extractive-transcript" and lp.get("transcript_excerpt") and lp.get("video"):
                from .youtube import VideoCandidate

                v = lp["video"]
                cand = VideoCandidate(video_id=v["video_id"], title=v.get("title") or "", channel=v.get("channel"))
                result, method, error = self._conclude(g["title"], cand, lp["transcript_excerpt"], "transcript")
                if result is None:
                    return upgraded
                lp.update(result, method=method, error=None, updated_at=iso(utcnow()))
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
