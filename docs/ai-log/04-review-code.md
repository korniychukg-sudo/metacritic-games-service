# Senior code review

Агент `04-review-code`, начат 14.10.2026 22:24, работал около 8 мин, вызовов инструментов: 23.

## Промпт, который получил агент

```text
ТЕСТОВОЕ ЗАДАНИЕ (AI Automation Engineer).
Обязательная часть. Реализовать сервис, который 1 раз в час заходит на Metacritic в раздел Games, берёт новые 20 игр, которые сегодня не обрабатывал, и добавляет информацию о них в свою базу или обновляет её, если игра уже есть в базе.
Выборка игр: 1) первые 20 игр берём со страницы Games https://www.metacritic.com/game/ — раздел New Releases; 2) при последующих обработках берём игры из раздела SEE ALL (сортировка Новые) https://www.metacritic.com/browse/game/all/all/all-time/new/ — можно за каждый заход обрабатывать очередную страницу с играми. Каждый новый день начинаем выбирать заново: сначала игры из пункта 1, затем из 2.
Информация, которую забираем: название игры; картинка-обложка; платформа + Metascore + Userscore (платформ может быть много: ps5, ns2 и т.д.); разработчик; описание; ссылка на видео. Сервис просматривает отзывы и пишет короткое резюме, что игрокам нравится в игре, что нет. Резюме делается отдельно для отзывов критиков и отдельно для отзывов пользователей; эта часть может обновляться при повторных обходах.
Веб-интерфейс: список загруженных игр (краткая информация); карточка игры (вся информация выше); фильтр по платформам; поиск по названию; сортировка по рейтингу. Для каждой игры подобрать похожие игры из тех, что уже есть в базе, и показывать их в карточке; клик по имени открывает карточку этой игры.
Дополнительная часть 1: для каждой игры найти летсплеи на YouTube, взять самый популярный летсплей, перевести рассказ блогера в текст, по тексту сделать заключение и добавить его к информации об игре со ссылкой на ролик.
Дополнительная часть 2: в веб-интерфейсе мониторинг работы сервиса в реальном времени (статусы воркеров, количество обработанных записей и т.п.) и кнопка принудительного запуска обработки.
Результат: ссылка на репозиторий, ссылка на работающий сервис, переписка с нейросетью.

Engineering rules:
- Work only in ~/dev/skytec-metacritic (create it). Python 3.11+ (use /usr/bin/env python3; create a venv in ~/dev/skytec-metacritic/.venv). Keep dependencies small and pinned in requirements.txt.
- Be polite to Metacritic and YouTube: realistic browser User-Agent, at most about 1 request per second, retries with backoff, timeouts. Never hammer endpoints while exploring; cache sample responses to ~/dev/skytec-metacritic/docs/samples/ and reuse them.
- LLM: OpenAI-compatible chat API configured by env LLM_BASE_URL, LLM_API_KEY, LLM_MODEL. Default (no key) is the free anonymous endpoint https://text.pollinations.ai/openai with model "openai" (verified working; it may rate-limit, so serialize calls, add retries and a gap of a few seconds). If the LLM fails, fall back to a deterministic extractive summary so the card is never empty, and mark which method produced it.
- Do not deploy, do not git push, do not print or store any secrets. The service must run with zero secrets.
- All user-facing text (UI, README) in Russian. Code comments sparse.

Builder's report:
I built the service in `~/dev/skytec-metacritic`, and it works against live Metacritic, YouTube and the free LLM. All 54 tests pass. The final live run processed 5 games out of 5, with LLM summaries and a let's play conclusion for every game. It is committed locally (`f2dc1f9`), not pushed and not deployed.

**What works**
- **Hourly schedule and selection:** runs every 60 minutes, and about 15 seconds after startup when the database is empty. Each day starts with the 20 New Releases, then walks the SEE ALL (newest) pages with a saved per-day cursor. A partly used page is picked up again on the next run. Games already done today are skipped, games already in the database are updated, and a new day starts over. The default batch is 20 (`BATCH_SIZE`).
- **Game card data:**
  - title, cover, description and developer/publisher;
  - Metacritic trailer link, when the game has one;
  - Metascore and Userscore for each platform; a platform with no ratings shows "—", not 0.
- **Review summaries:** separate "likes / dislikes / verdict" for critics and for players, in Russian. A summary is only redone when the set of reviews changes.
- **Let's play:** picks the most-viewed matching let's play, turns the blogger's speech into text from YouTube captions, and writes a conclusion with the video link. The card also shows the transcript excerpts and the other videos it considered.
- **Similar games:** top 5 from games already in the database, recalculated after each run; clicking one opens its card.
- **Web pages:**
  - Game list with search, platform filter and sorting by Metascore, Userscore, date or title. Choosing a platform also switches the scores shown and sorted to that platform.
  - Full game card.
  - Live monitoring page: worker statuses, progress, counters, history, errors and a log. Its "Запустить обработку сейчас" button refuses a second run while one is going.
- **API:** JSON endpoints for games, status and runs, plus a health check.
- **Never empty:** if the LLM fails, the card gets a quote-based summary marked as such, and later runs try the LLM again on those. If a video has no captions, it tries the next video, then the video description, then keeps just the link.
- **Deploy files:** Dockerfile, Procfile and `railway.json` are ready. The service needs no secrets. README is in Russian.

**Live smoke runs** (fresh database, batch of 5, started automatically)
- 5 of 5 games in 399 s, no errors: Over the Hill, Warhammer 40,000: Boltgun 2, Planet Zoo 2, Total War: Shogun 2 Complete Edition, Valor Mortis.
- Critic summaries came from the LLM for all 5; the player summary came from the LLM where reviews existed (Valor Mortis) and says "no reviews yet" for the others.
- All 5 got a let's play found, transcribed and concluded by the LLM.
- Earlier runs also covered the manual run, the parallel-run guard (second request rejected) and upgrading quote-based summaries to the LLM (4 upgraded).
- All three pages were checked in the browser with real data, on desktop and phone widths. A live request to the SEE ALL listing returned 24 games. The server is stopped; the local database `data/games.db` keeps 5 games.

**Problems found during the smoke test and fixed**
- **Free LLM limit:** it allows about one request per 30 seconds and answers "402" when called more often (I measured this). Calls are now spaced 32 s apart. If a request is still rejected twice, the prompt is made shorter.
- **English answers:** the model sometimes replied in English; those answers are now rejected and asked again.
- **Wrong videos:** let's plays for other games were matched (Boltgun 2 → Space Marine 2, Planet Zoo 2 → Planet Zoo). Title matching now requires the subtitle and the sequel number next to the title. I re-ran Planet Zoo 2 and it now picks the right video.
- **Junk quote:** the placeholder "Quotation forthcoming." no longer appears among reviews.
- **Card layout:** the let's play thumbnail overflowed and covered the text below it.
- **Secrets in test data:** the saved Metacritic/YouTube responses contained public site keys and visitor ids; these are replaced with "REDACTED" before the commit.

**Known gaps**
- **Run length:** with the free LLM, a full batch of 20 games takes about 30–40 minutes (3 LLM calls per game). It fits in the hour; with your own LLM key it takes a few minutes.
- **Captions on cloud hosting:** YouTube will likely block captions from Railway-type IPs. The let's play then falls back to the video description unless `YOUTUBE_PROXY_URL` is set.
- **Speech-to-text:** this is YouTube's own captioning; running Whisper on the audio is not included.
- **Review coverage:** summaries use Metacritic's sample of up to 7 reviews per tone for the lead platform (topped up with negative ones), not every review.
- **Userscore:** brand-new games often have no Userscore yet.
- **Similar games with few games:** with only a handful of games in the database, the matches are weak.
- **Page shifts:** if many games are added during the day, SEE ALL pages can shift and a game may slip to the next day.
- **Single process:** SQLite and the scheduler live in one process, one replica only.
- **Unofficial source:** this uses Metacritic's own website backend, not a public API.

Files are in `~/dev/skytec-metacritic`:
- README.md
- app/pipeline.py
- app/selection.py
- app/metacritic.py
- app/youtube.py
- app/llm.py
- app/summarize.py
- app/similarity.py
- app/main.py
- app/scheduler.py
- tests/
- Dockerfile
- railway.json

TASK: senior code review of ~/dev/skytec-metacritic: correctness and robustness of the scheduler and concurrency (no parallel runs, restart safety, timezone of the daily reset), SQLite usage and migrations, scraping resilience (timeouts, retries, rate limiting, schema drift), LLM call handling and fallback, security of the force-run endpoint (simple protection like a token or rate limit without blocking reviewers from trying it), XSS in templates, deployment config for Railway (PORT, volume, healthcheck), test coverage gaps. Run the tests. Do not fix code. Return a prioritized list of concrete issues with file/line and suggested fix.
```

