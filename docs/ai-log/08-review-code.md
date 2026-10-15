# Ревью: код

Агент `08-review-code`, начат 15.10.2026 01:59, работал около 15 мин, вызовов инструментов: 57.

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

Code lens. Senior review of the current HEAD versus f2dc1f9 and of the whole codebase: correctness, concurrency, restart safety, error handling, schema drift, data loss, test quality (tests that assert nothing, missing edge cases), dead code, leftover debug output.
Return only real problems with exact file and line and a concrete fix; severity blocker means it would make the employer reject the submission.
```

## Итоговый ответ агента

I reviewed HEAD (695f428) against f2dc1f9 and read the whole codebase. The code is in good shape:
- All 192 tests pass offline in about 6 s, and every test asserts something.
- No print, debug or TODO leftovers. The only comments are six `# noqa: E402` lint markers in tests/conftest.py.
- Schema migrations work (from user_version 0 up to 4).
- Selection, day rollover, lease, heartbeat and run bookkeeping hold up under the edge cases I traced.

I confirmed the problems below against real data, not just by reading:
- **Popularity:** in the earlier smoke and proof databases, 2 of 9 found let's plays (echo-weaver and valor-mortis) are not the most-viewed candidate, yet the card says they are.
- **Data loss on refresh:** a probe script built on the repo's own test fakes showed a transcript-based let's play being overwritten by a description-based one on day 9, and the video disappearing on day 11.
- **YouTube format change:** a search page whose `ytInitialData` has no `videoRenderer` is parsed as an empty list, with no error.
- **Shutdown hang:** a real uvicorn on port 8793 with one open `/api/events` stream was still "Waiting for connections to close" 20 s after SIGTERM. I killed it.

Housekeeping: 8765 and 8793 are free. Port 8791 had another uvicorn of this repo (PID 81150, not started by me), and by the end of my run it was gone. I did not modify or commit anything. Scratch copies are deleted. Nothing called Metacritic, YouTube or the LLM.

Найдено проблем: 11

1. **[blocker]** `README.md:1-10 (the repo as a whole)`
   - Что: The ТЗ asks for three deliverables: the repo, a working service, and the chat log with the AI. The README has no live URL, and the repo has no chat transcript and no link to one, so the submission is missing two of the three.
   - Как исправить: Deploy (user action; Railway steps are already in README §Деплой), then put the public URL in the first lines of the README. Export the AI conversation to docs/ai-chat.md (or a shared link) and link it right under the URL.
2. **[major]** `git history: commit f2dc1f9, docs/samples/yt_search_star-wars-galactic-racer_*.html (3 files) and docs/samples/yt_watch_bGBXM6oxbag.html`
   - Что: HEAD is redacted, but commit f2dc1f9 still contains the user's home IP (in `remoteHost`) and the real `visitorData` tokens in 4 files. The first push would publish them.
   - Как исправить: Before the first push, rewrite history. Either squash all 13 commits into one with `git reset --soft $(git rev-list --max-parents=0 HEAD)` plus an amend, or run `git filter-repo --replace-text` over docs/samples. Then check with `git grep [IP] $(git rev-list --all)`.
3. **[major]** `app/pipeline.py:369-379 and app/pipeline.py:418-424`
   - Что: A refresh saves whatever `find_letsplay` returns, even when it is worse than what is stored. Only exceptions keep the old record. Reproduced: a captions-based `llm-transcript` result is replaced on the day-8 refresh by `no_transcript`/`llm-description` when captions are blocked, and the excerpt is lost. Two days later an empty search replaces it with `no_video` and the video link is gone. `ensure_trailer` does the same: a `status: none` result overwrites a stored `ok` trailer, so the card loses its only video link.
   - Как исправить: In `process_game`: if the old let's play has status ok with a method in FULL_LETSPLAY (or has a video) and the new one is not ok, store `{**old_lp, 'last_error': new.get('note'), 'checked_at': now}` instead. In `ensure_trailer`: if the old trailer is ok and the new result is `none`, keep it and only bump `checked_at`. Add tests in tests/test_pipeline.py next to `test_blocked_youtube_keeps_the_previous_video`.
