import os
from types import SimpleNamespace

import numpy as np
import pytest

from app import stt as stt_mod
from app.stt import (
    MAX_AUDIO_BYTES,
    SAMPLE_RATE,
    NoSpeech,
    SpeechFatal,
    SpeechUnavailable,
    WhisperTranscriber,
    YtDlpAudioSource,
    bytes_needed,
    decode_audio,
    min_speech_seconds,
    pick_audio_format,
)

from .conftest import FakeAudioSource, FakeTranscriber, make_stt, write_wav

GV = "https://rr3---sn-test.googlevideo.com/videoplayback?id=1"


def fmt(fid, abr, ext="webm", acodec="opus", vcodec="none", protocol="https", **kw):
    return {"format_id": fid, "abr": abr, "ext": ext, "acodec": acodec, "vcodec": vcodec, "protocol": protocol,
            "url": f"{GV}&itag={fid}", **kw}


YOUTUBE_FORMATS = [
    fmt("18", 96, ext="mp4", acodec="mp4a.40.2", vcodec="avc1"),
    fmt("137", None, ext="mp4", acodec="none", vcodec="avc1"),
    fmt("139-drc", 48.8, ext="m4a", acodec="mp4a.40.5", format_note="low, DRC"),
    fmt("139", 48.8, ext="m4a", acodec="mp4a.40.5"),
    fmt("249", 48.6),
    fmt("251", 125.3),
    fmt("599", 31.0, format_note="ultralow"),
    fmt("233", 64, protocol="m3u8_native"),
    fmt("777", 20, has_drm=True),
]


def test_pick_audio_format_prefers_a_small_plain_audio_track():
    assert pick_audio_format(YOUTUBE_FORMATS)["format_id"] == "249"
    assert pick_audio_format([f for f in YOUTUBE_FORMATS if f["format_id"] != "249"])["format_id"] == "139"
    assert pick_audio_format([fmt("599", 31.0), fmt("600", 35.0)])["format_id"] == "599"
    dubbed = [fmt("249-0", 48.6, language_preference=-1), fmt("251-1", 125.0, language_preference=10)]
    assert pick_audio_format(dubbed)["format_id"] == "251-1"


def test_pick_audio_format_ignores_video_manifests_and_drm():
    assert pick_audio_format([YOUTUBE_FORMATS[0], YOUTUBE_FORMATS[1], YOUTUBE_FORMATS[7], YOUTUBE_FORMATS[8]]) is None
    assert pick_audio_format(None) is None


def test_bytes_needed_covers_the_requested_minutes_and_nothing_more():
    assert bytes_needed({"abr": 48.0}, 900) == int(48 * 125 * 900 * 1.05) + 256 * 1024
    assert bytes_needed({"abr": 48.0, "filesize": 1_000_000}, 900) == 1_000_000
    assert bytes_needed({"filesize": 36_000_000}, 900, duration=3600) == int(9_000_000 * 1.05) + 256 * 1024
    assert bytes_needed({}, 900) == MAX_AUDIO_BYTES
    assert bytes_needed({"abr": 320}, 3600) == MAX_AUDIO_BYTES


