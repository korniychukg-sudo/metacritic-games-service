# Ревью: продукт и интерфейс

Агент `09-review-ux`, начат 15.10.2026 01:59, работал около 14 мин, вызовов инструментов: 93.

## Промпт, который получил агент

```text
ТЕСТОВОЕ ЗАДАНИЕ (AI Automation Engineer).
Обязательная часть. Сервис 1 раз в час заходит на Metacritic в раздел Games, берёт новые 20 игр, которые сегодня не обрабатывал, и добавляет информацию о них в свою базу или обновляет её, если игра уже есть.
Выборка: 1) первые 20 игр со страницы https://www.metacritic.com/game/ — New Releases; 2) при последующих обработках игры из SEE ALL (сортировка Новые) https://www.metacritic.com/browse/game/all/all/all-time/new/ — можно за каждый заход обрабатывать очередную страницу. Каждый новый день начинаем заново: сначала пункт 1, затем 2.
Данные: название; обложка; платформа + Metascore + Userscore (платформ может быть много); разработчик; описание; ссылка на видео. Резюме отзывов: что нравится и что нет, отдельно для критиков и для пользователей; может обновляться при повторных обходах.
Веб-интерфейс: список игр; карточка игры; фильтр по платформам; поиск по названию; сортировка по рейтингу; похожие игры из базы в карточке, клик открывает их карточку.
Доп. 1: самый популярный летсплей на YouTube, речь блогера в текст, заключение по тексту со ссылкой на ролик.
Доп. 2: мониторинг в реальном времени (статусы воркеров, количество обработанных записей и т.п.) и кнопка принудительного запуска.
Результат: репозиторий, работающий сервис, переписка с нейросетью.

Rules: work only in ~/dev/skytec-metacritic (existing repo, venv in .venv, Python 3.12). Keep dependencies pinned. Be polite to Metacritic and YouTube (about 1 request per second, backoff). The free LLM (Pollinations) allows about one request per 30 s. Do not deploy, do not git push, never print or store secrets; the service must run with zero secrets. User-facing text (UI, README) in Russian. Do not add code comments. Make sure nothing is listening on port 8765 or your test port when you finish (stop any server you start).
Never wait for the hourly scheduler in real wall-clock time: test scheduling and day-rollover logic with unit tests and injected clocks, or by calling the run function directly.
Do not modify any tracked file and do not commit. Do not scrape Metacritic or YouTube and do not call the LLM. Stop any server you start.

What was done so far:
FIX STEP: All 19 review items are fixed and every listed test gap now has real tests. The full suite is green at 153 tests, and a fresh end-to-end run against live Metacritic processed 10 games with 0 errors. The work is in 9 new commits on `main` (f2dc1f9..bfa7edc), nothing pushed or deployed.

Two things are still on you before submitting:
- **The user's home IP is still in git history.** Commit f2dc1f9 contains the visitor ids and the IP [IP] inside `docs/samples/yt_*.html`. I redacted the files in a new commit, but the old commit still has them. Rewriting history is destructive, so I didn't. Since nothing has been pushed, squashing or filtering before the first push would remove them.
- **The ТЗ also asks for a working service URL and the chat log with the AI.** I couldn't produce either: deploying was ruled out and I don't have the transcript.

**Requirements pass.** Everything in the ТЗ was already there except one gap: 3 of 5 cards had no video link, because Metacritic simply has no video for those games. Cards now fall back to the official trailer from YouTube, searched once a week and only for those games. I also made "similar games" require a real shared signal (franchise, developer, genre or similar description) instead of just sharing PC.

**Review items, all confirmed real, no false alarms:**
1. **Dockerfile:** `VOLUME` removed; README explains attaching a Railway volume at `/data` (`DB_PATH=/data/games.db` is the image default). A new start script `app/serve.py` reads `PORT`, adds `--proxy-headers --forwarded-allow-ips="*"`, one worker. It starts as root only to take ownership of the volume (Railway volumes are root-owned), then switches to user `app` (uid 10001). I built and ran the image in Docker on a fresh volume and a root-owned one: the process runs as uid 10001 and `/healthz` returns 200.
2. **Force-run button:**
   - 409 while a run is active.
   - 5-minute pause after any run ends: 429 with `Retry-After`, countdown on the button.
   - Cross-site POSTs get 403.
   - Optional `RUN_TOKEN` applies to `/api/run` only, so the button keeps working without it.
   - Cap of 24 manual runs a day.
3. **LLM:**
   - 402 from anonymous Pollinations is treated as a rate limit (back off, never shrink).
   - Prompts shrink only for the current call.
   - After 3 failures in a row, the LLM is skipped for the rest of that run (summaries built from quotes, upgraded later).
   - A run stops taking new games after 50 minutes and leaves the rest for the next run.
4. **Failed games** are retried the same day, at most 2 attempts, ahead of new games.
5. **Format changes** at Metacritic or YouTube now raise an error and show on the monitor marked `schema`, instead of looking like "nothing to process". Empty platforms or reviews never overwrite stored data.
6. **Schema migrations** via `PRAGMA user_version`, applied in one transaction; tested on an old-schema database.
7. **Dependencies:** FastAPI 0.141.1 and Starlette 1.7.0 (past CVE-2025-62727), with the full tree pinned from a clean environment. I chose 0.141.1 over 0.142+ because the newer one adds an OpenTelemetry dependency.
8. **Links and headers:** unsafe links are dropped by the parser and filtered again in templates. CSP plus `X-Content-Type-Options`; the inline scripts and styles are gone.
9. **Scheduler:**
   - It no longer drops a slot while a run is active, and a manual run counts as the slot.
   - Exceptions no longer kill the scheduler thread.
   - `/healthz` returns 503 if the scheduler is dead or overdue.
   - The interval is clamped to at least 1 minute.
10. **Let's play refresh:** 7 days for a result built from captions, 24 hours otherwise.
11. **Backfill** goes through games in order of last attempt and moves on when one fails; it also upgrades let's plays built from the video description.
12. **Time zones:** `tzdata` added, a warning is logged on fallback, and live-log times use the configured zone.
13. **One process at a time:** a lease in the database blocks a second process from running, and a run is only closed as interrupted once its heartbeat stops. README warns against `--workers`, `--reload` and extra replicas.
14. **Input:** `%` and `_` in search are matched literally; `page` is bounded to 1–10000 and `q` to 200 chars.
15. **Run bookkeeping** is in try/finally, so a run is always finished and the progress reset.
16. **Database load:** a pool of 8 connections, an index on `errors(day)`, old errors and processed rows pruned, and the status payload cached.
17. **Caption requests** now each go through the shared rate limiter, which goes beyond the README note the review asked for.
18. **Samples:** visitor ids and the home IP are redacted.
19. **app.js:** every value put into the page is escaped.

**Where I went beyond or against the review:**
- **Shrinking on empty replies.** The review said shrink only on 413. The first live run showed Pollinations returning empty replies: its reasoning model spent the whole 1500-token cap on reasoning, and identical requests come back from its cache, so plain retries can't help. Sending `reasoning_effort: low` fixed it (240 tokens, about 6.5 s instead of 15–20 s). As a safety net, an empty reply shrinks the prompt for that one call only.
- **No per-IP limits.** The pause and daily cap are global, so they don't depend on client IPs.
- **Docker Desktop.** I started it to test the image and quit it afterwards. While it ran, it auto-started your других проектов containers; quitting stopped them again, as they were before.

**Smoke test** (final code, fresh database, `BATCH_SIZE=5`, port 8799, now stopped; nothing listening on 8765 or 8799):

| | Result |
|---|---|
| Runs | a scheduled run at startup (381 s) and a manual run from the button after the pause (362 s) |
| Games processed | 10, 0 errors |
| Critic summaries | 10 of 10 by LLM, 0 fallback |
| User summaries | 5 of 5 by LLM; the other 5 games have no user reviews yet |
| Let's plays | 9 of 10 found, all 9 concluded by LLM from captions |
| YouTube trailers | 7, for the games with no Metacritic video |
| Cooldown | 429 with `Retry-After: 284`; the button showed "Запуск через 4:31" |

- **Missing let's play.** It's Dragon's Dogma 2: Dark Arisen. Strict title matching rejects videos without the subtitle; that strictness is what stops it picking the wrong game in a series. It is documented and retried daily.
- **No description.** over the hill has no description on Metacritic itself.
- **Earlier run.** An earlier run on the code before the `reasoning_effort` fix got 3 of 5 let's plays by LLM; the other 2 hit the empty replies, which led to that fix.
- **Not included.** Whisper is still not included; captions come from YouTube's own speech recognition, as the README says.

**Commits:**
- `3b3f3e6` dependency upgrade and lock
- `270096c` sample redaction
- `e9f653b` HTTP and LLM clients
- `74ae27e` parsers
- `e651b9b` config and time zones
- `3a5c4e7` similar games
- `b6c49f5` database, pipeline and scheduler
- `de39a6b` web, force run and CSP
- `4707335` Docker, start script and README
- `bfa7edc` smoke-test polish

Each commit's tests pass on a clean checkout.

Repo: `~/dev/skytec-metacritic`. Smoke database and logs: `[scratchpad]/smoke/`
SPEECH STEP: Whisper is now the speech-to-text fallback when captions aren't available, and it's committed as `695f428` on `main` (not pushed or deployed). The full suite passes at 192 tests, up from 153, including on a clean export of HEAD. I proved it on one real game with captions turned off, and also in a full scheduled run of the actual service.

**What was added**
- **New module `app/stt.py`.** If none of the candidate videos has captions, or `YOUTUBE_CAPTIONS=0`, it does this:
  - yt-dlp picks the smallest audio-only track (about 48 kbit/s). For videos with an AI dub it takes the original-language track.
  - It downloads only the first `STT_MAX_MINUTES` (15) minutes, about 5–6 MB, in 4 MB `Range` chunks through the shared rate limiter. yt-dlp pauses 1.1 s between its own requests.
  - PyAV decodes it to 16 kHz mono, so no system ffmpeg is needed.
  - Silero VAD drops music and silence. If there's almost no speech, the model is never loaded and the next video is tried.
  - faster-whisper transcribes the speech in 2-minute blocks (`base`, int8, CPU, greedy decoding), within a time budget.
  - Audio stays in a temp dir that is always deleted; none was left after the runs.
- **What gets recorded:** `transcript_method="whisper"`, plus an `stt` block with the model, minutes of audio and speech, and the time for each stage. The conclusion is `llm-whisper`, or `extractive-whisper` if the LLM fails, which the LLM backfill upgrades later. The card says the text came from Whisper and which model; the monitor has a new "Распознавание речи (Whisper)" worker.
- **Settings:** `STT_ENABLED` (on by default), `STT_MODEL` (`base`), `STT_MAX_MINUTES` (15), plus `STT_MAX_PER_RUN` (6), `STT_TIMEOUT_SECONDS` (300), `STT_THREADS`, `STT_MODEL_DIR` and `YOUTUBE_CAPTIONS`. Defaults are in `.env.example`.
- **Fails soft:** any error falls back to the existing description path; I checked that a run is never marked failed. Whisper is skipped for the rest of a run after a model error or two system errors in a row. It is also skipped when the run is close to its time limit.
- **Docker:** no CUDA or PyTorch packages. The linux/amd64 image is 196 MB compressed, about 600 MB on disk. I built and ran it on a fresh volume: it runs as uid 10001 and the model downloads to `/data/models`. Starting Docker Desktop for this also auto-started your других проектов containers. I quit Docker afterwards, which stopped them, and removed the test image and volume.
- **Dependencies:** pinned with the full tree. `av` is held at 18.x because 19.x broke a call faster-whisper makes.
- **Tests:** 39 new ones, with yt-dlp and the model mocked. Audio decoding is tested on real generated WAV files.

**Measured transcription speed** (Apple M1, 4 threads, first 15 minutes of a let's play, 520 s of speech):

| Model | Transcription time | Peak memory |
|---|---|---|
| `tiny` | 8.5 s (100x real time) | ~0.6 GB |
| `base` (chosen) | 13–15 s (60–67x) | 0.6–0.8 GB |
| `small` | 36 s (25x) | ~1.2 GB |

- With download and decoding, one video takes about 20 s on the M1. A video with no speech is rejected in about 4 s after download.
- In the amd64 container, which ran under emulation on the M1, `base` took 63 s and peaked at about 1.25 GB. The README therefore asks for at least 1.5 GB RAM, or `STT_MODEL=tiny`.
- I chose `base` because it is 2.5x faster than `small` and lighter on memory, and its accuracy is enough for a conclusion.

**Real-game proof** (Valor Mortis, captions off, 0 caption requests):
- The two most popular videos were gameplay with no commentary. The speech detector found 38–39 s of speech in each, so both were skipped without loading the model.
- The third was a Russian let's play. 632 s of speech became 5,417 characters in 20 s.
- The LLM then wrote a Russian conclusion: it praises the graphics and weapons and notes the game is still in development.
- The whole let's play step took 51 s.

**Full service run** (scheduled run, fresh database, Warhammer 40,000: Boltgun 2): Whisper transcribed 601 s of speech into 6,987 characters in 23 s. The LLM wrote the conclusion and the run finished in 45 s with 0 errors. The transcription happened while the service was waiting out the 32 s pause between LLM calls, so it added almost no time to the run. The server is stopped, and nothing is listening on 8765 or 8799.

**Changes outside the brief:**
- Whisper now tries up to 3 candidate videos instead of only the top one, because the live run showed the most popular videos are often gameplay without commentary.
- Videos titled "no commentary" / "без комментариев" are no longer treated as let's plays, since they have no blogger speech to transcribe.

**Two things you should know:**
- **Description fallback is still misleading.** When it kicks in, the LLM still writes "автор ролика отмечает…" from the video description, which can read as the blogger's opinion.
- **yt-dlp will need maintenance.** It already warns that without the Deno JavaScript runtime some YouTube formats may disappear. Audio tracks still come through today. If they stop, the service falls back to the description, and yt-dlp will need regular updates.

The proof logs and database are in `[scratchpad]/proof/`. The full-run logs are in `scratchpad/e2e/`. The downloaded models (682 MB) are in `scratchpad/models/` and can be deleted.

Files are in `~/dev/skytec-metacritic`:
- app/stt.py
- tests/test_stt.py
- app/pipeline.py
- README.md

Product and UI lens. Start the app on your own free port with a copy of the existing database and scheduled runs disabled. Request every page and API endpoint, read the templates, CSS and JS. Judge as a hiring manager: Russian text quality and consistency, empty states, error states, mobile layout (viewport meta, overflow), the monitoring page updating live, the card readability (summaries, platforms table, let's play block, similar games links), README clarity (architecture, how to run, decisions, honest limitations, requirements checklist). Report concrete fixes.
Return only real problems with exact file and line and a concrete fix; severity blocker means it would make the employer reject the submission.
```

