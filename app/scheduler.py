import logging
import threading
from datetime import datetime, timedelta

from .config import Settings, iso, parse_iso, utcnow
from .db import Database
from .monitor import Monitor
from .pipeline import AlreadyRunning, Pipeline

log = logging.getLogger("scheduler")

OVERDUE_GRACE = timedelta(minutes=5)


def interval_of(settings: Settings) -> timedelta:
    return timedelta(minutes=max(1, settings.run_interval_minutes))


def last_run_start(db: Database) -> datetime | None:
    last = db.last_run()
    return parse_iso(last["started_at"]) if last else None


def initial_next_run(settings: Settings, db: Database, now: datetime | None = None) -> datetime:
    now = now or utcnow()
    soon = now + timedelta(seconds=settings.startup_delay_seconds)
    last_start = last_run_start(db)
    if last_start:
        return max(soon, last_start + interval_of(settings))
    if db.count_games() == 0:
        return soon
    return now + interval_of(settings)


class Scheduler:
    def __init__(self, settings: Settings, db: Database, pipeline: Pipeline, monitor: Monitor, clock=utcnow):
        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.monitor = monitor
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_run: datetime | None = None
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None
        self.poll_seconds = 30.0
        self.min_wait_seconds = 1.0
        self._waiting_busy = False

    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            self.monitor.set_worker("scheduler", "off", "планировщик выключен (SCHEDULER_ENABLED=0)")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _set_next(self, when: datetime) -> None:
        self.next_run = when
        self.monitor.set_next_run(when)
        local = when.astimezone(self.settings.day_tz).strftime("%H:%M:%S")
        self.monitor.set_worker("scheduler", "waiting", f"следующий запуск в {local}")

    def _refresh_next(self, now: datetime) -> None:
        if self.next_run is None:
            self._set_next(initial_next_run(self.settings, self.db, now))
            log.info("Scheduler started, next run at %s", iso(self.next_run))
            return
        last_start = last_run_start(self.db)
        if last_start and last_start + interval_of(self.settings) > self.next_run:
            self._set_next(last_start + interval_of(self.settings))

    def tick(self, now: datetime | None = None) -> str:
        now = now or self.clock()
        self.last_tick_at = now
        closed = self.db.close_stale_runs(now, self.settings.lease_ttl_seconds)
        if closed:
            log.warning("Closed %d run(s) left running by a stopped process", closed)
        self._refresh_next(now)
        if now < self.next_run:
            self._waiting_busy = False
            return "wait"
        if self.pipeline.running:
            if not self._waiting_busy:
                log.info("Scheduled run is due but another run is in progress, waiting for it to finish")
                self.monitor.set_worker("scheduler", "waiting", "плановый запуск ждёт окончания текущей обработки")
                self._waiting_busy = True
            return "busy"
        self._waiting_busy = False
        due = self.next_run
        self.next_run = now + interval_of(self.settings)
        self.monitor.set_next_run(self.next_run)
        self.monitor.set_worker("scheduler", "running", "плановый запуск обработки")
        outcome = "ran"
        try:
            self.pipeline.run("schedule")
        except AlreadyRunning:
            outcome = "busy"
        except Exception as exc:
            log.exception("Scheduled run crashed")
            self.last_error = f"{exc.__class__.__name__}: {exc}"
            outcome = "crashed"
        after = self.clock()
        last_start = last_run_start(self.db)
        if outcome == "busy":
            self._set_next(due)
        elif last_start:
            self._set_next(max(last_start + interval_of(self.settings), after + timedelta(seconds=1)))
        else:
            self._set_next(after + interval_of(self.settings))
        return outcome

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = None
                remaining = (self.next_run - self.clock()).total_seconds() if self.next_run else self.poll_seconds
            except Exception as exc:
                log.exception("Scheduler tick failed, retrying shortly")
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                try:
                    self.monitor.set_worker("scheduler", "error", f"сбой планировщика: {exc}")
                except Exception:
                    pass
                remaining = self.poll_seconds
            self._stop.wait(min(max(remaining, self.min_wait_seconds), self.poll_seconds))
        self.monitor.set_worker("scheduler", "off", "остановлен")

    def health(self, now: datetime | None = None) -> dict:
        now = now or self.clock()
        info = {
            "enabled": self.settings.scheduler_enabled,
            "alive": self.alive,
            "next_run_at": iso(self.next_run),
            "last_tick_at": iso(self.last_tick_at),
            "last_error": self.last_error,
            "overdue_seconds": 0,
            "healthy": True,
        }
        if not self.settings.scheduler_enabled:
            return info
        if not self.alive:
            info["healthy"] = False
            return info
        if self.next_run and not self.pipeline.running:
            overdue = (now - self.next_run).total_seconds()
            info["overdue_seconds"] = max(0, round(overdue))
            if now - self.next_run > OVERDUE_GRACE:
                info["healthy"] = False
        return info
