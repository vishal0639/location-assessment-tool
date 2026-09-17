# Location Assessment Tool

An internal tool that replaces the "is this location worth pursuing?" spreadsheet. An analyst submits a US address or lat/lon. A background worker then pulls facts from public data sources, scores them against written rules, and keeps every raw figure. When a source is down, the report says so instead of leaving a blank cell or inventing a zero.

- **Scoring rules in plain English:** [SCORING.md](SCORING.md)
- **Decisions and rejected alternatives:** [DECISIONS.md](DECISIONS.md)

---

## Run it

Requires Docker with Compose v2. Nothing else: no local Python or Node needed.

```bash
docker compose up --build -d                  # Postgres, API, worker, web
docker compose exec api python -m app.seed    # optional: queue 6 real demo locations
```

Open **http://localhost:8080**. The API docs are at http://localhost:8000/docs.

Seeded locations reach a score within a few seconds each, because the worker calls the real public APIs. One of the six is a deliberately bad address, to show a failed geocode. Stop with `docker compose down`, or `docker compose down -v` to also wipe the database.

**Tests** (66, about 5 s; they need the database container running):

```bash
docker compose run --rm api pytest -q
```

### Poke the API by hand

[http/backend.http](http/backend.http) contains every endpoint, including the error cases. Each request is annotated with the response to expect, so a mismatch shows where the problem is. Open it in VS Code with the REST Client extension and click "Send Request". Start with:

- `GET /api/health`, which checks the API, the database and the queue backlog;
- `GET /api/diagnostics/sources`, which calls every upstream live and lists the failing ones with the reason.

[http/upstream-sources.http](http/upstream-sources.http) calls the four public sources directly. Use it to tell "the source is down" apart from "our code is wrong".

### Break a source and watch what happens

Every source URL can be overridden from the shell. Point one at a host that doesn't exist, restart the worker, and submit something:

```bash
USGS_EPQS_URL=https://epqs.invalid/v1/json docker compose up -d worker
# submit a location in the UI, then open it:
#   -> status "Partial - data missing", Elevation marked UNAVAILABLE with the DNS error,
#      points "not counted", verdict "review" with the possible score range stated
docker compose up -d worker   # restore
```

`CENSUS_GEOCODER_URL`, `OPEN_METEO_URL` and `FEMA_NFHL_URL` work the same way. Per-source timeouts are in [backend/app/config.py](backend/app/config.py).

### List at scale

```bash
docker compose exec api python -m app.seed --synthetic 10000
```

This inserts 10,000 rows labelled `synthetic-NNNNN`, with random scores and no factors. They never touch an upstream API and exist only to exercise server-side sorting, filtering and paging. On my machine every list query stayed under 80 ms through nginx.

---

## What works

All five core requirements, end to end:

1. **Submit.** `POST /api/assessments` validates input (address *or* a complete lat/lon pair, never both), inserts the assessment plus a `pending` run in one transaction, and returns `202` in about 20 ms. It makes no third-party calls, and a test enforces that.
2. **Enrich.** A separate worker process claims pending runs from Postgres (`FOR UPDATE SKIP LOCKED`). For an address it geocodes via the US Census Geocoder first. It then calls three independent sources in parallel:
   - **USGS EPQS** for elevation
   - **Open-Meteo archive** for hot days and precipitation over the last full year
   - **FEMA NFHL** for flood zone

   Every call is stored in `source_fetches` with its URL, HTTP status, timing, error kind and raw payload.
3. **Score.** Pure functions in [scoring.py](backend/app/scoring.py) produce a 0–100 score and four factors, each with a derived value, raw value, points and the rule applied. The rules are also written up in plain English in [SCORING.md](SCORING.md).
4. **Review.**
   - The list has server-side sort (label, status, score, date), filters (text, status, effective verdict) and paging. Filter state lives in the URL.
   - The detail page shows each factor's derived value, raw value, points, rule, source and fetch time, plus a table of every source call with the raw response.
   - Analysts can override with a verdict and a required reason. The machine score and verdict stay on screen beside the override, and the override history is kept.
5. **Survive a source failing.** Timeouts, 429s, 5xx, non-JSON responses, and valid-but-nonsense payloads all become an **unavailable** factor:
   - Its points are `NULL`, enforced by a DB constraint, and it is left out of the denominator.
   - The run is marked **partial**, and a yellow banner names each missing factor and why.
   - The verdict won't say pursue or reject unless the missing data couldn't change it.
   - Below 50% coverage there is no score at all.

