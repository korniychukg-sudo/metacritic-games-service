# Разведка источников данных

Агент `01-recon`, начат 14.10.2026 21:23, работал около 13 мин, вызовов инструментов: 49.

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

TASK FOR YOU: reconnaissance only, no app code yet. Find the most reliable way to get each required field, and write a precise data-access spec to ~/dev/skytec-metacritic/docs/DATA_SOURCES.md with sample payloads saved in ~/dev/skytec-metacritic/docs/samples/.
Investigate:
1. The New Releases block on https://www.metacritic.com/game/ (HTML or the JSON the page uses). Metacritic's frontend calls a JSON backend (backend.metacritic.com, finder/composer endpoints; the public apiKey is embedded in the site's JS bundle). Find the endpoint for the 20 New Releases, for the browse list sorted by new with pagination, for a game's details (title, cover image URL, all platforms each with Metascore and Userscore, developer, description, trailer/video URL), and for critic and user reviews per game (enough text for a summary, e.g. first 20 of each). If JSON is not available, document robust HTML parsing (Nuxt __NUXT_DATA__ or similar).
2. YouTube without an API key: how to search "<game> let's play" / "<game> прохождение" and pick the most viewed result (parse ytInitialData from the results page), and how to get the spoken text: youtube-transcript-api or the timedtext captions track from the watch page, including auto-generated captions; note what fails and the fallback.
3. Confirm the LLM endpoint above with one short call and note response shape and latency.
Return a concise summary of the spec: endpoints, params, field paths, pagination, pitfalls.
```

## Итоговый ответ агента

Spec written to `~/dev/skytec-metacritic/docs/DATA_SOURCES.md` (Russian). Sample payloads are in `~/dev/skytec-metacritic/docs/samples/` (31 files, 7.8 MB). The venv is at `~/dev/skytec-metacritic/.venv` (Python 3.12, from `~/.local/bin/python3.12`; the system `python3` is 3.9.6). No app code yet, nothing pushed, and no key values written into the spec. About 30 Metacritic requests, 4 YouTube requests and 3 LLM calls in total.

**Metacritic.** Every endpoint below works on `https://backend.metacritic.com` with no apiKey and no cookies (checked 2026-10-14). If a 401 or 403 ever appears, the spec says to pull the key from the `/game/` HTML at runtime with `apiKey=([A-Za-z0-9]{20,})` and never commit it.

- **New Releases (first run of the day):**
  - Request: `GET /finder/metacritic/web?componentName=new-releases-carousel&componentDisplayName=Newly+Released&componentType=ProductList&sortBy=-releaseDate&metaScoreMin=1&offset=0&limit=20&mcoTypeId=13`.
  - It returns the same 20 games, in the same order, as the block on `/game/`.
  - The list is in `data.items[]`, with `data.totalResults` and `links.next/tabs`.
- **SEE ALL, newest first (later runs):**
  - Request: `GET /finder/metacritic/web?sortBy=-releaseDate&productType=games&offset=(N-1)*24&limit=24&mcoTypeId=13`.
  - It matches `/browse/game/all/all/all-time/new/`. Paging is driven by `offset`.
  - 181,114 games, including today's releases with no score.
- **Fields in each list item:** `id`, `title`, `slug`, `releaseDate`, `criticScoreSummary.score`, `image.bucketPath`, `genres[]`. Two traps:
  - `platforms[]` holds only the lead platform.
  - `userScore.score` is often null.
