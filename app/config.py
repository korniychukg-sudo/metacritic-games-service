import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache

log = logging.getLogger("config")


def _int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None and value < minimum:
        log.warning("%s=%s is below the minimum, using %s", name, value, minimum)
        value = minimum
    if maximum is not None and value > maximum:
        log.warning("%s=%s is above the maximum, using %s", name, value, maximum)
        value = maximum
    return value


def _float(name: str, default: float, minimum: float | None = None) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None and value < minimum:
        value = minimum
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


MSK = timezone(timedelta(hours=3), "MSK")


@lru_cache(maxsize=32)
def resolve_tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:
        if name in {"Europe/Moscow", "MSK"}:
            log.warning("Time zone %r is unavailable (%s), using fixed UTC+3", name, exc.__class__.__name__)
            return MSK
        log.warning("Unknown time zone %r (%s), falling back to UTC", name, exc.__class__.__name__)
        return timezone.utc


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "./data/games.db"))
    batch_size: int = field(default_factory=lambda: _int("BATCH_SIZE", 20, 1, 100))
    browse_page_size: int = field(default_factory=lambda: _int("BROWSE_PAGE_SIZE", 24, 1, 100))
    browse_max_pages: int = field(default_factory=lambda: _int("BROWSE_MAX_PAGES", 4, 1, 20))
    run_interval_minutes: int = field(default_factory=lambda: _int("RUN_INTERVAL_MINUTES", 60, 1))
    run_max_minutes: int = field(default_factory=lambda: _int("RUN_MAX_MINUTES", 50, 1))
    scheduler_enabled: bool = field(default_factory=lambda: _bool("SCHEDULER_ENABLED", True))
    startup_delay_seconds: int = field(default_factory=lambda: _int("STARTUP_DELAY_SECONDS", 15, 0))
    day_tz_name: str = field(default_factory=lambda: os.environ.get("DAY_TZ", "Europe/Moscow"))
    failed_game_max_attempts: int = field(default_factory=lambda: _int("FAILED_GAME_MAX_ATTEMPTS", 2, 1, 10))
    lease_ttl_seconds: int = field(default_factory=lambda: _int("RUN_LEASE_TTL_SECONDS", 90, 30))
    errors_keep_days: int = field(default_factory=lambda: _int("ERRORS_KEEP_DAYS", 14, 1))
    processed_keep_days: int = field(default_factory=lambda: _int("PROCESSED_KEEP_DAYS", 30, 2))

    manual_cooldown_seconds: int = field(default_factory=lambda: _int("MANUAL_RUN_COOLDOWN_SECONDS", 300, 0))
    manual_daily_cap: int = field(default_factory=lambda: _int("MANUAL_RUNS_PER_DAY", 24, 0))
    run_token: str = field(default_factory=lambda: os.environ.get("RUN_TOKEN", ""))

    http_min_interval: float = field(default_factory=lambda: _float("HTTP_MIN_INTERVAL", 1.1, 0.0))
    http_timeout: float = field(default_factory=lambda: _float("HTTP_TIMEOUT", 30.0, 1.0))
    http_retries: int = field(default_factory=lambda: _int("HTTP_RETRIES", 3, 0, 10))
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        )
    )

    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "https://text.pollinations.ai/openai").rstrip("/")
    )
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "openai"))
    llm_enabled: bool = field(default_factory=lambda: _bool("LLM_ENABLED", True))
    llm_timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT", 60.0, 5.0))
    llm_min_gap: float = field(default_factory=lambda: _float("LLM_MIN_GAP_SECONDS", 0.0, 0.0))
    llm_retries: int = field(default_factory=lambda: _int("LLM_RETRIES", 3, 1, 10))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))
    llm_max_prompt_chars: int = field(default_factory=lambda: _int("LLM_MAX_PROMPT_CHARS", 0, 0))
    llm_backfill_limit: int = field(default_factory=lambda: _int("LLM_BACKFILL_LIMIT", 4, 0))
    llm_failure_threshold: int = field(default_factory=lambda: _int("LLM_MAX_CONSECUTIVE_FAILURES", 3, 1))
    llm_reasoning_effort: str | None = field(default_factory=lambda: os.environ.get("LLM_REASONING_EFFORT"))

    youtube_enabled: bool = field(default_factory=lambda: _bool("YOUTUBE_ENABLED", True))
    youtube_proxy_url: str = field(default_factory=lambda: os.environ.get("YOUTUBE_PROXY_URL", ""))
    youtube_max_candidates: int = field(default_factory=lambda: _int("YOUTUBE_MAX_CANDIDATES", 3, 1, 10))
    letsplay_refresh_days: int = field(default_factory=lambda: _int("LETSPLAY_REFRESH_DAYS", 7, 1))
    letsplay_retry_hours: int = field(default_factory=lambda: _int("LETSPLAY_RETRY_HOURS", 24, 1))
    trailer_fallback: bool = field(default_factory=lambda: _bool("YOUTUBE_TRAILER_FALLBACK", True))
    trailer_refresh_days: int = field(default_factory=lambda: _int("TRAILER_REFRESH_DAYS", 7, 1))

    similar_top_n: int = field(default_factory=lambda: _int("SIMILAR_TOP_N", 5, 1, 20))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    @property
    def day_tz(self) -> tzinfo:
        return resolve_tz(self.day_tz_name)

    def today(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        return now.astimezone(self.day_tz).date().isoformat()

    def next_midnight(self, now: datetime) -> datetime:
        local = now.astimezone(self.day_tz)
        start = datetime(local.year, local.month, local.day, tzinfo=self.day_tz) + timedelta(days=1)
        return start.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


settings = Settings()
