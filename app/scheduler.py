import logging
import threading
from datetime import datetime, timedelta

from .config import Settings, utcnow
from .db import Database
from .monitor import Monitor
from .pipeline import AlreadyRunning, Pipeline

log = logging.getLogger("scheduler")


def initial_next_run(settings: Settings, db: Database, now: datetime | None = None) -> datetime:
    now = now or utcnow()
    soon = now + timedelta(seconds=settings.startup_delay_seconds)
    interval = timedelta(minutes=settings.run_interval_minutes)
    last = db.last_run()
    if last:
        try:
            last_start = datetime.fromisoformat(last["started_at"])
        except (TypeError, ValueError):
            last_start = None
        if last_start:
            return max(soon, last_start + interval)
    if db.count_games() == 0:
        return soon
    return now + interval


class Scheduler:
    def __init__(self, settings: Settings, db: Database, pipeline: Pipeline, monitor: Monitor):
        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.monitor = monitor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_run: datetime | None = None

    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            self.monitor.set_worker("scheduler", "off", "планировщик выключен (SCHEDULER_ENABLED=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _set_next(self, when: datetime) -> None:
        self.next_run = when
        self.monitor.set_next_run(when)
        local = when.astimezone(self.settings.day_tz).strftime("%H:%M:%S")
        self.monitor.set_worker("scheduler", "waiting", f"следующий запуск в {local}")

    def _loop(self) -> None:
        interval = timedelta(minutes=self.settings.run_interval_minutes)
        self._set_next(initial_next_run(self.settings, self.db))
        log.info("Scheduler started, next run at %s", self.next_run.isoformat(timespec="seconds"))
        while not self._stop.is_set():
            remaining = (self.next_run - utcnow()).total_seconds()
            if remaining > 0:
                self._stop.wait(min(remaining, 30))
                continue
            started = utcnow()
            if self.pipeline.running:
                log.info("Scheduled run skipped: a run is already in progress")
                self._set_next(started + interval)
                continue
            self.monitor.set_worker("scheduler", "running", "плановый запуск обработки")
            try:
                self.pipeline.run("schedule")
            except AlreadyRunning:
                log.info("Scheduled run skipped: a run is already in progress")
            except Exception:
                log.exception("Scheduled run crashed")
            self._set_next(started + interval)
        self.monitor.set_worker("scheduler", "off", "остановлен")
