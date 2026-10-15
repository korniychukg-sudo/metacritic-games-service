import copy
import json
import os
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["SEED_DB_PATH"] = ""

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.llm import LLMError, LLMSkipped  # noqa: E402
from app.metacritic import ListItem, parse_composer, parse_list  # noqa: E402
from app.stt import AudioFile, SpeechToText, Transcript  # noqa: E402
from app.youtube import (  # noqa: E402
    TranscriptUnavailable,
    VideoCandidate,
    YouTubeBlocked,
    parse_search_html,
    parse_watch_html,
    rank_candidates,
    transcript_to_text,
)


def load_json(name: str):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> datetime:
        self.now += timedelta(**kw)
        return self.now


@pytest.fixture
def samples():
    return SAMPLES


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "games.db"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("MANUAL_RUN_COOLDOWN_SECONDS", "0")
    s = Settings()
    s.llm_min_gap = 0
    return s


@pytest.fixture
def db(settings):
    return Database(settings.db_path)


@pytest.fixture
def clock():
    return FakeClock()


def product_item(payload: dict) -> dict:
    return next(c for c in payload["components"] if c["meta"].get("componentName") == "product")["data"]["item"]


class FakeMetacritic:
    def __init__(self, fail_slugs=(), fail_once=(), no_video=False):
        self.composer = load_json("composer_game_star-wars-galactic-racer.json")
        self.new = parse_list(load_json("finder_new_releases.json"))[0]
        self.browse_p1 = parse_list(load_json("finder_browse_new_p1.json"))[0]
        self.fail_slugs = set(fail_slugs)
        self.fail_once = set(fail_once)
        self.no_video = no_video
        self.drop: dict[str, set[str]] = {}
        self.ids: dict[str, int] = {}
        self.raise_for: dict[str, BaseException] = {}
        self.on_game = None
        self.calls = []
        self.list_error: Exception | None = None

    def new_releases(self, limit=20):
        self.calls.append(("new", limit))
        if self.list_error:
            raise self.list_error
        return self.new[:limit]

    def browse_new(self, offset, limit=24):
        self.calls.append(("browse", offset))
        if self.list_error:
            raise self.list_error
        if offset == 0:
            return self.browse_p1[:limit]
        return [ListItem(id=900000 + offset + i, slug=f"old-game-{offset + i}", title=f"Old Game {offset + i}")
                for i in range(limit)]

    def payload_for(self, slug):
        payload = copy.deepcopy(self.composer)
        known = {i.slug: i for i in self.new + self.browse_p1}
        item = product_item(payload)
        src = known.get(slug)
        if slug != item["slug"]:
            item["id"] = src.id if src else abs(hash(slug)) % 10**9
            item["slug"] = slug
            item["title"] = src.title if src else slug
            item["description"] = f"{item['title']} is a game about {slug.replace('-', ' ')}."
            item["gameTaxonomy"]["franchises"] = []
            item["genres"] = [{"name": (src.genres[0] if src and src.genres else "Action")}]
        if slug in self.ids:
            item["id"] = self.ids[slug]
        if self.no_video:
            item["video"] = None
        drop = self.drop.get(slug, set())
        if "platforms" in drop:
            item.pop("platforms", None)
        payload["components"] = [c for c in payload["components"]
                                 if c["meta"].get("componentName") not in drop]
        return payload

    def full_game(self, slug):
        self.calls.append(("game", slug))
        if self.on_game:
            self.on_game(slug)
        if slug in self.raise_for:
            raise self.raise_for.pop(slug)
        if slug in self.fail_slugs:
            raise RuntimeError(f"boom {slug}")
        if slug in self.fail_once:
            self.fail_once.discard(slug)
            raise RuntimeError(f"temporary boom {slug}")
        return parse_composer(self.payload_for(slug))


class FakeYouTube:
    def __init__(self, transcript_ok=True, blocked=False):
        self.cands = parse_search_html(load_text("yt_search_star-wars-galactic-racer_lets-play_over20min.html"))
        snippets = load_json("yt_transcript_api_bGBXM6oxbag_first200.json")["snippets"]
        self.text = transcript_to_text(snippets)
        self.watch = parse_watch_html(load_text("yt_watch_bGBXM6oxbag.html"))
        self.transcript_ok = transcript_ok
        self.blocked = blocked
        self.empty = False
        self.no_trailer = False
        self.searches = 0
        self.trailer_searches = 0
        self.transcript_calls = 0
        self.transcript_texts: dict[str, str] = {}
        self.last_stats: dict = {}
        self.release_dates = []

    def find_letsplays(self, title, release_date=None):
        self.searches += 1
        self.release_dates.append(release_date)
        if self.blocked:
            raise YouTubeBlocked("YouTube показал капчу")
        if self.empty:
            self.last_stats = {"found": 0, "eligible": 0}
            return []
        cands = [VideoCandidate(**{**c.__dict__, "queries": [], "refined": True}) for c in self.cands]
        self.last_stats = {}
        ranked = rank_candidates("Star Wars: Galactic Racer", cands, stats=self.last_stats)
        for c in ranked:
            c.title = f"{title} - {c.title}"
        return ranked

    def find_trailer(self, title, release_date=None, owners=None):
        self.trailer_searches += 1
        if self.blocked:
            raise YouTubeBlocked("YouTube показал капчу")
        if self.no_trailer:
            return None
        return VideoCandidate("TrAiLeR0001", f"{title} - Official Trailer", views=1000, length_seconds=120,
                              channel="IGN")

    def transcript(self, video_id):
        self.transcript_calls += 1
        if not self.transcript_ok:
            raise TranscriptUnavailable("автор отключил субтитры", "TranscriptsDisabled")
        return self.transcript_texts.get(video_id, self.text), "en"

    def watch_details(self, video_id):
        return dict(self.watch, video_id=video_id)


