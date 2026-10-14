import copy
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "samples"
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.llm import LLMError  # noqa: E402
from app.metacritic import ListItem, parse_composer, parse_list  # noqa: E402
from app.youtube import VideoCandidate, parse_search_html, parse_watch_html, rank_candidates, transcript_to_text  # noqa: E402


def load_json(name: str):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def load_text(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


@pytest.fixture
def samples():
    return SAMPLES


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "games.db"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    s = Settings()
    s.llm_min_gap = 0
    return s


@pytest.fixture
def db(settings):
    return Database(settings.db_path)


class FakeMetacritic:
    def __init__(self, fail_slugs=()):
        self.composer = load_json("composer_game_star-wars-galactic-racer.json")
        self.new = parse_list(load_json("finder_new_releases.json"))[0]
        self.browse_p1 = parse_list(load_json("finder_browse_new_p1.json"))[0]
        self.fail_slugs = set(fail_slugs)
        self.calls = []

    def new_releases(self, limit=20):
        self.calls.append(("new", limit))
        return self.new[:limit]

    def browse_new(self, offset, limit=24):
        self.calls.append(("browse", offset))
        if offset == 0:
            return self.browse_p1[:limit]
        return [ListItem(id=900000 + offset + i, slug=f"old-game-{offset + i}", title=f"Old Game {offset + i}")
                for i in range(limit)]

    def full_game(self, slug):
        self.calls.append(("game", slug))
        if slug in self.fail_slugs:
            raise RuntimeError(f"boom {slug}")
        payload = copy.deepcopy(self.composer)
        known = {i.slug: i for i in self.new + self.browse_p1}
        item = next(c for c in payload["components"] if c["meta"].get("componentName") == "product")["data"]["item"]
        src = known.get(slug)
        if slug != item["slug"]:
            item["id"] = src.id if src else abs(hash(slug)) % 10**9
            item["slug"] = slug
            item["title"] = src.title if src else slug
            item["description"] = f"{item['title']} is a game about {slug.replace('-', ' ')}."
            item["gameTaxonomy"]["franchises"] = []
            item["genres"] = [{"name": (src.genres[0] if src and src.genres else "Action")}]
        return parse_composer(payload)


class FakeYouTube:
    def __init__(self, transcript_ok=True):
        self.cands = parse_search_html(load_text("yt_search_star-wars-galactic-racer_lets-play_over20min.html"))
        snippets = load_json("yt_transcript_api_bGBXM6oxbag_first200.json")["snippets"]
        self.text = transcript_to_text(snippets)
        self.watch = parse_watch_html(load_text("yt_watch_bGBXM6oxbag.html"))
        self.transcript_ok = transcript_ok
        self.searches = 0

    def find_letsplays(self, title):
        self.searches += 1
        cands = [VideoCandidate(**{**c.__dict__, "queries": []}) for c in self.cands]
        return rank_candidates("Star Wars: Galactic Racer", cands)

    def transcript(self, video_id):
        from app.youtube import TranscriptUnavailable

        if not self.transcript_ok:
            raise TranscriptUnavailable("TranscriptsDisabled")
        return self.text, "en"

    def watch_details(self, video_id):
        return dict(self.watch, video_id=video_id)


class FakeLLM:
    model = "fake-model"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0
        self.budgets = []

    def ask_json(self, build, validator=None):
        self.budgets.append(1600)
        return self.chat_json(build(1600), validator)

    def chat_json(self, messages, validator=None):
        self.calls += 1
        if self.fail:
            raise LLMError("HTTP 503")
        prompt = messages[-1]["content"]
        if '"summary"' in prompt:
            value = {"summary": "Автор ролика проходит первые гонки.", "pros": ["скорость"], "cons": ["сложность"],
                     "verdict": "Блогеру понравилось."}
        else:
            value = {"likes": ["красивые трассы"], "dislikes": ["нет сплит-скрина"], "verdict": "В целом хвалят."}
        return validator(value) if validator else value


@pytest.fixture
def fake_mc():
    return FakeMetacritic()


@pytest.fixture
def fake_yt():
    return FakeYouTube()


@pytest.fixture
def fake_llm():
    return FakeLLM()
