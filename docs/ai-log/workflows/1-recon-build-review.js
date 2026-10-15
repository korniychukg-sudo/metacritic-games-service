// Первый оркестратор. Этапы Recon, Build и review:code завершились; review:requirements остановлен
// вручную, когда агент начал ждать следующего часового запуска в реальном времени. Этап Fix этого скрипта
// не запускался: исправления выполнил второй оркестратор (2-fix-speech-review-repair.js).
export const meta = {
  name: 'skytec-metacritic-test',
  description: 'Build the Metacritic games service test task: recon, build, two reviews, fixes',
  phases: [
    { title: 'Recon', detail: 'map Metacritic, YouTube and LLM data sources' },
    { title: 'Build', detail: 'implement the service' },
    { title: 'Review', detail: 'requirements check on live data + code review' },
    { title: 'Fix', detail: 'apply review findings' },
  ],
}

const REPO = '~/dev/skytec-metacritic'
const TASK = `ТЕСТОВОЕ ЗАДАНИЕ (AI Automation Engineer).
Обязательная часть. Реализовать сервис, который 1 раз в час заходит на Metacritic в раздел Games, берёт новые 20 игр, которые сегодня не обрабатывал, и добавляет информацию о них в свою базу или обновляет её, если игра уже есть в базе.
Выборка игр: 1) первые 20 игр берём со страницы Games https://www.metacritic.com/game/ — раздел New Releases; 2) при последующих обработках берём игры из раздела SEE ALL (сортировка Новые) https://www.metacritic.com/browse/game/all/all/all-time/new/ — можно за каждый заход обрабатывать очередную страницу с играми. Каждый новый день начинаем выбирать заново: сначала игры из пункта 1, затем из 2.
Информация, которую забираем: название игры; картинка-обложка; платформа + Metascore + Userscore (платформ может быть много: ps5, ns2 и т.д.); разработчик; описание; ссылка на видео. Сервис просматривает отзывы и пишет короткое резюме, что игрокам нравится в игре, что нет. Резюме делается отдельно для отзывов критиков и отдельно для отзывов пользователей; эта часть может обновляться при повторных обходах.
Веб-интерфейс: список загруженных игр (краткая информация); карточка игры (вся информация выше); фильтр по платформам; поиск по названию; сортировка по рейтингу. Для каждой игры подобрать похожие игры из тех, что уже есть в базе, и показывать их в карточке; клик по имени открывает карточку этой игры.
Дополнительная часть 1: для каждой игры найти летсплеи на YouTube, взять самый популярный летсплей, перевести рассказ блогера в текст, по тексту сделать заключение и добавить его к информации об игре со ссылкой на ролик.
Дополнительная часть 2: в веб-интерфейсе мониторинг работы сервиса в реальном времени (статусы воркеров, количество обработанных записей и т.п.) и кнопка принудительного запуска обработки.
Результат: ссылка на репозиторий, ссылка на работающий сервис, переписка с нейросетью.`

const RULES = `Engineering rules:
- Work only in ${REPO} (create it). Python 3.11+ (use /usr/bin/env python3; create a venv in ${REPO}/.venv). Keep dependencies small and pinned in requirements.txt.
- Be polite to Metacritic and YouTube: realistic browser User-Agent, at most about 1 request per second, retries with backoff, timeouts. Never hammer endpoints while exploring; cache sample responses to ${REPO}/docs/samples/ and reuse them.
- LLM: OpenAI-compatible chat API configured by env LLM_BASE_URL, LLM_API_KEY, LLM_MODEL. Default (no key) is the free anonymous endpoint https://text.pollinations.ai/openai with model "openai" (verified working; it may rate-limit, so serialize calls, add retries and a gap of a few seconds). If the LLM fails, fall back to a deterministic extractive summary so the card is never empty, and mark which method produced it.
- Do not deploy, do not git push, do not print or store any secrets. The service must run with zero secrets.
- All user-facing text (UI, README) in Russian. Code comments sparse.`

phase('Recon')
const recon = await agent(`${TASK}

${RULES}

TASK FOR YOU: reconnaissance only, no app code yet. Find the most reliable way to get each required field, and write a precise data-access spec to ${REPO}/docs/DATA_SOURCES.md with sample payloads saved in ${REPO}/docs/samples/.
Investigate:
1. The New Releases block on https://www.metacritic.com/game/ (HTML or the JSON the page uses). Metacritic's frontend calls a JSON backend (backend.metacritic.com, finder/composer endpoints; the public apiKey is embedded in the site's JS bundle). Find the endpoint for the 20 New Releases, for the browse list sorted by new with pagination, for a game's details (title, cover image URL, all platforms each with Metascore and Userscore, developer, description, trailer/video URL), and for critic and user reviews per game (enough text for a summary, e.g. first 20 of each). If JSON is not available, document robust HTML parsing (Nuxt __NUXT_DATA__ or similar).
2. YouTube without an API key: how to search "<game> let's play" / "<game> прохождение" and pick the most viewed result (parse ytInitialData from the results page), and how to get the spoken text: youtube-transcript-api or the timedtext captions track from the watch page, including auto-generated captions; note what fails and the fallback.
3. Confirm the LLM endpoint above with one short call and note response shape and latency.
Return a concise summary of the spec: endpoints, params, field paths, pagination, pitfalls.`, { label: 'recon', phase: 'Recon' })

