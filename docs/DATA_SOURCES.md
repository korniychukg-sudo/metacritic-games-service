# Источники данных: спецификация доступа

Разведка проведена 14.10.2026 с обычного домашнего IP, браузерный User-Agent, не чаще 1 запроса в секунду.
Все примеры ответов лежат в `docs/samples/` (имена файлов указаны в скобках). Парсеры и тесты стоит писать
по этим файлам, без обращения к сети.

Главный вывод: у Metacritic есть открытый JSON-бэкенд `https://backend.metacritic.com`. Все нужные эндпоинты
(finder, composer, reviews) **отвечают без apiKey** и без cookies. HTML-парсинг нужен только как запасной вариант.

---

## 0. Общие правила HTTP

| Параметр | Значение |
|---|---|
| User-Agent | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36` |
| Заголовки | `Accept: application/json` для backend, `Accept-Language: en-US,en;q=0.9` |
| Темп | один общий лимитер на хост, пауза ≥ 1.0–1.2 с между запросами |
| Таймаут | 30 с |
| Ретраи | 3 попытки, экспоненциальная пауза 2/4/8 с, на 429/5xx/сетевые ошибки; 404 не ретраить |
| Кэш на стороне Metacritic | ответы идут через Cloudflare (`server: cloudflare`, заголовок `age`), то есть кэшируются на минуты. Заголовков rate-limit нет. |

robots.txt (`metacritic_robots.txt`): для `User-agent: *` запрещены только `/search`, `/signup`, `/login`, `/user`
и пара служебных путей. `/game/` и `/browse/` разрешены. У `backend.metacritic.com` robots.txt нет (404).

**apiKey.** В HTML страниц встречается публичный ключ фронтенда (`apiKey=...`), но только в URL-ах шапки и
подвала. Для finder/composer/reviews он не нужен. Если когда-нибудь backend начнёт отвечать 401/403, ключ
надо доставать на лету регэкспом `apiKey=([A-Za-z0-9]{20,})` из HTML `https://www.metacritic.com/game/` и
добавлять параметром. Ключ не хардкодить и не коммитить.

---

## 1. Список игр

### 1.1 Первый проход дня: блок «New Releases» на /game/

Блок на `https://www.metacritic.com/game/` рендерится на сервере из этого запроса (порядок карточек в HTML совпадает
с JSON один в один, проверено на 20 играх):

```
GET https://backend.metacritic.com/finder/metacritic/web
    ?componentName=new-releases-carousel
    &componentDisplayName=Newly+Released
    &componentType=ProductList
    &sortBy=-releaseDate
    &metaScoreMin=1
    &offset=0
    &limit=20
    &mcoTypeId=13
```
(`finder_new_releases.json`)

- `mcoTypeId=13` означает игры, `metaScoreMin=1` оставляет только игры с Metascore. Это фильтр самого блока.
- У блока три вкладки (`links.tabs`): «New Releases» (`sortBy=-releaseDate`), «Top Critics' Picks»
  (`sortBy=-metaScore`) и «Most Popular» (`sortBy=-popularityCount`). По заданию берём первую.
- Ответ: `data.totalResults` (18620), `data.items[]` (20 шт.), `links.next.href`.

### 1.2 Последующие проходы: SEE ALL, сортировка «новые»

Ссылка «See All» ведёт на `https://www.metacritic.com/browse/game/all/all/all-time/new/` (24 игры на страницу,
HTML-пагинация `?page=N`). Тот же список отдаёт JSON:

```
GET https://backend.metacritic.com/finder/metacritic/web
    ?sortBy=-releaseDate
    &productType=games
    &page=1
    &offset=0
    &limit=24
    &mcoTypeId=13
```
(`finder_browse_new_p1.json`)

- Пагинация через `offset`: в `links.next` остаётся `page=1`, а `offset` растёт до 24, то есть страницу задаёт `offset`.
  Страница N: `offset=(N-1)*limit`. `limit=20` тоже принимается (его использует блок 1.1).
- `data.totalResults` = 181114. `links.next/last` дают готовые URL.
- Сюда входят **все** игры, в том числе сегодняшние релизы без единой рецензии (`criticScoreSummary.score = null`).
  Будущих дат в начале списка нет: первая страница датирована сегодняшним днём.

### 1.3 Поля элемента списка (`data.items[]`, общие для 1.1 и 1.2)

