import asyncio
import hmac
import json
import logging
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, iso, memory_limit_bytes, parse_iso, settings as default_settings, utcnow
from .db import SORTS, Database
from .http_client import PoliteClient
from .llm import LLMClient
from .metacritic import MetacriticClient, safe_url
from .monitor import TRIGGER_LABELS, Monitor, MonitorLogHandler, monitor as default_monitor
from .pipeline import Pipeline
from .scheduler import Scheduler
from .stt import SpeechToText
from .youtube import VIDEO_ID, YouTubeClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 24
MAX_PAGE = 10000
STATIC_VERSION = "8"
SSE_MAX_SECONDS = 600
API_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json", "/healthz", "/static/")
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
SORT_LABELS = {
    "date_desc": "Сначала новые",
    "date_asc": "Сначала старые",
    "metascore_desc": "Metascore: по убыванию",
    "metascore_asc": "Metascore: по возрастанию",
    "userscore_desc": "Userscore: по убыванию",
    "userscore_asc": "Userscore: по возрастанию",
    "title": "По названию",
    "updated": "Недавно обновлённые",
}
METHOD_LABELS = {
    "llm": "LLM",
    "extractive": "цитаты без LLM",
    "none": "нет данных",
    "llm-transcript": "LLM по расшифровке речи",
    "extractive-transcript": "фразы из расшифровки без LLM",
    "llm-whisper": "LLM по речи, распознанной Whisper",
    "extractive-whisper": "фразы из речи, распознанной Whisper, без LLM",
    "llm-description": "LLM по описанию ролика",
    "description": "описание ролика",
}
STATUS_LABELS = {
    "idle": "ожидает", "running": "работает", "waiting": "ждёт запуска", "ok": "в норме", "error": "ошибка",
    "off": "выключен", "partial": "частично", "failed": "сбой", "empty": "нечего обрабатывать",
    "interrupted": "прерван", "cut": "остановлен по времени", "skipped": "пропускается",
}
STEP_LABELS = {
    "run": "Запуск",
    "selection": "Список игр Metacritic",
    "similar": "Похожие игры",
    "youtube": "YouTube: поиск летсплея",
    "llm:critic": "LLM: резюме критиков",
    "llm:user": "LLM: резюме игроков",
    "данные Metacritic": "Metacritic: неполные данные",
    "game": "Игра",
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; "
       "frame-src https://cdn.jwplayer.com https://www.youtube-nocookie.com; connect-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}
CSP_EXEMPT = ("/docs", "/redoc")

log = logging.getLogger("app")
STT_MIN_MEMORY = 1100 * 1024 * 1024
STT_BASE_MEMORY = 1536 * 1024 * 1024


def source_label(source: str | None) -> str:
    labels = []
    for part in (source or "").split(","):
        part = part.strip()
        if not part:
            continue
        if part == "new-releases":
            labels.append("New Releases")
        elif part == "retry":
            labels.append("повтор упавших")
        elif part.startswith("browse:p") and part[8:].isdigit():
            labels.append(f"SEE ALL, стр. {part[8:]}")
        else:
            labels.append(part)
    return ", ".join(labels) or "—"


def step_label(step: str | None) -> str:
    step = step or ""
    schema = step.endswith(":schema")
    base = step[: -len(":schema")] if schema else step
    label = STEP_LABELS.get(base, base or "—")
    return f"{label} (формат ответа изменился)" if schema else label


def run_seconds(run: dict) -> int | None:
    start, end = parse_iso(run.get("started_at")), parse_iso(run.get("finished_at"))
    if not start or not end:
        return None
    return max(0, round((end - start).total_seconds()))


def public_run(run: dict | None) -> dict | None:
    if not run:
        return None
    out = {k: v for k, v in run.items() if k not in ("owner", "heartbeat_at")}
    out["seconds"] = run_seconds(run)
    out["source_label"] = source_label(run.get("sources"))
    out["trigger_label"] = TRIGGER_LABELS.get(run.get("trigger"), run.get("trigger"))
    return out


def stt_memory_plan(s: Settings, limit: int | None) -> tuple[bool, str | None, str]:
    if limit is None or not s.stt_enabled:
        return s.stt_enabled, None, s.stt_model
    gib = limit / 1024 ** 3
    if limit < STT_MIN_MEMORY:
        return False, (f"распознавание речи выключено: контейнеру доступно {gib:.1f} ГБ памяти, а Whisper нужно "
                       "больше 1,1 ГБ; остаются субтитры YouTube"), s.stt_model
    if limit < STT_BASE_MEMORY and not s.stt_model_explicit and s.stt_model == "base":
        return True, f"доступно {gib:.1f} ГБ памяти, поэтому выбрана лёгкая модель tiny", "tiny"
    return True, None, s.stt_model


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        exempt = scope.get("path", "").startswith(CSP_EXEMPT)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in SECURITY_HEADERS.items():
                    if exempt and key in ("content-security-policy", "x-frame-options"):
                        continue
                    if key not in headers:
                        headers[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


def setup_logging(level: str, monitor: Monitor) -> None:
    root = logging.getLogger()
    handler = next((h for h in root.handlers if isinstance(h, MonitorLogHandler)), None)
    if handler is None:
        root.addHandler(MonitorLogHandler(monitor))
    else:
        handler.monitor = monitor
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, MonitorLogHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(sh)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "faster_whisper", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_services(s: Settings, db: Database, monitor: Monitor) -> Pipeline:
    http = PoliteClient(s.user_agent, s.http_min_interval, s.http_timeout, s.http_retries,
                        on_request=monitor.http_event)
    llm = LLMClient(s.llm_base_url, s.llm_model, s.llm_api_key, s.llm_timeout, s.llm_min_gap or None,
                    s.llm_retries, s.llm_temperature, s.llm_enabled, on_call=monitor.llm_event,
                    max_prompt_chars=s.llm_max_prompt_chars or None, failure_threshold=s.llm_failure_threshold,
                    on_state=lambda status, detail: monitor.set_worker("llm", status, detail),
                    reasoning_effort=s.llm_reasoning_effort)
    yt = YouTubeClient(http, s.youtube_proxy_url) if s.youtube_enabled else None
    stt = None
    stt_on, memory_note, model = stt_memory_plan(s, memory_limit_bytes())
    if memory_note:
        log.warning("Whisper: %s", memory_note)
    if not s.youtube_enabled:
        monitor.set_worker("youtube", "off", "поиск летсплеев выключен (YOUTUBE_ENABLED=0)")
        monitor.set_worker("stt", "off", "выключено вместе с YouTube (YOUTUBE_ENABLED=0)")
    elif not s.stt_enabled:
        monitor.set_worker("stt", "off", "распознавание речи выключено (STT_ENABLED=0), остаются субтитры YouTube")
    elif not stt_on:
        monitor.set_worker("stt", "off", memory_note)
    else:
        s.stt_model = model
        stt = SpeechToText(s, http.limiter, on_state=lambda status, detail: monitor.set_worker("stt", status, detail))
        when = ("субтитры отключены (YOUTUBE_CAPTIONS=0), речь всегда распознаётся локально" if not s.youtube_captions
                else "включается, если у роликов нет субтитров")
        extra = f"; {memory_note}" if memory_note else ""
        monitor.set_worker("stt", "idle", f"{stt.describe()}; {when}{extra}")
    if not s.llm_enabled:
        monitor.set_worker("llm", "off", "LLM выключена, резюме строятся без неё")
    return Pipeline(s, db, monitor, MetacriticClient(http), yt, llm, stt=stt)


def fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        d = datetime.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def fmt_num(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", " ")


def fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h} ч {m} мин" if h else f"{m} мин"


def fmt_wait(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_seconds(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    m, s = divmod(seconds, 60)
    return f"{m} мин {s:02d} с" if m < 60 else f"{m // 60} ч {m % 60:02d} мин"


def meta_class(score) -> str:
    if score is None:
        return "none"
    return "good" if score >= 75 else "mixed" if score >= 50 else "bad"


def user_class(score) -> str:
    if score is None:
        return "none"
    return "good" if score >= 7.5 else "mixed" if score >= 5 else "bad"


def ru_plural(n, one: str, few: str, many: str) -> str:
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def fmt_user(score) -> str:
    return "—" if score is None else f"{score:.1f}"


def web_url(value) -> str:
    return safe_url(value, schemes=("https", "http")) or ""


def make_templates(s: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
    env = templates.env

    def fmt_dt(value: str | None) -> str:
        if not value:
            return "—"
        dt = parse_iso(value)
        if dt is None:
            return value
        return dt.astimezone(s.day_tz).strftime("%d.%m %H:%M")

    env.filters.update(
        date_ru=fmt_date, num=fmt_num, duration=fmt_duration, meta_class=meta_class, user_class=user_class,
        user_fmt=fmt_user, dt=fmt_dt, wait=fmt_wait, url=web_url, plural=ru_plural, secs=fmt_seconds,
        method=lambda m: METHOD_LABELS.get(m or "none", m or ""),
        status_ru=lambda st: STATUS_LABELS.get(st, st),
        trigger_ru=lambda t: TRIGGER_LABELS.get(t, t),
        source_ru=source_label, step_ru=step_label,
    )
    env.globals.update(sort_labels=SORT_LABELS, tz_name=s.day_tz_name, static_version=STATIC_VERSION)
    return templates


@dataclass
class RunDecision:
    allowed: bool
    status_code: int = 202
    reason: str = ""
    retry_after: int = 0
    code: str = "ok"


class RunGuard:
    def __init__(self, settings: Settings, db: Database, pipeline: Pipeline, clock=utcnow):
        self.settings = settings
        self.db = db
        self.pipeline = pipeline
        self.clock = clock
        self._lock = threading.Lock()

    def state(self) -> dict:
        s = self.settings
        now = self.clock()
        day = s.today(now)
        runs_today = self.db.count_runs(day, "manual")
        info = {
            "allowed": True,
            "code": "ok",
            "reason": "",
            "retry_after": 0,
            "available_at": None,
            "runs_today": runs_today,
            "daily_cap": s.manual_daily_cap,
            "cooldown_seconds": s.manual_cooldown_seconds,
            "token_required_for_api": bool(s.run_token),
        }
        if self.pipeline.running:
            info.update(allowed=False, code="busy", reason="Обработка уже идёт")
            return info
        finished = parse_iso(self.db.last_finished_at())
        if finished and s.manual_cooldown_seconds:
            ready = finished.timestamp() + s.manual_cooldown_seconds
            wait = math.ceil(ready - now.timestamp())
            if wait > 0:
                info.update(allowed=False, code="cooldown", retry_after=wait,
                            available_at=iso(datetime.fromtimestamp(ready, now.tzinfo)),
                            reason=f"Между запусками нужна пауза {max(1, s.manual_cooldown_seconds // 60)} мин, "
                                   "чтобы не перегружать Metacritic, YouTube и LLM")
                return info
        if s.manual_daily_cap and runs_today >= s.manual_daily_cap:
            midnight = s.next_midnight(now)
            wait = max(1, math.ceil((midnight - now).total_seconds()))
            info.update(allowed=False, code="cap", retry_after=wait, available_at=iso(midnight),
                        reason=f"Достигнут дневной лимит ручных запусков ({s.manual_daily_cap}). "
                               "Плановые запуски продолжаются каждый час")
        return info

    def try_start(self, limit: int | None = None) -> RunDecision:
        with self._lock:
            st = self.state()
            if not st["allowed"]:
                code = 409 if st["code"] == "busy" else 429
                return RunDecision(False, code, st["reason"], st["retry_after"], st["code"])
            if not self.pipeline.start_background("manual", limit):
                return RunDecision(False, 409, "Обработка уже идёт", 0, "busy")
            return RunDecision(True, 202, "Обработка запущена", 0, "started")


def from_our_page(request: Request) -> bool:
    return request.headers.get("sec-fetch-site") == "same-origin"


def same_origin(request: Request) -> bool:
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True
    if origin == "null":
        return False
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlparse(origin).netloc.lower() == host.split(",")[0].strip().lower()


def token_ok(request: Request, token: str) -> bool:
    if not token:
        return True
    auth = request.headers.get("authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-run-token", "")
    return bool(given) and hmac.compare_digest(given.encode(), token.encode())


def skip_reason(item: dict, lp: dict) -> str:
    if item.get("reason") != "субтитров нет, речь не распознана":
        return item.get("reason") or ""
    vid = item.get("video_id")
    captions = [c["reason"] for c in lp.get("checked") or [] if c.get("video_id") == vid]
    speech = [n["reason"] for n in lp.get("stt_checked") or [] if n.get("video_id") == vid]
    parts = [f"субтитры: {captions[0]}"] if captions else []
    if speech:
        parts.append(f"Whisper: {speech[0]}")
    return "; ".join(parts) or item["reason"]


def video_info(g: dict) -> dict | None:
    embed = safe_url(g.get("video_embed_url"))
    if embed:
        return {"source": "metacritic", "embed_url": embed, "url": embed, "title": g.get("video_title"),
                "manifest_url": safe_url(g.get("video_manifest_url"))}
    trailer = g.get("trailer") or {}
    video = trailer.get("video") or {}
    vid = video.get("video_id")
    if trailer.get("status") == "ok" and isinstance(vid, str) and VIDEO_ID.match(vid):
        return {"source": "youtube", "embed_url": f"https://www.youtube-nocookie.com/embed/{vid}",
                "url": f"https://www.youtube.com/watch?v={vid}", "title": video.get("title"),
                "channel": video.get("channel"), "views": video.get("views"), "manifest_url": None,
                "base_game": bool(video.get("base_game"))}
    return None


class StatusCache:
    def __init__(self, max_age: float = 5.0):
        self.max_age = max_age
        self._lock = threading.Lock()
        self._key = None
        self._at = 0.0
        self._value: dict | None = None

    def get(self, version: int, build) -> dict:
        with self._lock:
            if self._value is not None and self._key == version and time.monotonic() - self._at < self.max_age:
                return self._value
        value = build()
        with self._lock:
            self._key, self._at, self._value = version, time.monotonic(), value
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None


def build_status(app: FastAPI) -> dict:
    s: Settings = app.state.settings
    db: Database = app.state.db
    mon: Monitor = app.state.monitor
    snap = mon.snapshot()
    day = s.today(app.state.clock())
    snap.update(
        day=day,
        running=app.state.pipeline.running,
        totals={
            "games_total": db.count_games(),
            "processed_today": db.processed_count(day),
            "processed_today_ok": db.processed_count(day, "ok"),
            "errors_today": db.error_count(day),
            "errors_total": db.error_count(),
        },
        day_state=db.get_day_state(day).__dict__,
        runs_today=db.count_runs(day, "manual") + db.count_runs(day, "schedule"),
        last_run=public_run(next(iter(db.public_runs(1)), None)),
        runs=[public_run(r) for r in db.public_runs(8)],
        errors=[{**e, "step_label": step_label(e.get("step"))} for e in db.recent_errors(8)],
        manual=app.state.guard.state(),
        scheduler=app.state.scheduler.health(),
        config={
            "batch_size": s.batch_size,
            "interval_minutes": s.run_interval_minutes,
            "run_max_minutes": s.run_max_minutes,
            "scheduler_enabled": s.scheduler_enabled,
            "llm_model": s.llm_model,
            "llm_endpoint": s.llm_base_url.split("//")[-1].split("/")[0],
            "youtube_enabled": s.youtube_enabled,
            "timezone": s.day_tz_name,
            "browse_page_size": s.browse_page_size,
        },
    )
    return snap


def status_payload(app: FastAPI) -> dict:
    return app.state.status_cache.get(app.state.monitor.version, lambda: build_status(app))


def create_app(s: Settings | None = None, pipeline: Pipeline | None = None, start_scheduler: bool = True,
               monitor: Monitor | None = None, clock=utcnow) -> FastAPI:
    s = s or default_settings
    mon = monitor or default_monitor
    mon.tz = s.day_tz

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(s.log_level, mon)
        closed = app.state.db.close_stale_runs(clock(), s.lease_ttl_seconds)
        if closed:
            log.warning("Закрыто запусков, оставшихся от остановленного процесса: %d", closed)
        if start_scheduler:
            app.state.scheduler.start()
        log.info("Сервис запущен: база %s, схема v%d", os.path.basename(s.db_path), app.state.db.schema_version)
        yield
        if start_scheduler:
            app.state.scheduler.stop()

    app = FastAPI(title="Metacritic Games Digest", lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    db = pipeline.db if pipeline is not None else Database(s.db_path)
    app.state.settings = s
    app.state.db = db
    app.state.monitor = mon
    app.state.clock = clock
    app.state.pipeline = pipeline or build_services(s, db, mon)
    app.state.scheduler = Scheduler(s, db, app.state.pipeline, mon, clock=clock)
    app.state.guard = RunGuard(s, db, app.state.pipeline, clock=clock)
    app.state.status_cache = StatusCache()
    templates = make_templates(s)
    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

    def wants_html(request: Request) -> bool:
        return not request.url.path.startswith(API_PREFIXES)

    def error_page(request: Request, status: int, title: str, text: str) -> HTMLResponse:
        return templates.TemplateResponse(request, "error.html", {"status": status, "title": title, "text": text},
                                          status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if not wants_html(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        if exc.status_code == 404:
            return error_page(request, 404, "Страница не найдена",
                              exc.detail if exc.detail and exc.detail != "Not Found" else
                              "Такой страницы нет. Возможно, ссылка устарела.")
        if exc.status_code == 405:
            return error_page(request, 405, "Так нельзя", "Этот адрес не принимает такой запрос.")
        return error_page(request, exc.status_code, "Что-то пошло не так", "Попробуйте позже.")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if not wants_html(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        return error_page(request, 422, "Некорректный адрес страницы",
                          "В адресе есть параметры, которые сервис не понимает. Откройте список игр заново.")

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.exception("Ошибка при обработке %s", request.url.path)
        if not wants_html(request):
            return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)
        return error_page(request, 500, "Что-то пошло не так", "Произошла внутренняя ошибка. Попробуйте позже.")

    def game_view(slug: str) -> dict:
        g = db.get_game(slug=slug)
        if not g:
            raise HTTPException(status_code=404, detail="Игра не найдена")
        sim = g.get("similar") or []
        briefs = db.brief_games([x["id"] for x in sim])
        g["similar_games"] = [{**briefs[x["id"]], "score": x["score"], "reasons": x.get("reasons", []),
                               "weak": bool(x.get("weak"))}
                              for x in sim if x["id"] in briefs]
        lp = g.get("letsplay")
        if isinstance(lp, dict) and lp.get("passed_over"):
            lp["passed_over"] = [{**item, "reason": skip_reason(item, lp)} for item in lp["passed_over"]]
        g["metacritic_url"] = f"https://www.metacritic.com/game/{g['slug']}/"
        g["video"] = video_info(g)
        g["video_url"] = g["video"]["url"] if g["video"] else None
        for key in ("backfill_at", "backfill_attempts", "title_search"):
            g.pop(key, None)
        return g

    def query_games(q, platform, sort, page):
        sort = sort if sort in SORTS else "date_desc"
        rows, total = db.list_games(q or None, platform or None, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE)
        return rows, total, sort

    def run_response(decision: RunDecision) -> JSONResponse:
        headers = {"Retry-After": str(decision.retry_after)} if decision.retry_after else {}
        if decision.allowed:
            app.state.status_cache.invalidate()
        return JSONResponse({"started": decision.allowed, "code": decision.code, "detail": decision.reason,
                             "retry_after": decision.retry_after}, status_code=decision.status_code, headers=headers)

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def index(request: Request, q: str = Query("", max_length=200), platform: str = Query("", max_length=100),
              sort: str = "date_desc", page: str = Query("1", max_length=12)):
        requested = int(page) if page.strip().isdigit() else 1
        total = db.list_games(q or None, platform or None, "date_desc", 1, 0)[1]
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(1, requested), pages)
        rows, total, sort = query_games(q, platform, sort, page)

        def page_url(n: int) -> str:
            params = {k: v for k, v in {"q": q, "platform": platform, "sort": sort, "page": n}.items() if v}
            return "/?" + urlencode(params)

        return templates.TemplateResponse(request, "index.html", {
            "games": rows, "total": total, "q": q, "platform": platform, "sort": sort, "page": page,
            "pages": pages, "page_url": page_url, "platforms": db.platform_options(),
            "games_in_db": db.count_games(), "running": app.state.pipeline.running, "batch_size": s.batch_size,
            "interval_label": "час" if s.run_interval_minutes == 60 else f"{s.run_interval_minutes} мин",
            "scheduler_enabled": s.scheduler_enabled, "requested_page": requested,
        })

    @app.get("/game/{slug}", response_class=HTMLResponse)
    def game_page(request: Request, slug: str):
        return templates.TemplateResponse(request, "game.html", {"g": game_view(slug)})

    @app.get("/monitor", response_class=HTMLResponse)
    def monitor_page(request: Request, started: int = 0, busy: int = 0, wait: int = 0, cap: int = 0,
                     forbidden: int = 0):
        return templates.TemplateResponse(request, "monitor.html", {
            "st": build_status(app), "started": started, "busy": busy, "wait": wait, "cap": cap,
            "forbidden": forbidden,
        })

    @app.post("/run")
    def run_form(request: Request):
        wants_json = "application/json" in request.headers.get("accept", "")
        if not same_origin(request):
            decision = RunDecision(False, 403, "Запуск разрешён только со страницы мониторинга этого сервиса",
                                   code="forbidden")
        elif s.run_token and not from_our_page(request) and not token_ok(request, s.run_token):
            decision = RunDecision(False, 401, "Запуск не со страницы мониторинга требует токен RUN_TOKEN",
                                   code="unauthorized")
        else:
            decision = app.state.guard.try_start()
        if wants_json:
            return run_response(decision)
        query = {"started": "started=1", "busy": "busy=1", "forbidden": "forbidden=1", "unauthorized": "forbidden=1",
                 "cap": "cap=1", "cooldown": f"wait={decision.retry_after}"}.get(decision.code, "busy=1")
        return RedirectResponse("/monitor?" + query, status_code=303)

    @app.post("/api/run")
    def run_api(request: Request, limit: int | None = Query(default=None, ge=1, le=100)):
        if not same_origin(request):
            return run_response(RunDecision(False, 403, "Кросс-доменные запросы на запуск запрещены",
                                            code="forbidden"))
        if not token_ok(request, s.run_token):
            return JSONResponse({"started": False, "code": "unauthorized",
                                 "detail": "Нужен токен: заголовок Authorization: Bearer <RUN_TOKEN>"},
                                status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return run_response(app.state.guard.try_start(limit))

    @app.get("/api/games")
    def api_games(q: str = Query("", max_length=200), platform: str = Query("", max_length=100),
                  sort: str = "date_desc", page: int = Query(1, ge=1, le=MAX_PAGE)):
        rows, total, sort = query_games(q, platform, sort, page)
        return {"total": total, "page": page, "page_size": PAGE_SIZE, "sort": sort, "items": rows}

    @app.get("/api/games/{slug}")
    def api_game(slug: str):
        return game_view(slug)

    @app.get("/api/platforms")
    def api_platforms():
        return db.platform_options()

    @app.get("/api/status")
    def api_status():
        return status_payload(app)

    @app.get("/api/runs")
    def api_runs(limit: int = Query(default=20, ge=1, le=200)):
        return [public_run(r) for r in db.public_runs(limit)]

    @app.get("/api/events")
    async def events(request: Request, last_log: int = Query(0, ge=0), once: int = 0):
        async def stream():
            last_version = -1
            last_id = last_log
            idle = 0.0
            opened = time.monotonic()
            while time.monotonic() - opened < SSE_MAX_SECONDS:
                if await request.is_disconnected():
                    break
                version = mon.version
                if version != last_version or idle >= 15:
                    payload = await asyncio.to_thread(status_payload, app)
                    yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    logs = mon.logs_since(last_id)
                    if logs:
                        last_id = logs[-1]["id"]
                        yield f"event: log\ndata: {json.dumps(logs, ensure_ascii=False)}\n\n"
                    last_version = version
                    idle = 0.0
                    if once:
                        break
                await asyncio.sleep(1)
                idle += 1

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        sched = app.state.scheduler.health()
        try:
            games = db.count_games()
            db_ok = True
        except Exception:
            games, db_ok = None, False
        healthy = db_ok and sched["healthy"]
        body = {"status": "ok" if healthy else "degraded", "db": db_ok, "games": games,
                "running": app.state.pipeline.running, "scheduler": sched}
        return JSONResponse(body, status_code=200 if healthy else 503)

    return app
