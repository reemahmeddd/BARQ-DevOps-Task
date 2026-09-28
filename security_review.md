# Security and production-readiness review

Record at least 8 concrete risks or improvements relevant to your final solution.
This is a review requirement, not the number of hidden faults.

Part A lists risks I found and fixed, with proof. Part B lists risks that are still open in my
solution, with the production follow-up. Evidence files are in `evidence/`.
The checks for Part B were run on the final setup (`evidence/44-security-review-checks.txt`).

## Part A: Implemented fixes

### A1. Database password in the repository, the image and the logs (secrets)
- Risk and evidence: The password was committed in `config/app.env` and `docker-compose.yml`, copied into the image by the Dockerfile, and printed in the `configuration_loaded` log line (`evidence/23-secrets-proof.txt`). The repository is public.
- Impact: Anyone who can read the repository, the image or the logs can log in to the database.
- Implemented fix / commit: Removed the copy from the Dockerfile (c26861d), redacted the password in the log (7545b57), moved the credentials to the git-ignored `.env` with `${VAR:?}` guards and a placeholder `.env.example` (7236e9a), and rotated the leaked password (b5489bd).
- Production follow-up: A secret manager or Docker secrets mounted as files, and scheduled rotation. The old password is still readable in the git history, but it no longer works.
- How to verify: `git grep` for the full old password finds it only in `evidence/23-secrets-proof.txt` (and in the git history); `docker run --rm --entrypoint cat barq-assessment-app-01 /srv/app.env` fails; the app log shows `barq_app:***@`; logging in with the old password fails (`evidence/27-password-rotation.txt`).

### A2. Database and cache ports exposed, nginx able to reach them (ports and networks)
- Risk and evidence: Compose declared host ports 15432 and 16379 for postgres and redis, and nginx was on the backend network, where it could connect to postgres and get `+PONG` from redis (`evidence/19-isolation-proof.txt`).
- Impact: The databases could be reached without going through the app, and a compromised nginx could reach them directly.
- Implemented fix / commit: nginx only on `frontend`, the database port lines removed, and only nginx published on `127.0.0.1` (ff7de2b).
- Production follow-up: TLS on nginx and host firewall rules, in addition to the Docker networks.
- How to verify: `python3 validate.py` checks network membership, that nginx cannot reach postgres or redis, and that only nginx publishes a host port.

### A3. App running as root (container user)
- Risk and evidence: `docker exec app-01 id` returned `uid=0(root)` because the Dockerfile set `USER root` (`evidence/28-root-user-proof.txt`).
- Impact: A bug in the app would give an attacker root inside the container, which makes escaping the container and changing files much easier.
- Implemented fix / commit: `USER app` (uid 10001) (d14c2e7).
- Production follow-up: See B5 (read-only filesystem, dropped capabilities).
- How to verify: `docker exec app-01 id` shows `uid=10001(app)`. `validate.py` checks it for every app instance.

### A4. Data lost when containers are recreated (persistence)
- Risk and evidence: Postgres stored its data on a tmpfs (memory), and the named volume was mounted on an unused folder. Redis persistence was switched off. Records and the counter were lost after recreating the containers (`evidence/17`, `21`).
- Impact: Every restart or update of the database containers loses all data.
- Implemented fix / commit: Postgres data on the named volume (cedded0). Redis AOF on the named volume `redis-data` (668f163).
- Production follow-up: See B7 (backups off the host).
- How to verify: Create a record, run `docker compose up -d --force-recreate postgres app-01 app-02`, and the record is still listed. Increase the counter, recreate redis, and the counter continues.

### A5. No backup and restore
- Risk and evidence: The starter `backup.sh` and `restore.sh` only exited with code 2.
- Impact: No way to recover from deleted or corrupted data.
- Implemented fix / commit: `backup.sh` (pg_dump custom format, checked with `pg_restore --list`, sha256 file) and `restore.sh` (explicit file, checksum check, single transaction) (89c2671).
- Production follow-up: See B7.
- How to verify: `evidence/35-backup-restore.txt`: backup, delete all rows, restore, and exactly the backed-up rows return. A tampered or fake file is refused.

