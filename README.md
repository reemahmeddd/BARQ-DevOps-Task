<img src="assets/barq-logo.svg" alt="BARQ Systems" width="180">

# BARQ DevOps Internship Task

A Flask API running as two instances behind NGINX, with PostgreSQL and Redis, started with Docker Compose.
I started from the supplied broken environment (tag `starter-v2.0.0`, commit `8442da3`), investigated
and fixed it, and added validation, failure, backup/restore scripts and CI.

| Document | Content |
|---|---|
| [troubleshooting.md](troubleshooting.md) | Investigation journal: 18 issues with symptoms, failed attempts, fixes and retests |
| [log_analysis.md](log_analysis.md) | Answers about the three historical logs, with commands and counts |
| [decisions.md](decisions.md) | 9 technical decisions with alternatives and trade-offs |
| [security_review.md](security_review.md) | 17 risks: 8 fixed, 9 planned |
| [AI_USAGE.md](AI_USAGE.md) | How AI was used and verified |
| [architecture.png](architecture.png) | Request flow, ports, networks, storage and health checks |
| [docs/EVIDENCE_INDEX.md](docs/EVIDENCE_INDEX.md) | Requirement → file/output → commit → video timestamp |
| `evidence/` | Saved output of every test I ran, numbered in the order I ran them |

## Architecture

```
laptop 127.0.0.1:8080
        │
     nginx  (only published port; health: /nginx-health on 127.0.0.1:8081 inside the container)
        │   frontend network
  ┌─────┴─────┐
app-01      app-02   (Flask, uid 10001, health: /health, readiness: /ready)
  └─────┬─────┘
        │   backend network (internal: true)
 postgres (volume postgres-data)     redis (AOF on volume redis-data)
```

nginx balances requests round-robin between the apps (shared `zone`), skips an app that cannot be
reached, and re-resolves the app names every 5 s. nginx cannot reach PostgreSQL or Redis.

## Requirements

- Linux or WSL2, Git, Docker with Compose v2, Python 3.12+ (for the scripts; standard library only).
- Run the shell scripts (`backup.sh`, `restore.sh`, `video_challenge.sh`) in Linux/WSL or Git Bash.
- On Windows PowerShell, use `python` instead of `python3`.

## Setup

```bash
git clone https://github.com/reemahmeddd/BARQ-DevOps-Task.git
cd BARQ-DevOps-Task
cp .env.example .env
# Edit .env and set a real POSTGRES_PASSWORD (letters and digits). .env is git-ignored.
```

`.env` holds `PUBLIC_PORT`, `POSTGRES_USER`, `POSTGRES_DB` and `POSTGRES_PASSWORD`. Compose stops with
an error if one of the database values is missing. PostgreSQL uses the password only when the volume
is created for the first time.

## Build and start

```bash
docker compose build
docker compose up -d --wait --wait-timeout 120   # waits until every service is healthy
docker compose ps
```

The project name is `barq-assessment` (set in `docker-compose.yml`). Startup order: postgres and redis
become healthy, then app-01 and app-02, then nginx.

## Use the API

```bash
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/ready
curl -i http://127.0.0.1:8080/instance
curl -H 'Content-Type: application/json' -d '{"title":"Video proof"}' http://127.0.0.1:8080/records
curl http://127.0.0.1:8080/records
curl http://127.0.0.1:8080/counter
for i in 1 2 3 4 5 6; do curl -s http://127.0.0.1:8080/instance; echo; done   # alternates app-01 / app-02
```

## Stop and start

```bash
docker compose stop            # stop containers, keep them and the data
docker compose start
docker compose down            # remove containers, keep the volumes (data stays)
docker compose up -d --wait
```

## Tests

```bash
python3 validate.py                      # 41 PASS/FAIL checks, exit 1 if any fails
python3 validate.py --url http://127.0.0.1:8090   # after changing PUBLIC_PORT
python3 failure_test.py                  # stops app-02 under traffic, restores it, checks recovery (~40 s)
python3 scripts/analyze_logs.py          # answers the log questions from logs/ (read-only)
docker run --rm -v "$PWD/tests:/srv/tests:ro" -w /srv barq-assessment-app-01 \
  python -m unittest discover -s tests -v   # the supplied app unit tests
```

`validate.py` waits up to 90 s for readiness, then checks the containers, every endpoint, that every app
instance answers through nginx (app containers are discovered automatically), network isolation and host
ports. `failure_test.py` only targets a running `app-*` container of this project, never the last one, and
always starts it again at the end.

## Failure test by hand

```bash
docker stop app-02
for i in $(seq 1 10); do curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' http://127.0.0.1:8080/instance; done
docker start app-02
sleep 15
for i in $(seq 1 6); do curl -s http://127.0.0.1:8080/instance; echo; done   # app-02 answers again
```

## Backup and restore

