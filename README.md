# Atlas

Personal AI operating system for Karnataka real estate investing — Bangalore
first, Mysore data-ready. Vision: [overall_plan.md](overall_plan.md) · Roadmap:
[atlas_roadmap.md](atlas_roadmap.md) · Launch point: [handoff.md](handoff.md).

## Layout

- `atlas/` — FastAPI app + ingestion pipeline (raw → parsed → stored)
- `alembic/` — migrations (DDL source of truth; designed in [docs/schema.sql](docs/schema.sql))
- `deploy/` — Caddyfile (HTTPS)
- `scripts/backup.sh` — nightly `pg_dump` (cron on the VPS)
- `tests/` — golden-fixture parser tests + end-to-end pipeline test
- `trial/` — retired spike, reference only (`trial/sources/rera.py` gets ported in Phase 1)

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

## VPS deploy

```sh
cp .env.example .env   # fill in: POSTGRES_PASSWORD, ATLAS_API_TOKEN, ATLAS_DOMAIN
docker compose up -d --build
```

- App boots via `alembic upgrade head`, serves on `:8000` behind Caddy (HTTPS + auto-certs).
- Every endpoint except `GET /health` requires `Authorization: Bearer $ATLAS_API_TOKEN`.
- Postgres is bound to `127.0.0.1` only.
- Nightly backups: add the `scripts/backup.sh` cron line; test restores monthly.
- Phase 1 adds the dead-man's switch (healthchecks.io) on the daily job.
