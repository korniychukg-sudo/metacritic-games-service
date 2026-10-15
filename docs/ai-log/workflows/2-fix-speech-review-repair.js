
const NOWAIT = `Never wait for the hourly scheduler in real wall-clock time: test scheduling and day-rollover logic with unit tests and injected clocks, or by calling the run function directly.`

const PROBLEMS = {
  type: 'object',
  properties: {
    problems: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          where: { type: 'string' },
          what: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'where', 'what', 'fix'],
      },
    },
    summary: { type: 'string' },
  },
  required: ['problems', 'summary'],
}

phase('Fix')
const fixed = await agent(`${TZ}

${RULES}
${NOWAIT}

The service is built and committed (commit f2dc1f9, 54 tests green). The goal now is a submission that closes every requirement of the test task completely and makes the employer want to hire the candidate. Time is not a constraint; completeness and correctness are.

CODE REVIEW of f2dc1f9:
${CR}

TASK:
1) Requirements pass: walk every item of the ТЗ above against the code, the existing local database (data/games.db) and the pages. Anything partial or missing gets implemented properly.
2) Fix every item of the code review, 1 through 19, and close every listed test-coverage gap with real tests (replace the assertions that check nothing). Verify each item first; if one is truly a false alarm, say why in your report.
   Specifics: remove VOLUME from the Dockerfile and document a Railway volume at /data with DB_PATH=/data/games.db; non-root user; PORT from env; --forwarded-allow-ips="*". Force-run protection that still lets reviewers press the button: cooldown about 5 minutes after the last run ends with the remaining time on the button, same-origin check, 429 with Retry-After, optional RUN_TOKEN for the JSON endpoint, daily cap. LLM: 402 from Pollinations is a rate limit (back off, never shrink), shrink only on 413 and restore after success, quote-based summary for the rest of the run after about 3 consecutive failures, run stops taking new games after about 50 minutes. Schema migrations with PRAGMA user_version. Upgrade FastAPI/Starlette past CVE-2025-62727 and pin the full dependency tree (requirements.txt generated from a resolved lock or pip freeze of the venv). CSP and X-Content-Type-Options with inline scripts moved out. Retry failed games the same day (capped). Schema-drift detection that raises instead of silently returning nothing, and never overwriting good stored data with empty data. Let's play refresh periods per status. Backfill that cannot be blocked by one game. tzdata, log timestamps in the configured zone. try/finally run bookkeeping. SQLite index on errors.day, pruning, cached status payload, connection handling. LIKE escaping, page bounds. Redact visitor_data in samples. esc() in app.js.
3) Run the full test suite until green.
4) Smoke test: a fresh local end-to-end run with BATCH_SIZE=5 on a fresh database file against live Metacritic, then confirm the list, a card, the monitoring page and the force-run cooldown work. Stop the server.
5) Commit in logical commits with clear messages.
Return: list of fixes per review item, anything deliberately left with reasons, final test count, smoke numbers (games processed, critic and user summaries by LLM vs fallback, let's plays found and by which method), and the commit hashes.`, { label: 'fix', phase: 'Fix' })

phase('Speech')
const speech = await agent(`${TZ}

${RULES}
${NOWAIT}

Previous step report:
${fixed}

Context: the let's play feature turns the blogger's speech into text using YouTube captions (youtube-transcript-api), and falls back to the video description when captions are missing. The ТЗ asks to "перевести рассказ блогера в текст", so add a real speech-recognition fallback.

TASK:
- When a chosen video has no usable captions, download only the audio of the first part of the video (about 15-20 minutes is enough for a conclusion) with yt-dlp (audio-only format, no system ffmpeg required) and transcribe it locally with faster-whisper (CPU, int8, a small model such as "base" or "small"; choose by measured speed on this machine). Record method="whisper" so the card shows how the text was produced.
- Make it configurable by env (STT_ENABLED default on, STT_MODEL, STT_MAX_MINUTES) and fail soft: any error falls back to the existing description path, never breaks a run. Keep the audio in a temp dir and delete it.
- Keep the Docker image reasonable (no CUDA wheels) and document memory and time costs in the README.
- Add tests with the network and model mocked.
- Prove it on one real game locally: force the captions path to be skipped for that game and show the whisper transcript length and the LLM conclusion.
- Run the whole test suite, commit.
Return: what was added, measured transcription speed, test count, commit hash.`, { label: 'speech', phase: 'Speech' })