class FakeResponse:
    def __init__(self, status, data):
        self.status = status
        self._data = data
        self._pos = 0
        self.closed = False

    def read(self, n=None):
        n = len(self._data) - self._pos if n is None else n
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class HTTPFailure(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP Error {status}")
        self.status = status


class FakeYDL:
    def __init__(self, info, data=b"", status=206, fail_from=None, error=None, raise_extract=None):
        self.info = info
        self.data = data
        self.status = status
        self.fail_from = fail_from
        self.error = error
        self.raise_extract = raise_extract
        self.ranges = []
        self.extracted = []
        self.opts = None

    def __call__(self, opts):
        self.opts = opts
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        self.extracted.append((url, download))
        if self.raise_extract:
            raise self.raise_extract
        return self.info

    def urlopen(self, req):
        start, end = (int(x) for x in req.headers["Range"].split("=")[1].split("-"))
        self.ranges.append((start, end))
        if self.error is not None and (self.fail_from is None or start >= self.fail_from):
            raise self.error
        if self.status == 206:
            return FakeResponse(206, self.data[start:end + 1])
        return FakeResponse(200, self.data)


class Limiter:
    def __init__(self):
        self.hosts = []

    def wait(self, host):
        self.hosts.append(host)


def info_with(formats, **kw):
    return {"id": "abcdefghijk", "duration": 3600, "formats": formats, **kw}


def test_audio_source_downloads_only_the_first_minutes_in_polite_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(stt_mod, "CHUNK_BYTES", 100_000)
    data = bytes(range(256)) * 1200
    ydl = FakeYDL(info_with([fmt("249", 0.025, filesize=len(data))]), data=data)
    limiter = Limiter()
    source = YtDlpAudioSource(limiter, proxy_url="http://proxy:8080", request_gap=1.5, ydl_factory=ydl)
    need = bytes_needed({"abr": 0.025}, 60)
    assert need < len(data)
    audio = source.fetch("abcdefghijk", str(tmp_path), 60)
    assert ydl.extracted == [("https://www.youtube.com/watch?v=abcdefghijk", False)]
    assert ydl.ranges == [(0, 99_999), (100_000, 199_999), (200_000, need - 1)]
    assert audio.bytes == need and audio.complete and audio.format_id == "249"
    assert open(audio.path, "rb").read() == data[:need]
    assert limiter.hosts[0] == "www.youtube.com"
    assert limiter.hosts[1:] == ["rr3---sn-test.googlevideo.com"] * len(ydl.ranges)
    assert ydl.opts["proxy"] == "http://proxy:8080" and ydl.opts["sleep_interval_requests"] == 1.5
    assert ydl.opts["skip_download"] and ydl.opts["cachedir"] is False
    assert "hls" in ydl.opts["extractor_args"]["youtube"]["skip"]


def test_audio_source_stops_reading_when_the_server_ignores_range(tmp_path):
    data = b"x" * 500_000
    ydl = FakeYDL(info_with([fmt("249", 1.0)]), data=data, status=200)
    audio = YtDlpAudioSource(ydl_factory=ydl).fetch("abcdefghijk", str(tmp_path), 60)
    need = bytes_needed({"abr": 1.0}, 60)
    assert len(ydl.ranges) == 1 and audio.bytes == need == os.path.getsize(audio.path)


def test_audio_source_keeps_a_partial_download_and_does_not_retry_403(tmp_path, monkeypatch):
    monkeypatch.setattr(stt_mod, "CHUNK_BYTES", 1000)
    monkeypatch.setattr(stt_mod, "MIN_PARTIAL_BYTES", 2000)
    data = b"y" * 10_000
    sleeps = []
    ydl = FakeYDL(info_with([fmt("249", 1.0, filesize=len(data))]), data=data, fail_from=3000,
                  error=HTTPFailure(403))
    audio = YtDlpAudioSource(ydl_factory=ydl, sleep=sleeps.append).fetch("abcdefghijk", str(tmp_path), 600)
    assert audio.bytes == 3000 and not audio.complete
    assert ydl.ranges.count((3000, 3999)) == 1 and sleeps == []


def test_audio_source_retries_network_errors_then_gives_up(tmp_path):
    sleeps = []
    ydl = FakeYDL(info_with([fmt("249", 1.0)]), data=b"z" * 1000, error=ConnectionResetError("reset"))
    with pytest.raises(SpeechUnavailable, match="не удалось скачать аудио"):
        YtDlpAudioSource(ydl_factory=ydl, retries=2, sleep=sleeps.append).fetch("abcdefghijk", str(tmp_path), 60)
    assert len(ydl.ranges) == 3 and sleeps == [2.0, 4.0]


@pytest.mark.parametrize("info, message", [
    (info_with([fmt("249", 48)], is_live=True), "прямом эфире"),
    (info_with([YOUTUBE_FORMATS[0]]), "нет отдельной аудиодорожки"),
    (None, "пустой ответ"),
])
def test_audio_source_rejects_unusable_videos(tmp_path, info, message):
    with pytest.raises(SpeechUnavailable, match=message):
        YtDlpAudioSource(ydl_factory=FakeYDL(info)).fetch("abcdefghijk", str(tmp_path), 60)


def test_audio_source_reports_extractor_errors(tmp_path):
    error = Exception("\x1b[0;31mERROR:\x1b[0m [youtube] abcdefghijk: Sign in to confirm you're not a bot")
    with pytest.raises(SpeechUnavailable, match="YouTube просит подтвердить, что запрос не от бота") as info:
        YtDlpAudioSource(ydl_factory=FakeYDL({}, raise_extract=error)).fetch("abcdefghijk", str(tmp_path), 60)
    assert "\x1b" not in str(info.value) and "Sign in" not in str(info.value)
    other = Exception("ERROR: [youtube] abcdefghijk: Unsupported container quirk")
    with pytest.raises(SpeechUnavailable, match="Unsupported container quirk"):
        YtDlpAudioSource(ydl_factory=FakeYDL({}, raise_extract=other)).fetch("abcdefghijk", str(tmp_path), 60)


def test_decode_audio_resamples_to_16k_mono_and_caps_the_length(tmp_path):
    path = write_wav(tmp_path / "tone.wav", 12.0, rate=8000, channels=2)
    audio = decode_audio(path, 60)
    assert audio.dtype == np.float32 and abs(len(audio) / SAMPLE_RATE - 12.0) < 0.05
    assert 0.2 < float(np.abs(audio).max()) <= 1.0
    assert len(decode_audio(path, 5)) == 5 * SAMPLE_RATE


def test_decode_audio_survives_a_truncated_file_and_rejects_garbage(tmp_path):
    path = write_wav(tmp_path / "tone.wav", 10.0, rate=16000, channels=1)
    raw = open(path, "rb").read()
    cut = tmp_path / "cut.wav"
    cut.write_bytes(raw[: len(raw) // 2 + 3])
    audio = decode_audio(str(cut), 60)
    assert 4.5 < len(audio) / SAMPLE_RATE < 5.5
    junk = tmp_path / "junk.webm"
    junk.write_bytes(b"not an audio file at all" * 100)
    with pytest.raises(SpeechUnavailable, match="декодировать"):
        decode_audio(str(junk), 60)


class FakeSegment(SimpleNamespace):
    pass


class FakeWhisperModel:
    def __init__(self, clock=None, step=0.0, lines=None):
        self.calls = []
        self.clock = clock
        self.step = step
        self.lines = lines or ["Hello there.", "Hello there.", "This game is great."]

    def transcribe(self, audio, **kw):
        self.calls.append((len(audio), kw))

        def gen():
            for line in self.lines:
                if self.clock:
                    self.clock.advance(seconds=self.step)
                yield FakeSegment(text=f"  {line} ")

        return gen(), SimpleNamespace(language="en")


class MonoClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def whole_window_is_speech(part):
    return [(0, len(part))]


def test_transcriber_feeds_whisper_speech_in_bounded_blocks_and_reuses_the_language():
    model = FakeWhisperModel()
    factory_calls = []

    def factory(name, model_dir, threads):
        factory_calls.append((name, model_dir, threads))
        return model

    tr = WhisperTranscriber("base", "/models", 2, model_factory=factory, vad=whole_window_is_speech)
    audio = np.zeros(300 * SAMPLE_RATE, dtype=np.float32)
    result = tr.transcribe(audio, budget_seconds=600)
    assert [n // SAMPLE_RATE for n, _ in model.calls] == [120, 120, 60]
    assert [kw["language"] for _, kw in model.calls] == [None, "en", "en"]
    assert all(kw["beam_size"] == 1 and kw["vad_filter"] is False for _, kw in model.calls)
    assert result.text.count("Hello there.") == 3 and result.text.endswith("This game is great.")
    assert result.language == "en" and result.speech_seconds == 300 and result.complete
    tr.transcribe(audio[: 60 * SAMPLE_RATE], budget_seconds=600)
    assert factory_calls == [("base", "/models", 2)]
    tr.release()
    assert not tr.loaded
    tr.transcribe(audio[: 60 * SAMPLE_RATE], budget_seconds=600)
    assert len(factory_calls) == 2


def test_transcriber_drops_silence_reported_by_vad():
    model = FakeWhisperModel()

    def vad(part):
        return [(0, 10 * SAMPLE_RATE), (30 * SAMPLE_RATE, 40 * SAMPLE_RATE)]

    tr = WhisperTranscriber(model_factory=lambda *a: model, vad=vad)
    result = tr.transcribe(np.zeros(240 * SAMPLE_RATE, dtype=np.float32), budget_seconds=600)
    assert [n // SAMPLE_RATE for n, _ in model.calls] == [80]
    assert result.speech_seconds == 80


def test_transcriber_skips_videos_without_speech_before_loading_the_model():
    loads = []

    def factory(*a):
        loads.append(a)
        return FakeWhisperModel()

    tr = WhisperTranscriber(model_factory=factory, vad=lambda part: [(0, SAMPLE_RATE)])
    with pytest.raises(NoSpeech, match="детектор речи нашёл 15 с из 900"):
        tr.transcribe(np.zeros(900 * SAMPLE_RATE, dtype=np.float32), budget_seconds=600)
    assert loads == []
    assert min_speech_seconds(900) == 60 and min_speech_seconds(60) == 15


def test_no_speech_does_not_count_as_a_failure_of_the_service(settings):
    stt = make_stt(settings, transcriber=FakeTranscriber(error=NoSpeech("в аудио почти нет речи")))
    stt.begin_run()
    for vid in ("abcdefghijk", "bbcdefghijk", "cbcdefghijk"):
        with pytest.raises(NoSpeech):
            stt.transcribe_video(vid)
    assert stt.unavailable_reason() is None and stt.failures == 0 and stt.used == 3


def test_transcriber_stops_at_the_time_budget():
    clock = MonoClock()
    model = FakeWhisperModel(clock=clock, step=20.0)
    tr = WhisperTranscriber(model_factory=lambda *a: model, vad=whole_window_is_speech, clock=clock)
    result = tr.transcribe(np.zeros(600 * SAMPLE_RATE, dtype=np.float32), budget_seconds=50)
    assert not result.complete and len(model.calls) == 1 and result.text


def test_transcriber_turns_load_errors_into_fatal_errors():
    def missing(*a):
        raise ImportError("No module named 'faster_whisper'")

    def offline(*a):
        raise OSError("Cannot reach huggingface.co")

    with pytest.raises(SpeechFatal, match="не установлен faster-whisper"):
        WhisperTranscriber(model_factory=missing).load()
    with pytest.raises(SpeechFatal, match="huggingface"):
        WhisperTranscriber("small", model_factory=offline).load()


def test_speech_to_text_returns_text_and_metadata_and_cleans_up(settings, tmp_path):
    tmp_root = tmp_path / "stt"
    tmp_root.mkdir()
    states = []
    source = FakeAudioSource(seconds=40)
    tr = FakeTranscriber()
    stt = make_stt(settings, source, tr, tmp_root, on_state=lambda s, d: states.append(s))
    stt.begin_run()
    result = stt.transcribe_video("abcdefghijk")
    assert result.language == "en" and len(result.text) > 200 and result.model == "base"
    meta = result.meta()
    assert meta["method"] == "whisper" and meta["model"] == "base" and meta["audio_seconds"] == 40
    assert meta["audio_format"].startswith("249") and meta["complete"] and meta["model_load_seconds"] == 0.4
    assert source.calls[0][:2] == ("abcdefghijk", settings.stt_max_minutes * 60)
    assert abs(tr.calls[0][0] / SAMPLE_RATE - 40) < 0.1
    assert os.listdir(tmp_root) == []
    assert states == ["idle", "running", "running", "ok"]
    stt.end_run()
    assert tr.released == 1


@pytest.mark.parametrize("source, transcriber, message, status", [
    (FakeAudioSource(error=SpeechUnavailable("yt-dlp не получил данные ролика: Sign in")), None, "Sign in", "error"),
    (FakeAudioSource(seconds=10), None, "слишком мало аудио", "error"),
    (None, FakeTranscriber(text="Music. Music."), "почти нет речи", "idle"),
    (None, FakeTranscriber(error=RuntimeError("ctranslate2 exploded")), "RuntimeError", "error"),
])
def test_speech_to_text_fails_soft_and_removes_the_audio(settings, tmp_path, source, transcriber, message, status):
    tmp_root = tmp_path / "stt"
    tmp_root.mkdir()
    states = []
    stt = make_stt(settings, source, transcriber, tmp_root, on_state=lambda s, d: states.append(s))
    with pytest.raises(SpeechUnavailable, match=message):
        stt.transcribe_video("abcdefghijk")
    assert os.listdir(tmp_root) == []
    assert states[-1] == status and stt.failed_total == 1


def test_speech_to_text_limits_runs_and_blocks_after_repeated_failures(settings):
    settings.stt_max_per_run = 2
    stt = make_stt(settings)
    stt.begin_run()
    stt.transcribe_video("abcdefghijk")
    stt.transcribe_video("bbcdefghijk")
    assert stt.unavailable_reason() and "лимит" in stt.unavailable_reason()
    with pytest.raises(SpeechUnavailable, match="лимит"):
        stt.transcribe_video("cbcdefghijk")
    stt.begin_run()
    assert stt.unavailable_reason() is None

    settings.stt_max_per_run = 10
    failing = make_stt(settings, FakeAudioSource(error=SpeechUnavailable("HTTP 429")))
    failing.begin_run()
    for _ in range(2):
        with pytest.raises(SpeechUnavailable):
            failing.transcribe_video("abcdefghijk")
    assert "до конца запуска" in failing.unavailable_reason()
    assert len(failing.source.calls) == 2
    failing.begin_run()
    assert failing.unavailable_reason() is None


def test_fatal_errors_block_whisper_for_the_rest_of_the_run(settings):
    stt = make_stt(settings, transcriber=FakeTranscriber(error=SpeechFatal("модель Whisper «base» не загрузилась")))
    stt.begin_run()
    with pytest.raises(SpeechFatal):
        stt.transcribe_video("abcdefghijk")
    assert stt.unavailable_reason() and "не загрузилась" in stt.unavailable_reason()


def test_disabled_or_bad_ids_never_touch_the_network(settings):
    settings.stt_enabled = False
    source = FakeAudioSource()
    stt = make_stt(settings, source)
    assert "STT_ENABLED=0" in stt.unavailable_reason()
    with pytest.raises(SpeechUnavailable):
        stt.transcribe_video("abcdefghijk")
    settings.stt_enabled = True
    with pytest.raises(SpeechUnavailable, match="id"):
        make_stt(settings, source).transcribe_video("../../etc/passwd")
    assert source.calls == []


def test_real_components_are_wired_from_settings(settings, monkeypatch):
    monkeypatch.setenv("STT_THREADS", "3")
    from app.config import Settings

    s = Settings()
    stt = stt_mod.SpeechToText(s, limiter=Limiter())
    assert isinstance(stt.source, YtDlpAudioSource) and isinstance(stt.transcriber, WhisperTranscriber)
    assert stt.transcriber.threads == 3 and stt.transcriber.model_name == "base"
    assert stt.transcriber.model_dir == os.path.join(os.path.dirname(os.path.abspath(s.db_path)), "models")
    assert stt.max_seconds == 15 * 60 and stt.source.request_gap == s.http_min_interval