| Поле | Путь | Примечание |
|---|---|---|
| ID тайтла | `id` | например `1300714334` |
| Название | `title` | |
| Slug | `slug` | ключ для всех остальных запросов |
| Дата релиза | `releaseDate` | `YYYY-MM-DD` |
| Metascore (ведущая платформа) | `criticScoreSummary.score` | может быть `null` |
| Userscore | `userScore.score` | часто `null`, даже когда оценки есть. Брать из 2.3 |
| Платформы | `platforms[].name` | **только ведущая платформа.** Полный список см. в 2.1 |
| Обложка | `image.bucketPath` | см. 2.2 |
| Жанры | `genres[].name` | |
| Описание | `description` | часто `null`, полное описание в 2.1 |

### 1.4 Логика «20 новых за проход»

- Состояние хранить по датам: `processed_today(date, game_id)` и курсор `browse_offset(date)`.
- Первый проход дня: запрос 1.1, всё, чего нет в `processed_today`.
- Следующие проходы: запрос 1.2 начиная с `browse_offset`, страница за страницей, пока не наберётся 20 игр,
  которых сегодня ещё не было (верхний предел 3–4 страницы за проход). Списки 1.1 и 1.2 сильно пересекаются,
  потому что оба сортируются по дате.
- **Подвох с одинаковыми датами.** Внутри одной даты порядок не задан: SSR-HTML страницы browse и JSON-запрос
  вернули один и тот же набор игр в разном порядке. Повторный запрос того же JSON совпал, но это может быть
  кэш Cloudflare. Дедуплицировать по `id`/`slug` и не полагаться на точные границы страниц.

---

## 2. Карточка игры

### 2.1 Основной запрос: composer (один запрос на почти всё)

```
GET https://backend.metacritic.com/composer/metacritic/pages/games/{slug}/web
```
(`composer_game_star-wars-galactic-racer.json`, ~215 КБ, большая часть веса приходится на header/footer)

Ответ: `components[]`. Нужный компонент ищется по `meta.componentName`:

| componentName | Что внутри | Отдельный эндпоинт с тем же содержимым |
|---|---|---|
| `product` | все данные игры | `/games/metacritic/{slug}/web?componentName=product&componentDisplayName=Product&componentType=Product` (`product_*.json`, 8.6 КБ) |
| `critic-score-summary` | Metascore ведущей платформы | `/reviews/metacritic/critic/games/{slug}/stats/web?...` |
| `critic-reviews` | до 7 рецензий критиков на каждую тональность | `/reviews/metacritic/critic/games/{slug}/summary/web?componentName=critic-reviews&componentDisplayName=Critic+Reviews&componentType=ProductReviewsSummary` |
| `user-score-summary` | Userscore ведущей платформы | `/reviews/metacritic/user/games/{slug}/stats/web?...` |
| `user-reviews` | до 7 отзывов игроков на каждую тональность | `/reviews/metacritic/user/games/{slug}/summary/web?componentName=user-reviews&componentDisplayName=User+Reviews&componentType=ProductReviewsSummary` |
| `related-carousel`, `related-news`, `where-to-buy` | не нужны | |
| `game-stats` | всегда `status: 404` | игнорировать |

Несуществующий slug: HTTP 404 с телом `{"errors":[{"code":404,...}]}` (`product_404.json`).

Поля `product` (`components[name=product].data.item`):

| Поле задания | Путь | Примечание |
|---|---|---|
| Название | `title` | |
| Описание | `description` | сырой текст с `\r\n` |
| Разработчик | `production.companies[]` где `typeName == "Developer"` | бывает несколько. Издатель: `typeName == "Publisher"` |
| Платформы + Metascore | `platforms[]`: `name`, `slug`, `isLeadPlatform`, `criticScoreSummary.score`, `criticScoreSummary.reviewCount`, `releaseDate` | `score = null`, если рецензий нет |
| Ведущая платформа | `platform` (имя) или `platforms[isLeadPlatform]` | |
| Обложка | `images[]` где `typeName == "cardImage"` (вертикальный постер), иначе `"mainImage"` (горизонтальный) → `bucketPath` | URL см. 2.2 |
| Видео (трейлер) | `video.embedUrl` (страница плеера JW Player), `video.manifestUrl` (HLS `.m3u8`), `video.title` | `video` может быть `null`. В `title` встречаются HTML-сущности (`&#8211;`), нужен `html.unescape` |
| Жанры | `genres[].name` | пригодятся для похожих игр |
| Франшизы | `gameTaxonomy.franchises[].name` | пригодятся для похожих игр |
| Прочее | `releaseDate`, `rating` (ESRB), `mustPlay`, `criticScoreSummary` (ведущая платформа) | |