phase('Build')
const build = await agent(`${TASK}

${RULES}

DATA ACCESS SPEC (from recon, also in ${REPO}/docs/DATA_SOURCES.md):
${recon}

TASK FOR YOU: implement the whole service in ${REPO}.
Architecture: FastAPI + SQLite (path from env DB_PATH, default ./data/games.db) + background scheduler running the pipeline every hour (and once shortly after startup if the DB is empty). One run = pick the next 20 games per the selection rules (per-day state: first the 20 New Releases, later runs take the next browse page sorted by new; a game already processed today is skipped; a new day resets the cursor; games already in the DB are updated). For each game: fetch details and all platforms with scores, developer, description, cover, video; fetch critic and user reviews; produce two LLM summaries (критики / игроки: что нравится, что нет); find the most popular let's play on YouTube, get its spoken text from captions, produce an LLM conclusion with the video link (store status if unavailable). Similar games: compute from data already in the DB (e.g. TF-IDF over description plus genres/platforms/developer, cosine similarity), recompute after each run, show top 5.
Web UI (server-rendered Jinja2 + a little vanilla JS, clean responsive design): list with cover, title, platforms, Metascore/Userscore; filters by platform, search by title, sort by rating (Metascore/Userscore, asc/desc) and by date; game card with every field, both review summaries, let's play conclusion with link, similar games as links. Monitoring page: worker statuses (scheduler, current step, current game), counters (games processed today and total, errors), last/next run time, live log, updated in real time via Server-Sent Events, plus a «Запустить обработку сейчас» button (POST, guarded against parallel runs). JSON API endpoints for games and status.
Quality: pytest tests for selection/cursor logic, parsers (against saved samples), similarity, summary fallback; all tests must pass. Logging. Graceful error handling per game so one failure does not stop the run.
Deployment readiness: Dockerfile and/or Procfile + railway.json, PORT env, DB_PATH env for a mounted volume, healthcheck endpoint. README.md in Russian: what it does, architecture, how each requirement is met, how to run locally, env vars, known limitations.
Finally actually run it locally: start the server, trigger one processing run against live Metacritic for a few games (you may temporarily limit to 3-5 games for the smoke test, but the default must be 20), confirm the UI pages render with real data, then stop the server. Initialise git and commit (no push). Return: what works, test results, smoke-run results, known gaps.`, { label: 'build', phase: 'Build' })

phase('Review')
const [rq, cr] = await parallel([
  () => agent(`${TASK}

${RULES}

Builder's report:
${build}

TASK: act as the hiring company's strict reviewer. In ${REPO}, start the service locally, trigger a full processing run (20 games, against live Metacritic) and check EVERY requirement of the test task item by item: selection rules (New Releases first, then browse pages by new, per-day reset, skip processed-today, update existing), every field present and correct for several games compared with the live Metacritic pages (all platforms with both scores), critic and user summaries separate and meaningful, let's play conclusion and link, list/card/filter/search/sort, similar games clickable, monitoring live via SSE, force-run button. Also check the README claims match reality. Do not fix code. Return a prioritized list of concrete defects with file/line and reproduction steps, plus what is verified working.`, { label: 'review:requirements', phase: 'Review' }),
  () => agent(`${TASK}

${RULES}

Builder's report:
${build}

TASK: senior code review of ${REPO}: correctness and robustness of the scheduler and concurrency (no parallel runs, restart safety, timezone of the daily reset), SQLite usage and migrations, scraping resilience (timeouts, retries, rate limiting, schema drift), LLM call handling and fallback, security of the force-run endpoint (simple protection like a token or rate limit without blocking reviewers from trying it), XSS in templates, deployment config for Railway (PORT, volume, healthcheck), test coverage gaps. Run the tests. Do not fix code. Return a prioritized list of concrete issues with file/line and suggested fix.`, { label: 'review:code', phase: 'Review' }),
])

phase('Fix')
const fixed = await agent(`${TASK}

${RULES}

Two reviews of ${REPO}:
REQUIREMENTS REVIEW:
${rq}

CODE REVIEW:
${cr}

TASK: fix every real defect (verify each first, skip false alarms with a note). Keep tests green and add tests for fixed logic. Run a fresh local end-to-end processing run of 20 games against live Metacritic and confirm the UI and monitoring work. Update README (requirements checklist with honest status, limitations). Commit. Return: list of fixes, final test results, final smoke-run numbers (games processed, summaries by LLM vs fallback, let's plays found), and the exact local commands to run it.`, { label: 'fix', phase: 'Fix' })

return { recon, build, rq, cr, fixed }