## Итоговый ответ агента

**Senior code review: ~/dev/skytec-metacritic (commit f2dc1f9)**

All 54 tests pass on Python 3.12.13. I changed no code. I checked the items marked [verified] with a throwaway script against the real code; that script is at `[scratchpad]/review/probe.py`.

**What holds up**
- **Restart:** stale runs are closed before the scheduler starts, the next run is timed from the last run's start, and each game is recorded as soon as it is done. The page cursor is saved only at the end, so an interrupted run resumes cleanly.
- **One run at a time:** a non-blocking lock in the process prevents parallel runs.
- **SQL:** all queries are parameterised and sort keys come from a whitelist.
- **Templates:** Jinja autoescape is on. I checked that a title like `<img onerror>` comes out escaped.

**P1: fix before deploying or submitting**

1. **The Railway build will likely fail** (`Dockerfile:14`, `VOLUME ["/data"]`).
   - Railway refuses Dockerfiles that use `VOLUME` and tells you to use Railway volumes instead.
   - Fix: delete the line, attach a Railway Volume mounted at `/data`, and say so in the README. Lines 119–121 mention `DB_PATH` but not the mount path.

2. **Anyone can start a run, with no cooldown, from any website** (`app/main.py:251-260`). [verified]
   - A POST to `/run` with `Origin: https://evil.example` returns 303 `started=1`. A new `/api/run` is accepted the moment the previous run ends.
   - Anyone can therefore keep the service scraping Metacritic and YouTube and calling the LLM non-stop. With a paid LLM key, that costs money.
   - Fix, without blocking reviewers:
     - a cooldown of about 5 minutes after the last run ends, answering 429 with `Retry-After` and showing the remaining time on the button;
     - a same-origin check on both endpoints (`Sec-Fetch-Site`/`Origin`);
     - an optional `RUN_TOKEN` env for `/api/run` only, so the button stays open;
     - a daily cap on manual runs.
   - Per-IP limits only work if uvicorn trusts Railway's proxy headers (`--forwarded-allow-ips`; see item 13).