Пример `platforms[]`:
```json
{"id":1500000019,"name":"PC","slug":"pc","isLeadPlatform":false,"releaseDate":"2026-10-06",
 "criticScoreSummary":{"score":84,"reviewCount":24,"positiveCount":21,"neutralCount":1,"negativeCount":0,
 "sentiment":"Generally favorable","url":"/game/star-wars-galactic-racer/critic-reviews/?platform=pc"}}
```

### 2.2 URL обложки

`https://www.metacritic.com/a/img/catalog{bucketPath}`
пример: `https://www.metacritic.com/a/img/catalog/provider/7/2/7-1782239544.jpg` → 200 image/jpeg, 172 КБ (проверено).
URL вида `/a/img/resize/{hash}/catalog/...` из HTML подписаны хэшем, самим их не собирать.

### 2.3 Userscore по каждой платформе

В `product.platforms[]` Userscore нет. Composer отдаёт его только для ведущей платформы. Для остальных
платформ нужен отдельный запрос:

```
GET https://backend.metacritic.com/reviews/metacritic/user/games/{slug}/platform/{platformSlug}/stats/web
    ?componentName=user-score-summary&componentDisplayName=user+Score+Summary&componentType=MetaScoreSummary
```
(`user_stats_star-wars-galactic-racer_platform_pc.json`)

Ответ: `data.item.score` (0–10), `reviewCount`, `positiveCount`/`neutralCount`/`negativeCount`, `sentiment`.

Подвохи:
- Параметр `?platform=pc` в query **молча игнорируется**, сервер возвращает ведущую платформу
  (`user_stats_..._query-platform-IGNORED.json`). Платформа задаётся только сегментом пути `/platform/{slug}/`.
- Если оценок нет, сервер отдаёт `score: 0` и `reviewCount: 0` (`user_stats_niph_platform_pc.json`).
  Это надо хранить как `null`, иначе в интерфейсе появится «0.0».
- `reviewCount` в stats считает все оценки (172), а `totalResults` в списках отзывов только отзывы с текстом (32).
- ID платформ в разных ответах не совпадают: PS5 в `product.platforms` имеет id `1500000128`, а в
  `reviewedProduct.platform` у рецензий `1500000062`. Связывать платформы по `slug`/`name`, а не по id.

Короткие коды для фильтра (по `platforms[].slug`): `playstation-5`→PS5, `playstation-4`→PS4,
`nintendo-switch-2`→NS2, `nintendo-switch`→NS, `xbox-series-x`→XSX, `xbox-one`→XONE, `pc`→PC, остальное по `name`.

### 2.4 Отзывы для резюме

**Основной источник: summary в composer** (запрос 2.1, без дополнительных запросов):
`components[name=critic-reviews].data.item` и `components[name=user-reviews].data.item`. Ключи `positive`,
`neutral`, `negative`, `default`, в каждом до 7 отзывов ведущей платформы. Это готовая сбалансированная выборка
«что нравится / что нет». `data.totalResults` равно общему числу отзывов.

Поля отзыва:
- критик: `quote`, `score` (0–100, **может быть `null`**), `publicationName`, `url` (полная рецензия), `date`, `platform`;
- игрок: `quote`, `score` (0–10), `author`, `date`, `spoiler` (bool), `platform`, `thumbsUp`/`thumbsDown`.

**Если нужно больше текста (например, по 20 каждого типа)**, есть постраничный ReviewList:
```
GET https://backend.metacritic.com/reviews/metacritic/{critic|user}/games/{slug}/platform/{platformSlug}/web
    ?offset=0&limit=20&filterBySentiment={all|positive|neutral|negative}&sort={score|date}
    &componentName={critic|user}-reviews&componentDisplayName={critic|user}+Reviews&componentType=ReviewList
```
(`critic_reviews_..._ps5.json`, `user_reviews_..._ps5.json`)
- Ответ: `data.totalResults`, `data.items[]`, `links.next.href`.
- **У критиков `limit` урезается до 10** (запросили 20, получили 10, и `next` уже с `limit=10`). Значит, 20 рецензий
  это 2 запроса. Для игроков `limit=20` работает (фронтенд сам ставит 50).
