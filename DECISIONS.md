# Decisions

**1. The queue is a Postgres table, claimed with `FOR UPDATE SKIP LOCKED`. I rejected FastAPI `BackgroundTasks` and Celery/Redis.**
`BackgroundTasks` runs inside the API process, so a deploy or crash silently loses in-flight work, and nothing records that the work ever existed. Celery plus Redis would add two moving parts for a team of three. The `enrichment_runs` row *is* the job. A worker that dies mid-run leaves a lease that expires after 5 minutes, and another worker picks the run up again. After 3 attempts the run is marked failed instead of looping forever. At a few hundred runs a day, polling once a second costs nothing.

**2. Every enrichment is a separate *run*. Results are never written onto the assessment itself.**
The alternative was score columns on `assessments`, which a re-run would overwrite. That model can't answer "why did we reject this in March?" once the data changes. With runs, each re-run adds a row, and the raw payload of every source call is kept (`source_fetches.raw_payload`), so an old verdict can always be traced back to its figures. This is also why re-run was nearly free to build.

**3. A missing value is `NULL`, is left out of the denominator, and the verdict only commits when the missing data couldn't change it.**
I rejected the two obvious alternatives. Scoring a missing factor as 0 penalises a location for FEMA having an outage. Normalising the score and still issuing "pursue" makes a partial report look complete. The rule I chose: take the worst and best case for the missing factors, and if they land in different verdict bands, the answer is "review" with the range stated. The same principle holds at every layer: a DB `CHECK` constraint (`points IS NULL` exactly when the factor is unavailable), the API returning `null`, and a UI banner that names the missing factors.

**4. Sources fail by returning a value, not by raising an exception.**
`fetch_json` never raises. A timeout, 429, 5xx, HTML page, ArcGIS "error inside HTTP 200", or implausible value all come back as a `SourceResult` with an `error_kind`. The alternative was try/except in the orchestrator, where one forgotten `except` lets one source take down the whole run. Sanity checks live in each source's parser. For example, USGS's `-1000000` no-data value is caught before it could ever be scored as an elevation of −1,000,000 ft.

**5. Source calls run in parallel with per-source timeouts, plus one retry for gateway errors only.** *(optional feature #1)*
I built this optional because a run's duration is otherwise the *sum* of every source's worst case (FEMA alone can hang for 20 s); in parallel it's the *max*. The timeout wraps the whole call, not just httpx's per-phase timeouts, so a server that trickles bytes can't stretch it. There is one retry, after 1 s, for 502/503/504 or dropped connections, because the Census geocoder returned a 502 during the build. 429s and timeouts are never retried: hammering a free service that asked us to back off is rude, and retrying a timeout doubles the wait.

**6. Re-run keeps history.** *(optional feature #2)*
I picked this over caching, maps and CSV export because it tests the data model rather than adding a screen, and because the upstreams really are flaky: a location that failed at 10:00 because FEMA was down needs a way back. Overrides record the run they were made against, and the UI flags when a newer run exists. I didn't build caching. With re-runs meant to get *fresh* data, a cache would work against that, and at a few hundred calls a day we are well within every source's published limits.

**7. Overrides are append-only, with a `current_override_id` pointer. The machine verdict on the run is never modified.**
The alternative was an editable "verdict" column on the assessment, which loses the machine's opinion and every earlier human opinion. A non-blank reason is enforced by both the API and a DB `CHECK`; the analyst name is required by the API.

**8. Postgres over SQLite.**
SQLite would have been fine for the data volume, but the design relies on `SKIP LOCKED` for concurrent workers, and it has two processes (API, worker) writing at once. Docker Compose makes Postgres no harder for a reviewer to run.

**9. `latest_run_id` and `current_override_id` are denormalised onto `assessments`.**
The list view then needs two indexed joins instead of a "latest run per assessment" window query. With 10,000 synthetic rows, sorting by score, deep offsets and combined filters all return in under 80 ms through the full stack. The cost is that the pointers must be updated in the same transaction as the insert, and they are.

**10. Scoring is pure functions in one file, with the English version in `SCORING.md`.**
Scoring has no DB, no HTTP and no clock, so all 36 scoring tests run in milliseconds and pin down every band boundary. Unknown values (e.g. an unrecognised FEMA zone code) become *unavailable* rather than taking a guessed score. Runs record `scoring_version` so a rule change doesn't silently reinterpret old runs.

## Smaller calls on ambiguous parts of the brief

- **Coordinates outside the US** are accepted, not rejected. The US-only sources report no data, and the run comes back partial or unscored. That is an honest outcome, and it avoids maintaining a US boundary check.
- **A failed geocode** means a *failed* run with no score, not a zero score. Without coordinates there is nothing to assess. The error names the cause (no match vs. geocoder down), and the analyst can re-run.
- **Weather uses the last full calendar year**, not "the last 365 days", so two runs in the same year see the same inputs.
- **"Verdict"** is pursue / review / reject. The brief doesn't define it, and a three-way answer fits a triage workflow where "review" is the honest answer to partial data.
- **Tables are created with `create_all`, not Alembic migrations.** It is fine for a prototype with no deployed data; it's the first thing I'd change (see README).
