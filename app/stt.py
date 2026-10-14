import gc
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from .youtube import VIDEO_ID

log = logging.getLogger("stt")

SAMPLE_RATE = 16000
CHUNK_BYTES = 4 * 1024 * 1024
MAX_AUDIO_BYTES = 64 * 1024 * 1024
MIN_PARTIAL_BYTES = 1024 * 1024
MIN_AUDIO_SECONDS = 30
MIN_TEXT_CHARS = 200
MIN_ABR = 40
VAD_WINDOW_SECONDS = 60
BLOCK_SPEECH_SECONDS = 120
FAILURE_THRESHOLD = 2
WATCH_URL = "https://www.youtube.com/watch?v={}"
NO_RETRY_STATUSES = {401, 403, 404, 410}


class SpeechUnavailable(Exception):
    pass


class SpeechFatal(SpeechUnavailable):
    pass


class NoSpeech(SpeechUnavailable):
    pass


def min_speech_seconds(audio_seconds: float) -> float:
    return max(15.0, min(60.0, audio_seconds * 0.07))


def short_error(exc, limit: int = 200) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    text = re.sub(r"^ERROR:\s*", "", text) or exc.__class__.__name__
    return text[:limit]


def _is_drc(fmt: dict) -> bool:
    return "drc" in f"{fmt.get('format_id', '')} {fmt.get('format_note', '')}".lower()


def audio_only_formats(formats) -> list[dict]:
    out = []
    for f in formats or []:
        if not isinstance(f, dict) or f.get("has_drm"):
            continue
        if f.get("vcodec") != "none" or f.get("acodec") in (None, "none"):
            continue
        if f.get("protocol") not in ("https", "http") or not f.get("url"):
            continue
        out.append(f)
    return out


def pick_audio_format(formats) -> dict | None:
    candidates = audio_only_formats(formats)
    if not candidates:
        return None

    def key(f: dict):
        abr = f.get("abr") or f.get("tbr") or 0
        return (_is_drc(f), -(f.get("language_preference") or 0), not abr, abr < MIN_ABR, abr)

    return min(candidates, key=key)


def bytes_needed(fmt: dict, seconds: float, duration: float | None = None) -> int:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    abr = fmt.get("abr") or fmt.get("tbr")
    if abr:
        need = int(abr * 125 * seconds * 1.05) + 256 * 1024
    elif size and duration:
        need = int(size * min(1.0, seconds / duration) * 1.05) + 256 * 1024
    else:
        need = MAX_AUDIO_BYTES
    if size:
        need = min(need, int(size))
    return max(1, min(need, MAX_AUDIO_BYTES))


@dataclass
class AudioFile:
    path: str
    format_id: str
    ext: str
    codec: str
    abr: float | None
    bytes: int
    duration: int | None
    complete: bool

    def label(self) -> str:
        kbps = f", {round(self.abr)} кбит/с" if self.abr else ""
        return f"{self.format_id} ({self.ext}/{self.codec}{kbps})"


class _YtDlpLogger:
    def debug(self, msg):
        log.debug("yt-dlp: %s", msg)

    def info(self, msg):
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg):
        log.debug("yt-dlp: %s", msg)

    def error(self, msg):
        log.info("yt-dlp: %s", short_error(msg))


def _read_upto(resp, limit: int) -> bytes:
    parts, got = [], 0
    while got < limit:
        piece = resp.read(min(1024 * 1024, limit - got))
        if not piece:
            break
        parts.append(piece)
        got += len(piece)
    return b"".join(parts)


