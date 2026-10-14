import logging
from datetime import datetime, timezone

from app import config
from app.config import MSK, Settings, resolve_tz


def test_day_rolls_over_at_moscow_midnight(monkeypatch):
    monkeypatch.setenv("DAY_TZ", "Europe/Moscow")
    s = Settings()
    assert s.today(datetime(2026, 10, 14, 20, 59, 59, tzinfo=timezone.utc)) == "2026-10-14"
    assert s.today(datetime(2026, 10, 14, 21, 0, 0, tzinfo=timezone.utc)) == "2026-10-15"
    assert s.next_midnight(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)) == datetime(
        2026, 10, 14, 21, 0, tzinfo=timezone.utc)


def test_unknown_time_zone_falls_back_to_utc_with_a_warning(monkeypatch, caplog):
    resolve_tz.cache_clear()
    monkeypatch.setenv("DAY_TZ", "Mars/Olympus")
    with caplog.at_level(logging.WARNING, logger="config"):
        s = Settings()
        assert s.day_tz == timezone.utc
    assert any("Mars/Olympus" in r.getMessage() for r in caplog.records)
    assert s.today(datetime(2026, 10, 14, 23, 30, tzinfo=timezone.utc)) == "2026-10-14"


def test_moscow_has_a_fixed_offset_fallback_without_tzdata(monkeypatch):
    import zoneinfo

    resolve_tz.cache_clear()

    def broken(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(zoneinfo, "ZoneInfo", broken)
    try:
        assert resolve_tz("Europe/Moscow") is MSK
        assert resolve_tz("MSK") is MSK
    finally:
        resolve_tz.cache_clear()


def test_tzdata_package_is_available():
    import importlib.util

    assert importlib.util.find_spec("tzdata") is not None


def test_numeric_settings_are_clamped(monkeypatch, caplog):
    monkeypatch.setenv("RUN_INTERVAL_MINUTES", "0")
    monkeypatch.setenv("BATCH_SIZE", "-5")
    monkeypatch.setenv("RUN_MAX_MINUTES", "abc")
    monkeypatch.setenv("MANUAL_RUN_COOLDOWN_SECONDS", "-1")
    with caplog.at_level(logging.WARNING, logger="config"):
        s = Settings()
    assert s.run_interval_minutes == 1
    assert s.batch_size == 1
    assert s.run_max_minutes == 50
    assert s.manual_cooldown_seconds == 0
    assert any("RUN_INTERVAL_MINUTES" in r.getMessage() for r in caplog.records)


def test_parse_iso_handles_naive_and_bad_values():
    assert config.parse_iso("2026-10-14T10:00:00").tzinfo == timezone.utc
    assert config.parse_iso("nope") is None and config.parse_iso(None) is None


def test_speech_recognition_settings(monkeypatch, tmp_path):
    for name in ("STT_ENABLED", "STT_MODEL", "STT_MAX_MINUTES", "STT_MAX_PER_RUN", "STT_TIMEOUT_SECONDS",
                 "STT_THREADS", "STT_MODEL_DIR", "YOUTUBE_CAPTIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db" / "games.db"))
    s = Settings()
    assert s.stt_enabled and s.youtube_captions and s.stt_model == "base"
    assert (s.stt_max_minutes, s.stt_max_per_run, s.stt_timeout_seconds) == (15, 6, 300)
    assert s.stt_model_dir == str(tmp_path / "db" / "models")
    assert 1 <= s.stt_cpu_threads <= 4

    monkeypatch.setenv("STT_ENABLED", "0")
    monkeypatch.setenv("STT_MODEL", "small")
    monkeypatch.setenv("STT_MAX_MINUTES", "500")
    monkeypatch.setenv("STT_MAX_PER_RUN", "0")
    monkeypatch.setenv("STT_THREADS", "2")
    monkeypatch.setenv("STT_MODEL_DIR", "/models")
    monkeypatch.setenv("YOUTUBE_CAPTIONS", "off")
    s = Settings()
    assert not s.stt_enabled and not s.youtube_captions and s.stt_model == "small"
    assert s.stt_max_minutes == 60 and s.stt_max_per_run == 1 and s.stt_cpu_threads == 2
    assert s.stt_model_dir == "/models"
    monkeypatch.setenv("STT_MAX_MINUTES", "0")
    monkeypatch.setenv("STT_MODEL", "  ")
    s = Settings()
    assert s.stt_max_minutes == 1 and s.stt_model == "base"
