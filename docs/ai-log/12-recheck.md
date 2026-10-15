# Перепроверка исправлений, раунд 2

Агент `12-recheck`, начат 15.10.2026 10:59, работал около 13 мин, вызовов инструментов: 80.

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
Do not modify tracked files. Do not scrape or call the LLM.

These problems were reported:
[
  {
    "severity": "blocker",
    "where": "README.md:1-11 (no live URL anywhere); Dockerfile + railway.json are ready but nothing is deployed",
    "what": "The ТЗ deliverable «работающий сервис» is missing: there is no public URL and the README gives none. The README says itself (README.md:206-212) that YouTube usually blocks datacenter IPs. On Railway, captions and yt-dlp may therefore both fail, which leaves Доп. 1 degraded. With less than 1.5 GB RAM, Whisper `base` can also get the single web process (UI and scheduler) killed for lack of memory.",
    "fix": "Deploy from the existing Dockerfile (Railway volume at /data). If the plan has less than 1.5 GB RAM, set STT_MODEL=tiny. Let at least 2 hourly runs finish, then open 3-4 cards. If let's plays show «Расшифровка недоступна», set YOUTUBE_PROXY_URL, or upload a games.db built locally (where YouTube works) to /data. Put the URL in the first lines of README.md as «Работающий сервис: https://…», along with /monitor.",
    "lens": "requirements"
  },
  {
    "severity": "blocker",
    "where": "README.md (no section), docs/ (no log); source material is in ~/.claude/projects/[project]/*.jsonl and [внешний диск]",
    "what": "The ТЗ deliverable «переписка с нейросетью» is not in the repo, and the README does not mention it. An employer reviewing the repo sees no AI chat log at all.",
    "fix": "Export the relevant Claude Code sessions to Markdown and save them as docs/ai-log/*.md. Strip every system-reminder/memory block first: the transcripts contain the user's private memory and tool output with tokens. Add a «Переписка с нейросетью» section near the top of README.md that links the log and summarises the prompt pipeline from [личные заметки].",
    "lens": "requirements"
  },
  {
    "severity": "blocker",
    "where": "git history: commit f2dc1f9, docs/samples/yt_search_*.html (3 files) and docs/samples/yt_watch_bGBXM6oxbag.html; `git remote -v` is empty",
    "what": "The «репозиторий» deliverable does not exist yet: nothing is pushed. The first commit still holds the home IP [IP] (`git grep [IP] f2dc1f9` matches 4 files) plus the visitorData values. Pushing as-is publishes them.",
    "fix": "Rewrite history before the first push and keep the commit sequence: `pip install git-filter-repo`, then write expr.txt with `[IP]==>203.0.113.7` and a regex line for each visitorData/VISITOR_DATA value redacted in 270096c (`regex:\"visitorData\":\"[^\"]+\"==>\"visitorData\":\"REDACTED\"` and so on). Run `git filter-repo --replace-text expr.txt --force` and confirm `git log -p --all | grep -c [IP]` returns 0. Then create a public GitHub repo, push, and put its link in README.md.",
    "lens": "requirements"
  },
  {
    "severity": "major",
    "where": "app/pipeline.py:435-449 (captions accepted without any commentary check), app/pipeline.py:454 (Whisper loop, same issue), app/summarize.py:259-294 (LETSPLAY_SYSTEM/TASK, validate_letsplay_json), app/youtube.py:284 (NOISE)",
    "what": "A let's play conclusion is invented for a video without commentary. Kingdom Hearts Collection I ~ III picked Ben-Gun's «Gameplay Walkthrough … FULL GAME»: its captions are cutscene dialogue plus music artifacts («Heat. Heat. Heat… [bell]»). The card still says «Автор ролика отмечает… ценит улучшенную производительность на PS5 Pro» and labels the excerpt «Фрагменты расшифровки речи блогера». Other conclusions list things that are not about the game: Valor Mortis pros include «Позитивный настрой и энтузиазм автора», its cons are about the video («Отсутствие подробного обзора…»), and a Planet Zoo 2 con is «не любить есть пищу на полу». This is the AI part an employer will read most closely.",
    "fix": "Add `\"has_commentary\": true|false` and `\"evidence\": [\"2-3 цитаты оценок ведущего\"]` to the JSON in LETSPLAY_TASK. In both candidate loops, treat has_commentary=false or empty evidence as «нет речи блогера» and try the next candidate. Before calling the LLM, reject transcripts with almost no first-person or audience markers (I/I'm/guys/let's/я/ребята/давайте per 1000 words). Extend NOISE to drop runs of «Heat.» and «[bell]». Add to the prompt: «плюсы и минусы — только об игре и только то, что сказал ведущий; если минусов нет — пустой список». In validate_letsplay_json, drop list items that mention автор/ролик/видео/блогер.",
    "lens": "requirements"
  },
  {
    "severity": "major",
    "where": "README.md:373-379 (Проверка на живых данных); database state: day_state new_releases_done=0, browse_offset=0, both runs selected=5, sources=new-releases",
    "what": "Selection rule 2 (SEE ALL paging, sorted by newest) and the default batch of 20 have never run against live Metacritic. All 10 games in every local database come from New Releases with BATCH_SIZE=5, so New Releases was never even completed. The README therefore has no live evidence for half of the selection logic. The user's own plan ([личные заметки], step 8: «живой прогон двух партий по 20 игр») is not reflected yet. The repo's data/games.db is also stale (schema v0, 5 games, no trailers or Whisper).",
    "fix": "On a fresh database with default settings, let the startup run finish (20 New Releases), then press the button after the 5-minute pause (or run a 3rd time). Check that runs.sources goes `new-releases` → `browse:p1, browse:p2` and that day_state moves to new_releases_done=1 with browse_offset 24 or 48. Add a table of these runs (games, sources, minutes, errors, LLM/fallback counts) to README.md:373. Use this database for the deployed volume and any local demo.",
    "lens": "requirements"
  },
  {
    "severity": "major",
    "where": "app/similarity.py:73,76,121-128 (exact-match genres, MIN_DESC=0.06, min_score=0.05); app/templates/game.html:166",
    "what": "In the existing database, 8 of 10 cards show «Похожих игр в базе пока нет»; only Valor Mortis and Dragon's Dogma 2 link to each other. Metacritic genres are single, narrow labels (FPS, Action RPG, Tycoon, Action Adventure), so exact genre equality almost never matches. Description cosine stays below the thresholds, e.g. Valor Mortis vs Total War scores 0.047 against min_score 0.05. A reviewer clicking cards will conclude the feature does not work.",
    "fix": "Ship a bigger database (at least 100 games from a full day's runs, see the item above). In _norm_set for genres, also add word tokens, so «Action RPG» and «Action Adventure» share «action» at a reduced weight. When fewer than SIMILAR_TOP_N games pass the threshold, fill the list with the next best-scoring games labelled «ближайшие из имеющихся», keeping the reasons chips honest.",
    "lens": "requirements"
  },
  {
    "severity": "major",
    "where": "app/templates/game.html:119-123 («Понравилось блогеру» shown for every method); app/summarize.py:259-262 (LETSPLAY_SYSTEM forces «автор ролика»), app/summarize.py:279-280 (description prompt)",
    "what": "When there is no transcript, the conclusion is built from the video's title and description (methods llm-description/description). It is still written as «Автор ролика отмечает…» and shown under «Понравилось блогеру», so the bloger's supposed speech is invented. The local database has none of these yet. On a cloud host where YouTube blocks captions and yt-dlp, though, this becomes the result for most cards, which contradicts «речь блогера в текст».",
    "fix": "Use a separate system prompt for source=='description': «это описание ролика, а не речь ведущего; не приписывай мнения автору, пиши «судя по описанию ролика»». In game.html, for lp.method in ('llm-description','description'), rename the headings to «По описанию ролика: плюсы/минусы» and show a warning badge «речь блогера не расшифрована».",
    "lens": "requirements"
  },
  {
    "severity": "minor",
    "where": "app/summarize.py:150-164 (ensure_russian joins likes, dislikes and verdict before comparing letter counts)",
    "what": "English bullets get through the «only Russian» check. The Total War: SHOGUN 2 Complete Edition card shows «Не нравится: botched remaster process, aggressive business practices, severe technical downgrades, use of AI slop» under a Russian verdict, because the combined text has more Cyrillic letters overall.",
    "fix": "Check the verdict and each list item separately: fail any item that has Latin words but no Cyrillic letters (allowing proper names inside Cyrillic text), so the existing retry runs. Then add a one-off migration that sets method to 'extractive' on stored summaries failing the new check, so backfill_llm recomputes them.",
    "lens": "requirements"
  },
  {
    "severity": "minor",
    "where": "app/youtube.py:38 (STOP includes collection/complete/edition/remastered), app/youtube.py:111-131 (title_matches: «~» is not a subtitle separator), app/youtube.py:257-269 (rank_trailers)",
    "what": "Re-releases get the wrong YouTube trailer. Kingdom Hearts Collection I ~ III shows «Kingdom Hearts III - Official Extended Trailer | E3 2018» because «collection» is dropped as a stop word and «~» is not treated as a subtitle separator, so the 2018 trailer matches.",
    "fix": "In rank_trailers, require edition words (collection, remaster(ed), complete, definitive, edition, dark arisen…) to appear in the video title whenever they appear in the game title, and add «~» to the separator regex. Prefer trailers published near the release date: parse publishedTimeText and drop results older than about 1 year when newer matches exist.",
    "lens": "requirements"
  },
  {
    "severity": "minor",
    "where": "app/youtube.py:241-254 (rank_candidates)",
    "what": "Demo and preview videos are picked as «the let's play» for games that are already out. Valor Mortis gets a demo playthrough, and its conclusion says «игра ещё в разработке» although the card shows release on 13 October 2026. Truckful and Boltgun 2 also get «FULL DEMO» videos, and Planet Zoo 2 gets «Played Early!».",
    "fix": "Pass the release date to find_letsplays. If it has passed, rank titles matching demo|beta|early|preview|демо|бета below the rest, without excluding them, so they are only used when nothing else exists.",
    "lens": "requirements"
  },
  {
    "severity": "minor",
    "where": "app/db.py:166 (search_key), app/db.py:473 (single LIKE on the raw casefolded title)",
    "what": "Title search fails on obvious queries. «dragons dogma», «warhammer 40000» and «kingdom hearts collection i-iii» all return 0 results, while «dragon's» and «40,000» work.",
    "fix": "Normalise both the stored title_search and the query to alphanumeric tokens (drop apostrophes and commas; turn - ~ : into spaces). Match every token with an AND of LIKE clauses. Add a migration that recomputes title_search for existing rows.",
    "lens": "requirements"
  },
  {
    "severity": "minor",
    "where": "app/selection.py:62",
    "what": "A run that finishes the New Releases block returns early instead of filling up to 20. Example: if run 1 stops at the 50-minute limit after 15 games, run 2 processes only the 5 remaining New Releases and wastes the hourly slot. The same happens when retries come first. test_selection.py:71 locks this in: the second call gets 15 games with batch=20.",
    "fix": "Return early only when the batch is full, or when New Releases items were taken and the block is still not done (meaning the batch is full). When `take(items, 'new-releases')` finished the block in this run, continue into the SEE ALL loop to fill the batch. Update test_limit_smaller_than_block_keeps_block_open to expect 15 New Releases followed by SEE ALL items.",
    "lens": "requirements"
  },
  {
    "severity": "minor",
    "where": "app/pipeline.py:432 vs README.md:27",
    "what": "The card note for a missing let's play says «летсплеев длиннее 10 минут», but the README says the search keeps «ролики длиннее 20 минут», and the search filter sp=CAMSAhgC really is longer than 20 minutes. The Dragon's Dogma 2 card shows the wrong number.",
    "fix": "Change the note to «длиннее 20 минут» and add the reason from README.md:226-229, e.g. «название ролика должно содержать подзаголовок игры».",
    "lens": "requirements"
  },
  {
    "severity": "blocker",
    "where": "README.md:1-10 (the repo as a whole)",
    "what": "The ТЗ asks for three deliverables: the repo, a working service, and the chat log with the AI. The README has no live URL, and the repo has no chat transcript and no link to one, so the submission is missing two of the three.",
    "fix": "Deploy (user action; Railway steps are already in README §Деплой), then put the public URL in the first lines of the README. Export the AI conversation to docs/ai-chat.md (or a shared link) and link it right under the URL.",
    "lens": "code"
  },
  {
    "severity": "major",
    "where": "git history: commit f2dc1f9, docs/samples/yt_search_star-wars-galactic-racer_*.html (3 files) and docs/samples/yt_watch_bGBXM6oxbag.html",
    "what": "HEAD is redacted, but commit f2dc1f9 still contains the user's home IP (in `remoteHost`) and the real `visitorData` tokens in 4 files. The first push would publish them.",
    "fix": "Before the first push, rewrite history. Either squash all 13 commits into one with `git reset --soft $(git rev-list --max-parents=0 HEAD)` plus an amend, or run `git filter-repo --replace-text` over docs/samples. Then check with `git grep [IP] $(git rev-list --all)`.",
    "lens": "code"
  },
  {
    "severity": "major",
    "where": "app/pipeline.py:369-379 and app/pipeline.py:418-424",
    "what": "A refresh saves whatever `find_letsplay` returns, even when it is worse than what is stored. Only exceptions keep the old record. Reproduced: a captions-based `llm-transcript` result is replaced on the day-8 refresh by `no_transcript`/`llm-description` when captions are blocked, and the excerpt is lost. Two days later an empty search replaces it with `no_video` and the video link is gone. `ensure_trailer` does the same: a `status: none` result overwrites a stored `ok` trailer, so the card loses its only video link.",
    "fix": "In `process_game`: if the old let's play has status ok with a method in FULL_LETSPLAY (or has a video) and the new one is not ok, store `{**old_lp, 'last_error': new.get('note'), 'checked_at': now}` instead. In `ensure_trailer`: if the old trailer is ok and the new result is `none`, keep it and only bump `checked_at`. Add tests in tests/test_pipeline.py next to `test_blocked_youtube_keeps_the_previous_video`.",
    "lens": "code"
  },
  {
    "severity": "major",
    "where": "app/pipeline.py:434-470, app/youtube.py:254, app/templates/game.html:128",
    "what": "The ТЗ asks for the most popular let's play, but the code skips the most-viewed video whenever it has no captions. It tries captions on the top 3 first and only runs Whisper after all three fail, so a less popular captioned video wins even though Whisper could transcribe the top one. Ranking also puts titles with PREFER keywords ahead of view count. The card still says 'Самый популярный подходящий ролик выбран из поиска YouTube по просмотрам'. Real data: echo-weaver got the #2 video (1,831 views, while #1 has 2,050) and valor-mortis got #3 (141k, while #1 has 294k). The 'Другие найденные ролики' list on the same card shows the more-viewed videos.",
    "fix": "Go through candidates in rank order, and for each one try captions, then Whisper (if available and within the deadline), before moving on. Skip to the next video only on NoSpeech or when both fail. In `rank_candidates`, sort by views only (`-c.views`). In game.html:128, when `lp.checked` or `stt_checked` is not empty, say which more popular videos were skipped and why, instead of the fixed 'самый популярный' sentence.",
    "lens": "code"
  },
  {
    "severity": "major",
    "where": "app/summarize.py:259-283 (line 280 description head), app/pipeline.py:483-491, app/templates/game.html:121-122",
    "what": "When there is no transcript, the LLM still gets LETSPLAY_SYSTEM and LETSPLAY_TASK, which ask how the blogger rates the game and for pros and cons. It invents the blogger's opinion from the video description, often links or marketing text. The card shows this under 'Понравилось блогеру / Не понравилось'. On a cloud deployment, where the README says captions and yt-dlp are blocked, this is the path for nearly every game, so what the employer sees for this extra feature is made up.",
    "fix": "For source == 'description', use a separate prompt that asks only what the video is about, with no opinions attributed to the blogger, and a validator that returns only `summary`. Do not render the pros/cons headings for `no_transcript` records. Label the block 'По описанию ролика, речь блогера недоступна'.",
    "lens": "code"
  },
  {
    "severity": "major",
    "where": "app/youtube.py:194-219",
    "what": "`parse_search_html` raises SchemaError only when `ytInitialData` is missing. If YouTube changes the result markup (for example to `lockupViewModel`), it returns [] with no error. Tested: a page with `ytInitialData` and no `videoRenderer` returns []. Every game then becomes `no_video` or trailer `none` with no schema error on the monitor. Together with the overwrite issue above, this wipes stored let's plays and trailers within about a week.",
    "fix": "After the loop, if no `videoRenderer` was found and the page has no 'No results' marker (`backgroundPromoRenderer`), raise SchemaError('на странице поиска YouTube нет videoRenderer (сменился формат)'). The pipeline already turns that into a `youtube:schema` error and keeps old data. Add a test with a `lockupViewModel` sample.",
    "lens": "code"
  },
  {
    "severity": "minor",
    "where": "app/main.py:527-552 and app/serve.py:23-32",
    "what": "The `/api/events` generator never ends, and uvicorn starts without `--timeout-graceful-shutdown`. Reproduced: with the monitor page open, SIGTERM leaves the process at 'Waiting for connections to close' indefinitely, and lifespan shutdown never runs. Locally Ctrl+C needs a second press. Docker or Railway stop waits for SIGKILL.",
    "fix": "Add `\"--timeout-graceful-shutdown\", \"5\"` to `uvicorn_args`, and the same flag to the README's alternative `uvicorn app.asgi:app` command. Optionally end the stream after a maximum lifetime, such as 10 minutes; EventSource reconnects on its own.",
    "lens": "code"
  },
  {
    "severity": "minor",
    "where": "app/main.py:480-492 vs app/main.py:494-503",
    "what": "RUN_TOKEN protects nothing. `/run` returns the same JSON when `Accept: application/json` is sent, never checks the token, and `same_origin` lets through requests with no Origin header. So `curl -X POST -H 'Accept: application/json' host/run` starts a run even with RUN_TOKEN set, and the token on `/api/run` does nothing.",
    "fix": "Either remove RUN_TOKEN (the cooldown and daily cap are the real protection) and say so in the README, or in `/run` require `token_ok` whenever the request lacks `Sec-Fetch-Site: same-origin`, so only non-browser clients need the token.",
    "lens": "code"
  },
  {
    "severity": "minor",
    "where": "app/stt.py:304-318, app/main.py:130-136, app/config.py:113",
    "what": "Whisper is on by default and loads in the web process (0.6–1.25 GB peak per the README). On a small instance the kernel's out-of-memory killer ends the whole service: web UI, monitor and the current run. `except Exception` cannot catch that, so 'fails soft' does not cover this case. The MemoryError test gives false confidence.",
    "fix": "In `build_services`, read the cgroup limit (`/sys/fs/cgroup/memory.max`). If it is under about 1.5 GB (1 GB for `tiny`), disable STT and show the reason on the 'stt' worker. Or run `transcribe_video` in a `multiprocessing` child process with a timeout, so an OOM kill only takes down the child.",
    "lens": "code"
  },
  {
    "severity": "minor",
    "where": "tests/test_selection.py:60-65",
    "what": "In `test_new_day_starts_from_new_releases_again`, the line `assert yesterday.browse_offset == 96` checks a DayState the test built itself and never passed to code under test, so it checks nothing. Nothing here proves that yesterday's saved cursor is ignored.",
    "fix": "Save the 2026-10-13 state with `db.save_day_state(...)`, then select with `db.get_day_state('2026-10-14')`. Assert the new state has `new_releases_done False`, `browse_offset 0`, and that the first item comes from 'new-releases'.",
    "lens": "code"
  },
  {
    "severity": "minor",
    "where": "app/youtube.py:76-81, app/stt.py:493 and 592, app/stt.py:515-517",
    "what": "Dead code: `title_overlap` is used only by tests (the ranking uses `title_matches`). `SpeechToText.last` is written and never read. `SpeechToText.available` is used only in tests.",
    "fix": "Delete `title_overlap` and its test assertions, the `self.last` attribute and assignment, and the `available` property (tests can call `unavailable_reason() is None`).",
    "lens": "code"
  },
  {
    "severity": "blocker",
    "where": "README.md:1-11 (intro); nothing anywhere in README or docs/",
    "what": "The brief's 'Результат' asks for three things: the repository, a working service and the chat log with the AI. The README has no section with the live service URL and no chat log or link to one (grep for 'переписк', 'нейросет' or 'railway.app' finds nothing). A reviewer opening the repo cannot find 2 of the 3 required results.",
    "fix": "Add a '## Результат' section right after line 11 with: the GitHub link; 'Работающий сервис: https://<name>.up.railway.app (мониторинг: /monitor)'; 'Переписка с нейросетью: docs/ai-chat/' plus one paragraph on how the AI was used (research, code, review, smoke tests). The user has to deploy with the existing railway.json, export the session transcripts to docs/ai-chat/*.md with tokens and IPs scrubbed, and fill in both links before submitting.",
    "lens": "ux"
  },
  {
    "severity": "major",
    "where": "app/summarize.py:259-267 (LETSPLAY_SYSTEM/LETSPLAY_TASK), 280 (prompt for the description fallback), 286-294 (validate_letsplay_json); app/summarize.py:103-108 (REVIEW_SYSTEM/REVIEW_TASK); app/youtube.py:284 (NOISE); app/pipeline.py:433-447; app/templates/game.html:121-122",
    "what": "The prompt always asks how the blogger rates the game, and the validator rejects empty pros and cons, so the LLM invents opinions. Kingdom Hearts Collection: the video (Ben-Gun 'Gameplay Walkthrough Part 1 - FULL GAME') has no commentary. Its caption text is game dialogue plus 'Heat. Heat. Heat.' (YouTube's speech recognition mishearing music), and the card shows that excerpt. Yet the card says 'Автор ролика отмечает… Он ценит улучшенную производительность' and lists 'Понравилось блогеру'. Valor Mortis lists 'Позитивный настрой и энтузиазм автора' as a pro, and 'Не указаны конкретные проблемы' and 'Отсутствие подробного обзора' as cons; those describe the video, not the game. The description fallback has the same problem: 'автор ролика отмечает' written from a video description. Critic summaries have a milder form: Dragon's Dogma 2's 'Нравится' list includes 'Положительная оценка от критиков и фанатов'. A reviewer hiring an AI Automation Engineer will read this as uncontrolled hallucination.",
    "fix": "Add a field '\"has_opinion\": true|false' to the let's play JSON. Tell the model: 'пункты только о самой игре; не пиши про автора, его настрой или полноту обзора; если в тексте нет мнения автора об игре (диалоги игры, музыка, геймплей без комментариев) — has_opinion=false и пустые списки'. Let the validator accept empty lists when has_opinion is false. In find_letsplay, treat has_opinion=false like 'no captions': record the reason 'в ролике нет комментариев блогера' and try the next candidate. Extend NOISE to drop runs of 3 or more 'Heat.' tokens. For the description source, rewrite the prompt to 'опиши, что показано в ролике, не приписывай автору мнений', and in game.html change the headings to 'По описанию ролика: плюсы/минусы' when lp.method is llm-description or description. In REVIEW_TASK add 'только свойства игры (геймплей, сюжет, графика, техника, контент, цена), не кто и как её оценил'. Add tests that use the Kingdom Hearts excerpt as a fixture.",
    "lens": "ux"
  },
  {
    "severity": "major",
    "where": "app/similarity.py:73 (MIN_DESC) and 123 (the condition that decides whether a match counts); app/templates/game.html:165-167",
    "what": "A match only counts with the same franchise, the same developer, the exact same genre string, or a description cosine of at least 0.06. In the database 8 of 10 cards show 'Похожих игр в базе пока нет'; only Valor Mortis and Dragon's Dogma 2 link to each other. On a fresh deployment with about 20 varied new releases, the 'похожие игры' requirement will look broken on most cards.",
    "fix": "Add a map of genre families, for example Action RPG / Action Adventure / Metroidvania / FPS → экшен; Tycoon / Turn-Based Strategy / RTS → стратегии; Auto Racing / Driving → гонки; Point-and-Click / Adventure → приключения. Score a family match at half the genre weight with the reason 'близкий жанр'. When the strict list is still empty and the database has at least 3 games, show the top 3 by total score under the subheading 'Ближе всего из базы (слабое сходство)', still excluding matches that share only platforms. Cover both with tests in tests/test_similarity.py.",
    "lens": "ux"
  },
  {
    "severity": "major",
    "where": "app/main.py:424-427 (game_view raises HTTPException for the HTML route), 451-453 (index Query validation); there is no error template",
    "what": "An unknown game, /game/nope, returns raw JSON {\"detail\":\"Игра не найдена\"}. /?page=0 or /?page=abc returns an English validation dump ('Input should be greater than or equal to 1'). An unhandled error returns plain 'Internal Server Error'. Nothing gives a way back to the site, which looks unfinished in an otherwise polished Russian HTML app.",
    "fix": "Add app/templates/error.html (extends base: heading, Russian explanation, '← Все игры' button). Register handlers for StarletteHTTPException, RequestValidationError and Exception: when the path does not start with /api, /docs or /openapi.json, render error.html with the right status ('Игра не найдена', 'Некорректный адрес страницы', 'Что-то пошло не так, попробуйте позже'); otherwise keep JSON. In index(), accept bad page values and clamp to 1..pages instead of returning 422. Add web tests for 404, 422 and 500 HTML.",
    "lens": "ux"
  },
  {
    "severity": "major",
    "where": "app/templates/monitor.html:65-68; app/static/app.js:115-119; app/templates/monitor.html:40; app/templates/game.html:170",
    "what": "The order of sources is the core rule of the brief: New Releases first, then SEE ALL pages, and a new day starts over. The monitor never shows which source a run took, although runs.sources ('new-releases', 'browse:p2', 'retry') and runs.message are stored. The runs table shows only id, start, trigger, status and ok/selected, with no duration. The cursor tile says 'SEE ALL с позиции 24', which is an internal offset. The card footer shows the raw code 'источник: new-releases'.",
    "fix": "Add a Jinja filter and a matching JS map: new-releases → 'New Releases', browse:pN → 'SEE ALL, стр. N', retry → 'повтор упавших'. Add columns 'Источник' and 'Длительность' (finished_at - started_at) to the runs table in both monitor.html and app.js, with runs.message as a title tooltip or second line. Change the tile text to 'New Releases пройден, дальше SEE ALL со страницы {{ offset // page_size + 1 }}' and use the same filter in the game.html footer.",
    "lens": "ux"
  },
  {
    "severity": "major",
    "where": "README.md:13-28 (requirements table), 125-178 (Whisper section), whole file (418 lines)",
    "what": "The README is thorough but hard to skim. Requirement table cells run past 100 words (the 'Доп. 1' row is one long paragraph). There are no screenshots, so with no live URL a reviewer has to install about 430 MB of dependencies and wait several minutes before seeing any UI. Design decisions are scattered instead of collected. 55 lines of Whisper benchmarks sit in the main path.",
    "fix": "Put a short summary at the top: one sentence on what it is, the links from the Результат section, and 3 screenshots in docs/screenshots/ (list with platform filter, Valor Mortis card, monitor during a run). Reduce 'Возможности' to a checklist: requirement → where to see it in the UI → module. Add '## Ключевые решения' with 5-7 bullets: JSON backend instead of HTML scraping, SQLite with migrations and a run lease, anonymous Pollinations with quote-based fallbacks, captions first and then local Whisper base, server-side rendering plus SSE. Move the STT benchmarks and force-run protection details to docs/STT.md and docs/SECURITY.md and link them.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/pipeline.py:432; app/youtube.py:15 (SP_POPULAR_LONG) and 242",
    "what": "For Dragon's Dogma 2: Dark Arisen the card says 'На YouTube не нашлось подходящих летсплеев длиннее 10 минут'. The search already filters to videos over 20 minutes (README line 27), and the real cause is the strict title check: videos without 'Dark Arisen' in the title are rejected. For a big AAA game the message reads as false.",
    "fix": "If the merged search results are non-empty but rank_candidates drops them all, write 'Летсплеи по игре есть, но ни в одном названии нет полного названия «…» — строгая проверка, чтобы не взять ролик другой части серии. Проверим снова через сутки'. Otherwise say 'длиннее 20 минут' to match the actual filter.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/static/app.js:63-69 (renderButton uses manual.reason); app/main.py:270-272; app/main.py:166-169 and app/static/app.js:33-37 (wait format)",
    "what": "During the cooldown the button counts down every second ('Запуск через 4:31' → '3:50'), but the hint below keeps the server text with a frozen time, 'Повторный запуск возможен через 4:31…'. I confirmed in the browser that after 4 s the button had changed and the hint had not. Both formatters print only minutes:seconds, so long waits show as '1364:03'.",
    "fix": "Remove the time from the server reason ('Между запусками нужна пауза N мин, чтобы не перегружать Metacritic, YouTube и LLM') and build the hint on the client as 'Можно запустить через ' + wait(left). Make fmt_wait and wait() print 'Ч:ММ:СС' once the wait is 3600 s or more.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/main.py:405; app/pipeline.py:199-200, 220-222, 263, 273, 291; app/scheduler.py:77, 95; app/db.py:544-546 (recent_runs → /api/runs, /api/status)",
    "what": "The live log on the public Russian monitor shows English lines and the absolute DB path ('Service started, DB /private/tmp/…/games.db (schema v4)'). /api/runs and /api/status return the 'owner' field with the machine hostname ('[host]:76659:…'). The worker detail prints the raw trigger code ('запуск #3 (manual): выбор игр'), and line 220 lower-cases a message that can contain exception text and proper names.",
    "fix": "Log only the database file name and schema version. Translate the roughly 10 run-level messages a viewer sees (run started or finished, selection, time limit, retry, scheduler next run), or rename the section to 'Технический журнал'. Drop 'owner' from API output: select explicit columns in recent_runs. Use TRIGGER_LABELS in the worker detail and remove .lower().",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/db.py:167 (search_key) and 472-474",
    "what": "Search is a substring match after casefold only, so 'dragons dogma' does not find \"Dragon's Dogma 2\" and 'warhammer 40000' does not find 'Warhammer 40,000: Boltgun 2'. Both are natural queries, and a reviewer gets 'Ничего не найдено'.",
    "fix": "In search_key, remove apostrophes, commas and periods without inserting spaces (re.sub(r\"['’.,]\", '', s)), replace other non-word characters with a space, then collapse whitespace. Apply it to both stored titles and queries, add a migration that recomputes games.title_search, and add tests for both queries.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/youtube.py:38 (STOP contains 'collection', 'remastered', 'complete'…), 257-268 (rank_trailers)",
    "what": "The YouTube trailer fallback picked 'Kingdom Hearts III - Official Extended Trailer | E3 2018' for Kingdom Hearts Collection I ~ III. That is a different product from 8 years earlier, yet it is captioned as the most suitable trailer.",
    "fix": "For trailers, keep edition words (collection, remaster, complete, definitive, edition) as required when they appear in the game title. Prefer results whose publishedTimeText is within about 1 year of the release date. If only an older or base-game trailer matches, label it 'трейлер оригинальной игры'.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/youtube.py:395; app/pipeline.py:476-483; app/templates/game.html:106, 127; app/templates/monitor.html:83",
    "what": "Card notes and the errors table show technical text: video ids and Python exception class names ('Расшифровка недоступна (субтитры: <id>: TranscriptsDisabled; Whisper: …)'), and step codes like 'youtube:schema'. On a cloud host, where YouTube returns RequestBlocked, every card would show this text.",
    "fix": "Map exception classes to Russian phrases (TranscriptsDisabled → 'автор отключил субтитры', RequestBlocked/IpBlocked → 'YouTube не отдаёт субтитры этому серверу', NoTranscriptFound → 'субтитров нет'). Show only the phrase plus a count of videos checked, and keep ids in JSON. In the errors table, map step codes to labels ('YouTube: формат ответа изменился').",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/templates/game.html:19, 43-45; app/static/style.css:226-237",
    "what": "On a 375 px screen the 4-column platforms table scrolls sideways inside its card (scrollWidth 408 vs 305), hiding Userscore counts and the release column. Rows repeat the name as a chip ('PC PC'). The hero shows the Metascore review count ('PC, 48 рецензий') but no Userscore count. The PS5 row shows '— · 1 оценка' with no explanation why there is no score.",
    "fix": "At max-width 640px hide the 4th column (th:nth-child(4), td:nth-child(4) {display:none}). Render the short-name chip only when p.short != p.name. Add '{{ lead user_count }} оценок' under Userscore in the hero. Show 'мало оценок для Userscore' when user_count > 0 and userscore is none.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/templates/index.html:30, 65-75",
    "what": "A page past the end (/?page=2 with 10 games) shows 'Найдено: 10' together with 'Ничего не найдено'. 'Найдено: N' has no noun and no plural form.",
    "fix": "Clamp page to the number of pages in index(), or render 'На странице N пусто, всего страниц: M' with a link to page 1. Change the counter to 'Найдено: {{ total }} {{ total|plural(\"игра\",\"игры\",\"игр\") }}'.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/templates/index.html:69; app/templates/monitor.html:8, 40; app/monitor.py:26",
    "what": "Empty and disabled states are misleading. With SCHEDULER_ENABLED=0 the empty index still promises an automatic first run, and the monitor still says 'запускается каждые 60 мин'. With no runs yet the cursor tile says 'сегодня ещё идёт блок New Releases', which suggests something is running. After a restart the Metacritic, YouTube and LLM worker cards show only 'ожидает —'.",
    "fix": "Branch on st.config.scheduler_enabled ('Автозапуск выключен, запустите обработку кнопкой'). Use 'сегодня первым будет обработан блок New Releases' while new_releases_done is false and no run exists today. Initialise worker detail to 'с момента старта сервиса запросов не было' and optionally add the last run's finish time.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/static/style.css:108, 161 (similar chips), 199 (.log), 209 (.run-box); app/templates/base.html:13; app/static/app.js:145-151",
    "what": "Small visual issues. The reason chips under similar games stretch to the score badge height and look like tall pills. Long log lines do not wrap and scroll sideways on phones. At about 1100 px the force-run button wraps under the description on the left. On phones the brand reads 'MC Игры' right next to the nav item 'Игры'. The 'Обработка запущена.' banner stays after the run ends.",
    "fix": "Add .similar .chips{align-items:center}, .log div{white-space:pre-wrap;overflow-wrap:anywhere}, .page-head>div:first-child{flex:1 1 420px} and .run-box{margin-left:auto}. Shorten the mobile brand to 'MC Дайджест'. Hide #flash in render() when running changes from true to false.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "app/config.py:60-122 (reads os.environ only); README.md:248-251",
    "what": "The repo ships .env.example, but nothing loads a .env file and the README never mentions it. A reviewer who copies it to .env gets no effect.",
    "fix": "Under 'Переменные окружения', state that values come only from the process environment and give the one-liner 'set -a; . ./.env; set +a' for local runs. Alternatively, have app/serve.py read ./.env into os.environ.setdefault before exec, without adding a new dependency.",
    "lens": "ux"
  },
  {
    "severity": "minor",
    "where": "README.md:34 and 46 (architecture diagram)",
    "what": "The ASCII diagram is off by one column. The right border on line 34 is at column 67 while the rest of the box is at 66. The top border of the Metacritic box on line 46 ends at column 42 while its sides are at 43. GitHub renders this as broken box lines.",
    "fix": "Remove one '─'/space on line 34 before the right '│' and add one '─' to the top border on line 46. Check that every box edge sits in the same column.",
    "lens": "ux"
  },
  {
    "severity": "blocker",
    "where": "README.md:1-11 (no live link anywhere in the README; nothing in docs/ either)",
    "what": "The ТЗ asks for three things: a repo, a working service and the chat log with the AI. The repo has neither a deployed service URL nor the chat log. A reviewer opening the README has nothing to click and no AI transcript to read. This alone can get the submission rejected, however good the code is.",
    "fix": "The user has to deploy it; agents were not allowed to. Deploy to Railway: Dockerfile builder, a Volume mounted at /data, no variables required. On the Trial plan also set STT_MODEL=tiny or STT_ENABLED=0 (see the memory item below). Wait for the first scheduled run, about 15 s after start plus 30–40 min for a full batch. On /monitor, check whether the YouTube worker shows RequestBlocked. If only captions are blocked, set YOUTUBE_CAPTIONS=0. Then add a line such as «Демо: https://<name>.up.railway.app (мониторинг: /monitor)» directly under the H1 in README.md. Export the AI transcript to e.g. docs/ai-chat.md and link it from the README. Before committing it, scrub it with grep -nE '[IP]|@gmail\\.com|@mail\\.ru|github-token|railway-token|/Users/'. The sessions contain the home IP, both email addresses and token file paths.",
    "lens": "security-deploy"
  },
  {
    "severity": "major",
    "where": "git history: commits f2dc1f9 and 3b3f3e6, docs/samples/yt_search_star-wars-galactic-racer_lets-play_byviews.html, ..._lets-play_over20min.html, ..._prohozhdenie_over20min.html, yt_watch_bGBXM6oxbag.html (line 18)",
    "what": "Checked with git grep over every rev: the user's home IP [IP] is in 4 files in both f2dc1f9 and 3b3f3e6, so not only f2dc1f9 as the handoff said. remoteHost and about 277 ip= stream-URL parameters carry it, next to the unredacted VISITOR_DATA/visitorData. 270096c only redacts it going forward, so the first push would publish the IP. Commit authorship is also mixed: f2dc1f9 is [email] and the other 11 commits are [email].",
    "fix": "Nothing is pushed yet, so rewrite before the first push. First commit the extra redaction from the minor item below, call that commit R. Then run: git filter-branch --prune-empty --tree-filter 'for f in docs/samples/yt_search_star-wars-galactic-racer_lets-play_byviews.html docs/samples/yt_search_star-wars-galactic-racer_lets-play_over20min.html docs/samples/yt_search_star-wars-galactic-racer_prohozhdenie_over20min.html docs/samples/yt_watch_bGBXM6oxbag.html; do [ -f \"$f\" ] && git show R:\"$f\" > \"$f\"; done' -- main. Optionally add --env-filter 'export GIT_AUTHOR_EMAIL=<chosen> GIT_COMMITTER_EMAIL=<chosen>' to unify the identity. Then delete the backup refs and prune: git for-each-ref --format='%(refname)' refs/original | xargs -n1 git update-ref -d; git reflog expire --expire=now --all; git gc --prune=now. Check that for c in $(git rev-list --all); do git grep -l [IP] $c; done prints nothing, and rerun pytest. Commit SHAs will change, so update any SHAs quoted elsewhere.",
    "lens": "security-deploy"
  },
  {
    "severity": "major",
    "where": "app/config.py:113-114 (STT on, model base by default); app/stt.py:303-318 (model loaded in the web process); railway.json:10-11",
    "what": "The README measures Whisper's container peak at about 1.25 GB for base and about 1.0 GB for tiny (README.md:152-171), and asks for at least 1.5 GB RAM. Railway Trial caps a service at 1 GB and Free at 0.5 GB (docs.railway.com/reference/pricing/plans). The user's Railway account is a trial. Whisper runs inside the uvicorn process, so an OOM kill takes the whole web UI down mid-run. The run is closed as interrupted and the service restarts. With restartPolicyType ON_FAILURE and 5 retries, repeated OOMs on later runs can leave the deployment stopped (the exact retry-counting semantics are unverified). The next run with no captions triggers it again: Railway IPs usually get RequestBlocked, which pushes every let's play to Whisper whenever yt-dlp still gets through. This is plausible from the published limits and the measured peaks, but not reproduced on Railway itself. The README's Railway section (README.md:231-245) doesn't mention memory at all.",
    "fix": "In Settings, read the cgroup limit (/sys/fs/cgroup/memory.max, falling back to /sys/fs/cgroup/memory/memory.limit_in_bytes). If it is below 1.5 GB and STT_MODEL is not set explicitly, use tiny. Below 1.1 GB, set stt_enabled=False and show the reason on the stt worker. Better still, run the transcribe step in a multiprocessing 'spawn' child, so an OOM kills only the child and becomes a SpeechUnavailable. Add a step 4 to the Railway deploy section: «Trial/Free (≤1 ГБ RAM): STT_MODEL=tiny или STT_ENABLED=0». Change restartPolicyType to ALWAYS, or raise restartPolicyMaxRetries, so one OOM streak cannot leave the demo down.",
    "lens": "security-deploy"
  },
  {
    "severity": "minor",
    "where": "app/serve.py:10-13",
    "what": "drop_privileges runs as root and calls os.chown(name, uid, gid) on every entry os.walk lists under /data. os.chown follows symlinks by default, and uid 10001 can write to /data. If the app process were ever compromised, for example through a media-parsing bug in av or yt-dlp on downloaded audio, it could plant /data/x -> /etc/passwd or a file under site-packages. On the next container start, root would chown that target to the app user, which is a classic CWE-59 escalation inside the container.",
    "fix": "Skip links and don't follow them: `if os.path.islink(name): continue` then `os.chown(name, pw.pw_uid, pw.pw_gid, follow_symlinks=False)`, and pass followlinks=False to os.walk explicitly. Add a test in tests/test_serve.py: create a symlink in a temp data dir pointing outside it, and assert the target's owner is unchanged (or mock os.chown and assert follow_symlinks=False).",
    "lens": "security-deploy"
  },
  {
    "severity": "minor",
    "where": "docs/samples/yt_search_star-wars-galactic-racer_lets-play_byviews.html:18, ..._lets-play_over20min.html:18, ..._prohozhdenie_over20min.html:18, yt_watch_bGBXM6oxbag.html:18",
    "what": "The redaction is incomplete, which contradicts the claim in README.md:416-418. Each file still has a per-visitor DATASYNC_ID / datasyncId (V[id]||, V[id]||, V[id]||, V[id]||, 13 times each) and a rolloutToken. Both are signed-out visitor identifiers, the same kind of data visitorData is.",
    "fix": "Run sed -E -i '' 's/(\"(DATASYNC_ID|datasyncId)\":\")V[0-9a-f]+\\|\\|/\\1REDACTED||/g; s/(\"rolloutToken\":\")[^\"]+/\\1REDACTED/g' on the four files, run pytest, and commit before doing the history rewrite above, so the rewrite also cleans the old commits. Extend the README sentence to mention datasyncId and rolloutToken.",
    "lens": "security-deploy"
  },
  {
    "severity": "minor",
    "where": "Dockerfile:1",
    "what": "`FROM python:3.12-slim` is a floating tag, while the brief says to keep dependencies pinned and requirements.txt pins the full tree. Each Railway rebuild can pull a different Python patch and Debian base, so the image the employer runs isn't the one that was tested.",
    "fix": "Pin a patch version and digest, e.g. `FROM python:3.12.<x>-slim-bookworm@sha256:<digest>`. Get the digest with `docker buildx imagetools inspect python:3.12.<x>-slim-bookworm`. Mention it in the README «Зависимости» section with the update command.",
    "lens": "security-deploy"
  }
]