**Optional features built (two):** re-running enrichment with history kept, and parallel source calls with per-source timeouts. The reasons are in DECISIONS.md #5 and #6.

Also verified by hand against the running stack, beyond the tests:
- the broken-URL scenario above;
- a run orphaned by a "crashed" worker (status `running`, expired lease) is reclaimed and finished;
- nginx keeps routing after the API container is recreated. This was a real bug I hit and fixed.

## What happened with the real sources today

The brief said one source might be flaky. Two were:

- **FEMA NFHL was unreachable all day** from the build machine: TCP connected, then the TLS handshake was reset (`curl: Recv failure: Connection was reset`). So **every live assessment I ran is "partial"** with flood zone unavailable. That is the app working as designed, but it also means **I never saw a successful FEMA response live**. The FEMA parser and the flood-zone rules are tested against payloads built from the documented ArcGIS REST format and NFHL field names (`FLD_ZONE`, `ZONE_SUBTY`, `SFHA_TF`), not a captured response. If FEMA is reachable from your network, that path is the least proven part of the code.
- **The US Census Geocoder returned HTTP 502** (after about 11 s) for the first two seeded addresses. Those runs failed cleanly with the reason shown. Re-running them later succeeded, and the failed run stayed in the history. This outage is why transient 502/503/504 errors now get exactly one retry.

## What I left out, and why

| Left out | Why |
|---|---|
| **Auth / per-user scoping** | A handful of internal analysts. The override's "analyst" field is free text remembered in localStorage, which is **not** identity. Real auth would sit in front (SSO proxy), and `analyst` would come from the session. |
| **Caching upstream responses** | It works against re-run, whose purpose is fetching *fresh* data after an outage. The volume (a few hundred runs a day) is well within published limits. |
| **Map view, CSV export** | They add screens, not correctness. CSV would be about 20 lines on top of the existing list query. |
| **Alembic migrations** | `create_all` is fine with no deployed data. It's the first thing to add before real use (see next steps). |
| **Automatic retry of partial/failed runs** | Re-run is manual. Auto-retrying on a schedule is easy with this model, but choosing the policy (how often, and whether it should replace a run an analyst already overrode) is a product call I didn't want to guess at. |
| **Nominatim fallback geocoder** | The Census geocoder is a single point of failure for address input: a failed geocode means a failed run. Coordinates input bypasses it. A fallback needs care with Nominatim's 1 req/s policy. |
| **Frontend tests** | The UI only renders what the API returns. The honesty rules (null not zero, partial status, verdict logic) live in the backend and are tested there. |
| **Visual polish, mobile layout** | Explicitly out of scope. |

## Known limitations and edge cases

- **Label/address search is `ILIKE '%q%'`**, a sequential scan. It's fine at 10k rows (about 30 ms); at around 1M it needs `pg_trgm`.
- **The list count** (`SELECT count(*)` over the filtered query) runs on every page load. It's fine at 10k; at scale, cap or estimate it.
- **The worker processes one run at a time.** That covers a few hundred a day (a run takes about 1–3 s when sources respond; the worst case is about 40 s, a slow geocode followed by a hanging FEMA). You can scale with `docker compose up --scale worker=3`, since `SKIP LOCKED` makes that safe. There is no *global* rate limiter across workers, so many workers could exceed a source's limit.
- **Lease timeout is 5 minutes.** A run on a crashed worker waits that long before it is reclaimed.
- **The same-year weather window** means a re-run on 1 January uses a different year than one on 31 December.
- **Out-of-US coordinates** are accepted and come back partial or unscored, rather than being rejected up front.
- **Raw payloads are stored in full** (Open-Meteo is about 15 KB per run). That's fine for years at this volume, but at 50× volume it needs a retention policy.
- **The frontend polls** (every 2–3 s) only while something on screen is pending or running. There are no websockets.
- **Scoring version** is recorded per run, but nothing re-scores old runs when the rules change. That's deliberate: history should show what was decided at the time.

## With another week

1. Alembic migrations, plus a `/api/health` that also reports worker liveness (a heartbeat row) and queue age. The endpoint already returns pending count and oldest pending time.
2. Per-source circuit breaker: after N consecutive failures, skip that source for M minutes and mark factors unavailable immediately, instead of making every run wait for a timeout.
3. Scheduled automatic re-run of partial runs once the missing source recovers, surfaced to analysts as "new data available".
4. Nominatim fallback geocoding, with a global 1 req/s limiter.
5. Real auth via an SSO proxy, with `analyst` taken from the session.
6. A small E2E test (Playwright) for submit → partial banner → override.