- **Game details, one call:** `GET /composer/metacritic/pages/games/{slug}/web`. Pick the parts out of `components[]` by `meta.componentName`.
  - `product.data.item` has:
    - `title` and `description`
    - developer: `production.companies[typeName=="Developer"].name`
    - `platforms[]` with `name`, `slug`, `isLeadPlatform` and `criticScoreSummary.score` (the Metascore for each platform)
    - cover: `images[typeName=="cardImage"|"mainImage"].bucketPath`, served at `https://www.metacritic.com/a/img/catalog{bucketPath}` (checked, returns 200 JPEG)
    - video: `video.embedUrl` (JW Player page) or `video.manifestUrl` (HLS). `video` can be null.
    - `genres` and `gameTaxonomy.franchises`, useful for similar games.
  - `user-score-summary` and `critic-score-summary` cover the lead platform only.
  - `critic-reviews.data.item` and `user-reviews.data.item` each give `positive`, `neutral` and `negative`, up to 7 reviews per group. That is a balanced sample for the likes/dislikes summary with no extra requests.
  - An unknown slug returns 404 JSON.
- **Userscore for the other platforms:** `GET /reviews/metacritic/user/games/{slug}/platform/{platformSlug}/stats/web?componentName=user-score-summary&componentDisplayName=user+Score+Summary&componentType=MetaScoreSummary`, read `data.item.score`. Three traps:
  - A `?platform=` query parameter is silently ignored. Only the `/platform/{slug}/` path segment works.
  - With no ratings it returns `score:0, reviewCount:0`. Store that as null.
  - Platform ids differ between responses (PS5 is 1500000128 in `product` and 1500000062 in reviews). Join platforms on slug or name.
- **More reviews if needed:** `GET /reviews/metacritic/{critic|user}/games/{slug}/platform/{p}/web?offset&limit&filterBySentiment=all|positive|neutral|negative&sort=score|date&componentName=...&componentType=ReviewList`.
  - Critic `limit` is capped at 10, so 20 critic reviews take two requests. User `limit=20` works.
  - `sort=score` returns only 10s, so the sample is skewed.
  - Critic `score` can be null.
- **Budget:** 1 composer call per game, plus 1 stats call for each platform after the lead, so usually 1–4 requests per game.
- **Other trap:** order among games with the same release date is not stable (the browse HTML and the JSON disagreed), so dedupe by `id`.
- **Fallback:** the spec includes a `__NUXT_DATA__` (devalue) decoder and the paths where the data sits for `/game/`, `/browse/...` and `/game/{slug}/`.
- **robots.txt** allows `/game/` and `/browse/`.

**YouTube**
- **Search:**
  - Request: `https://www.youtube.com/results?search_query={q}&sp=CAMSAhgC&hl=en&gl=US`, with cookies `SOCS=CAI; CONSENT=YES+cb` to get past the consent page.
  - `CAMSAhgC` means sort by Popularity and longer than 20 minutes.
  - There is no sort by view count any more, and the Popularity order is not by views. So parse view counts and pick the maximum locally.
- **Parsing:** regex `var ytInitialData = (\{.*?\});</script>`, then walk every `videoRenderer`:
  - `videoId`, `title.runs`, `lengthText`, `ownerText`
  - views from `viewCountText.simpleText` ("465,300 views")
  - Titles can come back auto-translated or cut short; one came back as just "Star Wars". Get the exact title from `videoDetails.title` on the watch page.
- **Choosing the video:** the game title must appear in the video title, at least 10 minutes long, exclude trailers and reviews, prefer let's play / walkthrough / прохождение. The test pick was `bGBXM6oxbag` (theRadBrad walkthrough, 465K views).
- **Transcript:** `youtube-transcript-api==1.2.4` works, including auto-generated captions.
  - Calls: `YouTubeTranscriptApi().list(id).find_transcript(["en","ru"]).fetch().to_raw_data()`.
  - On that video: 1.9 s, 1,704 snippets, 52,690 characters.
- **What fails:** the watch-page `captionTracks[].baseUrl` (marked `exp=xpe`) returns 200 with an empty body, because it needs a PO token.
- **Deploy risk:** on cloud IPs the library will likely be blocked (`RequestBlocked`/`IpBlocked`). An optional proxy can be set through `GenericProxyConfig`.
- **Fallback order:** next candidate video (up to 3), then a summary from the video's title and description marked `method="description"`, then just the link.