The repairer says:
All 48 findings are handled except two that need you: deploying the service and pushing the repository. The rules forbid both for agents, so the README says the links will be added after those steps. 225 tests pass, and nothing is listening on 87xx or 8000.

History was rewritten locally with `git filter-branch`, so every commit hash changed. HEAD is now `0f4ca85`, and nothing has been pushed.

**Commits (new hashes):**
- A = `71740c1`: redact the remaining visitor ids in the YouTube samples
- B = `7fa6fe2`: let's plays (pick by views, require the blogger's own commentary, no invented opinions), trailers, YouTube schema check, Russian logs
- C = `37b4cab`: selection, search, similar games, data migration v5
- D = `1762d29`: web pages, monitor, card, `RUN_TOKEN`, SSE lifetime, Whisper memory check
- E = `7cda312`: graceful shutdown, symlink-safe chown, `.env` loading, pinned base image, restart policy
- F = `db8957c`: card polish (exact skip reasons, phone-width platforms table)
- G = `0f4ca85`: README, live-run table, screenshots, `docs/ai-log`, `docs/STT.md`, `docs/SECURITY.md`

**Live run.** Fresh database, default settings, Whisper `base`. The first run was started by the scheduler. Runs 2–5 were started through the button endpoint after each 5-minute pause, by a polling driver rather than a person clicking.