phase('Review')
const LENSES = [
  { key: 'requirements', prompt: `Requirements lens. For every single item of the ТЗ (each data field, each UI feature, each selection rule including the daily restart and the SEE ALL paging, both additional parts, the deliverables), find the concrete evidence in code and in the running app with the existing local database, and report anything missing, partial, wrong or unconvincing to an employer.` },
  { key: 'code', prompt: `Code lens. Senior review of the current HEAD versus f2dc1f9 and of the whole codebase: correctness, concurrency, restart safety, error handling, schema drift, data loss, test quality (tests that assert nothing, missing edge cases), dead code, leftover debug output.` },
  { key: 'ux', prompt: `Product and UI lens. Start the app on your own free port with a copy of the existing database and scheduled runs disabled. Request every page and API endpoint, read the templates, CSS and JS. Judge as a hiring manager: Russian text quality and consistency, empty states, error states, mobile layout (viewport meta, overflow), the monitoring page updating live, the card readability (summaries, platforms table, let's play block, similar games links), README clarity (architecture, how to run, decisions, honest limitations, requirements checklist). Report concrete fixes.` },
  { key: 'security-deploy', prompt: `Security and deploy lens. Secrets or personal data in tracked files and git history (grep for emails, tokens, home paths, phone numbers, visitor ids), XSS, CSP, force-run abuse, dependency CVEs, Dockerfile and railway.json correctness for Railway (no VOLUME, PORT, healthcheck, non-root, volume mount at /data documented), image size, start command.` },
]
const reviews = await parallel(LENSES.map(l => () => agent(`${TZ}

${RULES}
${NOWAIT}
Do not modify any tracked file and do not commit. Do not scrape Metacritic or YouTube and do not call the LLM. Stop any server you start.

What was done so far:
FIX STEP: ${fixed}
SPEECH STEP: ${speech}

${l.prompt}
Return only real problems with exact file and line and a concrete fix; severity blocker means it would make the employer reject the submission.`, { label: `review:${l.key}`, phase: 'Review', schema: PROBLEMS })))

let open = reviews.filter(Boolean).flatMap((r, i) => r.problems.map(p => ({ ...p, lens: LENSES[i].key })))
log(`${open.length} problems from reviews`)

let repairs = []
for (let round = 1; round <= 3 && open.length; round++) {
  phase('Repair')
  const rep = await agent(`${TZ}

${RULES}
${NOWAIT}

Problems found by independent reviewers (round ${round}):
${JSON.stringify(open, null, 2)}

TASK: verify each problem; fix every real one; for a false alarm, explain why. Keep tests green and add tests where logic changes. Commit. Return per problem: fixed or false alarm with reason, and the commit hash.`, { label: `repair:${round}`, phase: 'Repair' })
  repairs.push(rep)
  const check = await agent(`${TZ}

${RULES}
${NOWAIT}
Do not modify tracked files. Do not scrape or call the LLM.

These problems were reported:
${JSON.stringify(open, null, 2)}

The repairer says:
${rep}

TASK: independently check each problem against the current HEAD (read the code, run the tests, request the pages on your own port with a copy of the database). Return only the problems that are still real, plus any new problem the repair introduced.`, { label: `recheck:${round}`, phase: 'Repair', schema: PROBLEMS })
  open = check ? check.problems.filter(p => p.severity !== 'minor' || round < 3) : []
  log(`round ${round}: ${open.length} problems remain`)
}

phase('Live')
const live = await agent(`${TZ}

${RULES}

TASK: final live proof on this machine.
1) Start the service on a fresh database file (data/live.db) with default settings (BATCH_SIZE=20) and let the automatic first run process the 20 New Releases with live Metacritic, YouTube and the LLM. Wait for it to finish (expect 30-60 minutes because of the free LLM rate limit); watch the log, not the clock.
2) Then trigger the second batch through the force-run endpoint the way a reviewer would (respect the cooldown; if the cooldown is long, call the pipeline's run function directly in-process instead of changing settings) and let it process the next 20 games from the SEE ALL listing, proving the paging and the "not yet processed today" rule.
3) Check the result like a reviewer: list with search, platform filter and sorting; three different cards (summaries for critics and users, platforms with Metascore and Userscore, developer, description, video link, let's play conclusion with link and transcript method, similar games that open); the monitoring page with counters and history.
4) Stop the server. Leave data/live.db in place (do not commit it if data files are git-ignored).
5) Update the README section with these real numbers (games, LLM vs fallback summaries, let's plays by method, run durations) and commit.
Return the numbers, run durations, any errors seen and how they were handled, and the final commit hash.`, { label: 'live', phase: 'Live' })

return { fixed, speech, reviews, repairs, remaining: open, live }