- `sort=score` выдаёт подряд одни десятки, выборка получается перекошенной. Для резюме лучше брать по
  `filterBySentiment=positive` и `=negative` отдельно, или `sort=date`.
- Без сегмента `/platform/{slug}/` возвращается ведущая платформа, а не все платформы
  (`critic_reviews_..._allplatforms.json`).

Пустая игра: summary приходит как `{"totalResults":0,"item":{"default":[],"positive":[],"negative":[],"neutral":[]}}`
(`critic_summary_niph.json`). В этом случае писать «отзывов пока нет», LLM не вызывать.

### 2.5 Бюджет запросов на игру

composer (1) + user stats для каждой неведущей платформы (P−1) + по желанию ReviewList (0–4).
Обычно выходит 1–4 запроса, то есть 20 игр укладываются в 1–2 минуты при 1 запросе в секунду.

### 2.6 Запасной вариант: HTML + `__NUXT_DATA__`

Страницы Nuxt 3/4 кладут состояние в `<script type="application/json" id="__NUXT_DATA__">`. Это плоский массив
в формате devalue: числа являются индексами в массиве, обёртки выглядят как `["Reactive", idx]`,
`["ShallowReactive", idx]`, `["Ref", idx]`, `["Set", ...]`, `["Map", k, v, ...]`, `["Date", iso]`, а `["EmptyRef"]`
означает отсутствие значения. Декодер:

```python
def load_nuxt(html):
    arr = json.loads(re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S).group(1))
    memo = {}
    WRAP = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "NuxtError", "Object"}
    def h(i):
        if not isinstance(i, int) or i < 0: return None if isinstance(i, int) else i
        if i in memo: return memo[i]
        v = arr[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in WRAP: memo[i] = h(v[1]); return memo[i]
            if v and v[0] in ("EmptyRef", "EmptyShallowRef"): return None
            if v and v[0] == "Date": return v[1]
            out = memo[i] = []; out.extend(h(x) for x in v); return out
        if isinstance(v, dict):
            out = memo[i] = {}; out.update({k: h(x) for k, x in v.items()}); return out
        return v
    return h(0)
```

Где лежат данные после декодирования:
- `/game/`: `pinia.pageData.pages[0].components[]` с `meta.componentName == "new-releases-carousel"`, у него `.data.items`
  (`game_index.html`). Ещё проще взять ссылки `href="/game/{slug}/"` внутри элемента с
  `aria-label="New Releases content"`.
- `/browse/game/all/all/all-time/new/?page=N`: `data["browse-game-/browse/game/all/all/all-time/new/"].items`
  (24 шт.) и `.total` (`browse_new_p1.html`).
- `/game/{slug}/`: `pinia.pageData.pages[0].components[]` с той же структурой, что у composer
  (`game_page_star-wars-galactic-racer.html`).
- `/game/{slug}/user-reviews/?platform=pc`: тут видно, какие backend-URL использует фронт (так и нашлись 2.3/2.4).

---

## 3. YouTube без API-ключа

### 3.1 Поиск

```
GET https://www.youtube.com/results?search_query={q}&sp=CAMSAhgC&hl=en&gl=US
Cookie: SOCS=CAI; CONSENT=YES+cb        # обходит страницу согласия для EU-IP
```
(`yt_search_*_over20min.html`, разобранные результаты в `yt_search_parsed.json`; ответ около 1.3 МБ за 0.5–0.7 с)

- Значения `sp`, снятые из `searchFilterGroupRenderer` самой страницы:
  `CAM%3D` = Prioritize: Popularity; `CAMSAhgC` = Popularity + длиннее 20 минут (лучший вариант для летсплеев);
  `CAMSAhgF` = Popularity + 3–20 минут; `CAMSAhAB` = Popularity + только видео.
- **Сортировки «по просмотрам» в поиске больше нет**, есть только «Popularity», и порядок по просмотрам
  не монотонный (проверено). Просмотры парсим сами и максимум выбираем локально.
- Запросы: `"{title} let's play"` и `"{title} прохождение"`, кандидаты объединяем по `videoId`.
- Парсинг: регэксп `var ytInitialData = (\{.*?\});</script>` → `json.loads` → рекурсивный обход, собирать каждый
  `videoRenderer`:

| Поле | Путь в `videoRenderer` |
|---|---|
| ID | `videoId` |
| Название | `title.runs[].text` (склеить). **Может быть автопереведено или обрезано:** в русскоязычном запросе пришло просто «Star Wars». Точное название брать со страницы просмотра (3.3) |
| Просмотры | `viewCountText.simpleText` → `"465,300 views"` (с `hl=en`) → `int(re.sub(r'\D','',s))`. Сейчас идущий эфир: `viewCountText.runs` → «N watching», такие пропускать |
| Длительность | `lengthText.simpleText` (`"1:42:50"`) |
| Канал | `ownerText.runs[0].text` |
| Дата | `publishedTimeText.simpleText` (`"4d ago"`, `"Streamed 6d ago"`) |

- Отбор «самого популярного летсплея»: (1) нормализованное название игры должно входить в название ролика
  (пересечение токенов ≥ 0.6; без этого в выдачу попал «Star Wars Episode I Racer Playthrough»);
  (2) длительность ≥ 10 мин; (3) отсеять `trailer`, `review`, `обзор`, `трейлер`; (4) приоритет названиям с
  `let's play|walkthrough|playthrough|gameplay|part 1|прохождение|летсплей`; (5) из оставшихся взять максимум
  просмотров. Пример: победил `bGBXM6oxbag` (theRadBrad, «Walkthrough Gameplay Part 1», 465 300 просмотров, 1:42:50).

### 3.2 Расшифровка: `youtube-transcript-api` (работает)

Версия 1.2.4 (`pip install youtube-transcript-api==1.2.4`, тянет за собой `requests` и `defusedxml`).
```python
api = YouTubeTranscriptApi()                 # можно передать proxy_config=GenericProxyConfig(...)
tl = api.list(video_id)                      # Transcript: language_code, is_generated, is_translatable
tr = tl.find_transcript(["en", "ru"])        # сначала ручные, потом автоматические
data = tr.fetch().to_raw_data()              # [{"text","start","duration"}, ...]
```
Замер на `bGBXM6oxbag`: в списке одна дорожка «English (auto-generated)», `list` занял 1.07 с, `fetch` 0.83 с,
вышло 1704 фрагмента и 52 690 символов на 1 ч 42 мин (`yt_transcript_api_bGBXM6oxbag_first200.json`).
Автосубтитры работают. Перед отправкой в LLM вычищать `[Music]`, `[Applause]` и т. п.

Ошибки (`youtube_transcript_api._errors`): `TranscriptsDisabled`, `NoTranscriptFound`, `VideoUnavailable`,
`VideoUnplayable`, `AgeRestricted`, `PoTokenRequired`, `RequestBlocked`/`IpBlocked`. Последние две типичны для
дата-центровых IP (Railway, AWS, GCP): **на задеплоенном сервисе расшифровка с большой вероятностью
заблокируется.** Прокси подключается через `GenericProxyConfig`, адрес можно брать из env, но это опционально.

### 3.3 Что не работает и чем заменить

- **timedtext со страницы просмотра не работает.** `ytInitialPlayerResponse.captions.playerCaptionsTracklistRenderer.captionTracks[].baseUrl`
  содержит `exp=xpe`. Запрос на него (`&fmt=json3`) возвращает **200 с пустым телом**: без PO-токена ничего
  не отдаётся (проверено). Этим путём не пользоваться.
- Зато со страницы просмотра `https://www.youtube.com/watch?v={id}&hl=en` (`yt_watch_bGBXM6oxbag.html`, регэксп
  `var ytInitialPlayerResponse = (\{.*?\});(?:var|</script>)`) удобно брать
  `videoDetails.title` (точное название), `viewCount` (точное число), `lengthSeconds`, `author`, `isLiveContent`,
  `shortDescription`. Можно использовать и для проверки выбранного ролика.
- Цепочка запасных вариантов: следующий кандидат (до 3 штук) → если расшифровок нет ни у одного, заключение
  строится по `title + shortDescription` ролика с пометкой `method = "description"` → если нет и этого,
  сохраняем только ссылку на ролик с пометкой «расшифровка недоступна». Тяжёлый вариант (yt-dlp + Whisper)
  в базовую поставку не включать.

---

## 4. LLM (бесплатный анонимный эндпоинт)