4. **[major]** `app/pipeline.py:434-470, app/youtube.py:254, app/templates/game.html:128`
   - Что: The ТЗ asks for the most popular let's play, but the code skips the most-viewed video whenever it has no captions. It tries captions on the top 3 first and only runs Whisper after all three fail, so a less popular captioned video wins even though Whisper could transcribe the top one. Ranking also puts titles with PREFER keywords ahead of view count. The card still says 'Самый популярный подходящий ролик выбран из поиска YouTube по просмотрам'. Real data: echo-weaver got the #2 video (1,831 views, while #1 has 2,050) and valor-mortis got #3 (141k, while #1 has 294k). The 'Другие найденные ролики' list on the same card shows the more-viewed videos.
   - Как исправить: Go through candidates in rank order, and for each one try captions, then Whisper (if available and within the deadline), before moving on. Skip to the next video only on NoSpeech or when both fail. In `rank_candidates`, sort by views only (`-c.views`). In game.html:128, when `lp.checked` or `stt_checked` is not empty, say which more popular videos were skipped and why, instead of the fixed 'самый популярный' sentence.
5. **[major]** `app/summarize.py:259-283 (line 280 description head), app/pipeline.py:483-491, app/templates/game.html:121-122`
   - Что: When there is no transcript, the LLM still gets LETSPLAY_SYSTEM and LETSPLAY_TASK, which ask how the blogger rates the game and for pros and cons. It invents the blogger's opinion from the video description, often links or marketing text. The card shows this under 'Понравилось блогеру / Не понравилось'. On a cloud deployment, where the README says captions and yt-dlp are blocked, this is the path for nearly every game, so what the employer sees for this extra feature is made up.
   - Как исправить: For source == 'description', use a separate prompt that asks only what the video is about, with no opinions attributed to the blogger, and a validator that returns only `summary`. Do not render the pros/cons headings for `no_transcript` records. Label the block 'По описанию ролика, речь блогера недоступна'.
6. **[major]** `app/youtube.py:194-219`
   - Что: `parse_search_html` raises SchemaError only when `ytInitialData` is missing. If YouTube changes the result markup (for example to `lockupViewModel`), it returns [] with no error. Tested: a page with `ytInitialData` and no `videoRenderer` returns []. Every game then becomes `no_video` or trailer `none` with no schema error on the monitor. Together with the overwrite issue above, this wipes stored let's plays and trailers within about a week.
   - Как исправить: After the loop, if no `videoRenderer` was found and the page has no 'No results' marker (`backgroundPromoRenderer`), raise SchemaError('на странице поиска YouTube нет videoRenderer (сменился формат)'). The pipeline already turns that into a `youtube:schema` error and keeps old data. Add a test with a `lockupViewModel` sample.
7. **[minor]** `app/main.py:527-552 and app/serve.py:23-32`
   - Что: The `/api/events` generator never ends, and uvicorn starts without `--timeout-graceful-shutdown`. Reproduced: with the monitor page open, SIGTERM leaves the process at 'Waiting for connections to close' indefinitely, and lifespan shutdown never runs. Locally Ctrl+C needs a second press. Docker or Railway stop waits for SIGKILL.
   - Как исправить: Add `"--timeout-graceful-shutdown", "5"` to `uvicorn_args`, and the same flag to the README's alternative `uvicorn app.asgi:app` command. Optionally end the stream after a maximum lifetime, such as 10 minutes; EventSource reconnects on its own.
8. **[minor]** `app/main.py:480-492 vs app/main.py:494-503`
   - Что: RUN_TOKEN protects nothing. `/run` returns the same JSON when `Accept: application/json` is sent, never checks the token, and `same_origin` lets through requests with no Origin header. So `curl -X POST -H 'Accept: application/json' host/run` starts a run even with RUN_TOKEN set, and the token on `/api/run` does nothing.
   - Как исправить: Either remove RUN_TOKEN (the cooldown and daily cap are the real protection) and say so in the README, or in `/run` require `token_ok` whenever the request lacks `Sec-Fetch-Site: same-origin`, so only non-browser clients need the token.