## Итоговый ответ агента

Product and UI review of ~/dev/skytec-metacritic at 695f428, done as a hiring manager would. I started the app on 127.0.0.1:8813 with a copy of the proof database (10 games, including the Whisper one), the scheduler off and outbound HTTP blocked by a dead proxy. I requested every page and API endpoint (/, filters, sorts, paging, /game/{slug} and an unknown slug, /monitor, /api/games, /api/games/{slug}, /api/platforms, /api/status, /api/runs, /api/events, /healthz, /docs, static files). I also briefly ran a second copy on port 8814 with an empty database to check the empty states. I read every template plus the CSS, JS and README, checked for horizontal overflow at 375 px, and confirmed the monitor really updates live. Both servers are stopped, nothing listens on 8765, 8799, 8813 or 8814, the tree is clean, and nothing was scraped or sent to the LLM. Test count matches the README (192).

What already works: the Russian UI is clean and consistent. Light and dark themes are fine, the viewport meta is set and no page scrolls sideways at 375 px. Filters, sorting and platform-specific scores work. The method badges are honest. Limitations are stated frankly in the README.

Problems found:
- **Blocker:** the README does not deliver 2 of the 3 required results: the working service URL and the chat log with the AI.
- **Major, invented opinions:** let's play conclusions make up the blogger's opinion when the text has none. The Kingdom Hearts card is built from a no-commentary walkthrough whose captions are game dialogue plus "Heat. Heat." (YouTube's speech recognition mishearing music), yet it lists "Понравилось блогеру". Valor Mortis lists the author's mood as a pro.
- **Major, similar games:** 8 of 10 cards show no similar games.
- **Major, error pages:** errors come back as raw JSON (404 for an unknown game, English 422 for a bad page number).
- **Major, monitor:** it never shows which source each run took (New Releases, SEE ALL page N, retries), though that order is the core rule of the brief.
- **Major, README:** it has no screenshots and no short summary at the top.
- **Minor:** about 14 more items on wording, consistency and layout.

Найдено проблем: 18

1. **[blocker]** `README.md:1-11 (intro); nothing anywhere in README or docs/`
   - Что: The brief's 'Результат' asks for three things: the repository, a working service and the chat log with the AI. The README has no section with the live service URL and no chat log or link to one (grep for 'переписк', 'нейросет' or 'railway.app' finds nothing). A reviewer opening the repo cannot find 2 of the 3 required results.
   - Как исправить: Add a '## Результат' section right after line 11 with: the GitHub link; 'Работающий сервис: https://<name>.up.railway.app (мониторинг: /monitor)'; 'Переписка с нейросетью: docs/ai-chat/' plus one paragraph on how the AI was used (research, code, review, smoke tests). The user has to deploy with the existing railway.json, export the session transcripts to docs/ai-chat/*.md with tokens and IPs scrubbed, and fill in both links before submitting.
2. **[major]** `app/summarize.py:259-267 (LETSPLAY_SYSTEM/LETSPLAY_TASK), 280 (prompt for the description fallback), 286-294 (validate_letsplay_json); app/summarize.py:103-108 (REVIEW_SYSTEM/REVIEW_TASK); app/youtube.py:284 (NOISE); app/pipeline.py:433-447; app/templates/game.html:121-122`
   - Что: The prompt always asks how the blogger rates the game, and the validator rejects empty pros and cons, so the LLM invents opinions. Kingdom Hearts Collection: the video (Ben-Gun 'Gameplay Walkthrough Part 1 - FULL GAME') has no commentary. Its caption text is game dialogue plus 'Heat. Heat. Heat.' (YouTube's speech recognition mishearing music), and the card shows that excerpt. Yet the card says 'Автор ролика отмечает… Он ценит улучшенную производительность' and lists 'Понравилось блогеру'. Valor Mortis lists 'Позитивный настрой и энтузиазм автора' as a pro, and 'Не указаны конкретные проблемы' and 'Отсутствие подробного обзора' as cons; those describe the video, not the game. The description fallback has the same problem: 'автор ролика отмечает' written from a video description. Critic summaries have a milder form: Dragon's Dogma 2's 'Нравится' list includes 'Положительная оценка от критиков и фанатов'. A reviewer hiring an AI Automation Engineer will read this as uncontrolled hallucination.
   - Как исправить: Add a field '"has_opinion": true|false' to the let's play JSON. Tell the model: 'пункты только о самой игре; не пиши про автора, его настрой или полноту обзора; если в тексте нет мнения автора об игре (диалоги игры, музыка, геймплей без комментариев) — has_opinion=false и пустые списки'. Let the validator accept empty lists when has_opinion is false. In find_letsplay, treat has_opinion=false like 'no captions': record the reason 'в ролике нет комментариев блогера' and try the next candidate. Extend NOISE to drop runs of 3 or more 'Heat.' tokens. For the description source, rewrite the prompt to 'опиши, что показано в ролике, не приписывай автору мнений', and in game.html change the headings to 'По описанию ролика: плюсы/минусы' when lp.method is llm-description or description. In REVIEW_TASK add 'только свойства игры (геймплей, сюжет, графика, техника, контент, цена), не кто и как её оценил'. Add tests that use the Kingdom Hearts excerpt as a fixture.
3. **[major]** `app/similarity.py:73 (MIN_DESC) and 123 (the condition that decides whether a match counts); app/templates/game.html:165-167`
   - Что: A match only counts with the same franchise, the same developer, the exact same genre string, or a description cosine of at least 0.06. In the database 8 of 10 cards show 'Похожих игр в базе пока нет'; only Valor Mortis and Dragon's Dogma 2 link to each other. On a fresh deployment with about 20 varied new releases, the 'похожие игры' requirement will look broken on most cards.
   - Как исправить: Add a map of genre families, for example Action RPG / Action Adventure / Metroidvania / FPS → экшен; Tycoon / Turn-Based Strategy / RTS → стратегии; Auto Racing / Driving → гонки; Point-and-Click / Adventure → приключения. Score a family match at half the genre weight with the reason 'близкий жанр'. When the strict list is still empty and the database has at least 3 games, show the top 3 by total score under the subheading 'Ближе всего из базы (слабое сходство)', still excluding matches that share only platforms. Cover both with tests in tests/test_similarity.py.
4. **[major]** `app/main.py:424-427 (game_view raises HTTPException for the HTML route), 451-453 (index Query validation); there is no error template`
   - Что: An unknown game, /game/nope, returns raw JSON {"detail":"Игра не найдена"}. /?page=0 or /?page=abc returns an English validation dump ('Input should be greater than or equal to 1'). An unhandled error returns plain 'Internal Server Error'. Nothing gives a way back to the site, which looks unfinished in an otherwise polished Russian HTML app.
   - Как исправить: Add app/templates/error.html (extends base: heading, Russian explanation, '← Все игры' button). Register handlers for StarletteHTTPException, RequestValidationError and Exception: when the path does not start with /api, /docs or /openapi.json, render error.html with the right status ('Игра не найдена', 'Некорректный адрес страницы', 'Что-то пошло не так, попробуйте позже'); otherwise keep JSON. In index(), accept bad page values and clamp to 1..pages instead of returning 422. Add web tests for 404, 422 and 500 HTML.
5. **[major]** `app/templates/monitor.html:65-68; app/static/app.js:115-119; app/templates/monitor.html:40; app/templates/game.html:170`
   - Что: The order of sources is the core rule of the brief: New Releases first, then SEE ALL pages, and a new day starts over. The monitor never shows which source a run took, although runs.sources ('new-releases', 'browse:p2', 'retry') and runs.message are stored. The runs table shows only id, start, trigger, status and ok/selected, with no duration. The cursor tile says 'SEE ALL с позиции 24', which is an internal offset. The card footer shows the raw code 'источник: new-releases'.
   - Как исправить: Add a Jinja filter and a matching JS map: new-releases → 'New Releases', browse:pN → 'SEE ALL, стр. N', retry → 'повтор упавших'. Add columns 'Источник' and 'Длительность' (finished_at - started_at) to the runs table in both monitor.html and app.js, with runs.message as a title tooltip or second line. Change the tile text to 'New Releases пройден, дальше SEE ALL со страницы {{ offset // page_size + 1 }}' and use the same filter in the game.html footer.
6. **[major]** `README.md:13-28 (requirements table), 125-178 (Whisper section), whole file (418 lines)`
   - Что: The README is thorough but hard to skim. Requirement table cells run past 100 words (the 'Доп. 1' row is one long paragraph). There are no screenshots, so with no live URL a reviewer has to install about 430 MB of dependencies and wait several minutes before seeing any UI. Design decisions are scattered instead of collected. 55 lines of Whisper benchmarks sit in the main path.
   - Как исправить: Put a short summary at the top: one sentence on what it is, the links from the Результат section, and 3 screenshots in docs/screenshots/ (list with platform filter, Valor Mortis card, monitor during a run). Reduce 'Возможности' to a checklist: requirement → where to see it in the UI → module. Add '## Ключевые решения' with 5-7 bullets: JSON backend instead of HTML scraping, SQLite with migrations and a run lease, anonymous Pollinations with quote-based fallbacks, captions first and then local Whisper base, server-side rendering plus SSE. Move the STT benchmarks and force-run protection details to docs/STT.md and docs/SECURITY.md and link them.
7. **[minor]** `app/pipeline.py:432; app/youtube.py:15 (SP_POPULAR_LONG) and 242`
   - Что: For Dragon's Dogma 2: Dark Arisen the card says 'На YouTube не нашлось подходящих летсплеев длиннее 10 минут'. The search already filters to videos over 20 minutes (README line 27), and the real cause is the strict title check: videos without 'Dark Arisen' in the title are rejected. For a big AAA game the message reads as false.
   - Как исправить: If the merged search results are non-empty but rank_candidates drops them all, write 'Летсплеи по игре есть, но ни в одном названии нет полного названия «…» — строгая проверка, чтобы не взять ролик другой части серии. Проверим снова через сутки'. Otherwise say 'длиннее 20 минут' to match the actual filter.
8. **[minor]** `app/static/app.js:63-69 (renderButton uses manual.reason); app/main.py:270-272; app/main.py:166-169 and app/static/app.js:33-37 (wait format)`
   - Что: During the cooldown the button counts down every second ('Запуск через 4:31' → '3:50'), but the hint below keeps the server text with a frozen time, 'Повторный запуск возможен через 4:31…'. I confirmed in the browser that after 4 s the button had changed and the hint had not. Both formatters print only minutes:seconds, so long waits show as '1364:03'.
   - Как исправить: Remove the time from the server reason ('Между запусками нужна пауза N мин, чтобы не перегружать Metacritic, YouTube и LLM') and build the hint on the client as 'Можно запустить через ' + wait(left). Make fmt_wait and wait() print 'Ч:ММ:СС' once the wait is 3600 s or more.
9. **[minor]** `app/main.py:405; app/pipeline.py:199-200, 220-222, 263, 273, 291; app/scheduler.py:77, 95; app/db.py:544-546 (recent_runs → /api/runs, /api/status)`
   - Что: The live log on the public Russian monitor shows English lines and the absolute DB path ('Service started, DB /private/tmp/…/games.db (schema v4)'). /api/runs and /api/status return the 'owner' field with the machine hostname ('[host]:76659:…'). The worker detail prints the raw trigger code ('запуск #3 (manual): выбор игр'), and line 220 lower-cases a message that can contain exception text and proper names.
   - Как исправить: Log only the database file name and schema version. Translate the roughly 10 run-level messages a viewer sees (run started or finished, selection, time limit, retry, scheduler next run), or rename the section to 'Технический журнал'. Drop 'owner' from API output: select explicit columns in recent_runs. Use TRIGGER_LABELS in the worker detail and remove .lower().
10. **[minor]** `app/db.py:167 (search_key) and 472-474`
   - Что: Search is a substring match after casefold only, so 'dragons dogma' does not find "Dragon's Dogma 2" and 'warhammer 40000' does not find 'Warhammer 40,000: Boltgun 2'. Both are natural queries, and a reviewer gets 'Ничего не найдено'.
   - Как исправить: In search_key, remove apostrophes, commas and periods without inserting spaces (re.sub(r"['’.,]", '', s)), replace other non-word characters with a space, then collapse whitespace. Apply it to both stored titles and queries, add a migration that recomputes games.title_search, and add tests for both queries.
11. **[minor]** `app/youtube.py:38 (STOP contains 'collection', 'remastered', 'complete'…), 257-268 (rank_trailers)`
   - Что: The YouTube trailer fallback picked 'Kingdom Hearts III - Official Extended Trailer | E3 2018' for Kingdom Hearts Collection I ~ III. That is a different product from 8 years earlier, yet it is captioned as the most suitable trailer.
   - Как исправить: For trailers, keep edition words (collection, remaster, complete, definitive, edition) as required when they appear in the game title. Prefer results whose publishedTimeText is within about 1 year of the release date. If only an older or base-game trailer matches, label it 'трейлер оригинальной игры'.
12. **[minor]** `app/youtube.py:395; app/pipeline.py:476-483; app/templates/game.html:106, 127; app/templates/monitor.html:83`
   - Что: Card notes and the errors table show technical text: video ids and Python exception class names ('Расшифровка недоступна (субтитры: <id>: TranscriptsDisabled; Whisper: …)'), and step codes like 'youtube:schema'. On a cloud host, where YouTube returns RequestBlocked, every card would show this text.
   - Как исправить: Map exception classes to Russian phrases (TranscriptsDisabled → 'автор отключил субтитры', RequestBlocked/IpBlocked → 'YouTube не отдаёт субтитры этому серверу', NoTranscriptFound → 'субтитров нет'). Show only the phrase plus a count of videos checked, and keep ids in JSON. In the errors table, map step codes to labels ('YouTube: формат ответа изменился').
13. **[minor]** `app/templates/game.html:19, 43-45; app/static/style.css:226-237`
   - Что: On a 375 px screen the 4-column platforms table scrolls sideways inside its card (scrollWidth 408 vs 305), hiding Userscore counts and the release column. Rows repeat the name as a chip ('PC PC'). The hero shows the Metascore review count ('PC, 48 рецензий') but no Userscore count. The PS5 row shows '— · 1 оценка' with no explanation why there is no score.
   - Как исправить: At max-width 640px hide the 4th column (th:nth-child(4), td:nth-child(4) {display:none}). Render the short-name chip only when p.short != p.name. Add '{{ lead user_count }} оценок' under Userscore in the hero. Show 'мало оценок для Userscore' when user_count > 0 and userscore is none.
14. **[minor]** `app/templates/index.html:30, 65-75`
   - Что: A page past the end (/?page=2 with 10 games) shows 'Найдено: 10' together with 'Ничего не найдено'. 'Найдено: N' has no noun and no plural form.
   - Как исправить: Clamp page to the number of pages in index(), or render 'На странице N пусто, всего страниц: M' with a link to page 1. Change the counter to 'Найдено: {{ total }} {{ total|plural("игра","игры","игр") }}'.
15. **[minor]** `app/templates/index.html:69; app/templates/monitor.html:8, 40; app/monitor.py:26`
   - Что: Empty and disabled states are misleading. With SCHEDULER_ENABLED=0 the empty index still promises an automatic first run, and the monitor still says 'запускается каждые 60 мин'. With no runs yet the cursor tile says 'сегодня ещё идёт блок New Releases', which suggests something is running. After a restart the Metacritic, YouTube and LLM worker cards show only 'ожидает —'.
   - Как исправить: Branch on st.config.scheduler_enabled ('Автозапуск выключен, запустите обработку кнопкой'). Use 'сегодня первым будет обработан блок New Releases' while new_releases_done is false and no run exists today. Initialise worker detail to 'с момента старта сервиса запросов не было' and optionally add the last run's finish time.
16. **[minor]** `app/static/style.css:108, 161 (similar chips), 199 (.log), 209 (.run-box); app/templates/base.html:13; app/static/app.js:145-151`
   - Что: Small visual issues. The reason chips under similar games stretch to the score badge height and look like tall pills. Long log lines do not wrap and scroll sideways on phones. At about 1100 px the force-run button wraps under the description on the left. On phones the brand reads 'MC Игры' right next to the nav item 'Игры'. The 'Обработка запущена.' banner stays after the run ends.
   - Как исправить: Add .similar .chips{align-items:center}, .log div{white-space:pre-wrap;overflow-wrap:anywhere}, .page-head>div:first-child{flex:1 1 420px} and .run-box{margin-left:auto}. Shorten the mobile brand to 'MC Дайджест'. Hide #flash in render() when running changes from true to false.
17. **[minor]** `app/config.py:60-122 (reads os.environ only); README.md:248-251`
   - Что: The repo ships .env.example, but nothing loads a .env file and the README never mentions it. A reviewer who copies it to .env gets no effect.
   - Как исправить: Under 'Переменные окружения', state that values come only from the process environment and give the one-liner 'set -a; . ./.env; set +a' for local runs. Alternatively, have app/serve.py read ./.env into os.environ.setdefault before exec, without adding a new dependency.
18. **[minor]** `README.md:34 and 46 (architecture diagram)`
   - Что: The ASCII diagram is off by one column. The right border on line 34 is at column 67 while the rest of the box is at 66. The top border of the Metacritic box on line 46 ends at column 42 while its sides are at 43. GitHub renders this as broken box lines.
   - Как исправить: Remove one '─'/space on line 34 before the right '│' and add one '─' to the top border on line 46. Check that every box edge sits in the same column.

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

- `Bash` Show repo status, history and files
- `Bash` Count lines in app sources
- `Read` ~/dev/skytec-metacritic/README.md
- `Read` ~/dev/skytec-metacritic/app/main.py
- `Bash` Show base, macros and index templates
- `Bash` Show game card template
- `Bash` Show monitor template and JS
- `Bash` Show stylesheet
- `Bash` Show monitor module and candidate databases
- `Bash` List smoke/proof databases and their game counts
- `Bash` Copy database for review and check listening ports
- `Bash` Show config module
- `Bash` Show serve entrypoint and HTTP client proxy handling
- `Bash` Start app on port 8813 with copied DB and scheduler off
- `Bash` Request every page and API endpoint
- `Bash` Inspect 404, 422 and empty-state responses
- `Skill` skill=anthropic-skills:built-in-browser
- `preview_start` http://127.0.0.1:8813/
- `computer` screenshot
- `javascript_tool` javascript_exec
- `read_console_messages` 
- `resize_window` 
- `javascript_tool` javascript_exec
- `navigate` http://127.0.0.1:8813/game/valor-mortis
- `javascript_tool` javascript_exec
- `computer` screenshot
- `get_page_text` 
- `resize_window` 
- `navigate` http://127.0.0.1:8813/
- `computer` screenshot
- `javascript_tool` javascript_exec
- `computer` zoom
- `resize_window` 
- `computer` screenshot
- `computer` scroll
- `computer` screenshot
- `navigate` http://127.0.0.1:8813/monitor
- `get_page_text` 
- `Bash` List log statements shown in live log
- `Bash` List progress steps and worker statuses
- `Bash` Show run lifecycle and step labels in pipeline
- `Bash` Show run body and its result messages
- `Bash` Inspect runs, status and health payloads
- `Bash` Show selection logic and source labels
- `navigate` http://127.0.0.1:8813/game/dragons-dogma-2-dark-arisen
- `get_page_text` 
- `Bash` Find let's play minimum length setting and message
- `Bash` Show let's play ranking and search filter
- `Bash` Show YouTube search parameters
- `Bash` Extract text of two game cards
- `Bash` Show summarization prompts
- `Bash` Show let's play prompt and validators
- `Bash` Summarize let's play and similar data for remaining games
- `Bash` Show similarity logic and game metadata
- `Bash` Check sorting, platform filter and search behaviour
- `Bash` Test search with Cyrillic and punctuation variations
- `Bash` Check how search normalizes titles
- `Bash` Check README for deliverable links and AI chat log
- `Bash` Show deployment files and env example
- `Bash` Check reasoning effort handling and .env loading
- `navigate` http://127.0.0.1:8813/monitor
- `javascript_tool` javascript_exec
- `resize_window` 
- `javascript_tool` javascript_exec
- `javascript_tool` javascript_exec
- `navigate` http://127.0.0.1:8813/?platform=PlayStation%205
- `javascript_tool` javascript_exec
- `find` 
- `javascript_tool` javascript_exec
- `navigate` http://127.0.0.1:8813/game/kingdom-hearts-collection-i-iii
- `javascript_tool` javascript_exec
- `computer` screenshot
- `Bash` Show title matching and trailer ranking
- `Bash` Check ASCII architecture diagram alignment
- `Bash` Start an empty-DB instance and read its pages
- `Bash` Stop the empty-DB instance
- `Bash` Show let's play processing and notes
- `Bash` Check transcript error messages
- `resize_window` 
- `navigate` http://127.0.0.1:8813/game/valor-mortis
- `computer` screenshot
- `javascript_tool` javascript_exec
- `computer` screenshot
- `navigate` http://127.0.0.1:8813/monitor
- `computer` screenshot
- `Bash` Count collected tests
- `Bash` Sum test counts and confirm clean tree
- `Bash` Locate run listing and search key code
- `resize_window` 
- `Bash` Stop review server and confirm ports and tree are clean
- `preview_stop` 
- `tabs_close` 

</details>