| Run | Source | Games | Time | Errors |
|---|---|---|---|---|
| 1 | New Releases | 20 | 28 min 04 s | 0 |
| 2 | SEE ALL p1 | 20 | 9 min 03 s | 0 |
| 3 | SEE ALL p1–p2 | 20 | 10 min 55 s | 0 |
| 4 | SEE ALL p2–p3 | 20 | 3 min 03 s | 0 |
| 5 | SEE ALL p3–p4 | 20 | 4 min 38 s | 0 |

- After run 5 the day state shows New Releases done and the SEE ALL cursor at 72.
- Page 1 was taken twice because every game on it came out that day, and Metacritic reorders games with the same date between requests. No game was processed twice.
- Runs 2–5 picked up games released that same day. Most have no reviews yet, so only 22 of the 100 have critic reviews and 13 have user reviews. All of those got LLM summaries, with no LLM failures.
- Let's plays: 52 conclusions from captions, 8 description retellings, 40 games with no suitable video.
- 17 videos were skipped for having no commentary from the blogger, including the Kingdom Hearts walkthrough the reviewers flagged. Whisper ran 9 times and each time found gameplay with no voice.
- 94 of 100 games have strong similar-game matches.
- The 100-game database is now the local demo at `data/games.db` (git-ignored).

**Per problem**