### A6. One failing app or dependency breaks the whole service (availability)
- Risk and evidence: With retries off, half of the requests failed when app-02 stopped (`evidence/15`). A crashed app stayed down (`evidence/30`). nginx got traffic before the apps were ready (19 × 502 at startup, `evidence/38`). A Redis outage made nginx mark both apps down, so even `/health` failed (`evidence/42`).
- Impact: Users see errors for problems that one healthy app could have handled.
- Implemented fix / commit: nginx failover (2d9ee10, f2daf3b), `restart: unless-stopped` (b8e3381), startup order with `service_healthy` (347068f), re-resolving app names (b6b943f), and retrying only unreachable apps with a 10 s read timeout (8ccc646).
- Production follow-up: See B9 (remaining single points of failure).
- How to verify: `python3 failure_test.py` (0 errors while app-02 is stopped). With redis stopped, `/health` still returns 200 and `/counter` returns 503 (`evidence/43`).

### A7. No resource limits (availability)
- Risk and evidence: `docker inspect` showed `memory=0 nanocpus=0` for every container (`evidence/30`).
- Impact: One container with a memory leak or a load spike can use all the memory of the host and take down everything else.
- Implemented fix / commit: CPU and memory limits for every service (b8e3381).
- Production follow-up: Set the values from load tests and alert on out-of-memory kills.
- How to verify: `docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}' app-01`.

### A8. Unlimited log files and nginx version disclosure (logging, images)
- Risk and evidence: All containers used the `json-file` log driver without a size limit, and nginx sent `Server: nginx/1.28.3` in every response (`evidence/44`, checks B and C).
- Impact: Logs could fill the disk, which would also stop Postgres and Redis from writing. The exact version helps an attacker look up known vulnerabilities.
- Implemented fix / commit: A shared `x-logging` block (`max-size: 10m`, `max-file: 3`, so at most 30 MB per container) used by all services, and `server_tokens off;` in nginx (14415e5).
- Production follow-up: Central logging (B3) and image scanning (B6).
- How to verify: `docker inspect -f '{{.HostConfig.LogConfig.Config}}' nginx` shows `max-file:3 max-size:10m`, and `curl -sI http://127.0.0.1:8090/health | grep -i server` shows `Server: nginx` without a version (`evidence/45-quick-hardening-retest.txt`).

## Part B: Open risks and planned improvements (not implemented)

### B1. The app uses a PostgreSQL superuser
- Risk and evidence: `\du` shows `barq_app` with `Superuser, Create role, Create DB, Replication, Bypass RLS`. The official postgres image makes `POSTGRES_USER` a superuser, and the app connects with that same account (`evidence/44`, check A).
- Impact: If the app is compromised (for example through SQL injection), the attacker has full control of the database server: they can drop all data, read everything and create new accounts.
- Implemented fix / commit: None.
- Production follow-up: Create a separate application role that can only `SELECT` and `INSERT` on `records` (and use its sequence), and keep the superuser only for administration and backups. On the existing volume this needs a migration script, because `init.sql` only runs on an empty volume.
- How to verify: `\du` shows the app role without `Superuser`, and a `DROP TABLE` run as that role fails.

### B2. Redis has no password
- Risk and evidence: `redis.Redis(host='redis').ping()` from app-01 returns `True` without a password (`evidence/44`, check D).
- Impact: Anything that reaches the backend network can read or change the cache. Today only the apps are on that network, so the network isolation (A2) is the only protection.
- Implemented fix / commit: None.
- Production follow-up: `requirepass` (or ACL users) from `.env`, and the password in `REDIS_URL`.
- How to verify: `redis-cli ping` without a password returns `NOAUTH`.

### B3. Container logs are kept only on the host (logging)
- Risk and evidence: Logs stay in Docker's `json-file` files on the host. Before the fix they had no size limit (`evidence/44`, check C), which is fixed now (A8).
- Impact: With the size limit, old log lines are deleted after 30 MB per container, so older history is lost, and the logs disappear if the host is lost.
- Implemented fix / commit: Size limit only (A8).
- Production follow-up: Ship logs to a central system (for example Loki or Elasticsearch) with a retention policy.
- How to verify: Logs from a container are searchable in the central system after the container was recreated.

