# Atlas

Personal AI operating system for Karnataka real estate investing — Bangalore
first, Mysore data-ready. Vision: [overall_plan.md](overall_plan.md) · Roadmap:
[atlas_roadmap.md](atlas_roadmap.md) · Launch point: [handoff.md](handoff.md).

## Layout

- `atlas/` — FastAPI app, ingestion pipeline (raw → parsed → stored), scoring, and the daily briefing
- `atlas/scoring/` — Deal Score: versioned weights, cited factors, seller-motivation extraction
- `alembic/` — migrations (DDL source of truth; designed in [docs/schema.sql](docs/schema.sql))
- `deploy/compose-snippet.yml` — the `atlas-db`/`atlas-app` services to paste into your own compose stack
- `scripts/backup.sh` — nightly `pg_dump` (cron on the VPS)
- `tests/` — golden-fixture parser tests + end-to-end pipeline, scoring and digest tests
- `trial/` — retired spike, reference only (`trial/sources/rera.py` was ported in Phase 1)

## What it does daily (Asia/Kolkata)

| Time | |
|---|---|
| 05:30 | Karnataka RERA registry |
| 06:00 | Portals (MagicBricks Bangalore + Mysore; 99acres plots when enabled) |
| 06:45 | Staleness sweep + legal-risk tagging |
| 07:00 | Deal Score pass |
| 07:15 | The daily briefing by email |

```sh
.venv\Scripts\python -m atlas.cli gate            # Phase-1 gate: consecutive clean days
.venv\Scripts\python -m atlas.cli plan            # capital plan: cash bar + countdown
.venv\Scripts\python -m atlas.cli score --dry-run # score distribution, writes nothing
.venv\Scripts\python -m atlas.cli top --limit 10  # ranked listings with evidence
.venv\Scripts\python -m atlas.cli digest --dry-run
```

Full command list and the rules that constrain changes: [CLAUDE.md](CLAUDE.md).

## Local development (Windows box, no Docker)

```sh
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest
```

Tests spin up a **throwaway Postgres** automatically — no Docker or local
Postgres install needed (portable binaries in `.pgbin/` on Windows, `pgserver`
on Linux/macOS). To test against a real Postgres instead, set
`ATLAS_TEST_DATABASE_URL` (CI does this with a pgvector service container).

To run the app locally against a persistent local database:

```powershell
scripts\dev.ps1     # starts portable Postgres (.pgdata), migrates, runs uvicorn --reload
```

Then open http://127.0.0.1:8000/docs (bearer token defaults to `dev-token`).
Stop Postgres later with `.pgbin\pgsql\bin\pg_ctl -D .pgdata stop`.

## VPS deploy — image-only

The dev box has no Docker, so nothing is built on it or on the server. CI
publishes to `ghcr.io`; the VPS holds **no source**, just its own compose file
with [deploy/compose-snippet.yml](deploy/compose-snippet.yml) pasted in as
`atlas-db`/`atlas-app`, routed by the user's existing reverse proxy.

```sh
docker compose pull atlas-app && docker compose up -d atlas-app
```

Full runbook — env keys, first-deploy ordering, verification, backups,
watchdog: **[docs/deploy-vps.md](docs/deploy-vps.md)**.

- App boots via `alembic upgrade head`; no published host port (the proxy holds TLS).
- Every endpoint except `GET /health` requires `Authorization: Bearer $ATLAS_API_TOKEN`; an *unset* token locks the API (503) rather than opening it. The one exception is the emailed 👍/👎 link, which carries a per-link HMAC instead and fails closed with no secret set.
- Nightly backups: add the `scripts/backup.sh` cron line; test restores monthly.
- Dead-man's switch (healthchecks.io) is wired to the digest job — it pings only on successful delivery, so a missed ping means the briefing did not arrive.