*Requirements lens*
1. Deploy and live URL: not done, deploying is forbidden for agents. The Railway steps, a memory step and a YouTube-blocking step are in the README. (D, E, G)
2. AI chat log: fixed. `docs/ai-log/` holds each agent's verbatim prompt and final answer, an action list without tool output, and the two orchestration scripts. Private notes, unrelated messages, paths, the IP and visitor ids are removed. (G)
3. Repository and IP in history: history rewritten and verified (0 hits for the IP or visitor ids in any commit, old objects pruned). Pushing is forbidden for agents.
4. Invented let's play conclusions: fixed. The prompt now asks for `has_commentary` plus verbatim quotes, quotes are checked against the transcript, a text pre-filter drops dialogue- or music-only transcripts, caption noise is stripped, and pros/cons about the author or the video are dropped. I tested it on four real transcripts before the live run. (B)
5. SEE ALL never run live: fixed, see the table above. (G)
6. Similar games mostly empty: fixed with related-genre families and a separate "слабое сходство" list, plus the 100-game database. (C)
7. Description-based conclusion shown as the blogger's opinion: fixed. A separate retelling prompt is used, the card says «речь блогера не расшифрована», and a migration resets old records. (B, C, D)
8. English bullets passing the Russian check: fixed, the check now runs per item, and a migration resets bad stored summaries for recompute. (B, C)
9. Wrong trailer for re-releases: fixed. Edition words are required, `~` is a subtitle separator, recent trailers are preferred, and a base-game fallback is labelled. (B)
10. Demo videos chosen for released games: fixed, demo and early-access videos rank last once the game is out. (B)
11. Search misses: fixed, punctuation is ignored, every word must match, and a migration recomputes stored keys. (C)
12. Early return after New Releases: fixed, the batch fills from SEE ALL, and the test was updated. (C)
13. "10 минут" note: fixed, it says 20 minutes or explains the strict title check. (B)

