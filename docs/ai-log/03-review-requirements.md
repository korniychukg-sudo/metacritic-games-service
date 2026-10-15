# Ревью по ТЗ в роли работодателя

Агент `03-review-requirements`, начат 14.10.2026 22:24, работал около 73 мин, вызовов инструментов: 122.

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

TASK: act as the hiring company's strict reviewer. In ~/dev/skytec-metacritic, start the service locally, trigger a full processing run (20 games, against live Metacritic) and check EVERY requirement of the test task item by item: selection rules (New Releases first, then browse pages by new, per-day reset, skip processed-today, update existing), every field present and correct for several games compared with the live Metacritic pages (all platforms with both scores), critic and user summaries separate and meaningful, let's play conclusion and link, list/card/filter/search/sort, similar games clickable, monitoring live via SSE, force-run button. Also check the README claims match reality. Do not fix code. Return a prioritized list of concrete defects with file/line and reproduction steps, plus what is verified working.
```

## Итоговый ответ агента

_(агент ещё работает, итогового ответа нет)_

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

- `Bash` Inspect project layout and git state
- `Bash` Read README
- `Bash` Read config, selection, scheduler, http client
- `Bash` Read Metacritic client
- `Bash` Read pipeline
- `Bash` Read db layer
- `Bash` Read web app and monitor
- `Bash` Read YouTube client
- `Bash` Read LLM and summarize modules
- `Bash` Check current time and existing DB state
- `Bash` Prepare scratch dir and check port
- `Bash` Start the service on a fresh database
- `Bash` Read list template and macros
- `Bash` Check server log
- `Bash` Read game card template
- `Bash` Read monitor template and JS
- `Bash` Read base template and check run log
- `Skill` skill=anthropic-skills:built-in-browser
- `preview_start` https://www.metacritic.com/game/
- `javascript_tool` javascript_exec
- `navigate` https://www.metacritic.com/browse/game/all/all/all-time/new/
- `javascript_tool` javascript_exec
- `get_page_text` 
- `read_network_requests` 
- `javascript_tool` javascript_exec
- `Bash` Save site browse list and check run log
- `Bash` Read data sources doc
- `Bash` Inspect composer sample review structure
- `Bash` Run the offline test suite
- `Bash` Count tests and read similarity module
- `Bash` Probe the SSE stream and check run log
- `navigate` https://www.metacritic.com/game/warhammer-40000-boltgun-2/
- `get_page_text` 
- `Bash` Fetch Boltgun 2 card from the service API
- `Bash` Fetch Boltgun 2 card from the service API
- `Bash` Check status payload and log
- `Bash` Fetch Over the Hill card
- `navigate` https://www.metacritic.com/game/over-the-hill/
- `get_page_text` 
- `navigate` http://127.0.0.1:8765/monitor
- `computer` screenshot
- `get_page_text` 
- `Bash` List test names
- `ToolSearch` query=select:Monitor, max_results=1
- `Monitor` description=Run #1 pipeline progress (games added, warnings, errors, fin, timeout_ms=1800000
- `navigate` http://127.0.0.1:8765/game/warhammer-40000-boltgun-2
- `computer` screenshot
- `Bash` Check that the JW Player trailer link is embeddable
- `Bash` Check dependency consistency
- `Bash` Check extractive fallback and smallest prompt offline
- `Bash` Check latest run log lines
- `Bash` Inspect Planet Zoo 2 and Shogun 2 cards
- `Bash` Probe one live YouTube let's play search for Planet Zoo 2
- `Bash` Check latest run log lines
- `Bash` Inspect Shogun 2 card
- `Bash` Test the parallel-run guard on the run endpoints
- `Bash` Inspect Valor Mortis card
- `navigate` https://www.metacritic.com/game/valor-mortis/
- `get_page_text` 
- `Bash` Check latest run log lines
- `Bash` Wait for run #1 to finish
- `Bash` Inspect Valor Mortis let's play result
- `resize_window` 
- `navigate` http://127.0.0.1:8765/?sort=metascore_desc
- `computer` screenshot
- `javascript_tool` javascript_exec
- `Bash` Test list API filter, search and sort
- `Bash` Check latest run log lines
- `Bash` Check wait task output
- `Bash` Wait until 12 games are processed
- `Bash` List processed games with summary methods
- `Bash` Inspect Neon Abyss 2 and Dragon's Dogma 2 cards
- `Bash` Reproduce let's play mismatch offline
- `Bash` Inspect Echo Weaver and Kingdom Hearts cards
- `Bash` Wait for run #1 to finish and show log
- `Bash` Inspect Star Wars, Gears and Forever Ago cards
- `resize_window` 
- `navigate` https://www.metacritic.com/game/star-wars-galactic-racer/user-reviews/?platform=pc
- `get_page_text` 
- `Bash` Wait for run #1 to finish
- `Bash` Summarize run #1 results from the DB
- `Bash` Compare chosen let's play with most-viewed candidate
- `Bash` Probe YouTube search for Dragon's Dogma 2 Dark Arisen (2 requests)
- `navigate` http://127.0.0.1:8765/monitor
- `find` 
- `computer` left_click
- `computer` screenshot
- `computer` screenshot
- `navigate` http://127.0.0.1:8765/game/neon-abyss-2
- `find` 
- `javascript_tool` javascript_exec
- `Bash` Inspect similar-games quality and reasons
- `Bash` Wait for run #2 to finish
- `navigate` https://www.metacritic.com/browse/game/all/all/all-time/new/?page=2
- `javascript_tool` javascript_exec
- `navigate` https://www.metacritic.com/browse/game/all/all/all-time/new/
- `javascript_tool` javascript_exec
- `Bash` Reproduce the Echo Weaver LLM rejection with one call
- `Bash` Summarize browse-run games
- `Bash` Show the conclusions attached to mismatched videos
- `Bash` Reproduce title-matching false positives offline
- `Bash` List LLM budget and warning log lines
- `Bash` Measure how much input the shrunken prompt budget keeps
- `Bash` Wait for the scheduled run #3 to start
- `Bash` Check next-run tile on the monitor page
- `Bash` Check error handling on HTML routes
- `Bash` Wait for the scheduled run #3 to start
- `Bash` Wait for run #3 to finish
- `Bash` Test graceful shutdown with an open SSE stream
- `Bash` Disconnect the SSE client and confirm shutdown completes
- `Bash` Snapshot DB before simulating the next day
- `Bash` Restart the service with a timezone where it is already the next day
- `Bash` Check the restarted service state and trigger a new-day run
- `Bash` Wait for the new-day run to finish
- `Bash` Compare game records before and after the new-day run
- `Bash` Inspect Dragon's Dogma and Echo Weaver after the new-day run
- `navigate` http://127.0.0.1:8765/game/thanks-light
- `get_page_text` 
- `Bash` Check batch size when New Releases is partly processed
- `Bash` Check repo cleanliness and sample redaction
- `Bash` Check that a stored cover URL serves an image
- `Bash` Stop the local service

</details>