```bash
./backup.sh                                  # writes backups/barq_tasks-<UTC>.dump and a .sha256 file
./restore.sh                                 # lists the available backups
./restore.sh backups/barq_tasks-<UTC>.dump   # replaces the current data with the backup
```

`backups/` is git-ignored. `restore.sh` refuses a missing, changed (checksum) or unreadable file and
restores in a single transaction.

## Persistence test

```bash
curl -H 'Content-Type: application/json' -d '{"title":"Persistence proof"}' http://127.0.0.1:8080/records
docker compose up -d --force-recreate postgres app-01 app-02
curl http://127.0.0.1:8080/records          # the record is still there
```

## CI

`.github/workflows/ci.yml` runs on every push and pull request: syntax checks (Python, Bash, Compose,
`nginx -t`), build, unit tests, `docker compose up -d --wait`, `validate.py` and `failure_test.py`.
Any failing step fails the run. Runs: https://github.com/reemahmeddd/BARQ-DevOps-Task/actions

## Cleanup

```bash
docker compose down                          # containers and networks, data kept
docker compose down --volumes                # ALSO deletes the PostgreSQL and Redis data
rm -rf backups/                              # local backups
```

Do not use `--volumes` during persistence tests, and avoid global `docker system prune` commands on a
shared machine.

## Recorded challenge

`./video_challenge.sh` is the supplied challenge script. It is run once, for the first time, during the
video recording, and its receipt is kept in `.assessment/challenge.json`.

## Answers to the task questions

**What failed first? What proved the cause? Which failed attempt taught you something?**
The first start gave an empty reply on port 8080: compose published the host port to container port 81,
but nginx listens on 80. `netstat` inside nginx showed only port 80 (Entry 1). The failed attempt that
taught me the most was Entry 2: I thought the wrong database ports made the apps unhealthy, but the app
log showed the health check was calling `/healthz`, which does not exist. I learned to read the logs
before trusting a guess.

**What patterns did the logs reveal? How did you avoid double-counting requests?**
720 requests in 30 minutes, 95 failures (13.19 %) in four separate incidents: app-02 unreachable (502),
Redis timeouts (503), Postgres errors (503), and slow `/records` responses cut off by nginx (504) while the
app still returned 200. I counted one request per unique `request_id` in access.log, after skipping
malformed lines and exact duplicates, so retried requests are counted once. Details: `log_analysis.md`.

**How do requests flow? Why these ports, networks and readiness checks?**
Client → `127.0.0.1:8080` → nginx → app-01 or app-02 on port 8080 (frontend network) → PostgreSQL 5432 and
Redis 6379 (backend network). Only nginx is published, and only on 127.0.0.1, because nothing else needs to
be reached from outside. nginx is not on the backend network, so it cannot reach the databases. The
container health check uses `/health` (liveness), so a database outage does not mark the apps dead;
`/ready` (readiness) is checked by `validate.py`. See `decisions.md`, Decisions 3 and 4.

**Why these timeouts, retries, restart settings and resource limits?**
Connect timeout 2 s so a stopped app is skipped quickly; read timeout 10 s because the slowest dependency
failure I measured took about 3.6 s plus 2 s; retry once, only when an app is unreachable or does not
answer, because a 503 from the app means a shared dependency is down (retrying it turned a Redis outage
into a full outage, Entry 18). `restart: unless-stopped` restarts crashes but respects a deliberate stop.
Limits (apps 256 MB, postgres 512 MB, redis and nginx 128 MB) stop one container from using all memory.
See `decisions.md`, Decisions 5 and 6.

**When should validation fail? What does green CI prove, or not prove?**
Validation fails when the stack is not ready within the time limit, an endpoint gives the wrong status or
data, an app instance does not answer through nginx, a container is on the wrong network, nginx can reach
the databases, or a port other than nginx's is published. I tested that it fails for a stopped app, a
wrong port and nginx on the backend network (`evidence/33`). Green CI proves the stack builds and passes
these checks and the failure test on a clean Linux machine with a fresh database. It does not prove
behaviour under real load, with real data, over time, or on another host.

**Which single points of failure remain? How would you fix them in production?**
nginx, PostgreSQL, Redis and the host itself each exist once; only the app layer is redundant. In
production: two nginx instances behind a load balancer, PostgreSQL with a replica and automatic failover
(or a managed database), Redis replication with Sentinel, off-host backups, and more than one host.
See `security_review.md`, B9.

**What would you improve? How did you verify AI-assisted work?**
Next: a non-superuser database account for the app, a Redis password, gunicorn instead of the Flask
development server, monitoring and alerts, an image scan in CI, and off-host backups (`security_review.md`,
Part B). I verified AI-assisted work by retesting every change against the running containers, saving
the output in `evidence/`, checking that the scripts also fail when something is broken, and running CI
on a clean machine. See `AI_USAGE.md`.