*Code lens*

14. Duplicate of 1 and 2.
15. Duplicate of 3; commit 3b3f3e6 is covered too.
16. Refresh overwriting good data: fixed, a worse result keeps the stored let's play or trailer, and old unverified conclusions count as replaceable. (B, C)
17. Most popular video skipped: fixed, ranking is by views only and each video goes captions → Whisper → next. The card lists the skipped videos and why. (B, D, F)
18. Same as 7.
19. Missing `videoRenderer` read as no results: fixed, it is now a schema error, and real "no results" pages are recognised from a live sample. (B)
20. SSE blocking shutdown: fixed with `--timeout-graceful-shutdown 5` and a 10-minute stream limit. (D, E)
21. `RUN_TOKEN` bypass: fixed, `/run` needs the token unless the request carries `Sec-Fetch-Site: same-origin`. (D)
22. Whisper out-of-memory: fixed, the service reads the cgroup memory limit, switches to `tiny` under 1.5 GB and turns Whisper off under 1.1 GB. (D)
23. Test that checked nothing: fixed, it now goes through the database. (C)
24. Dead code: removed. (B)

*UX lens*

25. Duplicate of 1 and 2 (README «Результат» section added).
26. Fixed, including the review prompt («только свойства игры»).
27. Same as 6.
28. Raw JSON error pages: fixed, HTML pages for 404/422/500 and clamped paging. (D)
29. Monitor missing sources and durations: fixed, plus a readable day cursor and labelled sources in the card footer. (D)
30. README hard to skim: restructured, with 3 screenshots, `docs/STT.md` and `docs/SECURITY.md`. (G)
31. Same as 13.
32. Frozen cooldown hint and hour formatting: fixed. (D)
33. English logs, absolute DB path, `owner` field, raw trigger code: fixed. (B, D)
34. Same as 11.
35. Same as 9.
36. Technical text in notes and the errors table: fixed, mapped to plain Russian. (B, D)
37. Platforms table on phones: fixed and checked at 390 px; hero Userscore count and «мало для Userscore» added. (D, F)
38. Empty page past the end and missing plural: fixed. (D)
39. Misleading empty and disabled states: fixed. (D)
40. CSS issues: fixed. (D)
41. `.env` ignored: fixed, `app/serve.py` loads it, and the README says so. (E, G)
42. Diagram misaligned: fixed. (G)