class YtDlpAudioSource:
    def __init__(self, limiter=None, proxy_url: str = "", request_gap: float = 1.1, timeout: float = 30.0,
                 retries: int = 2, ydl_factory=None, sleep=time.sleep):
        self.limiter = limiter
        self.proxy_url = proxy_url
        self.request_gap = request_gap
        self.timeout = timeout
        self.retries = retries
        self.ydl_factory = ydl_factory
        self._sleep = sleep

    def options(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "noplaylist": True,
            "cachedir": False,
            "socket_timeout": self.timeout,
            "sleep_interval_requests": self.request_gap,
            "extractor_retries": 1,
            "logger": _YtDlpLogger(),
            "extractor_args": {"youtube": {"skip": ["hls", "dash", "translated_subs"]}},
        }
        if self.proxy_url:
            opts["proxy"] = self.proxy_url
        return opts

    def _ydl(self):
        if self.ydl_factory is not None:
            return self.ydl_factory(self.options())
        try:
            import yt_dlp
        except ImportError as exc:
            raise SpeechFatal("не установлен yt-dlp") from exc
        return yt_dlp.YoutubeDL(self.options())

    def _wait(self, host: str) -> None:
        if self.limiter is not None:
            self.limiter.wait(host)

    def fetch(self, video_id: str, dest_dir: str, max_seconds: float) -> AudioFile:
        with self._ydl() as ydl:
            self._wait("www.youtube.com")
            try:
                info = ydl.extract_info(WATCH_URL.format(video_id), download=False)
            except Exception as exc:
                raise SpeechUnavailable(f"yt-dlp не получил данные ролика: {short_error(exc)}") from exc
            if not isinstance(info, dict):
                raise SpeechUnavailable("yt-dlp вернул пустой ответ")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise SpeechUnavailable("ролик идёт в прямом эфире или ещё не вышел")
            fmt = pick_audio_format(info.get("formats"))
            if fmt is None:
                raise SpeechUnavailable("у ролика нет отдельной аудиодорожки")
            duration = info.get("duration")
            need = bytes_needed(fmt, max_seconds, duration)
            ext = re.sub(r"[^a-z0-9]", "", str(fmt.get("ext") or "").lower()) or "audio"
            path = os.path.join(dest_dir, f"{video_id}.{ext}")
            got, complete = self._download(ydl, fmt, need, path)
        log.info("Audio for %s: format %s, %.1f of %.1f MB", video_id, fmt.get("format_id"), got / 1048576,
                 need / 1048576)
        return AudioFile(path=path, format_id=str(fmt.get("format_id") or "?"), ext=ext,
                         codec=str(fmt.get("acodec") or "?"), abr=fmt.get("abr") or fmt.get("tbr"), bytes=got,
                         duration=int(duration) if isinstance(duration, (int, float)) else None, complete=complete)

    def _download(self, ydl, fmt: dict, need: int, path: str) -> tuple[int, bool]:
        url = fmt["url"]
        host = urlparse(url).netloc
        headers = dict(fmt.get("http_headers") or {})
        got = 0
        with open(path, "wb") as fh:
            while got < need:
                end = min(need, got + CHUNK_BYTES) - 1
                try:
                    data, whole = self._fetch_range(ydl, url, headers, host, got, end, need)
                except SpeechUnavailable as exc:
                    if got >= MIN_PARTIAL_BYTES:
                        log.info("Audio download stopped at %.1f MB, using what arrived: %s", got / 1048576, exc)
                        return got, False
                    raise
                if not data:
                    break
                fh.write(data)
                got += len(data)
                if whole:
                    break
        if got == 0:
            raise SpeechUnavailable("аудиодорожка пустая")
        return got, got >= need

    def _fetch_range(self, ydl, url: str, headers: dict, host: str, start: int, end: int,
                     need: int) -> tuple[bytes, bool]:
        from yt_dlp.networking import Request

        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                resp = ydl.urlopen(Request(url, headers={**headers, "Range": f"bytes={start}-{end}"}))
                try:
                    status = getattr(resp, "status", 200)
                    if status == 206:
                        return resp.read(end - start + 1), False
                    if start == 0:
                        return _read_upto(resp, need), True
                    raise SpeechUnavailable("сервер YouTube не поддержал загрузку по частям")
                finally:
                    resp.close()
            except SpeechUnavailable:
                raise
            except Exception as exc:
                last = exc
                status = getattr(exc, "status", None)
                log.info("Audio range %d-%d failed (%s), attempt %d", start, end, short_error(exc), attempt + 1)
                if status in NO_RETRY_STATUSES:
                    break
                if attempt < self.retries:
                    self._sleep(2.0 * (2 ** attempt))
        raise SpeechUnavailable(f"не удалось скачать аудио: {short_error(last) if last else 'нет ответа'}")