9. **[minor]** `app/stt.py:304-318, app/main.py:130-136, app/config.py:113`
   - Что: Whisper is on by default and loads in the web process (0.6–1.25 GB peak per the README). On a small instance the kernel's out-of-memory killer ends the whole service: web UI, monitor and the current run. `except Exception` cannot catch that, so 'fails soft' does not cover this case. The MemoryError test gives false confidence.
   - Как исправить: In `build_services`, read the cgroup limit (`/sys/fs/cgroup/memory.max`). If it is under about 1.5 GB (1 GB for `tiny`), disable STT and show the reason on the 'stt' worker. Or run `transcribe_video` in a `multiprocessing` child process with a timeout, so an OOM kill only takes down the child.
10. **[minor]** `tests/test_selection.py:60-65`
   - Что: In `test_new_day_starts_from_new_releases_again`, the line `assert yesterday.browse_offset == 96` checks a DayState the test built itself and never passed to code under test, so it checks nothing. Nothing here proves that yesterday's saved cursor is ignored.
   - Как исправить: Save the 2026-10-13 state with `db.save_day_state(...)`, then select with `db.get_day_state('2026-10-14')`. Assert the new state has `new_releases_done False`, `browse_offset 0`, and that the first item comes from 'new-releases'.
11. **[minor]** `app/youtube.py:76-81, app/stt.py:493 and 592, app/stt.py:515-517`
   - Что: Dead code: `title_overlap` is used only by tests (the ranking uses `title_matches`). `SpeechToText.last` is written and never read. `SpeechToText.available` is used only in tests.
   - Как исправить: Delete `title_overlap` and its test assertions, the `self.last` attribute and assignment, and the `available` property (tests can call `unavailable_reason() is None`).

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

- `Bash` Show repo history, status and file sizes
- `Bash` Read pipeline module
- `Bash` Read database module
- `Bash` Read scheduler, selection and config
- `Bash` Read web app module
- `Bash` Read LLM, HTTP, monitor, serve modules
- `Bash` Read speech-to-text module
- `Bash` Check HF hub error hierarchy and faster-whisper download
- `Bash` Read YouTube client module
- `Bash` Read Metacritic client module
- `Bash` Read summarize and similarity modules
- `Bash` Check old schema and diff stats
- `Bash` Run test suite
- `Bash` Get test counts and slowest tests
- `Bash` Read test fixtures
- `Bash` Search for comments and debug output
- `Bash` Read pipeline tests part 1
- `Bash` Read pipeline tests part 2
- `Bash` Read deployment files and tracked file list
- `Bash` Check whether IP is present in history vs HEAD
- `Bash` Check visitor data redaction at HEAD
- `Bash` Look for remaining IP addresses in samples
- `Bash` Show context of remoteHost fields
- `Bash` Read game card templates
- `Bash` List smoke and proof directories
- `Bash` Find smoke test databases
- `Bash` Inspect smoke DB let's play choices vs candidate views
- `Bash` Inspect e2e and proof databases
- `Bash` Read monitor JS and template
- `Bash` Read index and base templates
- `Bash` Read scheduler tests
- `Bash` Read web tests
- `Bash` Check README env handling and run instructions
- `Bash` Read STT tests part 1
- `Bash` Read STT tests part 2
- `Bash` Read DB tests
- `Bash` Find tests without assertions
- `Bash` Test whether server shuts down with an open SSE stream
- `Bash` Identify existing listener and check free ports
- `Bash` Retest SSE shutdown on a free port
- `Bash` Check README for live URL and AI chat log mention
- `Bash` Read selection tests
- `Bash` Detect unused imports with AST scan
- `Bash` Check references to possibly dead functions
- `Bash` Show diff stat since initial commit
- `Bash` Check what tests were removed from summarize tests
- `Bash` List remaining test names
- `Bash` Read popularity and pacing tests
- `Bash` Compare stored review fields with old schema
- `Bash` Probe whether let's play refresh overwrites good data
- `Bash` Check search parser on a layout without videoRenderer
- `Bash` Search history for local paths and usernames
- `Bash` Verify ports, clean scratch copies, confirm clean tree

</details>