3. **A failing LLM can stretch a run to hours and permanently shrinks prompts.**
   - **The shrink never resets** (`app/llm.py:121-123, 160-177`). [verified] Two 402s cut `max_prompt_chars` from 6000 to 3600 for the rest of the process's life. By the builder's own measurement, Pollinations' 402 means "too frequent", not "too large". An empty reply also triggers a shrink.
   - **Worst case per call:**
     - all 402s: 4 attempts × 32 s ≈ 2 min per call, about 6 min per game, about 2 h per batch of 20;
     - hanging endpoint: 3 retries × (32 s + 120 s timeout) ≈ 7.6 min per call, about 23 min per game.
   - **No time limit per run** (`app/pipeline.py:105-119`), so runs then go back to back (`app/scheduler.py:79` schedules `started + interval`, which is already in the past).
   - Fix:
     - treat 402 from anonymous Pollinations as a rate limit: back off, don't shrink;
     - shrink only on 413, for that one call, and restore the budget after a success;
     - after about 3 failures in a row, use the quote-based summary for the rest of the run (the backfill step upgrades it later);
     - stop taking new games after about 50 minutes.

**P2: correctness and robustness**

4. **A game that fails once is skipped for the rest of the day** (`app/pipeline.py:117`, `app/db.py:189-191`). [verified]
   - It is recorded as processed with `status='error'`, and `processed_ids` ignores the status. One Metacritic 5xx means no retry today; a game deep in SEE ALL may never be picked up.
   - Fix: leave `status='error'` rows out of `processed_ids`, and add an attempts column capped at about 2 per day.

