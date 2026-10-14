import logging
import threading
from collections import deque
from datetime import datetime, timezone, tzinfo

from .config import iso, utcnow

TRIGGER_LABELS = {"manual": "вручную", "schedule": "по расписанию", "startup": "при старте"}
IDLE_DETAIL = "с момента старта сервиса запросов не было"
WORKER_TITLES = {
    "scheduler": "Планировщик",
    "pipeline": "Обработчик игр",
    "metacritic": "Metacritic",
    "youtube": "YouTube",
    "llm": "LLM",
    "stt": "Распознавание речи (Whisper)",
}


class Monitor:
    def __init__(self, log_size: int = 400, tz: tzinfo = timezone.utc):
        self.tz = tz
        self._lock = threading.Lock()
        self.version = 0
        self._log: deque = deque(maxlen=log_size)
        self._log_seq = 0
        self.workers: dict[str, dict] = {
            name: {"name": name, "title": title, "status": "idle",
                   "detail": IDLE_DETAIL if name in ("metacritic", "youtube", "llm") else "",
                   "updated_at": iso(utcnow())}
            for name, title in WORKER_TITLES.items()
        }
        self.counters = {
            "metacritic_ok": 0, "metacritic_failed": 0,
            "youtube_ok": 0, "youtube_failed": 0,
            "llm_ok": 0, "llm_failed": 0, "llm_last_latency": None,
        }
        self.progress = {"run_id": None, "done": 0, "total": 0, "ok": 0, "failed": 0, "current": None,
                         "step": None, "started_at": None}
        self.next_run_at: str | None = None
        self.started_at = iso(utcnow())

    def _bump(self) -> None:
        self.version += 1

    def set_worker(self, name: str, status: str, detail: str = "") -> None:
        with self._lock:
            w = self.workers[name]
            w.update(status=status, detail=detail, updated_at=iso(utcnow()))
            self._bump()

    def set_progress(self, **kwargs) -> None:
        with self._lock:
            self.progress.update(kwargs)
            self._bump()

    def reset_progress(self) -> None:
        with self._lock:
            self.progress = {"run_id": None, "done": 0, "total": 0, "ok": 0, "failed": 0, "current": None,
                             "step": None, "started_at": None}
            self._bump()

    def set_next_run(self, when) -> None:
        with self._lock:
            self.next_run_at = iso(when) if when else None
            self._bump()

    def http_event(self, host: str, ok: bool) -> None:
        key = "youtube" if "youtube" in host else "metacritic" if "metacritic" in host else None
        if not key:
            return
        with self._lock:
            self.counters[f"{key}_{'ok' if ok else 'failed'}"] += 1
            w = self.workers[key]
            w["status"] = "ok" if ok else "error"
            w["detail"] = f"запросов: {self.counters[key + '_ok']} успешных, {self.counters[key + '_failed']} с ошибкой"
            w["updated_at"] = iso(utcnow())
            self._bump()

    def llm_event(self, ok: bool, latency: float) -> None:
        with self._lock:
            self.counters["llm_ok" if ok else "llm_failed"] += 1
            self.counters["llm_last_latency"] = round(latency, 1)
            w = self.workers["llm"]
            w["status"] = "ok" if ok else "error"
            w["detail"] = (f"вызовов: {self.counters['llm_ok']} успешных, {self.counters['llm_failed']} с ошибкой; "
                           f"последний ответ за {latency:.1f} с")
            w["updated_at"] = iso(utcnow())
            self._bump()

    def add_log(self, record: logging.LogRecord, message: str) -> None:
        with self._lock:
            self._log_seq += 1
            self._log.append({
                "id": self._log_seq,
                "ts": datetime.fromtimestamp(record.created, self.tz).strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            })
            self._bump()

    def logs_since(self, last_id: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            items = [e for e in self._log if e["id"] > last_id]
        return items[-limit:]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "version": self.version,
                "workers": [dict(w) for w in self.workers.values()],
                "counters": dict(self.counters),
                "progress": dict(self.progress),
                "next_run_at": self.next_run_at,
                "started_at": self.started_at,
            }


class MonitorLogHandler(logging.Handler):
    def __init__(self, monitor: Monitor):
        super().__init__(level=logging.INFO)
        self.monitor = monitor

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.monitor.add_log(record, record.getMessage())
        except Exception:
            pass


monitor = Monitor()