*Security lens*

43. Duplicate of 1 and 2; the exported logs were scrubbed and grep-checked.
44. Same as 3. Commit emails are still mixed between your two addresses; I left that to you.
45. Whisper memory on Railway: fixed, plus restart policy ALWAYS and a README step. (D, E)
46. chown following symlinks: fixed and tested. (E)
47. `datasyncId`, `rolloutToken`, `appInstallData` and `XSRF_TOKEN` left in samples: redacted. (A)
48. Floating base image: pinned to `python:3.12.15-slim-trixie@sha256:a6e34c59…`, digest verified against the registry. (E)

No false alarms: every finding was real.

**What you need to do before submitting:**
- Deploy to Railway and push to GitHub, then put both links into README.md lines 9–11.
- Read `docs/ai-log/` once before publishing it.
- The exporter is at `[scratchpad]/ailog/export_ai_log.py`. Usage: `<subagents/workflows dir> <out dir> <scripts dir>`. Rerun it to add this stage's real final answer; the log currently uses a summary I wrote from the commits.

Files are in `~/dev/skytec-metacritic`:
- `README.md`
- `docs/ai-log/README.md`
- `docs/STT.md`
- `docs/SECURITY.md`
- `docs/screenshots/`

TASK: independently check each problem against the current HEAD (read the code, run the tests, request the pages on your own port with a copy of the database). Return only the problems that are still real, plus any new problem the repair introduced.
```

## Итоговый ответ агента

I checked HEAD 0f4ca85 in ~/dev/skytec-metacritic. All 225 tests pass. Neither the IP [IP] nor the visitor ids appear in any commit, and old objects are pruned. I served a copy of data/games.db on port 8791 with scheduler, LLM and YouTube off, so nothing was scraped or sent to the LLM. These are fixed: search («dragons dogma», «warhammer 40000», «kingdom hearts collection i-iii» each find their game), filling the batch after New Releases, the day_state test, live SEE ALL runs (day state: New Releases done, cursor at 72), similar games, HTML pages for 404/422 and clamped paging, the monitor's sources and durations, phone layout at 375 px, the description retelling labelled «речь блогера не расшифрована», the per-item Russian check, the Kingdom Hearts trailer, keeping stored data when a refresh is worse, the videoRenderer schema error, the cgroup memory check for Whisper, symlink-safe chown, the pinned base image, the dead code, and the AI log (no private paths, emails or IP). With the monitor's event stream open, SIGTERM stopped the server in about 7 s. Nothing is listening on 87xx or 8000 now.

Still open: deploy and push, which only the user can do. The repair also exposed a new major problem in the 100-game live database. Many of the obscure same-day games in SEE ALL get a let's play or trailer for a different game, because title matching only checks that the title's words appear somewhere in the video title, and does it on YouTube's machine-translated titles. Rock Hunter shows a Monster Hunter 4 let's play, and the LLM writes «Игра «Rock Hunter» предлагает интересный и требовательный геймплей» even though the quoted evidence reads «welcome back to Monster Hunter». At least 17 of the 52 «conclusions from the blogger's speech» are about another game, and about 11 of the 63 YouTube trailers are wrong, including a wedding video and a Jet Li film trailer. These cards are on the first page of the default list. There are also some smaller wording, documentation and RUN_TOKEN issues.

Найдено проблем: 8

1. **[blocker]** `README.md:9-13 (placeholders «будет добавлена после публикации / после деплоя»); `git remote -v` is empty`
   - Что: Two of the three required ТЗ deliverables are still missing. There is no public URL for «работающий сервис» and no GitHub link for «репозиторий». The README only promises them. History is clean now: 0 hits for the IP and visitor ids in any commit. Agents are not allowed to deploy or push, so this is a user action.
   - Как исправить: User action. Create a public GitHub repo, push main, and deploy on Railway from the Dockerfile with a Volume at /data. Then replace README.md:11-13 with the real links («Репозиторий: https://github.com/…», «Работающий сервис: https://….up.railway.app, мониторинг: /monitor»). Do not upload the current data/games.db to the volume until the wrong-video problems below are fixed and the affected records are recomputed: today it contains the wrong-game conclusions. On Railway Trial (1 GB) Whisper turns itself off, and YouTube usually blocks captions from cloud IPs, so expect description retellings unless YOUTUBE_PROXY_URL is set.
2. **[major]** `app/youtube.py:116-132 (title_matches: unordered bag of words, roman numerals ignored), app/youtube.py:288-306 (rank_candidates: no check of publish date against release date), app/pipeline.py:517 and 580-592 (_refine swaps in the original title without re-checking it), app/summarize.py:296-307 (LETSPLAY_TASK never asks whether the video is about this game); README.md:90-95 and 326-329 (claims)`
   - Что: Let's plays of completely different games are picked for short or generic titles, and the LLM writes a conclusion about the wrong game. Live database examples (status ok, method llm-transcript): Rock Hunter (released 2026-10-15) → «Monster Hunter 4 Ultimate - Part 12 Let's ROCK Basarios», published 11 years ago. The card says «Игра «Rock Hunter» предлагает интересный и требовательный геймплей», while its own evidence quote reads «welcome back to Monster Hunter». Also: HAUNT → Super Mario 64 «Big Boo's Haunt»; THE GUARDIANS → Guardians of the Galaxy; Hunt Me → The Witcher 3 Wild Hunt (evidence: «продолжаем проходить игру Ведьмак 3»); Bathyscaphe → «GTA 5 #29», matched through YouTube's machine translation «Theft of the bathyscaphe»; MEAT AND FLOWERS → Gaia Online devlog; Fatal Train → Fatal Frame; Melon Rally → Pac Man World Rally; Reason and Rhyme → Pokemon Stadium 2; Not My Son → GTA LCS; Thalia → MTG Arena; Alice Nation → SimCity; Toys Co. → FS19; FIBS → Lies of P; The Screamer → Minecraft; Frosthaven → a 7-year-old guide to the board game; The Nightmare Box Vol III → «vol 2». No_transcript cards do the same: Isle Forge → Zelda Classic, Labyrinth A Way Out → Kingdom Hearts 3, Backrooms Adventure → Super Bear Adventure, Pestilence → a 13-year-old Thai HoN video. At least 17 of the 52 «заключения по речи блогера» are about another game. They sit on page 1 of the default date-sorted list. The README's claim of 52 conclusions and its «strict matching» section say nothing about this. This is the exact hallucination an AI Automation Engineer reviewer is looking for.
   - Как исправить: (1) For let's plays, require the video to be published no earlier than about 1 year before release_date: parse publishedTimeText with the existing parse_age_days, and skip the candidate instead of falling back. (2) Make title_matches require the significant title words as one contiguous ordered phrase («rock hunter», «meat flowers», «fatal train»). Treat roman numerals (ii, iii, iv…) like digits that must match. (3) After _refine, re-run EXCLUDE, PRE_RELEASE and title_matches on the original title, and drop the candidate if it no longer matches, because search titles are machine-translated. (4) Add «"about_this_game": true|false» to LETSPLAY_TASK («ролик и речь автора именно об игре «{title}», а не о другой игре с похожим названием?»), and treat false like has_commentary=false: skip and try the next candidate. (5) Add a migration that resets letsplay records (checked_at → 2000-01-01, status no_video) whose stored video fails the new checks, add tests with these real title pairs, and update the README numbers after a re-run.
3. **[major]** `app/youtube.py:320-339 (rank_trailers: `pool = recent or eligible` falls back to old videos; bag-of-words title match; DLC subtitle not required), app/pipeline.py:450-474; README.md:96 («63 с официальным трейлером с YouTube»)`
   - Что: About 11 of the 63 YouTube «trailers» shown as the game's video are for something else. Vanisha → «ETHAN & VANISHA/ OUR WEDDING TRAILER», a wedding video. THE GUARDIANS → «BLADES OF THE GUARDIANS Official Trailer (2026) Jet Li», a film. Qiang Gu: Ghost Festival → «Ghost Month 2023 … Hungry Ghost Festival» (4 years old). Bathyscaphe → «Bathyscaphe Trailer», published 17 years ago, kept because the recency filter falls back to all matches. HAUNT → «Eternal Haunt». Pestilence → «Plague Doctor: Life in Pestilence». The Screamer → «Screamer» (a different racing game). The Nightmare Box Vol III → the Vol I trailer. House Flipper 2 + Sakura DLC → the Pets DLC trailer. FIBS → a 7-year-old «FIBS Trailer». Thalia → «Thalia & Leo». The card embeds each of these under «На Metacritic у этой игры видео нет, поэтому показан самый подходящий трейлер».
   - Как исправить: Use the same contiguous-phrase and roman-numeral rule as for let's plays. Require the game title at the start of the video title or bounded by separators (start, « - », «|», «:»). Require every word of a DLC or subtitle part («Sakura», «Vol III») in the video title. Replace `pool = recent or eligible` with a hard window (published no earlier than about 3 years before release, otherwise no trailer). Reject titles with movie or film markers (film, movie, wedding, «(2026) <actor>»), or ask the LLM a one-line yes/no in the same queue. Add a migration that sets trailer.checked_at old for stored trailers failing the new rule, so they are re-checked.
4. **[minor]** `app/youtube.py:306 (PRE_RELEASE and EXCLUDE run on search titles), app/youtube.py:342-351 (merge_candidates keeps the first, possibly translated, title), app/pipeline.py:580-592`
   - Что: The «demo and early access rank last» fix (#10) is defeated by YouTube's auto-translated titles. For Planet Zoo 2 (released) the search returned «Я поиграл в Planet Zoo 2 раньше всех!», which PRE_RELEASE does not match. So «I Played Planet Zoo 2 Early!» was picked over the released-game «Planet Zoo 2 — Часть 1 — Начало». After _refine the card shows the original English title, which PRE_RELEASE does match. 16 of 222 stored candidate titles are machine translations, and the «Другие найденные ролики» list mixes translated and original titles.
   - Как исправить: Fetch watch details, or at least the original title, for the top few eligible candidates before the final sort, and apply EXCLUDE, PRE_RELEASE and title_matches to the original title. Alternatively, for each video id prefer the object from the English query in merge_candidates. Store and show the original title in `candidates`.
5. **[minor]** `app/pipeline.py:482-484 (title_mismatch note), app/youtube.py:298-300 (counts every non-matching search result)`
   - Что: The new note «Летсплеи по игре на YouTube есть, но ни в одном названии нет полного названия «…»» is shown whenever any search result fails the title check. Most of those results are unrelated videos. KAEBON (20 of 20 results mismatched), WonderLang Russian (21 of 21), MeowMeow Balls, Sudoku University, College Hockey 27 and 10 more cards claim let's plays exist when YouTube simply returned other games.
   - Как исправить: Count a result as a «near miss» only when it contains at least half of the significant title words. Say «летсплеи есть» only when there is a near miss. Otherwise use the neutral text «YouTube вернул N роликов, но ни в одном названии нет «…»; проверим снова через сутки».
6. **[minor]** `app/templates/game.html:125`
   - Что: On description-retelling cards the fixed sentence «Ни субтитров, ни распознанной речи у подходящих роликов нет» contradicts the note below it when the real reason is «без комментариев автора». Kingdom Hearts Collection: the video had captions (dialogue only), and the note says «без комментариев автора: 1 ролик». Alveron, Pestilence, Cuboy Adventure, Backrooms Adventure, Platformino and Labyrinth have the same issue.
   - Как исправить: Build the sentence from lp.skipped and lp.checked. If any skipped reason is a no-commentary one, say «у подходящих роликов нет комментариев блогера (только игра, диалоги или музыка)». Use the «ни субтитров, ни распознанной речи» wording only when every skip reason is NO_TEXT.
7. **[minor]** `app/main.py:381-382 and 607-609 (from_our_page trusts the Sec-Fetch-Site header), docs/SECURITY.md:12-20, .env.example:13, tests/test_web.py:275`
   - Что: Any script still bypasses RUN_TOKEN by adding one header: `curl -X POST -H 'Sec-Fetch-Site: same-origin' -H 'Accept: application/json' host/run` gets 202. The repo's own test does exactly this with a non-browser client. Yet docs/SECURITY.md says «Токен нужен, если … вы не хотите, чтобы его запускали скриптами», which is false.
   - Как исправить: Either state honestly in SECURITY.md, README and .env.example that RUN_TOKEN only blocks clients that do not send Sec-Fetch-Site, and that cooldown plus the daily cap are the real protection. Or bind the button to a server-issued nonce: an HMAC of a time bucket embedded in the /monitor form and required by /run whenever no Bearer token is given. Add a test that a forged Sec-Fetch-Site without the nonce gets 401.
8. **[minor]** `docs/ai-log/11-repair.md:363-366 and 40; docs/ai-log/07-review-requirements.md:193; docs/ai-log/README.md:61`
   - Что: The log README says each agent's final answer is verbatim, but 11-repair.md's «Итоговый ответ агента» is a placeholder summary marked «записан по коммитам». The IP scrub also turned the quoted fix into nonsense: «write expr.txt with `203.0.113.7==>203.0.113.7`» and «git grep 203.0.113.7 f2dc1f9 matches 4 files». This verification stage is not logged at all.
   - Как исправить: Rerun the exporter after this stage, so 11-repair.md gets the real final answer and the review/verify stages are added. In the scrubber, replace an `<ip>==>` expression with `<IP>==>203.0.113.7` rather than replacing both sides, and replace the «git grep <ip>» phrases with «<IP>». Grep the export again for [IP], emails and /Users before committing.

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

> I'll start by examining the repository state.
- `Bash` Show repo history, status and layout
- `Bash` Check history for the home IP and leftover refs
- `Bash` Run the test suite
- `Bash` Test summary and visitor id scan in history
- `Bash` Show pytest summary line
- `Bash` Read the README top part
- `Bash` Read the rest of the README
- `Bash` Count lines and diff stats of repair
- `Bash` Read summarize module
- `Bash` Read pipeline module part 1
- `Bash` Read pipeline module part 2
- `Bash` Read youtube module
- `Bash` Scan the AI log for private data
- `Bash` Scan the AI log for names and other sensitive phrases
- `Bash` Read the AI log index and repair log header
- `Bash` List AI log sections and read the repair final answer
- `Bash` Inspect data dir, ignores and listening ports
- `Bash` Copy database and inspect runs and day state
- `Bash` Count processed games per run and source
- `Bash` Read the web routes in main.py
- `Bash` Read the frontend script and security doc
- `Bash` Read serve entrypoint and deploy configs
- `Bash` Read scheduler module
- `Bash` Start the service on test port 8791 with a DB copy and network features off
- `Bash` Wait for the server and check health
- `Bash` Test title search queries via the API
- `Bash` Inspect search normalisation code
- `Bash` Read selection logic and its tests
- `Bash` Read similarity module
- `Bash` Summarise let's play and trailer status counts
- `Bash` Dump let's play conclusions from the live database
- `Bash` List chosen videos with publish age per game
- `Bash` List YouTube trailers chosen per game
- `Bash` Check status codes of main pages and API
- `Skill` skill=anthropic-skills:built-in-browser
- `preview_start` http://127.0.0.1:8791/game/rock-hunter
- `get_page_text` 
- `computer` screenshot
- `Bash` Count games missing covers and other fields
- `Bash` Check overlap of missing covers and descriptions, list samples
- `Bash` Inspect cover extraction in the Metacritic parser
- `Read` ~/dev/skytec-metacritic/docs/screenshots/card.jpg
- `Read` ~/dev/skytec-metacritic/docs/screenshots/list.jpg
- `Read` ~/dev/skytec-metacritic/docs/screenshots/monitor.jpg
- `Bash` Check title matching on wrong-game pairs with HEAD code
- `navigate` http://127.0.0.1:8791/game/kingdom-hearts-collection-i-iii
- `get_page_text` 
- `navigate` http://127.0.0.1:8791/monitor
- `get_page_text` 
- `Bash` Check dead code removal and constants
- `Bash` Read database migrations
- `Bash` Read the let's play block of the card template
- `Bash` Read STT doc and VAD thresholds
- `Bash` Review conclusions for older (major) games
- `Bash` Show notes for games without a usable let's play
- `resize_window` 
- `navigate` http://127.0.0.1:8791/game/valor-mortis
- `javascript_tool` javascript_exec
- `navigate` http://127.0.0.1:8791/monitor
- `javascript_tool` javascript_exec
- `navigate` http://127.0.0.1:8791/?page=99
- `get_page_text` 
- `Bash` Scan AI log and docs for local paths
- `Bash` Narrow scan for local paths and personal identifiers
- `Bash` Test graceful shutdown with an open SSE stream
- `resize_window` 
- `Bash` Find run token tests
- `Bash` Read run token test
- `Bash` Inspect candidate ranking for demo-video cases
- `Bash` Check request language headers for YouTube
- `Bash` Confirm demo filter runs on translated titles
- `Bash` Confirm demo filter runs on translated titles using the venv
- `Bash` Check translated candidate titles for wrong-game picks
- `Bash` Count category-prefixed and meta items in review summaries
- `Bash` Confirm no servers are left running and tree is clean
- `tabs_close` 
- `Bash` Get exact line numbers for findings

</details>
