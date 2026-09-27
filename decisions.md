# Technical decisions

Record at least 5 decisions. Include assumptions and limits.

Evidence files are in `evidence/`. The investigation behind each decision is in `troubleshooting.md`.

## Decision 1: Base image for the app
- Choice: Keep the supplied `python:3.12-slim-bookworm` image, pinned by its `@sha256` digest, and run the app as the unprivileged `app` user (uid 10001).
- Why: It is an official image, much smaller than the full `python` image, and based on Debian (glibc), so the binary wheels in `requirements.txt` (for example `psycopg[binary]`) install without compilers. The digest makes every build use exactly the same image. The health check can use Python itself, which is already in the image, so no extra tool like `curl` is needed.
- Alternative: `python:3.12-alpine` (smaller, but musl instead of glibc, so some wheels need to be compiled) or a distroless image (no shell at all, harder to debug for this lab).
- Trade-off: A pinned digest does not get security updates automatically. Someone has to update it on purpose.
- Evidence / commit: `Dockerfile` line 1 (unchanged). Non-root user: d14c2e7 (`evidence/29-non-root-retest.txt`).
- Production improvement: A multi-stage build, a regular image scan in CI, and automated digest updates (for example Dependabot or Renovate).

## Decision 2: Keep the Flask development server
- Choice: The app still runs with `python -m app.server` (Flask's built-in server, `threaded=True`).
- Why: The task says to fix the environment, not to rewrite the app, and the built-in server is enough for this lab's traffic. All my tests pass with it.
- Alternative: `gunicorn`, which is already listed in `requirements.txt` but not used.
- Trade-off: The Flask server is not meant for production (limited concurrency, no worker management). `gunicorn` is also an installed but unused dependency, which the task asks to avoid.
- Evidence / commit: Listed as an open point in `troubleshooting.md`.
- Production improvement: Run the app with `gunicorn` (a few workers, with its own timeouts) and remove anything unused from `requirements.txt`.

## Decision 3: Health checks, and liveness versus readiness
- Choice:
  - Apps: the Docker health check calls `/health` (liveness: "is the process answering?") with Python's `urllib`, every 5 s.
  - `/ready` (readiness: "are Postgres and Redis reachable?") is checked by `validate.py` and is used in the video, but not as the container health check.
  - Postgres: `pg_isready`. Redis: `redis-cli ping`. nginx: its own `/nginx-health` page on `127.0.0.1:8081` inside the container, checked with the image's `wget`.
  - Startup order: `depends_on` with `condition: service_healthy` (postgres and redis, then the apps, then nginx).
- Why: If the container health check used `/ready`, a database outage would mark both apps unhealthy, even though they still serve `/health` and can report the problem with a clear 503. The nginx check only tests nginx itself, for the same reason. Every check uses a tool that already exists in its image.
- Alternative: Use `/ready` as the app health check, or check the apps through nginx.
- Trade-off: A "healthy" app can still fail requests that need a dependency. That is why `validate.py` checks `/ready` separately.
- Evidence / commit: a87ea15 (`/healthz` fixed), aede8a3 (nginx health check), 347068f (startup order: 19 × 502 at startup before, 0 after, `evidence/38` and `39`).
- Production improvement: Report readiness to the load balancer (so traffic is only sent to ready instances) and alert on it, instead of only checking it in a script.

## Decision 4: Networks and published ports
- Choice: Two networks. `frontend`: nginx and the apps. `backend` (`internal: true`): the apps, postgres and redis. Only nginx publishes a port, and only on `127.0.0.1:${PUBLIC_PORT}`. Services talk to each other by service name, and nginx re-resolves the names while running (`resolver 127.0.0.11 valid=5s` and `resolve`).
- Why: nginx never needs the databases, so it should not be able to reach them. If nginx were compromised, the databases would still be out of reach. Binding to 127.0.0.1 keeps the lab off the local network. Service names instead of IPs, because containers get new IPs when they are recreated.
- Alternative: One shared network (simpler, but nginx could reach the databases), or publishing on `0.0.0.0` (reachable from other machines).
- Trade-off: `internal: true` also blocks outgoing internet access from the backend. A DNS lookup of a stopped container took about 3.6 s (see Decision 5). The sealed network is a likely reason, but I did not test that.
- Evidence / commit: ff7de2b (nginx only on frontend, db ports removed), b6b943f (re-resolve), 485a811 (published port). `validate.py` checks all of it.
- Production improvement: TLS on nginx, and firewall rules or network policies in addition to the Docker networks.

## Decision 5: Timeouts, retries and failover in nginx
- Choice:
  - `proxy_connect_timeout 2s`: a stopped app is detected within about 2 s.
  - `proxy_read_timeout 10s`: longer than the slowest dependency failure I measured (about 3.6 s DNS lookup plus the app's 2 s timeouts).
  - `proxy_next_upstream error timeout;` with `proxy_next_upstream_tries 2`: only retry when the app could not be reached or did not answer, at most once, on the other app.
  - `max_fails=2 fail_timeout=10s`: after 2 failures an app is skipped for 10 s, then tried again.
  - `zone application_pool 64k`: all 12 nginx workers share one round-robin position.
- Why: With retries switched off, half of the requests failed when one app stopped (`evidence/15`). With these settings, 0 errors in 92 requests while app-02 was stopped (`evidence/34`). I removed `http_502/503/504` from the retry list because a 503 from the app means "my dependency is down", which the other app shares: with those in the list, a Redis outage marked both apps down and even `/health` failed (Entry 18). nginx does not resend a POST that was already sent to an app, so retries cannot create duplicate records.
- Alternative: No retries (simple, but users see every app failure), or retrying on every error including 503 (tested: it turns a dependency outage into a full outage).
- Trade-off: A recovered app gets traffic again only after `fail_timeout` (10.7 s in the failure test). A frozen app can hold a request for up to 10 s. At startup, early failures can disable both apps for 10 s, which is why Decision 3's startup order matters.
- Evidence / commit: 2d9ee10, f2daf3b, 8ccc646. Files `evidence/16`, `34`, `42`, `43`.
- Production improvement: Active health checks from the load balancer, and per-endpoint timeouts based on measured latency.

## Decision 6: Restart policy and resource limits
- Choice: `restart: unless-stopped` for all services. Limits: apps 0.5 CPU and 256 MB each, postgres 1 CPU and 512 MB, redis and nginx 0.5 CPU and 128 MB each (about 1.25 GB in total).
- Why: `unless-stopped` restarts a crashed container (app-02 was back in about 2 s) but respects a deliberate `docker stop`, which the failure test needs. `always` would also restart containers I stopped on purpose after a Docker restart, and `no` left a crashed app down. The limits stop one container from using all the memory of the machine, and the total fits the README's "4 GB free RAM".
- Alternative: `restart: always` or `on-failure`. No limits.
- Trade-off: The values are estimates, not load-tested. If an app uses more than 256 MB it is killed (and then restarted).
- Evidence / commit: b8e3381 (`evidence/30` and `31`).
- Production improvement: Set limits from measured usage (`docker stats` under load) and alert on restarts and out-of-memory kills.

## Decision 7: Storage, persistence and backups
- Choice:
  - Postgres data on the named volume `postgres-data` at `/var/lib/postgresql/data` (it was on a tmpfs before).
  - Redis with AOF persistence (`--appendonly yes`, written to disk every second) on the named volume `redis-data`.
  - Backups with `pg_dump -Fc` (`backup.sh`), a `.sha256` file next to each backup, and restores with `pg_restore --clean --if-exists --single-transaction` (`restore.sh`). Backups go to `backups/`, which is git-ignored.
- Why: Named volumes survive container recreation. AOF every second is a good trade-off for a counter. The custom dump format is compressed and can be checked with `pg_restore --list` before it is used, the checksum detects a changed file, and a single transaction means a failed restore changes nothing.
- Alternative: Bind mounts to a host folder, Redis RDB snapshots (can lose minutes of data), plain SQL dumps.
- Trade-off: Volumes and backups are on the same machine, so losing the machine loses both. With AOF, up to about 1 s of counter updates can be lost. `docker compose down -v` deletes the data.
- Evidence / commit: cedded0 (record survived recreation of postgres and the apps), 668f163 (counter 3 → recreate → 4), 89c2671 (backup, delete all rows, restore brought back exactly the backed-up rows).
- Production improvement: Scheduled backups copied off the machine, regular restore tests, and point-in-time recovery (WAL archiving) or a managed database.

## Decision 8: Secrets
- Choice: Credentials live only in the git-ignored `.env`. Compose reads them with `${VAR:?message}`, so a missing value stops Compose with a clear error. `.env.example` has a placeholder. The password is no longer copied into the image or written to the logs (`redact_url()`). The leaked password was rotated with `ALTER ROLE` instead of rewriting the git history.
- Why: The repository is public, and the old password is visible in its history. Rewriting history would also destroy the evidence that is reviewed, so the leaked password was made useless instead.
- Alternative: Rewrite the history (for example with `git filter-repo`), or use Docker secrets or a secret manager.
- Trade-off: Environment variables are visible to anyone who can run `docker inspect` on the host.
- Evidence / commit: c26861d, 7545b57, 7236e9a, b5489bd (`evidence/23` to `27`).
- Production improvement: A secret manager or Docker secrets mounted as files, and regular rotation.

## Decision 9: What CI runs, and pinning
- Choice: GitHub Actions on every push and pull request: syntax checks (Python, Bash, Compose, `nginx -t`), build, the app unit tests inside the built image, `docker compose up -d --wait --wait-timeout 120`, `validate.py` and `failure_test.py`, logs on failure, and always `down --volumes`. The runner is pinned to `ubuntu-24.04` and `actions/checkout@v5`, like the pinned image digests.
- Why: CI rebuilds everything on a clean machine, so it catches things that only work on my laptop. Pinning keeps the result reproducible: run 1 warned that `ubuntu-latest` changes to Ubuntu 26 on 2026-10-19 and that `checkout@v4` uses a deprecated Node.js.
- Alternative: `ubuntu-latest` (always current, but it can change behaviour without a code change), or only running the unit tests.
- Trade-off: Pinned versions must be updated on purpose. CI uses `.env.example` with a placeholder password and a fresh database.
- Evidence / commit: 02841e9 (run #1 green), a312d6b (run #2 green, no warnings).
- Production improvement: An image vulnerability scan in CI, and deploying only images that passed CI.

## Assumptions and limits
- This is a single-host lab. nginx, Postgres and Redis each run once, so they are single points of failure. Only the app layer is redundant.
- A green CI run proves that the stack builds and passes my checks on a clean Linux machine with a fresh database. It does not prove behaviour under real load, with real data, over a long time, or on another host.
- Resource limits and timeouts are based on a few measurements in this lab, not on load tests.
- The log-analysis counting rule (one client request per unique `request_id` in access.log) is my assumption about the historical logs, explained in `log_analysis.md`.
