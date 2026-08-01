# VPS deploy runbook

The Phase-1 gate (7 consecutive clean ingestion days) needs Atlas collecting
every day without a human present. The local Windows Scheduled Task
(`scripts\install-daily-task.ps1`) keeps the clock running on the dev box, but
it only counts days the laptop is awake. This is the durable answer.

Target: one small VPS (2 vCPU / 2–4 GB RAM / 40 GB disk is ample), Docker +
Docker Compose, a domain pointed at it.

---

## 1. Prerequisites on the VPS

```sh
# Docker Engine + compose plugin (Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh
docker compose version          # must print v2.x
```

Point an A record (e.g. `atlas.yourdomain.com`) at the VPS IP **before** the
first boot, so your reverse proxy can complete its certificate challenge.

Postgres must never be reachable publicly (plan §7). The merge snippet
publishes **no** host port for `atlas-db` at all — do not add one to debug; use
`docker compose exec atlas-db psql -U atlas atlas` instead.

## 2. Merge Atlas into your existing compose stack

**The VPS never holds the code.** Development happens on the Windows box; CI
builds the image; the VPS pulls it.

If the VPS already runs everything from **one** `docker-compose.yml`, do not
add a second stack — paste
[deploy/compose-snippet.yml](../deploy/compose-snippet.yml) into the compose
file you already have: two services under `services:`, one volume under
`volumes:`. Nothing else from this repo goes on the server.

Everything in the snippet is namespaced so it cannot collide with what you
already run:

| | Atlas uses | Why it matters |
|---|---|---|
| Services | `atlas-db`, `atlas-app` | `db` / `app` almost certainly already exist |
| Volume | `atlas_pgdata` | a bare `pgdata` would be shared with another Postgres |
| Env keys | `ATLAS_POSTGRES_PASSWORD`, `ATLAS_APIFY_TOKEN`, … | one compose = **one `.env`**; a generic `POSTGRES_PASSWORD` would be read by every service in the file |
| Ports | none published | see below |

**Three things the snippet deliberately does *not* do:**

1. **No reverse proxy.** You already have one on :80/:443 — a second would fail
   to bind. Atlas publishes no host ports; your proxy reaches it at
   `http://atlas-app:8000`. Because it is all one compose file, they share the
   default network and service-name DNS just works, with no extra config.
2. **No host port on Postgres.** `127.0.0.1:5432` would collide with any other
   Postgres on the box. Admin access is
   `docker compose exec atlas-db psql -U atlas atlas` — no published port needed.
3. **No separate database for Atlas inside your existing Postgres.** Atlas gets
   its own container: it needs PG 16 with `pg_trgm`, and `pgvector` in Phase 3.
   Sharing your existing instance is possible only if it already satisfies
   both — otherwise a Phase-3 migration fails against a database you can't
   freely change. Point `DATABASE_URL` at your own instance and drop
   `atlas-db` if you want to take that on.

### Routing from your existing proxy

**Caddy** — add a site block:

```
atlas.yourdomain.com {
	reverse_proxy atlas-app:8000
}
```

**nginx**:

```nginx
server {
    server_name atlas.yourdomain.com;
    location / { proxy_pass http://atlas-app:8000; proxy_set_header Host $host; }
}
```

**Traefik** — add labels to `atlas-app` instead:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.atlas.rule=Host(`atlas.yourdomain.com`)"
      - "traefik.http.services.atlas.loadbalancer.server.port=8000"
