# Trial — Apify reliability & cost monitor

A one-week instrumented trial before committing to the full [plan](../plan.md).
It scrapes Bangalore buy listings daily from three free Apify actors
(MagicBricks, 99acres, NoBroker), tracks changes, and serves a dashboard with
the exact numbers the design-vs-pricing decision needs:

- per-source **success rate**, volume stability, run duration
- **actual Apify spend** per day + projected 30-day cost
- **field coverage** (are price/GPS/area/RERA present in real data?)
- new / price-drop / removed / relisted counts (the future product's signals)
- **zero LLM calls** — actors return structured fields; parsing is deterministic

Architecture mirrors `stockquery`: FastAPI + APScheduler + SQLite, uvicorn in
Docker, token-auth single-page dashboard.

## Setup

```sh
cd trial
copy .env.example .env      # then edit:
#   APIFY_TOKEN      — console.apify.com -> Settings -> API & Integrations (free account)
#   DASHBOARD_TOKEN  — any long random string (empty = no auth, local dev only)
```

### Run in Docker (VPS — the intended mode)

```sh
docker compose up -d --build
# dashboard: http://<vps>:8010/?token=<DASHBOARD_TOKEN>
```

The scheduler scrapes daily at 06:30 IST (configurable via `SCRAPE_HOUR` /
`SCRAPE_MINUTE`). The **Run scrape now** button on the dashboard triggers a
cycle immediately — do one right after starting to seed day 1.

### Run locally (no Docker)

```sh
pip install -r trial/requirements.txt        # from repo root
python -m uvicorn trial.main:app --port 8010
```

### Offline test (no Apify token needed)

```sh
python -m trial.cli ingest-fixture    # feeds a saved real MagicBricks sample
python -m trial.cli report            # regenerates reports/trial-summary.md
```

## Reading the results (after ~7 days)

The dashboard banner shows the verdict flags; `reports/trial-summary.md` keeps
the markdown record. Decision guide:

| Observation | Call |
|---|---|
| All success rates ≥90%, coverage good, cost acceptable | Free actors are fine — proceed to Phase 1 on this stack |
| One source unreliable | Swap in its paid fallback actor (plan.md §4 table) and re-measure |
| Volumes erratic / fields missing | Investigate before building more; raw payloads are stored for re-parsing |

Costs: the first verified MagicBricks run was 15 items = **$0.0005**. Even at
300 items × 3 sources daily, expect cents per day — the projection tile on the
dashboard shows the real number.

## Notes

- `MAX_RESULTS=300` per source initially; raise it in `.env` once cost is
  confirmed negligible (restart the container after changes).
- An **anomalous** run (volume < 50% of trailing average) still stores data but
  never marks listings as removed — protects days-on-market tracking.
- 99acres/NoBroker field mappings are candidate-based until their first real
  run; if their coverage row shows gaps, the mapping in `normalize.py` needs a
  key added (raw data is preserved, so nothing is lost).
