import asyncio
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings, settings as default_settings
from .db import SORTS, Database
from .http_client import PoliteClient
from .llm import LLMClient
from .metacritic import MetacriticClient
from .monitor import Monitor, MonitorLogHandler, monitor as default_monitor
from .pipeline import Pipeline
from .scheduler import Scheduler
from .youtube import YouTubeClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 24
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
    "llm-description": "LLM по описанию ролика",
    "description": "описание ролика",
}
STATUS_LABELS = {
    "idle": "ожидает", "running": "работает", "waiting": "ждёт запуска", "ok": "в норме", "error": "ошибка",
    "off": "выключен", "partial": "частично", "failed": "сбой", "empty": "нечего обрабатывать",
    "interrupted": "прерван",
}
TRIGGER_LABELS = {"manual": "вручную", "schedule": "по расписанию", "startup": "при старте"}

log = logging.getLogger("app")


def setup_logging(level: str, monitor: Monitor) -> None:
    root = logging.getLogger()
    if not any(isinstance(h, MonitorLogHandler) for h in root.handlers):
        root.addHandler(MonitorLogHandler(monitor))
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, MonitorLogHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(sh)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_services(s: Settings, db: Database, monitor: Monitor) -> Pipeline:
    http = PoliteClient(s.user_agent, s.http_min_interval, s.http_timeout, s.http_retries,
                        on_request=monitor.http_event)
    llm = LLMClient(s.llm_base_url, s.llm_model, s.llm_api_key, s.llm_timeout, s.llm_min_gap or None,
                    s.llm_retries, s.llm_temperature, s.llm_enabled, on_call=monitor.llm_event,
                    max_prompt_chars=s.llm_max_prompt_chars or None)
    yt = YouTubeClient(http, s.youtube_proxy_url) if s.youtube_enabled else None
    if not s.youtube_enabled:
        monitor.set_worker("youtube", "off", "поиск летсплеев выключен (YOUTUBE_ENABLED=0)")
    if not s.llm_enabled:
        monitor.set_worker("llm", "off", "LLM выключена, резюме строятся без неё")
    return Pipeline(s, db, monitor, MetacriticClient(http), yt, llm)


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
    return f"{int(value):,}".replace(",", " ")


def fmt_duration(seconds) -> str:
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h} ч {m} мин" if h else f"{m} мин"


def meta_class(score) -> str:
    if score is None:
        return "none"
    return "good" if score >= 75 else "mixed" if score >= 50 else "bad"


def user_class(score) -> str:
    if score is None:
        return "none"
    return "good" if score >= 7.5 else "mixed" if score >= 5 else "bad"


def fmt_user(score) -> str:
    return "—" if score is None else f"{score:.1f}"


def make_templates(s: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
    env = templates.env

    def fmt_dt(value: str | None) -> str:
        if not value:
            return "—"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        return dt.astimezone(s.day_tz).strftime("%d.%m %H:%M")

    env.filters.update(
        date_ru=fmt_date, num=fmt_num, duration=fmt_duration, meta_class=meta_class, user_class=user_class,
        user_fmt=fmt_user, dt=fmt_dt,
        method=lambda m: METHOD_LABELS.get(m or "none", m or ""),
        status_ru=lambda st: STATUS_LABELS.get(st, st),
        trigger_ru=lambda t: TRIGGER_LABELS.get(t, t),
    )
    env.globals.update(sort_labels=SORT_LABELS, tz_name=s.day_tz_name)
    return templates


def status_payload(app: FastAPI) -> dict:
    s: Settings = app.state.settings
    db: Database = app.state.db
    mon: Monitor = app.state.monitor
    snap = mon.snapshot()
    day = s.today()
    last = db.last_run()
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
        last_run=last,
        runs=db.recent_runs(8),
        errors=db.recent_errors(8),
        config={
            "batch_size": s.batch_size,
            "interval_minutes": s.run_interval_minutes,
            "scheduler_enabled": s.scheduler_enabled,
            "llm_model": s.llm_model,
            "llm_endpoint": s.llm_base_url.split("//")[-1].split("/")[0],
            "youtube_enabled": s.youtube_enabled,
            "timezone": s.day_tz_name,
        },
    )
    return snap