### B4. No monitoring or alerting (monitoring)
- Risk and evidence: Health is only visible through `docker compose ps`, `validate.py` and the logs. Nothing notifies anyone when a container restarts, becomes unhealthy or the error rate rises. In the historical incident (`log_analysis.md`), 95 of 720 requests failed.
- Impact: Outages are only noticed when a user complains or someone runs a check by hand.
- Implemented fix / commit: None. (`validate.py` and CI only check on demand and on each push.)
- Production follow-up: Metrics (request rate, 5xx rate, latency per upstream, restarts, memory) with alerts, for example Prometheus with an nginx exporter, plus log-based alerts on `dependency_error`.
- How to verify: Stop app-02 and confirm an alert fires.

### B5. No extra container hardening (container user)
- Risk and evidence: No read-only root filesystem, no dropped Linux capabilities, no `no-new-privileges` on any container (`evidence/44`, check E).
- Impact: A compromised container can write to its filesystem and use more kernel features than it needs.
- Implemented fix / commit: None (only the non-root user, A3).
- Production follow-up: `read_only: true` with a small `tmpfs` where needed, `cap_drop: [ALL]` (adding back only what each service needs), and `security_opt: ["no-new-privileges:true"]`, then test each service.
- How to verify: `docker inspect` shows the options, and all checks in `validate.py` still pass.

### B6. Images are pinned but not scanned (images)
- Risk and evidence: All images are pinned by digest, which is reproducible, but nothing checks them for known vulnerabilities, and pinned images do not get updates by themselves. (The nginx version header was a related finding and is fixed, see A8.)
- Impact: Known vulnerabilities can stay unnoticed.
- Implemented fix / commit: None.
- Production follow-up: An image scan in CI (for example Trivy) that fails on critical findings, and automated digest updates.
- How to verify: The CI log shows the scan result.

### B7. Backups stay on the same machine and are not encrypted (backup)
- Risk and evidence: `backup.sh` writes to `backups/` on the same host as the Docker volumes. The files are not encrypted, and backups only run when someone starts the script.
- Impact: Losing the host (disk failure, deletion, ransomware) loses the data and the backups together. Anyone with access to the files can read all the data.
- Implemented fix / commit: None (A5 covers creating and restoring backups).
- Production follow-up: Scheduled backups, encrypted and copied off the host, with a retention policy and a regular restore test. Point-in-time recovery (WAL archiving) or a managed database.
- How to verify: A scheduled job's output shows the upload, and a restore from the off-site copy works.

### B8. Plain HTTP and development server
- Risk and evidence: nginx only listens on HTTP (port 80 in the container, 127.0.0.1 on the host). The app runs on the Flask development server, and `gunicorn` is installed but unused.
- Impact: Fine for a local lab, but traffic would be readable on a network, and the development server is not built for production load.
- Implemented fix / commit: None (binding to 127.0.0.1 keeps the lab local).
- Production follow-up: TLS on nginx, and `gunicorn` for the app (see `decisions.md`, Decision 2).
- How to verify: `curl -I https://...` works with a valid certificate. `docker exec app-01 ps` shows gunicorn workers.

### B9. Remaining single points of failure (availability)
- Risk and evidence: There is one nginx, one Postgres and one Redis on one host. Only the app layer has more than one instance.
- Impact: If nginx, Postgres, Redis or the host fails, the service is down (partly or fully), even though the apps are redundant.
- Implemented fix / commit: Partly: `restart: unless-stopped` brings crashed containers back, and a Redis outage no longer takes down the endpoints that do not need Redis (A6).
- Production follow-up: Two or more nginx instances behind a load balancer or a floating IP, Postgres with a replica and automatic failover (or a managed database), Redis with replication and Sentinel, and running across more than one host.
- How to verify: Stop each component in turn and measure the error rate with a script like `failure_test.py`.