```

### The one shared-compose hazard to know about

The snippet uses `${VAR:?...}` so compose refuses to start when an Atlas secret
is missing — that is what stops Atlas deploying with a dead scraper. In a
shared file **that refusal applies to the whole stack**: a missing
`ATLAS_APIFY_TOKEN` will block your other services from coming up too.

It only bites while you are setting the values, and it is loud rather than
silent. If you would rather not accept it, change `${ATLAS_APIFY_TOKEN:?...}`
to `${ATLAS_APIFY_TOKEN:-}` — Atlas will then start and record a failing
`magicbricks` source instead, which you'd see in `/sources`.

**Prerequisite (one time):** the image has to exist somewhere to pull from.
Push this repo to GitHub and the `release` workflow
([.github/workflows/release.yml](../.github/workflows/release.yml)) builds and
publishes `ghcr.io/<owner>/<repo>:latest` on every push to the default branch.
There is no local Docker on the dev machine, so CI is what makes this possible.
If the package is private, log the VPS in once:

```sh
echo <a GitHub PAT with read:packages> | docker login ghcr.io -u <user> --password-stdin
```

Add these keys to the **existing** `.env` next to your compose file (don't
replace it — your other services read from the same file). All are namespaced
so they can't clash with what's already in there:

| Var | Why it matters |
|---|---|
| `ATLAS_IMAGE` | The published image, e.g. `ghcr.io/<owner>/<repo>:latest`. |
| `ATLAS_POSTGRES_PASSWORD` | Atlas's own database password — deliberately *not* `POSTGRES_PASSWORD`, which another service in your file may already use. |
| `ATLAS_API_TOKEN` | A long random string (`openssl rand -base64 36`). **An unset token locks the API (503), it never opens it** — deliberate. |
| `ATLAS_APIFY_TOKEN` | Portal collectors raise without it; required so a deploy can't come up with a dead MagicBricks scraper. |
| `ATLAS_ENABLE_SCHEDULER` | Defaults to `1` — this is what actually runs the daily jobs. Set `0` only if driving ingestion from host cron. |

`ATLAS_POSTGRES_USER` / `ATLAS_POSTGRES_DB` default to `atlas` and can be left
unset. `DATABASE_URL` is assembled by compose (host `atlas-db`) — you do not
set it on the VPS.

There is no `ATLAS_DOMAIN`: the hostname lives in *your* proxy's config, not
Atlas's.

## 3. Boot

```sh
docker compose pull atlas-app
docker compose up -d atlas-db atlas-app
docker compose logs -f atlas-app        # migrations + scheduler start
```

Naming the services is a convenience, not a safety requirement: a bare
`docker compose up -d` is fine — compose only recreates containers whose image
or config actually changed, and leaves unchanged running ones alone.

**The one command to be careful with is `docker compose down -v`.** The `-v`
deletes named volumes, which includes `atlas_pgdata` — every day of collected
history, gone, with the raw archive that made it rebuildable. Plain
`docker compose down` removes containers but keeps volumes, so bringing the
whole stack down and back up is safe.

Never `--build` on the VPS — there is no source there to build from.

The app container runs `alembic upgrade head` before uvicorn, so the schema is
applied on boot. **Migrations ship inside the image**, so a schema change you
author here reaches the server by publishing a new image and pulling it; you
never run Alembic by hand on the VPS.

Expect to see APScheduler register three jobs.

### Shipping a change later

```sh
# on Windows: commit + push. CI builds and publishes the image.
# then on the VPS:
docker compose pull atlas-app && docker compose up -d atlas-app
```

That is the whole deploy loop, and it touches nothing else in your stack.
Postgres data lives in the `atlas_pgdata` volume, so recreating the app
container never touches collected history.

## 4. Verify the deploy (do not skip)

```sh
DOMAIN=atlas.yourdomain.com          # whatever you routed in your proxy
TOKEN=<your ATLAS_API_TOKEN>
curl -s https://$DOMAIN/health                             # no auth required
curl -s -H "Authorization: Bearer $TOKEN" https://$DOMAIN/sources | jq
curl -s -H "Authorization: Bearer $TOKEN" https://$DOMAIN/gate | jq
```

Then force one immediate ingestion rather than waiting for 05:30 IST, so you
find out today whether the token and actors work:

```sh
docker compose exec atlas-app python -m atlas.cli daily
docker compose exec atlas-app python -m atlas.cli gate
```

Checklist before you walk away:
- `/health` returns `{"status":"ok","db":true}`.
- `/sources` shows every source `healthy: true` with a recent `last_ok_at`.
- An unauthenticated call to `/sources` returns 401/403, not data.
- `atlas.cli gate` prints a CLEAN line for today covering **all three** sources
  (`rera_karnataka/karnataka`, `magicbricks/bangalore`, `magicbricks/mysore`).

## 5. Schedule

The in-process APScheduler (`ATLAS_ENABLE_SCHEDULER=1`) runs, all pinned to
`Asia/Kolkata` (the app container also sets `TZ`):

| Time (IST) | Job |
|---|---|
| 05:30 | RERA registry |
| 06:00 | Portals (Bangalore + Mysore) |
| 06:45 | Staleness sweep + legal tagging |

### Downtime and the daily window

APScheduler here uses the default **in-memory** jobstore. If the container is
down at 05:30, that fire is *not* replayed on restart — `next_run_time` is
computed from now, so today's run would simply never happen.
`misfire_grace_time` does **not** cover this; it only applies while the
scheduler process is alive.

So the app **catches up on startup**: if today's window has already passed and
today is not clean yet, it runs the daily sequence in a background thread as
soon as it boots (`atlas/jobs.py: catch_up_if_missed`). That makes stopping the
stack for maintenance safe — bring it back and the day repairs itself, because
a same-day success is what the gate counts.

It is guarded against looping: once today is clean, further restarts do
nothing. And it only ever collects for *today* — past days are gone and
re-running cannot change them.

Practical upshot: **you can `docker compose down` and back up whenever you
like.** The only outage that costs a gate day is one that lasts past midnight
IST without the app coming up at all.

**Alternative (host cron instead of the in-process scheduler)** — set
`ATLAS_ENABLE_SCHEDULER=0` and add:

```
30 5 * * *  cd /path/to/atlas && docker compose exec -T atlas-app python -m atlas.cli daily
```

`atlas.cli daily` runs every step even if an earlier one fails, and exits
non-zero if any did — so cron mail actually tells you about a bad day.

## 6. Backups

```
30 2 * * *  cd /path/to/atlas && sh scripts/backup.sh
```

The accumulated history *is* the asset — the raw archive plus these dumps are
what make Atlas rebuildable. Test a restore monthly; an untested backup is a
guess (see the header of `scripts/backup.sh`).

## 7. Watchdog

Create a check at healthchecks.io and have the daily job ping it on success, so
a *silent* failure (VPS down, container dead, scheduler wedged) pages you
instead of quietly costing a week of the gate. Set `HEALTHCHECKS_PING_URL` in
`.env`; wiring it into the job is a small follow-up, not yet implemented.

## 8. What the running image decides by itself, and what comes back to you

The split matters because development happens on the Windows box, not on the
server. The rule: **the container measures quality automatically; changing how
quality is judged is a code change and happens here.**

**Automatic on the VPS — no intervention, ever:**

- Collect, archive raw, parse, upsert, and version every listing.
- Classify each run `ok` / `anomalous` / `failed` against the thresholds in
  [atlas/ingest/pipeline.py](../atlas/ingest/pipeline.py) — empty fetch,
  unparsed ratio over `ANOMALY_UNPARSED_RATIO`, volume under
  `ANOMALY_VOLUME_RATIO` x trailing average. This *is* automated quality
  gating, and it runs unattended.
- Refuse to infer removals from a dead scraper (the sweep guard), so a broken
  source can never manufacture fake "removed" listings.
- Track the Phase-1 streak: `GET /gate` and `docker compose exec atlas-app python -m
  atlas.cli gate` compute consecutive clean days from stored run history.
- Report per-source health via `GET /sources`.

So **the quality milestone is measured on the server, from the data it
collected, with no input from you.** Checking `/gate` is a read, not a step in
the process.

**Comes back to the dev box — anything that changes judgement:**

- Fixing a parser after a portal changes its markup.
- Re-tuning `ANOMALY_VOLUME_RATIO` / `ANOMALY_UNPARSED_RATIO` / `stale_after_days`.
- Deal Score weights, prompts, new sources, schema migrations.
- Regenerating golden files — those are *reviewed artifacts*, never regenerated
  to make a test pass (CLAUDE.md).

The loop is: pull the data down → analyse and tune here → push → CI builds →
`docker compose pull && up -d` on the VPS.

**The reason this is safe: ingestion is raw-first.** Every raw item is archived
in `raw_payloads` *before* it is parsed. So if you discover a parser bug on day
5, you fix it here, ship a new image, and **re-parse the archived history** —
the collected days are not lost and the streak is not spent. That design rule
exists exactly so that tuning-after-the-fact costs nothing.

### Pulling the data down for local analysis

```powershell
scripts\fetch-vps-data.ps1 -VpsHost atlas@<ip> -IntoLocal
```

It dumps inside the db container, downloads, and restores into a **separate**
local database (`atlas_vps`) so your own local collection history is never
clobbered. The VPS keeps collecting throughout — this is a read-only snapshot.

## 9. Migrating the local streak (optional)

Days already collected on the dev box live in the local Postgres. To carry the
streak over rather than restarting at 0:

```sh
# on Windows, with the local PG running
.pgbin\pgsql\bin\pg_dump.exe -h 127.0.0.1 -U postgres postgres > atlas_local.sql
# on the VPS
docker compose exec -T atlas-db psql -U atlas -d atlas < atlas_local.sql
```

Load into a *fresh* database before the first VPS ingestion run. If the VPS has
already collected, merging two histories is not worth it — just restart the
7-day count and note the restart date.