## Scale and operations notes

- **At 50× volume** (about 15k runs a day, about 10 a minute), Postgres and the queue are fine. The limits are upstream rate limits (Open-Meteo's free tier is 10k calls a day, and Nominatim would be 1 req/s) and raw payload storage. You'd need a shared per-source rate limiter, response caching keyed on rounded coordinates, and payload retention or offloading to object storage.
- **What to monitor:**
  - oldest pending run age (the worker is stuck or dead);
  - failure rate per source and `error_kind` over the last hour (`source_fetches` already has it; this is one `GROUP BY`);
  - share of runs that are partial;
  - p95 run duration;
  - worker restarts.
- **How you'd find out at 3am:** alert when the oldest pending run is more than 5 minutes old, or when any source's error rate stays above 50% for 30 minutes. The first catches a dead worker; the second catches an upstream outage before analysts notice everything turning partial.

---

## Layout

```
docker-compose.yml        db (Postgres 16), api (FastAPI), worker (same image), web (nginx + built React)
backend/app/
  main.py                 HTTP API. Reads and writes Postgres only, never calls upstreams
  worker.py               claims runs (SKIP LOCKED + lease), persists results
  pipeline.py             geocode -> parallel sources -> score. No DB, so it's testable
  scoring.py              every scoring rule, pure functions
  sources/base.py         fetch_json: timeouts, retry, error classification. Never raises
  sources/{census,usgs,open_meteo,fema}.py   one module per upstream: URL, params, sanity checks
  models.py               assessments / enrichment_runs / source_fetches / factor_results / verdict_overrides
  seed.py                 demo locations, or --synthetic N
backend/tests/
  test_scoring.py         band boundaries, null-not-zero, partial-verdict logic, knock-out
  test_failure_paths.py   each failure mode classified; pipeline partial/failed; parallelism
  test_api.py             submit is instant and offline; override keeps machine verdict; re-run keeps history
frontend/src/
  pages/ListPage.tsx      submit form + list (URL-driven filters/sort/paging)
  pages/DetailPage.tsx    banners, score vs. override, factors, source calls, override form
```

Stack choices: **FastAPI** (the brief's preference). **SQLAlchemy 2 + psycopg 3**, sync in the API; the async part is only the outbound HTTP. **Vite + React + TypeScript + react-router**, with no state library, because two pages with polling don't need one. **Plain CSS.**

---

## AI usage

> **TODO (candidate): rewrite this section in your own words before submitting. Only you can say where you overrode the assistant and how you checked its work, and the brief scores that honesty directly.** The notes below are a factual log of what happened during the AI-assisted build, to start from.

- **Where the assistant helped most:** it generated most of the code (backend, tests, frontend) and the first drafts of these documents, working from a plan I gave it: FastAPI, a React/TS frontend, a real relational DB, the five core requirements, and honest scope cuts.
- **Where its first output was wrong, and what caught it:**
  - My initial brief suggested SQLite and "simulating" failures. We switched to Postgres (for `SKIP LOCKED`), and there was nothing to simulate because FEMA and Census really failed.
  - The DB-backed tests failed at first, because the circular foreign keys between assessments and runs had no constraint names, so their tables couldn't be dropped.
  - The test guard meant to catch "the API calls the network" also blocked the test client's own HTTP transport.
  - The list's tie-break ignored sort direction.
  - After recreating the API container, nginx returned 502 because it had cached the old container IP.
  - The test fakes' docstring claimed every payload was "copied from real responses", which wasn't true for FEMA.
  - Network errors were recorded with an empty message.
  - The FEMA outage and the Census 502 only showed up by running against live APIs. The Census 502 is why the single-retry logic exists.
- **How the output was checked:**
  - 66 automated tests.
  - A live run of all seeded locations against real APIs.
  - Deliberately breaking the USGS URL and confirming the UI and API reported it.
  - 10k synthetic rows to time the list queries.
  - Faking a crashed worker's orphaned run and confirming it was reclaimed.
  - The README instructions run from a clean copy of the repo.
  - *(Add your own review: which files you read line by line, what you changed.)*

## Time spent

> **TODO (candidate): fill in your actual time.** The brief compares what was built against the time reported, so this has to be accurate.