**LLM**
- `POST https://text.pollinations.ai/openai` works, and so does `/openai/chat/completions`.
- The response is OpenAI chat format: `choices[0].message.content`, plus a `reasoning` field to ignore, `usage`, and `user_tier: "anonymous"`.
- `model:"openai"` is actually served by `gpt-oss-20b`, a reasoning model.
- Latency was 7.7–17.2 s. No rate-limit headers.
- Traps:
  - JSON comes back wrapped in ```` ```json ```` fences.
  - It writes in the blogger's first person and repeats sponsor segments unless the prompt says otherwise.
  - A 50,000-character transcript is too big; send about 8–9K characters (start, middle, end).
  - Run calls one at a time with a 3–5 s gap, and fall back to an extractive summary marked `summary_method`.

## Ход работы

<details><summary>Действия агента по порядку (без вывода инструментов)</summary>

- `Bash` Create project dirs and check Python versions
- `Bash` Create venv and install recon libraries
- `Bash` Fetch Metacritic games index page politely
- `Bash` Search page for backend endpoints and apiKey
- `Bash` List endpoint URLs carrying apiKey
- `Bash` Extract endpoint URLs from page via Python regex
- `Bash` Inspect unescaped backend URLs and NUXT data
- `Bash` Show HTML context around New Releases heading
- `Bash` Fetch New Releases finder JSON without apiKey
- `Bash` Summarize New Releases items and compare to HTML order
- `Bash` Fetch browse-new page and extract its finder endpoint
- `Bash` Decode Nuxt devalue payload and show structure
- `Bash` Show browse items and finder page metadata
- `Bash` List JS bundle URLs referenced by browse page
- `Bash` Fetch Nuxt entry bundle and grep finder usage
- `Bash` Try finder endpoint for browse sorted by release date
- `Bash` Fetch a game page and list backend endpoints
- `Bash` Fetch product JSON and print its fields
- `Bash` Print platforms, video, images, companies
- `Bash` Verify unsigned cover image URL pattern
> Now stats and review summary endpoints.
- `Bash` Fetch user stats overall and per platform, critic summary
- `Bash` Fetch user-reviews page for PC and list backend calls
- `Bash` Fetch per-platform user stats and review lists
- `Bash` Inspect critic summary endpoint structure
- `Bash` Fetch user summary and all-platform critic list
- `Bash` Fetch empty-review game and 404 shapes
- `Bash` Check browse order stability and response headers
- `Bash` Find See All link target for New Releases
- `Bash` Fetch YouTube search results sorted by views
- `Bash` Parse ytInitialData video results from search page
- `Bash` List YouTube search filter options and params
- `Bash` Search YouTube let's play and Russian walkthrough, over 20 min
- `Bash` Test youtube-transcript-api on a walkthrough video
- `Bash` Test watch page captionTracks timedtext fallback
- `Bash` Call the free Pollinations LLM endpoint once
- `Bash` Inspect LLM response top-level keys and usage
- `Bash` Test LLM with a 9k-char transcript prompt
- `Bash` Fetch robots.txt for Metacritic and backend
- `Bash` List Nuxt page components on index and game pages
- `Bash` Inspect game-stats component and page meta
- `Bash` Test composer page endpoint without apiKey
- `Bash` List youtube-transcript-api error and proxy classes
- `Bash` Check title structure of an odd search result
- `Bash` Save parsed YouTube results and list samples
- `Bash` Confirm composer score summaries and review keys
- `Write` ~/dev/skytec-metacritic/docs/DATA_SOURCES.md
- `Bash` Check whether /chat/completions suffix works on Pollinations
- `Bash` Refine pagination and LLM notes in the spec
- `Bash` Verify no key values in spec and list project root
> Spec written to `~/dev/skytec-metacritic/docs/DATA_SOURCES.md` (Russian). Sample payloads are in `~/dev/skytec-metacritic/docs/samples/` (31 files, 7.8 MB). The venv is at `~/dev/skytec-metacritic/.venv` (Python 3.12, from `~/.loca

</details>
