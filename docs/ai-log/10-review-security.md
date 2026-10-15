# Ревью: безопасность и деплой

Агент `10-review-security`, начат 15.10.2026 01:59, работал около 7 мин, вызовов инструментов: 38.

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

Security and deploy lens. Secrets or personal data in tracked files and git history (grep for emails, tokens, home paths, phone numbers, visitor ids), XSS, CSP, force-run abuse, dependency CVEs, Dockerfile and railway.json correctness for Railway (no VOLUME, PORT, healthcheck, non-root, volume mount at /data documented), image size, start command.
Return only real problems with exact file and line and a concrete fix; severity blocker means it would make the employer reject the submission.
```

## Итоговый ответ агента

I reviewed ~/dev/skytec-metacritic at HEAD 695f428 through a security and deploy lens. I did not change any tracked file and did not commit. The 192 tests pass offline, and an OSV batch query over every pin in requirements.txt found no known vulnerabilities.

**What held up:**
- **XSS:** Jinja autoescape is on, and links pass safe_url plus the |url filter. Every innerHTML in app.js goes through esc(). The templates have no inline style or script.
- **CSP:** It allows only self, jwplayer and youtube-nocookie frames, with frame-ancestors 'none'. The YouTube iframe sets its own referrerpolicy.
- **Force-run button:**
  - The same-origin check uses Sec-Fetch-Site and Origin. A browser can't forge X-Forwarded-Host cross-site without a preflight.
  - A process lock plus a lease in the database allow one run at a time.
  - There is a 5-minute cooldown and a cap of 24 manual runs a day.
  - The `limit` parameter is clamped to BATCH_SIZE in pipeline.py:183, so it can't be used to enlarge a run.
- **Secrets:** None are tracked. There are no home paths or tokens in the tree, and .env is gitignored and never copied into the image.
- **Railway setup:**
  - The Dockerfile has no VOLUME and reads PORT.
  - railway.json sets the /healthz healthcheck and 1 replica.
  - The process drops to uid 10001.
  - The README documents mounting /data.
  - The start command is `python -m app.serve`, with proxy headers and a single worker.
  - The base model plus the database fit Railway's 0.5 GB Trial volume.

**Problems:**
1. **Blocker:** there is no live service URL and no AI chat log. The ТЗ requires both, and only the user can deploy and export the log.
2. **Home IP in git history:** commits f2dc1f9 and 3b3f3e6 both contain the home IP [IP] and visitor tokens. The earlier handoff named only f2dc1f9. Rewrite the history before the first push. Commit authors are also split between the gmail and mail.ru addresses.
3. **Railway memory:** the Trial plan's 1 GB cap is below Whisper's measured 1.0–1.25 GB peak in the container. Whisper runs inside the web process, so with default settings an OOM can kill the service the employer is looking at.
4. **Minor:**
   - At startup, the root-run chown of /data follows symlinks.
   - The samples still contain per-visitor ids (datasyncId, rolloutToken).
   - The base image tag is not pinned.

Найдено проблем: 6

1. **[blocker]** `README.md:1-11 (no live link anywhere in the README; nothing in docs/ either)`
   - Что: The ТЗ asks for three things: a repo, a working service and the chat log with the AI. The repo has neither a deployed service URL nor the chat log. A reviewer opening the README has nothing to click and no AI transcript to read. This alone can get the submission rejected, however good the code is.
   - Как исправить: The user has to deploy it; agents were not allowed to. Deploy to Railway: Dockerfile builder, a Volume mounted at /data, no variables required. On the Trial plan also set STT_MODEL=tiny or STT_ENABLED=0 (see the memory item below). Wait for the first scheduled run, about 15 s after start plus 30–40 min for a full batch. On /monitor, check whether the YouTube worker shows RequestBlocked. If only captions are blocked, set YOUTUBE_CAPTIONS=0. Then add a line such as «Демо: https://<name>.up.railway.app (мониторинг: /monitor)» directly under the H1 in README.md. Export the AI transcript to e.g. docs/ai-chat.md and link it from the README. Before committing it, scrub it with grep -nE '[IP]|@gmail\.com|@mail\.ru|github-token|railway-token|/Users/'. The sessions contain the home IP, both email addresses and token file paths.
2. **[major]** `git history: commits f2dc1f9 and 3b3f3e6, docs/samples/yt_search_star-wars-galactic-racer_lets-play_byviews.html, ..._lets-play_over20min.html, ..._prohozhdenie_over20min.html, yt_watch_bGBXM6oxbag.html (line 18)`
   - Что: Checked with git grep over every rev: the user's home IP [IP] is in 4 files in both f2dc1f9 and 3b3f3e6, so not only f2dc1f9 as the handoff said. remoteHost and about 277 ip= stream-URL parameters carry it, next to the unredacted VISITOR_DATA/visitorData. 270096c only redacts it going forward, so the first push would publish the IP. Commit authorship is also mixed: f2dc1f9 is [email] and the other 11 commits are [email].
   - Как исправить: Nothing is pushed yet, so rewrite before the first push. First commit the extra redaction from the minor item below, call that commit R. Then run: git filter-branch --prune-empty --tree-filter 'for f in docs/samples/yt_search_star-wars-galactic-racer_lets-play_byviews.html docs/samples/yt_search_star-wars-galactic-racer_lets-play_over20min.html docs/samples/yt_search_star-wars-galactic-racer_prohozhdenie_over20min.html docs/samples/yt_watch_bGBXM6oxbag.html; do [ -f "$f" ] && git show R:"$f" > "$f"; done' -- main. Optionally add --env-filter 'export GIT_AUTHOR_EMAIL=<chosen> GIT_COMMITTER_EMAIL=<chosen>' to unify the identity. Then delete the backup refs and prune: git for-each-ref --format='%(refname)' refs/original | xargs -n1 git update-ref -d; git reflog expire --expire=now --all; git gc --prune=now. Check that for c in $(git rev-list --all); do git grep -l [IP] $c; done prints nothing, and rerun pytest. Commit SHAs will change, so update any SHAs quoted elsewhere.
3. **[major]** `app/config.py:113-114 (STT on, model base by default); app/stt.py:303-318 (model loaded in the web process); railway.json:10-11`
   - Что: The README measures Whisper's container peak at about 1.25 GB for base and about 1.0 GB for tiny (README.md:152-171), and asks for at least 1.5 GB RAM. Railway Trial caps a service at 1 GB and Free at 0.5 GB (docs.railway.com/reference/pricing/plans). The user's Railway account is a trial. Whisper runs inside the uvicorn process, so an OOM kill takes the whole web UI down mid-run. The run is closed as interrupted and the service restarts. With restartPolicyType ON_FAILURE and 5 retries, repeated OOMs on later runs can leave the deployment stopped (the exact retry-counting semantics are unverified). The next run with no captions triggers it again: Railway IPs usually get RequestBlocked, which pushes every let's play to Whisper whenever yt-dlp still gets through. This is plausible from the published limits and the measured peaks, but not reproduced on Railway itself. The README's Railway section (README.md:231-245) doesn't mention memory at all.
   - Как исправить: In Settings, read the cgroup limit (/sys/fs/cgroup/memory.max, falling back to /sys/fs/cgroup/memory/memory.limit_in_bytes). If it is below 1.5 GB and STT_MODEL is not set explicitly, use tiny. Below 1.1 GB, set stt_enabled=False and show the reason on the stt worker. Better still, run the transcribe step in a multiprocessing 'spawn' child, so an OOM kills only the child and becomes a SpeechUnavailable. Add a step 4 to the Railway deploy section: «Trial/Free (≤1 ГБ RAM): STT_MODEL=tiny или STT_ENABLED=0». Change restartPolicyType to ALWAYS, or raise restartPolicyMaxRetries, so one OOM streak cannot leave the demo down.
4. **[minor]** `app/serve.py:10-13`
   - Что: drop_privileges runs as root and calls os.chown(name, uid, gid) on every entry os.walk lists under /data. os.chown follows symlinks by default, and uid 10001 can write to /data. If the app process were ever compromised, for example through a media-parsing bug in av or yt-dlp on downloaded audio, it could plant /data/x -> /etc/passwd or a file under site-packages. On the next container start, root would chown that target to the app user, which is a classic CWE-59 escalation inside the container.
   - Как исправить: Skip links and don't follow them: `if os.path.islink(name): continue` then `os.chown(name, pw.pw_uid, pw.pw_gid, follow_symlinks=False)`, and pass followlinks=False to os.walk explicitly. Add a test in tests/test_serve.py: create a symlink in a temp data dir pointing outside it, and assert the target's owner is unchanged (or mock os.chown and assert follow_symlinks=False).
5. **[minor]** `docs/samples/yt_search_star-wars-galactic-racer_lets-play_byviews.html:18, ..._lets-play_over20min.html:18, ..._prohozhdenie_over20min.html:18, yt_watch_bGBXM6oxbag.html:18`
   - Что: The redaction is incomplete, which contradicts the claim in README.md:416-418. Each file still has a per-visitor DATASYNC_ID / datasyncId (V[id]||, V[id]||, V[id]||, V[id]||, 13 times each) and a rolloutToken. Both are signed-out visitor identifiers, the same kind of data visitorData is.
   - Как исправить: Run sed -E -i '' 's/("(DATASYNC_ID|datasyncId)":")V[0-9a-f]+\|\|/\1REDACTED||/g; s/("rolloutToken":")[^"]+/\1REDACTED/g' on the four files, run pytest, and commit before doing the history rewrite above, so the rewrite also cleans the old commits. Extend the README sentence to mention datasyncId and rolloutToken.
6. **[minor]** `Dockerfile:1`
   - Что: `FROM python:3.12-slim` is a floating tag, while the brief says to keep dependencies pinned and requirements.txt pins the full tree. Each Railway rebuild can pull a different Python patch and Debian base, so the image the employer runs isn't the one that was tested.
   - Как исправить: Pin a patch version and digest, e.g. `FROM python:3.12.<x>-slim-bookworm@sha256:<digest>`. Get the digest with `docker buildx imagetools inspect python:3.12.<x>-slim-bookworm`. Mention it in the README «Зависимости» section with the update command.

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

- `Bash` Show repo status, history and tracked files
- `Bash` Show deploy files
- `Bash` Search history and tree for personal data
- `Bash` Check which commits contain the IP
- `Bash` Inspect sensitive keys in samples at HEAD
- `Bash` Search samples for cookies, geo, IPs
- `Bash` Line counts and repo size
- `Read` ~/dev/skytec-metacritic/app/main.py
- `Bash` Show scheduler and config
- `Bash` Search templates for XSS-relevant patterns
- `Bash` Check video URL parsing
- `Bash` Show safe_url and smoke dirs
- `Bash` Show frontend JS
- `Bash` Check monitor and proxy logging
- `Bash` Check LLM key handling and logging
- `Bash` Show dependency pins
- `Bash` Query OSV for known vulnerabilities in pins
- `Bash` Grep README deploy notes
- `Bash` Read README security and deploy sections
- `Bash` Search all history for personal data and tokens
- `Bash` List commit authors and grep personal data
- `Bash` Search samples for geo info and show redaction commit
- `Bash` Check stream URLs for IP params
- `Bash` Read yt-dlp audio source
- `Bash` Run test suite
- `Bash` Read STT model lifecycle
- `ToolSearch` query=select:WebSearch,WebFetch, max_results=2
- `WebFetch` url=https://docs.railway.com/reference/pricing/plans, prompt=What are the per-service RAM and vCPU limits for the Trial,
- `WebFetch` url=https://docs.railway.com/reference/volumes, prompt=What are the volume size limits per plan (Trial, Free, Hobby
- `Bash` Inspect downloaded model directory layout
- `Bash` Check how manual run limit is used
- `Bash` Search tracked files for local paths
- `Bash` Read monitor template head
- `Bash` Check README top for live URL and chat log
- `Bash` Collect exact line numbers

</details>