class FakeLLM:
    model = "fake-model"

    def __init__(self, fail=False, threshold=None, commentary=True, about=True):
        self.fail = fail
        self.commentary = commentary
        self.about = about
        self.threshold = threshold
        self.calls = 0
        self.enabled = True
        self.circuit_open = False
        self.failures = 0
        self.runs = 0
        self.prompts = []

    @property
    def available(self):
        return self.enabled and not self.circuit_open

    def begin_run(self):
        self.runs += 1
        self.circuit_open = False
        self.failures = 0

    def ask_json(self, build, validator=None):
        if self.circuit_open:
            raise LLMSkipped("skipped")
        try:
            value = self.chat_json(build(1600), validator)
        except LLMError:
            self.failures += 1
            if self.threshold and self.failures >= self.threshold:
                self.circuit_open = True
            raise
        self.failures = 0
        return value

    def chat_json(self, messages, validator=None):
        self.calls += 1
        if self.fail:
            raise LLMError("HTTP 503")
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        about = self.about(prompt) if callable(self.about) else self.about
        if '"has_commentary"' in prompt:
            commentary = self.commentary(prompt) if callable(self.commentary) else self.commentary
            body = prompt.split(":\n", 1)[1].split("\n\nСначала определи", 1)[0]
            quote = " ".join(body.replace("…", " ").split()[:9])
            value = {"played_game": "" if about else "Minecraft",
                     "has_commentary": commentary, "evidence": [quote] if commentary else [],
                     "summary": "Автор ролика проходит первые гонки." if commentary else "",
                     "pros": ["скорость"] if commentary else [], "cons": ["сложность"] if commentary else [],
                     "verdict": "Автору понравилось." if commentary else ""}
        elif '"summary"' in prompt:
            value = {"played_game": "" if about else "Minecraft",
                     "summary": "Судя по описанию ролика, в нём показаны первые гонки."}
        else:
            value = {"likes": ["красивые трассы"], "dislikes": ["нет сплит-скрина"], "verdict": "В целом хвалят."}
        return validator(value) if validator else value


WHISPER_TEXT = (
    "Alright, we are finally playing this racer. I really love the speed and the tracks look beautiful. "
    "Honestly the controls feel a bit clunky at first and the bugs are annoying. "
) * 6


def write_wav(path, seconds: float, rate: int = 8000, channels: int = 2, freq: float = 440.0) -> str:
    import numpy as np

    t = np.arange(int(seconds * rate)) / rate
    tone = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    frames = np.repeat(tone[:, None], channels, axis=1).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return str(path)


class FakeAudioSource:
    def __init__(self, seconds: float = 40.0, error: Exception | None = None):
        self.seconds = seconds
        self.error = error
        self.calls = []

    def fetch(self, video_id, dest_dir, max_seconds):
        self.calls.append((video_id, max_seconds, dest_dir))
        if self.error:
            raise self.error
        path = write_wav(os.path.join(dest_dir, f"{video_id}.wav"), self.seconds)
        return AudioFile(path=path, format_id="249", ext="wav", codec="pcm", abr=48.0, bytes=os.path.getsize(path),
                         duration=3600, complete=True)


class FakeTranscriber:
    def __init__(self, text: str = WHISPER_TEXT, error: BaseException | None = None):
        self.text = text
        self.error = error
        self.calls = []
        self.released = 0
        self.loaded = False
        self.load_seconds = 0.4

    def transcribe(self, audio, budget):
        self.calls.append((len(audio), budget))
        if self.error:
            raise self.error
        self.loaded = True
        return Transcript(text=self.text, language="en", speech_seconds=round(len(audio) / 16000 * 0.6, 1),
                          seconds=1.5, complete=True)

    def release(self):
        self.released += 1
        self.loaded = False


def make_stt(settings, source=None, transcriber=None, tmp_root=None, on_state=None):
    return SpeechToText(settings, source=source or FakeAudioSource(), transcriber=transcriber or FakeTranscriber(),
                        tmp_root=str(tmp_root) if tmp_root else None, on_state=on_state)


@pytest.fixture
def fake_mc():
    return FakeMetacritic()


@pytest.fixture
def fake_yt():
    return FakeYouTube()


@pytest.fixture
def fake_llm():
    return FakeLLM()
