import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        if name in {"Europe/Moscow", "MSK"}:
            return timezone(timedelta(hours=3), "MSK")
        return timezone.utc


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "./data/games.db"))
    batch_size: int = field(default_factory=lambda: _int("BATCH_SIZE", 20))
    browse_page_size: int = field(default_factory=lambda: _int("BROWSE_PAGE_SIZE", 24))
    browse_max_pages: int = field(default_factory=lambda: _int("BROWSE_MAX_PAGES", 4))
    run_interval_minutes: int = field(default_factory=lambda: _int("RUN_INTERVAL_MINUTES", 60))
    scheduler_enabled: bool = field(default_factory=lambda: _bool("SCHEDULER_ENABLED", True))
    startup_delay_seconds: int = field(default_factory=lambda: _int("STARTUP_DELAY_SECONDS", 15))
    day_tz_name: str = field(default_factory=lambda: os.environ.get("DAY_TZ", "Europe/Moscow"))

    http_min_interval: float = field(default_factory=lambda: _float("HTTP_MIN_INTERVAL", 1.1))
    http_timeout: float = field(default_factory=lambda: _float("HTTP_TIMEOUT", 30.0))
    http_retries: int = field(default_factory=lambda: _int("HTTP_RETRIES", 3))
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
    llm_timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT", 120.0))
    llm_min_gap: float = field(default_factory=lambda: _float("LLM_MIN_GAP_SECONDS", 0.0))
    llm_retries: int = field(default_factory=lambda: _int("LLM_RETRIES", 3))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))
    llm_max_prompt_chars: int = field(default_factory=lambda: _int("LLM_MAX_PROMPT_CHARS", 0))
    llm_backfill_limit: int = field(default_factory=lambda: _int("LLM_BACKFILL_LIMIT", 4))

    youtube_enabled: bool = field(default_factory=lambda: _bool("YOUTUBE_ENABLED", True))
    youtube_proxy_url: str = field(default_factory=lambda: os.environ.get("YOUTUBE_PROXY_URL", ""))
    youtube_max_candidates: int = field(default_factory=lambda: _int("YOUTUBE_MAX_CANDIDATES", 3))
    letsplay_refresh_days: int = field(default_factory=lambda: _int("LETSPLAY_REFRESH_DAYS", 7))

    similar_top_n: int = field(default_factory=lambda: _int("SIMILAR_TOP_N", 5))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    @property
    def day_tz(self) -> tzinfo:
        return _tz(self.day_tz_name)

    def today(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        return now.astimezone(self.day_tz).date().isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


settings = Settings()