5. **Changes in Metacritic's or YouTube's data format go unnoticed, and can wipe good data.** [verified]
   - **Empty list looks normal:** `parse_list` returns `[]` when `data.items` is missing or renamed (`app/metacritic.py:148-167`), even when `totalResults` is 50. The selection step then marks New Releases as done (`app/selection.py:50-56`) and the run finishes as "empty" ("нечего обрабатывать") instead of failed.
   - **Partial game data overwrites good data:** if a game response lacks `platforms` or `critic-reviews`, `upsert_game` deletes the stored platforms and writes nothing (`app/db.py:252-261`). The empty review list also changes the review hash, so a good LLM summary is replaced by "Отзывов пока нет" (`app/pipeline.py:162-176`).
   - **YouTube:** a consent or bot page without `ytInitialData` is saved as "no_video" (`app/youtube.py:163-166`).
   - Fix:
     - raise a schema error when the expected keys are missing, or when items are empty but `totalResults > 0`;
     - before overwriting, compare with the stored row and keep the old platforms, reviews and summaries when the new data is empty but the totals are above zero;
     - show these errors on the monitoring page.

6. **No schema migrations** (`app/db.py:11-106`, `145-146`).
   - The schema is only `CREATE TABLE IF NOT EXISTS`. Once the database lives on a volume, adding a column makes every `INSERT INTO games(...)` fail.
   - Fix: `PRAGMA user_version` plus an ordered list of `ALTER` steps applied at startup, and a test that opens an old-schema database.

7. **A known vulnerable Starlette version** (`requirements.txt:1-2`, fastapi 0.115.6 / starlette 0.41.3).
   - CVE-2025-62727: a crafted `Range` header causes an O(n²) CPU DoS in `FileResponse`, which the `/static` mount uses. Affects 0.39.0–0.49.0.
   - Fix: move to a FastAPI release that allows Starlette ≥ 0.49.1.
   - Also, indirect dependencies (anyio, h11, httpcore, pydantic_core, …) are not pinned. Add a lock file (pip-compile or freeze) for reproducible Docker builds.

8. **Links can carry `javascript:`** (`app/templates/game.html:30, 60-61, 89`; values from `app/metacritic.py:237, 321-322`). [verified]
   - Autoescape does not block the scheme: a stored `video_embed_url` of `javascript:alert(document.domain)` renders as `<iframe src="javascript:...">`, which runs script in the page's origin. The source is Metacritic, so this is defence in depth.
   - Fix:
     - accept only `https://` in the parser: allow only `cdn.jwplayer.com` for the embed, and only http(s) for review links;
     - add `Content-Security-Policy` (`frame-src` jwplayer/youtube, `img-src https:`, `script-src 'self'`) and `X-Content-Type-Options`. For CSP, move the inline `window.APP_TZ` and `initMonitor` scripts (`base.html:28`, `monitor.html:86`) into a data attribute.

9. **Scheduler edge cases** (`app/scheduler.py:58-80`).
   - If a manual run is active at the scheduled time, the slot is dropped and the next run is a full hour later (lines 68-71). Fix: re-check every minute, or count the manual run's start as the slot.
   - `initial_next_run`, `_set_next` and the monitor calls sit outside try/except (line 60 and others). Any error there kills the thread silently while `/healthz` still says ok. Fix: wrap the loop body, and have `/healthz` report scheduler liveness (thread alive, next run not overdue).
   - A value of 0 or less for `RUN_INTERVAL_MINUTES` makes it run continuously (`app/config.py:44`). Clamp it to at least 1.

10. **Let's plays are searched again every time a game is reprocessed** (`app/pipeline.py:196-203`).
    - Only a let's play concluded by the LLM from captions counts as fresh. Every other result (`llm-description`, `no_transcript`, `no_video`) is redone each time the game is reprocessed (about daily while it stays in New Releases): 2 searches, up to 3 caption attempts, watch pages and a 32 s LLM call.
    - On Railway, where captions are likely blocked, that applies to every game, every day, which invites YouTube bot detection.
    - Fix: give every status a refresh period, for example 1 day for degraded results and 7 days for full ones.

11. **One game can block the backfill step forever** (`app/pipeline.py:271-302`, `app/db.py:302-310`).
    - Games are picked by `updated_at DESC`, but `set_json` never updates `updated_at`. On the first non-LLM result the step returns.
    - So one game the LLM keeps failing on stays first, uses up the LLM calls every run, and no other game gets upgraded.
    - Fix: `continue` instead of `return`, record a backfill attempt count or timestamp, and order by it.
    - Let's plays with the `description` method are never upgraded either.

**P3: lower priority**

