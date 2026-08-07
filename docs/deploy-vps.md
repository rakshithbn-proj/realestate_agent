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

### Phase-2 keys — all optional, each degrades to a visible no-op

Unlike the four above, none of these will stop the container. Every one is
designed so that *absent* is a safe, honest state rather than a failure, which
is what makes a partial rollout sane: turn them on one at a time and watch.

| Var | Unset means | Cost |
|---|---|---|
| `ATLAS_ANTHROPIC_API_KEY` | The `seller_motivation` factor **abstains for every listing** and the score renormalises over the remaining weight. It must never read as "no seller here is motivated". | A few cents/day (Haiku, Batch API, ~650 short descriptions) |
| `ATLAS_RESEND_API_KEY` + `ATLAS_DIGEST_TO` | The briefing is still built and stored in `report_runs` every morning, just not delivered. Logged, never silent. | Free (3k/month) |
| `ATLAS_DIGEST_FROM` | Defaults to Resend's shared `onboarding@resend.dev`, which works without domain verification when sending to yourself. | — |
| `ATLAS_FEEDBACK_SECRET` | 👍/👎 links are omitted from the email **and** `/feedback` rejects everything. It fails closed on purpose: that endpoint is unauthenticated by design, so no secret must mean no writes, not open writes. | — |
| `ATLAS_PUBLIC_BASE_URL` | Links omitted. Set to the same origin as your Traefik `Host` rule, e.g. `https://atlas.example.com`. | — |
| `ATLAS_HEALTHCHECKS_PING_URL` | No dead-man's switch — a silent delivery failure goes unnoticed. | Free tier |

Generate the feedback secret with:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

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

### 4a. First deploy of Phase 2 — order matters, twice

Two steps here are one-way doors. Do them in this order on the deploy that
first carries Deal Score and the digest.

**1. Read the score distribution before writing weights v1.**

```sh
docker compose exec atlas-app python -m atlas.cli reparse --source magicbricks
docker compose exec atlas-app python -m atlas.cli reparse --source magicbricks_mysore
docker compose exec atlas-app python -m atlas.cli score --dry-run
```

`--dry-run` writes nothing. The row in `score_weights` is created by the first
*non-dry* run, and from then on changing any weight requires bumping
`WEIGHTS_VERSION` — deliberately, so stored scores stay attributable. So read
the output first. What you are looking for:

- **The FACTOR COVERAGE block.** Any factor abstaining on most listings is
  carrying its weight in name only. `price_vs_locality` is the one to watch:
  it needs ≥5 same-locality, same-asset-class comps, and on a thin sample it
  abstains everywhere.
- **The DISTRIBUTION block.** If every listing lands in one band the ranking
  is not discriminating and the weights need a rethink before they are fixed.

`reparse` first because it backfills `listings.posted_at` from the raw
archive, which is what makes days-on-market real rather than "days since Atlas
noticed". Scoring before it would read every listing as brand new.

**2. Leave the 99acres plot sources disabled until the gate reads 7/7.**

They ship `enabled=False` and the daily job skips them. The Phase-1 gate
requires every *enabled* source to land an `ok` run every day **from its first
run onward**, so switching on a scraper that has never run in production puts
the streak at the mercy of its first bad morning.

**Use the manual run first — it is free of both risks.** `atlas.cli run
<source>` resolves the spec directly and does **not** consult `enabled`, while
`_get_or_create_source()` still writes `enabled=False` to the `sources` row,
which the gate skips. So one manual run costs ~$1, lands real plots in
`listings`, and feeds scoring and the digest without joining the streak:

```sh
docker compose exec atlas-app python -m atlas.cli run acres99_land
docker compose exec atlas-app python -m atlas.cli score
docker compose exec atlas-app python -m atlas.cli top --limit 10
```

Only once that looks right is there a reason to accept daily billing and gate
exposure. To go daily:

```sh
# edit atlas/ingest/registry.py: enabled=False -> True on both acres99 specs,
# push, let CI publish, then
docker compose pull atlas-app && docker compose up -d atlas-app
docker compose exec atlas-app python -m atlas.cli run acres99_land
```

Budget once enabled: ~$0.98/day (~$29/mo) for Bangalore plus the Mysore spec.
`limit` is **per location**, so adding a corridor seed adds its own 40 results
to the bill.

## 5. Schedule

The in-process APScheduler (`ATLAS_ENABLE_SCHEDULER=1`) runs, all pinned to
`Asia/Kolkata` (the app container also sets `TZ`):

| Time (IST) | Job |
|---|---|
| 05:30 | RERA registry |
| 06:00 | Portals (Bangalore + Mysore) |
| 06:45 | Staleness sweep + legal tagging |
| 07:00 | Deal Score pass (collects finished motivation batches, then scores) |
| 07:15 | The daily briefing |

The digest is deliberately **not** part of `run_daily()`, which is what the
startup catch-up replays — otherwise a restart inside the morning window would
re-send the email. `report_runs.sent_at` guards it a second time.

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

Create a check at healthchecks.io and set `ATLAS_HEALTHCHECKS_PING_URL` in
`.env`. **Now wired** (was a stub until 2026-08-02): the digest job pings it
only on *successful delivery*, so a missed ping means the briefing did not
arrive — VPS down, container dead, scheduler wedged, or Resend refusing. A
dead process cannot report itself dead, which is the whole point of an outside
watcher.

Set the period to ~26h and the grace to ~2h: the digest fires at 07:15 IST, so
anything longer hides a whole missed day and anything shorter pages you for
normal jitter.

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
