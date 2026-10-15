# Ревью: требования

Агент `07-review-requirements`, начат 15.10.2026 01:59, работал около 12 мин, вызовов инструментов: 65.

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
- **The user's home IP is still in git history.** Commit f2dc1f9 contains the visitor ids and the IP 203.0.113.7 inside `docs/samples/yt_*.html`. I redacted the files in a new commit, but the old commit still has them. Rewriting history is destructive, so I didn't. Since nothing has been pushed, squashing or filtering before the first push would remove them.
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

Repo: `~/dev/skytec-metacritic`. Smoke database and logs: `<scratchpad>/smoke/`
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

The proof logs and database are in `<scratchpad>/proof/`. The full-run logs are in `scratchpad/e2e/`. The downloaded models (682 MB) are in `scratchpad/models/` and can be deleted.

Files are in `~/dev/skytec-metacritic`:
- app/stt.py
- tests/test_stt.py
- app/pipeline.py
- README.md

Requirements lens. For every single item of the ТЗ (each data field, each UI feature, each selection rule including the daily restart and the SEE ALL paging, both additional parts, the deliverables), find the concrete evidence in code and in the running app with the existing local database, and report anything missing, partial, wrong or unconvincing to an employer.
Return only real problems with exact file and line and a concrete fix; severity blocker means it would make the employer reject the submission.
```

## Итоговый ответ агента

Requirements review of ~/dev/skytec-metacritic at HEAD 695f428. The working tree is clean and there is no git remote. The full suite passes (192 tests). I ran the app on a copy of the latest local database (scratchpad/proof/proof.db: 10 games, 2 runs) on port 8791, with the scheduler, YouTube and LLM switched off, so it made no scraping or LLM calls. I also opened a migrated copy of the stale repo database data/games.db (schema v0, 5 games). Then I stopped the server: nothing is listening on 8765, 8799 or 8791. Another uvicorn on port 8813 (pid 80807, started 02:01) is not mine, so I left it running.

Every ТЗ item has working code behind it:
- **Selection logic** has offline tests: hourly scheduler, New Releases (the same finder query as the /game/ carousel, matched 1:1 against the saved HTML), SEE ALL with a per-day offset cursor, the daily restart, upsert, and same-day retries.
- **Data fields:** all of them are in the database and on the card, including per-platform Metascore and Userscore.
- **Review summaries:** separate likes/dislikes for critics and for users, recomputed when the review set changes.
- **Web UI:** list, card, platform filter (which also switches the scores shown), title search, rating sort, and similar games that link to their cards.
- **Доп. 1:** a let's play found through captions or Whisper, with an LLM conclusion and a link to the video.
- **Доп. 2:** a live SSE monitor with worker statuses, counts and a protected force-run button.

What would hurt the submission:
- **Deliverables:** two of the three are missing — no live service URL, no AI chat log, and the repo is not published. Git history still holds the home IP.
- **Live evidence:** the live runs never reached the SEE ALL rule or the default batch of 20.
- **Fabricated let's play conclusion:** the Kingdom Hearts card attributes opinions to a blogger whose video has no commentary; the "transcript" is in-game dialogue plus caption noise.
- **Similar games:** 8 of 10 cards in the existing database show «Похожих игр в базе пока нет».
- **Smaller visible defects:**
  - English bullet points in a Russian summary (Total War user reviews).
  - A 2018 Kingdom Hearts III trailer used for the Collection.
  - Search fails on simple punctuation variants.
  - Demo videos are picked as let's plays for games already released.
  - A run that finishes the New Releases block takes only the remainder instead of 20 games.
  - Description-based conclusions are labelled as the blogger's opinion; this would affect most cards on a cloud host where YouTube blocks requests.

Найдено проблем: 13

1. **[blocker]** `README.md:1-11 (no live URL anywhere); Dockerfile + railway.json are ready but nothing is deployed`
   - Что: The ТЗ deliverable «работающий сервис» is missing: there is no public URL and the README gives none. The README says itself (README.md:206-212) that YouTube usually blocks datacenter IPs. On Railway, captions and yt-dlp may therefore both fail, which leaves Доп. 1 degraded. With less than 1.5 GB RAM, Whisper `base` can also get the single web process (UI and scheduler) killed for lack of memory.
   - Как исправить: Deploy from the existing Dockerfile (Railway volume at /data). If the plan has less than 1.5 GB RAM, set STT_MODEL=tiny. Let at least 2 hourly runs finish, then open 3-4 cards. If let's plays show «Расшифровка недоступна», set YOUTUBE_PROXY_URL, or upload a games.db built locally (where YouTube works) to /data. Put the URL in the first lines of README.md as «Работающий сервис: https://…», along with /monitor.
2. **[blocker]** `README.md (no section), docs/ (no log); source material is in ~/.claude/projects/<project>/*.jsonl and <внешний диск>`
   - Что: The ТЗ deliverable «переписка с нейросетью» is not in the repo, and the README does not mention it. An employer reviewing the repo sees no AI chat log at all.
   - Как исправить: Export the relevant Claude Code sessions to Markdown and save them as docs/ai-log/*.md. Strip every system-reminder/memory block first: the transcripts contain the user's private memory and tool output with tokens. Add a «Переписка с нейросетью» section near the top of README.md that links the log and summarises the prompt pipeline from <личные заметки>.
3. **[blocker]** `git history: commit f2dc1f9, docs/samples/yt_search_*.html (3 files) and docs/samples/yt_watch_bGBXM6oxbag.html; `git remote -v` is empty`
   - Что: The «репозиторий» deliverable does not exist yet: nothing is pushed. The first commit still holds the home IP 203.0.113.7 (`git grep 203.0.113.7 f2dc1f9` matches 4 files) plus the visitorData values. Pushing as-is publishes them.
   - Как исправить: Rewrite history before the first push and keep the commit sequence: `pip install git-filter-repo`, then write expr.txt with `203.0.113.7==>203.0.113.7` and a regex line for each visitorData/VISITOR_DATA value redacted in 270096c (`regex:"visitorData":"[^"]+"==>"visitorData":"REDACTED"` and so on). Run `git filter-repo --replace-text expr.txt --force` and confirm `git log -p --all | grep -c 203.0.113.7` returns 0. Then create a public GitHub repo, push, and put its link in README.md.
4. **[major]** `app/pipeline.py:435-449 (captions accepted without any commentary check), app/pipeline.py:454 (Whisper loop, same issue), app/summarize.py:259-294 (LETSPLAY_SYSTEM/TASK, validate_letsplay_json), app/youtube.py:284 (NOISE)`
   - Что: A let's play conclusion is invented for a video without commentary. Kingdom Hearts Collection I ~ III picked Ben-Gun's «Gameplay Walkthrough … FULL GAME»: its captions are cutscene dialogue plus music artifacts («Heat. Heat. Heat… [bell]»). The card still says «Автор ролика отмечает… ценит улучшенную производительность на PS5 Pro» and labels the excerpt «Фрагменты расшифровки речи блогера». Other conclusions list things that are not about the game: Valor Mortis pros include «Позитивный настрой и энтузиазм автора», its cons are about the video («Отсутствие подробного обзора…»), and a Planet Zoo 2 con is «не любить есть пищу на полу». This is the AI part an employer will read most closely.
   - Как исправить: Add `"has_commentary": true|false` and `"evidence": ["2-3 цитаты оценок ведущего"]` to the JSON in LETSPLAY_TASK. In both candidate loops, treat has_commentary=false or empty evidence as «нет речи блогера» and try the next candidate. Before calling the LLM, reject transcripts with almost no first-person or audience markers (I/I'm/guys/let's/я/ребята/давайте per 1000 words). Extend NOISE to drop runs of «Heat.» and «[bell]». Add to the prompt: «плюсы и минусы — только об игре и только то, что сказал ведущий; если минусов нет — пустой список». In validate_letsplay_json, drop list items that mention автор/ролик/видео/блогер.
5. **[major]** `README.md:373-379 (Проверка на живых данных); database state: day_state new_releases_done=0, browse_offset=0, both runs selected=5, sources=new-releases`
   - Что: Selection rule 2 (SEE ALL paging, sorted by newest) and the default batch of 20 have never run against live Metacritic. All 10 games in every local database come from New Releases with BATCH_SIZE=5, so New Releases was never even completed. The README therefore has no live evidence for half of the selection logic. The user's own plan (<личные заметки>, step 8: «живой прогон двух партий по 20 игр») is not reflected yet. The repo's data/games.db is also stale (schema v0, 5 games, no trailers or Whisper).
   - Как исправить: On a fresh database with default settings, let the startup run finish (20 New Releases), then press the button after the 5-minute pause (or run a 3rd time). Check that runs.sources goes `new-releases` → `browse:p1, browse:p2` and that day_state moves to new_releases_done=1 with browse_offset 24 or 48. Add a table of these runs (games, sources, minutes, errors, LLM/fallback counts) to README.md:373. Use this database for the deployed volume and any local demo.
6. **[major]** `app/similarity.py:73,76,121-128 (exact-match genres, MIN_DESC=0.06, min_score=0.05); app/templates/game.html:166`
   - Что: In the existing database, 8 of 10 cards show «Похожих игр в базе пока нет»; only Valor Mortis and Dragon's Dogma 2 link to each other. Metacritic genres are single, narrow labels (FPS, Action RPG, Tycoon, Action Adventure), so exact genre equality almost never matches. Description cosine stays below the thresholds, e.g. Valor Mortis vs Total War scores 0.047 against min_score 0.05. A reviewer clicking cards will conclude the feature does not work.
   - Как исправить: Ship a bigger database (at least 100 games from a full day's runs, see the item above). In _norm_set for genres, also add word tokens, so «Action RPG» and «Action Adventure» share «action» at a reduced weight. When fewer than SIMILAR_TOP_N games pass the threshold, fill the list with the next best-scoring games labelled «ближайшие из имеющихся», keeping the reasons chips honest.
7. **[major]** `app/templates/game.html:119-123 («Понравилось блогеру» shown for every method); app/summarize.py:259-262 (LETSPLAY_SYSTEM forces «автор ролика»), app/summarize.py:279-280 (description prompt)`
   - Что: When there is no transcript, the conclusion is built from the video's title and description (methods llm-description/description). It is still written as «Автор ролика отмечает…» and shown under «Понравилось блогеру», so the bloger's supposed speech is invented. The local database has none of these yet. On a cloud host where YouTube blocks captions and yt-dlp, though, this becomes the result for most cards, which contradicts «речь блогера в текст».
   - Как исправить: Use a separate system prompt for source=='description': «это описание ролика, а не речь ведущего; не приписывай мнения автору, пиши «судя по описанию ролика»». In game.html, for lp.method in ('llm-description','description'), rename the headings to «По описанию ролика: плюсы/минусы» and show a warning badge «речь блогера не расшифрована».
8. **[minor]** `app/summarize.py:150-164 (ensure_russian joins likes, dislikes and verdict before comparing letter counts)`
   - Что: English bullets get through the «only Russian» check. The Total War: SHOGUN 2 Complete Edition card shows «Не нравится: botched remaster process, aggressive business practices, severe technical downgrades, use of AI slop» under a Russian verdict, because the combined text has more Cyrillic letters overall.
   - Как исправить: Check the verdict and each list item separately: fail any item that has Latin words but no Cyrillic letters (allowing proper names inside Cyrillic text), so the existing retry runs. Then add a one-off migration that sets method to 'extractive' on stored summaries failing the new check, so backfill_llm recomputes them.
9. **[minor]** `app/youtube.py:38 (STOP includes collection/complete/edition/remastered), app/youtube.py:111-131 (title_matches: «~» is not a subtitle separator), app/youtube.py:257-269 (rank_trailers)`
   - Что: Re-releases get the wrong YouTube trailer. Kingdom Hearts Collection I ~ III shows «Kingdom Hearts III - Official Extended Trailer | E3 2018» because «collection» is dropped as a stop word and «~» is not treated as a subtitle separator, so the 2018 trailer matches.
   - Как исправить: In rank_trailers, require edition words (collection, remaster(ed), complete, definitive, edition, dark arisen…) to appear in the video title whenever they appear in the game title, and add «~» to the separator regex. Prefer trailers published near the release date: parse publishedTimeText and drop results older than about 1 year when newer matches exist.
10. **[minor]** `app/youtube.py:241-254 (rank_candidates)`
   - Что: Demo and preview videos are picked as «the let's play» for games that are already out. Valor Mortis gets a demo playthrough, and its conclusion says «игра ещё в разработке» although the card shows release on 13 October 2026. Truckful and Boltgun 2 also get «FULL DEMO» videos, and Planet Zoo 2 gets «Played Early!».
   - Как исправить: Pass the release date to find_letsplays. If it has passed, rank titles matching demo|beta|early|preview|демо|бета below the rest, without excluding them, so they are only used when nothing else exists.
11. **[minor]** `app/db.py:166 (search_key), app/db.py:473 (single LIKE on the raw casefolded title)`
   - Что: Title search fails on obvious queries. «dragons dogma», «warhammer 40000» and «kingdom hearts collection i-iii» all return 0 results, while «dragon's» and «40,000» work.
   - Как исправить: Normalise both the stored title_search and the query to alphanumeric tokens (drop apostrophes and commas; turn - ~ : into spaces). Match every token with an AND of LIKE clauses. Add a migration that recomputes title_search for existing rows.
12. **[minor]** `app/selection.py:62`
   - Что: A run that finishes the New Releases block returns early instead of filling up to 20. Example: if run 1 stops at the 50-minute limit after 15 games, run 2 processes only the 5 remaining New Releases and wastes the hourly slot. The same happens when retries come first. test_selection.py:71 locks this in: the second call gets 15 games with batch=20.
   - Как исправить: Return early only when the batch is full, or when New Releases items were taken and the block is still not done (meaning the batch is full). When `take(items, 'new-releases')` finished the block in this run, continue into the SEE ALL loop to fill the batch. Update test_limit_smaller_than_block_keeps_block_open to expect 15 New Releases followed by SEE ALL items.
13. **[minor]** `app/pipeline.py:432 vs README.md:27`
   - Что: The card note for a missing let's play says «летсплеев длиннее 10 минут», but the README says the search keeps «ролики длиннее 20 минут», and the search filter sp=CAMSAhgC really is longer than 20 minutes. The Dragon's Dogma 2 card shows the wrong number.
   - Как исправить: Change the note to «длиннее 20 минут» and add the reason from README.md:226-229, e.g. «название ролика должно содержать подзаголовок игры».

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

- `Bash` Show repo status, history and layout
- `Bash` Show file sizes and config files
- `Bash` Read config, selection and scheduler modules
- `Bash` Read Metacritic scraper module
- `Bash` Read data sources doc
- `Bash` Read pipeline module
- `Bash` Read database module
- `Bash` Read web app module
- `Bash` Read HTML templates
- `Bash` Read monitor template, JS and monitor module
- `Bash` Read YouTube module
- `Bash` Read summarize and similarity modules
- `Bash` Show README section headings
- `Bash` Read first part of README
- `Bash` Read rest of README
- `Bash` List scratchpad databases and logs
- `Bash` Find candidate local databases
- `Bash` Check servers and database contents read-only
- `Bash` Copy databases and summarise game rows
- `Bash` Run the offline test suite
- `Bash` Check history for the leaked IP and list tracked files
- `Bash` Search README for deliverable references
- `Bash` Start the app on a database copy without scheduler or network
- `Bash` Exercise list API filters, sort and search
- `Bash` Inspect per-platform scores in the database copy
- `Bash` Render the Valor Mortis card as text
- `Bash` Render the rest of the Valor Mortis card as text
- `Bash` Show let's play selections and conclusions
- `Bash` Inspect transcript excerpts for two games
- `Bash` Show similar-games data per game
- `Skill` skill=anthropic-skills:built-in-browser
- `preview_start` http://127.0.0.1:8791/
- `computer` screenshot
- `resize_window` 
- `computer` screenshot
- `navigate` http://127.0.0.1:8791/monitor
- `computer` screenshot
- `get_page_text` 
- `Bash` Compare New Releases sample HTML with JSON
- `Bash` Compare SEE ALL sample HTML with JSON request
- `Bash` List selection, scheduler and pipeline tests
- `Bash` Show video and cover URLs per game
- `Bash` Show day state, runs and processed rows
- `Bash` Render pages against a migrated old-schema database copy
- `Bash` Dump review summary content
- `Bash` Confirm English bullets on card and find line numbers
- `Bash` Measure description similarity between stored games
- `Bash` Render let's play sections for two games
- `Bash` Read dev requirements, serve entrypoint and LLM client head
- `Bash` Check card API exposes every required field
- `Bash` Locate line numbers for findings
- `Bash` Locate more line numbers
- `Bash` Read selection tests
- `Bash` Test title search with punctuation variants
- `Bash` Inspect the redaction commit
- `Bash` Stop the test server and confirm ports are free
- `resize_window` 
- `tabs_close` 
- `Bash` Check leftover server processes
- `Bash` Confirm my server process is gone
- `Bash` Get remaining line numbers

</details>