def decode_audio(path: str, max_seconds: float):
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise SpeechFatal("не установлены PyAV или numpy") from exc
    limit = int(max_seconds * SAMPLE_RATE)
    out = np.zeros(limit, dtype=np.float32)
    filled = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

    def push(frames) -> None:
        nonlocal filled
        for fr in frames:
            pcm = fr.to_ndarray().reshape(-1)
            take = min(len(pcm), limit - filled)
            if take > 0:
                out[filled:filled + take] = pcm[:take].astype(np.float32) / 32768.0
                filled += take

    try:
        with av.open(path) as container:
            if not container.streams.audio:
                raise SpeechUnavailable("в скачанном файле нет аудио")
            for frame in container.decode(audio=0):
                push(resampler.resample(frame))
                if filled >= limit:
                    break
            else:
                push(resampler.resample(None))
    except SpeechUnavailable:
        raise
    except Exception as exc:
        if filled == 0:
            raise SpeechUnavailable(f"не удалось декодировать аудио: {short_error(exc)}") from exc
        log.debug("Audio decode stopped at %.0f s: %s", filled / SAMPLE_RATE, exc)
    return out[:filled]


def default_vad(audio) -> list[tuple[int, int]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return [(t["start"], t["end"]) for t in get_speech_timestamps(audio, VadOptions())]


def default_model_factory(name: str, model_dir: str, threads: int):
    os.makedirs(model_dir, exist_ok=True)
    os.environ.setdefault("HF_HOME", os.path.join(model_dir, "hf-home"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    from faster_whisper import WhisperModel

    kwargs = {"device": "cpu", "compute_type": "int8", "cpu_threads": threads, "download_root": model_dir}
    try:
        return WhisperModel(name, local_files_only=True, **kwargs)
    except ValueError:
        raise
    except Exception:
        log.info("Whisper model %s is not cached in %s yet, downloading it", name, model_dir)
    return WhisperModel(name, **kwargs)


@dataclass
class Transcript:
    text: str
    language: str
    speech_seconds: float
    seconds: float
    complete: bool


class WhisperTranscriber:
    def __init__(self, model_name: str = "base", model_dir: str = "./data/models", threads: int = 1,
                 model_factory=None, vad=None, clock=time.monotonic):
        self.model_name = model_name
        self.model_dir = model_dir
        self.threads = threads
        self.model_factory = model_factory or default_model_factory
        self.vad = vad or default_vad
        self.clock = clock
        self.load_seconds: float | None = None
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        with self._lock:
            if self._model is None:
                started = self.clock()
                try:
                    self._model = self.model_factory(self.model_name, self.model_dir, self.threads)
                except ImportError as exc:
                    raise SpeechFatal("не установлен faster-whisper") from exc
                except Exception as exc:
                    raise SpeechFatal(f"модель Whisper «{self.model_name}» не загрузилась: {short_error(exc)}") from exc
                self.load_seconds = round(self.clock() - started, 1)
                log.info("Whisper model %s loaded in %.1f s (%d threads)", self.model_name, self.load_seconds,
                         self.threads)
            return self._model

    def release(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
        gc.collect()
        log.info("Whisper model %s released", self.model_name)

    def speech_spans(self, audio) -> list[tuple[int, int]]:
        window = VAD_WINDOW_SECONDS * SAMPLE_RATE
        spans = []
        for start in range(0, len(audio), window):
            part = audio[start:start + window]
            spans.extend((start + s, start + e) for s, e in self.vad(part) if e > s)
        return spans

    def speech_blocks(self, audio, spans: list[tuple[int, int]]):
        import numpy as np

        block = BLOCK_SPEECH_SECONDS * SAMPLE_RATE
        pending, size = [], 0
        for s, e in spans:
            pending.append(audio[s:e])
            size += e - s
            if size >= block:
                yield np.concatenate(pending)
                pending, size = [], 0
        if pending:
            yield np.concatenate(pending)

    def transcribe(self, audio, budget_seconds: float) -> Transcript:
        started = self.clock()
        spans = self.speech_spans(audio)
        found = sum(e - s for s, e in spans) / SAMPLE_RATE
        total = len(audio) / SAMPLE_RATE
        if found < min_speech_seconds(total):
            raise NoSpeech(f"в аудио почти нет речи: детектор речи нашёл {found:.0f} с из {total:.0f}")
        model = self.load()
        texts: list[str] = []
        language = None
        speech = 0.0
        complete = True
        for block in self.speech_blocks(audio, spans):
            if self.clock() - started >= budget_seconds:
                complete = False
                break
            segments, info = model.transcribe(block, language=language, beam_size=1, vad_filter=False,
                                              condition_on_previous_text=False, without_timestamps=True)
            language = language or getattr(info, "language", None)
            for seg in segments:
                text = " ".join((getattr(seg, "text", "") or "").split())
                if text and (not texts or text != texts[-1]):
                    texts.append(text)
                if self.clock() - started >= budget_seconds:
                    complete = False
                    break
            speech += len(block) / SAMPLE_RATE
            if not complete:
                break
        return Transcript(text=" ".join(texts), language=language or "?", speech_seconds=round(speech, 1),
                          seconds=round(self.clock() - started, 1), complete=complete)


@dataclass
class SpeechResult:
    text: str
    language: str
    model: str
    audio_seconds: float
    speech_seconds: float
    transcribe_seconds: float
    total_seconds: float
    download_seconds: float
    decode_seconds: float
    load_seconds: float | None
    audio_format: str
    audio_bytes: int
    complete: bool

    @property
    def speed(self) -> float | None:
        if not self.transcribe_seconds:
            return None
        return round(self.audio_seconds / self.transcribe_seconds, 1)

    def meta(self) -> dict:
        return {
            "method": "whisper",
            "engine": "faster-whisper",
            "model": self.model,
            "audio_seconds": round(self.audio_seconds),
            "speech_seconds": round(self.speech_seconds),
            "transcribe_seconds": self.transcribe_seconds,
            "total_seconds": self.total_seconds,
            "download_seconds": self.download_seconds,
            "decode_seconds": self.decode_seconds,
            "model_load_seconds": self.load_seconds,
            "speed_x": self.speed,
            "audio_format": self.audio_format,
            "audio_bytes": self.audio_bytes,
            "complete": self.complete,
        }


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class SpeechToText:
    def __init__(self, settings, limiter=None, source=None, transcriber=None, clock=time.monotonic,
                 on_state=None, tmp_root: str | None = None):
        self.enabled = settings.stt_enabled
        self.model_name = settings.stt_model
        self.max_seconds = settings.stt_max_minutes * 60
        self.max_per_run = settings.stt_max_per_run
        self.timeout = settings.stt_timeout_seconds
        self.source = source or YtDlpAudioSource(limiter, settings.youtube_proxy_url, settings.http_min_interval,
                                                 settings.http_timeout)
        self.transcriber = transcriber or WhisperTranscriber(settings.stt_model, settings.stt_model_dir,
                                                             settings.stt_cpu_threads, clock=clock)
        self.clock = clock
        self.on_state = on_state
        self.tmp_root = tmp_root
        self.used = 0
        self.failures = 0
        self.blocked: str | None = None
        self.done_total = 0
        self.failed_total = 0
        self.last: SpeechResult | None = None

    def describe(self) -> str:
        return (f"Whisper «{self.model_name}» на CPU, первые {self.max_seconds // 60} мин аудио, "
                f"не больше {self.max_per_run} роликов за запуск")

    def _state(self, status: str, detail: str) -> None:
        if self.on_state:
            try:
                self.on_state(status, detail)
            except Exception:
                pass

    def unavailable_reason(self) -> str | None:
        if not self.enabled:
            return "распознавание речи выключено (STT_ENABLED=0)"
        if self.blocked:
            return f"распознавание речи пропускается до конца запуска: {self.blocked}"
        if self.used >= self.max_per_run:
            return f"исчерпан лимит распознаваний за запуск ({self.max_per_run})"
        return None

    @property
    def available(self) -> bool:
        return self.unavailable_reason() is None

    def begin_run(self) -> None:
        self.used = 0
        self.failures = 0
        self.blocked = None
        if self.enabled:
            self._state("idle", self.describe())

    def end_run(self) -> None:
        try:
            self.transcriber.release()
        except Exception:
            log.exception("Could not release the Whisper model")

    def _failed(self, exc: SpeechUnavailable) -> None:
        self.failed_total += 1
        if not isinstance(exc, NoSpeech):
            self.failures += 1
        if isinstance(exc, SpeechFatal) or self.failures >= FAILURE_THRESHOLD:
            self.blocked = short_error(exc, 160)
        tail = "; до конца запуска распознавание пропускается" if self.blocked else ""
        status = "idle" if isinstance(exc, NoSpeech) and not self.blocked else "error"
        self._state(status, f"{exc}{tail}")

    def transcribe_video(self, video_id: str) -> SpeechResult:
        if not VIDEO_ID.match(video_id or ""):
            raise SpeechUnavailable("некорректный id ролика")
        reason = self.unavailable_reason()
        if reason:
            raise SpeechUnavailable(reason)
        self.used += 1
        started = self.clock()
        workdir = tempfile.mkdtemp(prefix="stt-", dir=self.tmp_root)
        try:
            self._state("running", f"скачиваю аудио ролика {video_id} (первые {self.max_seconds // 60} мин)")
            audio_file = self.source.fetch(video_id, workdir, self.max_seconds)
            downloaded = self.clock()
            audio = decode_audio(audio_file.path, self.max_seconds)
            _remove(audio_file.path)
            decoded = self.clock()
            audio_seconds = len(audio) / SAMPLE_RATE
            if audio_seconds < MIN_AUDIO_SECONDS:
                raise SpeechUnavailable(f"скачано слишком мало аудио ({audio_seconds:.0f} с)")
            self._state("running", f"распознаю {audio_seconds / 60:.0f} мин аудио ролика {video_id}, "
                                   f"модель {self.model_name}")
            budget = max(10.0, self.timeout - (self.clock() - started))
            was_loaded = getattr(self.transcriber, "loaded", True)
            tr = self.transcriber.transcribe(audio, budget)
            del audio
            text = " ".join(tr.text.split())
            if len(text) < MIN_TEXT_CHARS:
                raise NoSpeech(f"в первых {audio_seconds / 60:.0f} мин аудио почти нет речи "
                               f"({len(text)} символов)")
        except SpeechUnavailable as exc:
            log.info("Whisper for %s failed: %s", video_id, exc)
            self._failed(exc)
            raise
        except Exception as exc:
            log.exception("Whisper for %s crashed", video_id)
            err = SpeechUnavailable(f"{exc.__class__.__name__}: {short_error(exc)}")
            self._failed(err)
            raise err from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        result = SpeechResult(text=text, language=tr.language, model=self.model_name, audio_seconds=audio_seconds,
                              speech_seconds=tr.speech_seconds, transcribe_seconds=tr.seconds,
                              total_seconds=round(self.clock() - started, 1),
                              download_seconds=round(downloaded - started, 1),
                              decode_seconds=round(decoded - downloaded, 1),
                              load_seconds=None if was_loaded else getattr(self.transcriber, "load_seconds", None),
                              audio_format=audio_file.label(), audio_bytes=audio_file.bytes,
                              complete=tr.complete and audio_file.complete)
        self.failures = 0
        self.done_total += 1
        self.last = result
        speed = f", в {result.speed:g} раз быстрее реального времени" if result.speed else ""
        self._state("ok", f"последний ролик {video_id}: {audio_seconds / 60:.0f} мин аудио распознано за "
                          f"{tr.seconds:.0f} с{speed}, {len(text)} символов; всего роликов {self.done_total}")
        log.info("Whisper %s for %s: %.0f s audio, %.0f s speech, %.1f s, %d chars, lang %s", self.model_name,
                 video_id, audio_seconds, tr.speech_seconds, tr.seconds, len(text), tr.language)
        return result