```
POST https://text.pollinations.ai/openai
Content-Type: application/json
{"model": "openai", "messages": [...], "temperature": 0.2}
```
(`llm_pollinations_response.json`, `llm_pollinations_letsplay_9k.json`)

- Ответ в формате OpenAI chat.completion: `choices[0].message.content`, плюс `choices[0].message.reasoning`
  (рассуждения модели, игнорировать), `finish_reason`, `usage`, `model`, `user_tier: "anonymous"`.
- `"model":"openai"` на деле обслуживает **`gpt-oss-20b`** (reasoning-модель), так что рассуждения съедают
  completion-токены: на 131 токен промпта пришлось 444 токена ответа.
- Задержка от 7.7 до 17.2 с: 10.2 с на маленький JSON-промпт, 7.7 с на 6 354 символа расшифровки (1693 токена),
  17.2 с на вопрос в одно слово. Время ответа сильно плавает, заголовков rate-limit нет.
- Подвохи: (1) JSON возвращается внутри ```` ```json ... ``` ````, ограждение надо снимать перед `json.loads`;
  (2) без явной инструкции модель пишет от первого лица блогера и пересказывает спонсорскую вставку, поэтому
  в промпте нужно «в третьем лице, игнорируй рекламу и просьбы подписаться»; (3) расшифровку в 50 тыс. символов
  целиком не отправлять: брать 3 куска (начало, середина, конец) общим объёмом ≤ 8–9 тыс. символов или делать
  map-reduce с паузами.
- Вызовы строго последовательные, пауза 3–5 с, 3 ретрая с backoff, таймаут 120 с. При неудаче строится
  экстрактивное резюме (самые частые содержательные фразы из positive/negative отзывов), в БД пишется
  `summary_method = "llm" | "extractive"`.
- Настройки через env `LLM_BASE_URL` (по умолчанию `https://text.pollinations.ai/openai`), `LLM_API_KEY`
  (по умолчанию пустой, заголовок `Authorization` тогда не отправлять), `LLM_MODEL` (по умолчанию `openai`).
  Работают оба адреса, `…/openai` и `…/openai/chat/completions` (проверено, оба 200), поэтому подходит
  стандартная схема OpenAI-клиентов «base_url + `/chat/completions`».

---

## 5. Похожие игры (данные для локального подбора)

Сторонний API не нужен: всё есть в `product`. Это `genres[].name`, `gameTaxonomy.franchises[].name`,
разработчик, платформы и `description` (TF-IDF / косинусная близость). Хватит взвешенной суммы: франшиза >
разработчик > жанр > близость описаний.

---

## 6. Файлы-примеры

| Файл | Что это |
|---|---|
| `game_index.html` | `/game/` целиком (SSR + `__NUXT_DATA__`) |
| `finder_new_releases.json` | 20 игр New Releases |
| `browse_new_p1.html`, `finder_browse_new_p1.json` | SEE ALL, сортировка «новые», страница 1: HTML и JSON |
| `composer_game_star-wars-galactic-racer.json` | композитный ответ карточки (основной источник) |
| `product_star-wars-galactic-racer.json`, `product_niph.json`, `product_404.json` | product: мультиплатформа / без рецензий / несуществующий slug |
| `critic_summary_*.json`, `user_summary_*.json` | summary-выборки 7/7/7 (у niph пустые) |
| `critic_reviews_*_ps5.json`, `user_reviews_*_ps5.json`, `critic_reviews_*_allplatforms.json` | ReviewList с пагинацией |
| `user_stats_*_platform_pc.json`, `user_stats_*_query-platform-IGNORED.json`, `user_stats_star-wars-galactic-racer.json`, `user_stats_niph_platform_pc.json` | Userscore по платформам и подвохи |
| `game_page_*.html`, `user_reviews_page_*_pc.html` | HTML страниц игры для запасного парсера |
| `yt_search_*.html`, `yt_search_parsed.json` | выдача YouTube и разобранные кандидаты |
| `yt_watch_bGBXM6oxbag.html` | страница просмотра (`ytInitialPlayerResponse`) |
| `yt_transcript_api_bGBXM6oxbag_first200.json` | первые 200 фрагментов автосубтитров |
| `llm_pollinations_*.json` | два реальных ответа LLM с задержками |
| `metacritic_robots.txt`, `backend_robots.txt` | robots.txt |