12. **Time zones** (`app/config.py:27-35`, `app/monitor.py:90`).
    - An unknown time zone silently becomes UTC. Only Moscow has a hard-coded fallback, and a slim image may lack system tzdata (the venv has no `tzdata` package either).
    - Fix: add `tzdata` to requirements and log a warning on fallback.
    - Live-log timestamps use `time.localtime` (UTC in the container) while the UI says Moscow time, a 3-hour mismatch. Format them with `settings.day_tz`.

13. **Deploy details** (`Dockerfile:20`, `Procfile:1`).
    - `--proxy-headers` without `--forwarded-allow-ips="*"` means Railway's headers are ignored: wrong client IP and scheme. This matters for item 2.
    - The container runs as root.
    - Railway ignores the Procfile when building from the Dockerfile; it also lacks the `DB_PATH` default.
    - Warn in the README never to use `--workers`, more than 1 replica or `--reload`: the run lock and scheduler live in one process. `close_stale_runs` would also wrongly close another process's run. A lease in the database would fix both.

14. **Input edge cases.**
    - Search doesn't escape LIKE wildcards: `q=%` and `q=_` match every game (`app/db.py:353-355`). [verified] Fix: `ESCAPE '\'`.
    - `page=10**20` gives a 500 (`app/main.py:227, 263`). [verified] Fix: `Query(1, ge=1, le=10000)`.

15. **Run bookkeeping is not in a `finally`** (`app/pipeline.py:121-147`). An exception in `save_day_state` or `finish_run` leaves the run marked "running" in the database and the progress bar stuck until restart. Fix: wrap the body in try/finally that always finishes the run (status `failed`) and resets progress.

16. **Database load.**
    - One SQLite connection per thread, never closed: up to about 70 across the two thread pools (`app/db.py:156-166`).
    - The `errors` table is never pruned and has no index on `day`, yet `error_count(day)` runs on every live-status update (`app/db.py:97-105, 425-429`).
    - `status_payload` runs about 10 queries per update per connected client (`app/main.py:149-180, 292-294`).
    - Fix: add the index, cache the payload by monitor version, and prune old errors.

17. **Caption requests skip the retry and rate-limit wrapper** (`app/youtube.py:321-331`). `api.list` makes several requests in a burst after a single limiter wait. That is acceptable, but note it in the README.

18. **Saved samples still hold visitor ids.** `"key":"visitor_data","value":"Cgt..."` (which encodes the RU country) remains in `docs/samples/yt_search_*.html` and `yt_watch_bGBXM6oxbag.html`, while `README.md:195` says visitor data was redacted. Redact these too or reword the README.

19. **Nit** (`app/static/app.js:18-21, 80, 88`): `fmt()` returns the raw string when a date doesn't parse, and that string goes into `innerHTML` unescaped. Today the values are server-generated, but wrap them in `esc()`.

**Gaps in test coverage**
- **Scheduler:** no tests for `Scheduler` or `initial_next_run` at all: timing after a restart, skipping while a run is active, surviving an exception, intervals of 0 or less.
- **Day boundary:** no test of `Settings.today()` at Moscow midnight (21:00 UTC goes to the next day) or of the time-zone fallback.
- **HTTP client:** no tests for 429 with `Retry-After`, backoff on 5xx, no retry on 404/403, or timeouts (`httpx.MockTransport` would do).
- **LLM client:** not tested: retries on 429/5xx, empty reply, restoring the prompt budget after shrinking (currently it never restores), and stopping LLM calls after repeated failures.
- **Pipeline:** not tested:
  - resuming after an interrupted run;
  - retrying a failed game the same day (item 4);
  - a run crossing midnight;
  - two games with the same slug but different ids;
  - keeping stored data when Metacritic returns partial data (item 5);
  - one game blocking the backfill step (item 11).
- **Web:** not tested:
  - escaping of titles and links (item 8);
  - the successful `/run` path and a cooldown;
  - that the platform filter also changes which scores are shown and sorted;
  - log events on the live stream;
  - page bounds.
- **Migrations:** no test.
- **Assertions that check nothing:** `tests/test_pipeline.py:73` (`db.mark_processed  # noqa`) and `:100` (`assert isinstance(threading.active_count(), int)`). Also `tests/test_web.py:42` names its result `only_pc` but filters by Xbox Series X.


## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

</details>