def create_app(s: Settings | None = None, pipeline: Pipeline | None = None, start_scheduler: bool = True,
               monitor: Monitor | None = None) -> FastAPI:
    s = s or default_settings
    mon = monitor or default_monitor

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(s.log_level, mon)
        app.state.db.close_stale_runs()
        if start_scheduler:
            app.state.scheduler.start()
        log.info("Service started, DB %s", os.path.abspath(s.db_path))
        yield
        if start_scheduler:
            app.state.scheduler.stop()

    app = FastAPI(title="Metacritic Games Digest", lifespan=lifespan)
    db = Database(s.db_path)
    app.state.settings = s
    app.state.db = db
    app.state.monitor = mon
    app.state.pipeline = pipeline or build_services(s, db, mon)
    app.state.scheduler = Scheduler(s, db, app.state.pipeline, mon)
    templates = make_templates(s)
    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

    def game_view(slug: str) -> dict:
        g = db.get_game(slug=slug)
        if not g:
            raise HTTPException(status_code=404, detail="Игра не найдена")
        sim = g.get("similar") or []
        briefs = db.brief_games([x["id"] for x in sim])
        g["similar_games"] = [{**briefs[x["id"]], "score": x["score"], "reasons": x.get("reasons", [])}
                              for x in sim if x["id"] in briefs]
        g["metacritic_url"] = f"https://www.metacritic.com/game/{g['slug']}/"
        return g

    def query_games(q, platform, sort, page):
        sort = sort if sort in SORTS else "date_desc"
        page = max(1, page)
        rows, total = db.list_games(q or None, platform or None, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE)
        return rows, total, sort, page

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, q: str = "", platform: str = "", sort: str = "date_desc", page: int = 1):
        rows, total, sort, page = query_games(q, platform, sort, page)
        pages = max(1, math.ceil(total / PAGE_SIZE))

        def page_url(n: int) -> str:
            params = {k: v for k, v in {"q": q, "platform": platform, "sort": sort, "page": n}.items() if v}
            return "/?" + urlencode(params)

        return templates.TemplateResponse(request, "index.html", {
            "games": rows, "total": total, "q": q, "platform": platform, "sort": sort, "page": page,
            "pages": pages, "page_url": page_url, "platforms": db.platform_options(),
            "games_in_db": db.count_games(), "running": app.state.pipeline.running,
        })

    @app.get("/game/{slug}", response_class=HTMLResponse)
    def game_page(request: Request, slug: str):
        return templates.TemplateResponse(request, "game.html", {"g": game_view(slug)})

    @app.get("/monitor", response_class=HTMLResponse)
    def monitor_page(request: Request, started: int = 0, busy: int = 0):
        return templates.TemplateResponse(request, "monitor.html", {
            "st": status_payload(app), "started": started, "busy": busy,
        })

    @app.post("/run")
    def run_form():
        ok = app.state.pipeline.start_background("manual")
        return RedirectResponse("/monitor?" + ("started=1" if ok else "busy=1"), status_code=303)

    @app.post("/api/run")
    def run_api(limit: int | None = Query(default=None, ge=1, le=100)):
        if not app.state.pipeline.start_background("manual", limit):
            return JSONResponse({"started": False, "detail": "Обработка уже идёт"}, status_code=409)
        return JSONResponse({"started": True}, status_code=202)

    @app.get("/api/games")
    def api_games(q: str = "", platform: str = "", sort: str = "date_desc", page: int = 1):
        rows, total, sort, page = query_games(q, platform, sort, page)
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
        return db.recent_runs(limit)

    @app.get("/api/events")
    async def events(request: Request, last_log: int = 0, once: int = 0):
        async def stream():
            last_version = -1
            last_id = last_log
            idle = 0.0
            while True:
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

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "games": db.count_games(), "running": app.state.pipeline.running}

    return app

