# Troubleshooting journal

Keep chronological entries. Copy this block for each meaningful investigation.

Times are local (UTC+3) and come from the related commits. All evidence files are in `evidence/`.
Commands were run from the repository root with the Compose project `barq-assessment`.

## Entry 1 / 2026-09-26 03:44 to 2026-09-27 01:57 / No answer on port 8080
- Symptom: After the first `docker compose -p barq-assessment up --build -d`, all five containers were "Up", but every request to `http://127.0.0.1:8080` failed with curl exit code 52 (empty reply from server). app-01 and app-02 were also "unhealthy".
- Hypothesis: `docker compose ps` showed nginx as `127.0.0.1:8080->81/tcp`. NGINX normally listens on 80, so I thought the host port was forwarded to a port where nothing listens.
- Command or test: `docker exec nginx netstat -tln`, then `docker exec nginx wget -S -O /dev/null http://127.0.0.1:81/` and the same on port 80.
- Actual output: netstat showed only `0.0.0.0:80` listening. Port 81 gave "Connection refused". Port 80 answered with `HTTP/1.1 502 Bad Gateway`, so NGINX itself was working on 80.
- Failed attempt and what changed your thinking: None for this issue. The first hypothesis was confirmed.
- Root cause: `docker-compose.yml` published the host port to container port 81, but `nginx/nginx.conf` has `listen 80;`.
- Fix: Changed the nginx port mapping to `127.0.0.1:${PUBLIC_PORT:-8080}:80`.
- Retest evidence: NGINX now answers on 127.0.0.1:8080 (`Server: nginx/1.28.3`). It returned 502, which was a separate problem with the apps. Files: `01-first-up.txt`, `02-first-status.txt`, `03-nginx-port-proof.txt`, `04-nginx-port-retest.txt`.
- Related commit: 292a3d8 (first run recorded), 0804137 (proof), 485a811 (fix)
- Remaining uncertainty: None for this issue.

## Entry 2 / 2026-09-27 02:06 to 02:12 / Apps unhealthy
- Symptom: app-01 and app-02 stayed "unhealthy" while postgres and redis were healthy.
- Hypothesis: At first I thought the wrong database and Redis ports in `config/app.env` (5433 and 6380) made the apps unhealthy.
- Command or test: `docker logs --tail 15 app-01` and `docker inspect app-01` to read the health check log. Then `grep -rn healthz` and a grep for the routes in `app/server.py`.
- Actual output: The app log showed `GET /healthz HTTP/1.1" 404` every 5 seconds, and the health log showed `HTTP Error 404: NOT FOUND`. The app only defines `/health`, not `/healthz`.
- Failed attempt and what changed your thinking: My database-port guess was wrong for this symptom. The logs showed the health check never reached the database at all. `/health` is only a liveness check, so it cannot fail because of the database. I learned to read the logs before trusting a guess.
- Root cause: The shared health check in `docker-compose.yml` (x-app template) called `/healthz`, which the app does not serve.
- Fix: Changed `/healthz` to `/health` in the x-app template, which fixes both apps because both use `<<: *app`.
- Retest evidence: Both apps became healthy. Requests through NGINX still returned 502. Files: `05-app-logs.txt`, `06-healthcheck-retest.txt`.
- Related commit: c338578 (proof), a87ea15 (fix)
- Remaining uncertainty: The wrong ports were still real. They showed up later on `/ready` (Entry 5).

## Entry 3 / 2026-09-27 02:14 to 02:19 / Still 502: apps only listen on 127.0.0.1
- Symptom: The apps were healthy, but every request through NGINX returned 502. The NGINX error log showed `connect() failed (111: Connection refused)` to app-01 on port 8081.
- Hypothesis: I thought the only problem was the port 8081 in `nginx.conf`.
- Command or test: From inside the nginx container: `docker exec nginx wget -qO- http://app-01:8081/health` and the same on port 8080. Then I checked the Dockerfile, `app/server.py` and the app environment in compose.
- Actual output: Both 8081 and 8080 were refused. So the app was running (its own health check worked), but no other container could connect. Compose had `APP_HOST: "127.0.0.1"`, which overrides the app default `0.0.0.0`.
- Failed attempt and what changed your thinking: My first hypothesis was incomplete, because 8080 was refused too. After fixing APP_HOST I predicted a mix of 200 and 502 (app-02 was on the correct port), but I got 10 out of 10 502. The access log showed all 10 requests went to app-01:8081 and each one was handled by a different NGINX worker (NGINX has 12 workers). Each worker keeps its own round-robin position and starts with the first server, and `proxy_next_upstream off` means no retry on app-02.
- Root cause: `APP_HOST: "127.0.0.1"` made the apps accept connections only from inside their own container. The health check runs inside the container, which is why it still passed.
- Fix: Changed `APP_HOST` to `"0.0.0.0"`.
- Retest evidence: From the nginx container, `app-01:8080/health` returned `"status":"alive"` (before: refused). Public requests still returned 502 because of port 8081 (Entry 4). Files: `07-nginx-502-logs.txt`, `08-apphost-retest.txt`.
- Related commit: 5275dc2 (proof), 6821c97 (fix)
- Remaining uncertainty: None for the bind address. The worker behaviour is handled in Entry 7.

## Entry 4 / 2026-09-27 02:23 / NGINX sends app-01 traffic to port 8081
- Symptom: All requests went to `app-01:8081` and were refused.
- Hypothesis: `nginx.conf` line 10 had `server app-01:8081`, but the app listens on 8080 (`APP_PORT: "8080"`).
- Command or test: Changed the port, ran `docker exec nginx nginx -t`, restarted nginx, then sent 10 requests to `/instance` and one to each endpoint.
- Actual output: `nginx -t` passed. 10/10 requests returned 200. `/`, `/health` and `/instance` worked. `/ready`, `/records` and `/counter` returned 503 with postgres and redis "unavailable".
- Failed attempt and what changed your thinking: None. This time my prediction (no more 502, mostly app-01) was right.
- Root cause: Wrong upstream port for app-01 in `nginx.conf`.
- Fix: `server app-01:8080`.
- Retest evidence: `09-upstream-port-retest.txt`.
- Related commit: 4fc0e36
- Remaining uncertainty: Every answer said app-01, but app-02 was also configured as "app-01" (Entry 6), so I could not yet prove that app-02 served requests.

## Entry 5 / 2026-09-27 14:29 to 14:35 / /ready 503: wrong database and Redis settings
- Symptom: `/ready` returned 503 with `"postgres":"unavailable","redis":"unavailable"`. `/records` and `/counter` also returned 503.
- Hypothesis: This was my guess from Entry 2. `config/app.env` used postgres port 5433 and redis port 6380, but postgres listens on 5432 and redis on 6379. When I compared the files I also saw that the password in `app.env` (ending `K8d`) was one letter different from the one in compose (ending `K8c`).
- Command or test: I wrote a small script (`evidence/10-conn_test.py`) and ran it inside app-01 with `docker exec -i app-01 python - < conn_test.py`. It tests each port and tries to log in to postgres with both passwords.
- Actual output: postgres:5433 and redis:6380 refused, 5432 and 6379 open. With the `app.env` password: `password authentication failed`. With the compose password: `LOGIN OK`.
- Failed attempt and what changed your thinking: My first edit of `app.env` fixed the password but missed the port on line 1. I saw it in `git diff` before testing and fixed it.
- Root cause: Three wrong starter values in `config/app.env` (two ports and the password). The database was created with the compose password, so the app had to match it.
- Fix: Changed `app.env` to port 5432, port 6379 and the correct password.
- Retest evidence: `/ready` 200 (postgres ready, redis ready), POST `/records` 201, GET `/records` 200, `/counter` counted 1 then 2, and an empty title was rejected with 400. Files: `10-dependency-proof.txt`, `10-conn_test.py`, `11-dependency-retest.txt`.
- Related commit: 9b6742e (proof), d124b01 (fix)
- Remaining uncertainty: The password was still committed in plain text. That is handled in Entry 12.

## Entry 6 / 2026-09-27 14:38 to 14:40 / app-02 says it is app-01
- Symptom: `/instance` always said app-01, so I could not prove that both backends work.
- Hypothesis: I noticed in compose that app-02 also had `INSTANCE_ID: "app-01"`.
- Command or test: `docker exec app-02 printenv INSTANCE_ID` and `docker exec nginx wget -qO- http://app-02:8080/instance` to ask app-02 directly.
- Actual output: Both returned app-01.
- Failed attempt and what changed your thinking: None.
- Root cause: Copy mistake in `docker-compose.yml`: the app-02 service had `INSTANCE_ID: "app-01"`.
- Fix: `INSTANCE_ID: "app-02"`.
- Retest evidence: Asked directly, app-02 now says app-02. Through NGINX, 20 requests split 16 app-01 and 4 app-02. Files: `12-instance-id-proof.txt`, `13-instance-id-retest.txt`.
- Related commit: 21324c0 (proof), 81f34d7 (fix)
- Remaining uncertainty: The split was uneven (Entry 7).

# Entry 7 / 2026-09-27 14:44 / Uneven traffic split between the apps
- Symptom: 20 requests split 16/4 instead of about 10/10.
- Hypothesis: Same cause I found in Entry 3. Each NGINX worker keeps its own round-robin state, so many workers send their first request to app-01.
- Command or test: Added a shared memory zone to the upstream block, ran `nginx -t`, restarted nginx and sent 20 requests to `/instance`.
- Actual output: 10 app-01 and 10 app-02.
- Failed attempt and what changed your thinking: None.
- Root cause: The upstream block had no `zone`, so the round-robin position was not shared between the 12 workers.
- Fix: Added `zone application_pool 64k;` to the upstream block in `nginx/nginx.conf`.
- Retest evidence: `14-upstream-zone-retest.txt` (evidence for the cause is in `08-apphost-retest.txt`).
- Related commit: f2daf3b
- Remaining uncertainty: None.

## Entry 8 / 2026-09-27 14:46 to 14:55 / No failover when one app stops
- Symptom: I stopped app-02 with `docker stop app-02` and sent 10 requests. They alternated between 200 from app-01 (about 5 ms) and 504 Gateway Timeout (after 2 seconds). Half of the users got an error.
- Hypothesis: `max_fails=0` means NGINX never marks a server as down, and `proxy_next_upstream off` means it never tries the other server.
- Command or test: Changed both settings, ran `nginx -t`, restarted nginx, stopped app-02, sent 10 requests to `/instance` and one POST to `/records`, then started app-02, waited 15 seconds and sent 10 more requests.
- Actual output: With app-02 stopped, 10/10 returned 200. Two requests were slower (2.0 s and 0.9 s) while NGINX detected the failure. POST `/records` returned 201. After app-02 came back, the split was 5/5.
- Failed attempt and what changed your thinking: I first thought NGINX never retries a POST. Then I learned it does not resend a POST that was already sent to an app (this could create a duplicate record), but it can retry when the connection itself failed, because nothing was sent yet.
- Root cause: The failover options in `nginx.conf` were switched off.
- Fix: `max_fails=2 fail_timeout=10s` on both servers, and `proxy_next_upstream error timeout http_502 http_503 http_504;` with `proxy_next_upstream_tries 2;`.
- Retest evidence: `15-failover-proof.txt` (before), `16-failover-retest.txt` (after).
- Related commit: 8f5712d (proof), 2d9ee10 (fix)
- Remaining uncertainty: The first requests after a failure can still take about 2 seconds (the `proxy_connect_timeout`).

## Entry 9 / 2026-09-27 15:07 to 15:18 / Database records lost after recreating postgres
- Symptom: I had 4 records. After `docker compose -p barq-assessment up -d --force-recreate postgres`, only the 2 seed records were left.
- Hypothesis: In compose I saw `tmpfs: [/var/lib/postgresql/data]` and the named volume mounted on `/var/lib/postgresql/backup`.
- Command or test: `docker exec postgres sh -c 'echo $PGDATA; df -h $PGDATA'`, `docker inspect postgres` for the mounts and `ls -A /var/lib/postgresql/backup`.
- Actual output: PGDATA is `/var/lib/postgresql/data` and it is a tmpfs (memory). The named volume was mounted on `/backup`, which was empty because postgres never writes there.
- Failed attempt and what changed your thinking: The first `ls` output was garbled by Git Bash path conversion (`D:/Git/var/...`). I ran it again with `MSYS_NO_PATHCONV=1` and added a note to the evidence file instead of editing the old output.
- Root cause: The database data lived in memory, and the persistent volume was attached to the wrong folder.
- Fix: Mounted `postgres-data` on `/var/lib/postgresql/data` and removed the tmpfs line.
- Retest evidence: The data folder is now on a real disk. I created a record "persistence test", force-recreated postgres, app-01 and app-02 (the volume was kept), and the record was still there. Files: `17-persistence-proof.txt`, `18-persistence-retest.txt`.
- Related commit: 9276510 (proof), cedded0 (fix)
- Remaining uncertainty: `docker compose down --volumes` would still delete the data. Backups are needed for that (Part 3).

## Entry 10 / 2026-09-27 15:28 to 15:36 / Network isolation
- Symptom: NGINX was attached to both the frontend and backend networks, and compose declared host ports for postgres (15432) and redis (16379).
- Hypothesis: I expected NGINX to reach the databases and the laptop to reach postgres and redis on those host ports.
- Command or test: `docker ps` for the published ports, a small Python socket test from the laptop to 127.0.0.1:15432 and 16379, `docker inspect nginx` for its networks, and `nc -z postgres 5432` plus a redis PING from inside nginx.
- Actual output: From NGINX, postgres was open and redis answered `+PONG`. But from the laptop both ports timed out, and `docker ps` showed no host mapping for them.
- Failed attempt and what changed your thinking: My hypothesis about the host ports was wrong. The backend network has `internal: true`, and Docker does not publish ports for containers that are only on an internal network. So the ports were blocked by accident, not by correct configuration. If someone removed `internal: true`, the database would become reachable.
- Root cause: NGINX was on the backend network, and the compose file declared database ports that should not exist.
- Fix: NGINX `networks: [frontend]` only. Removed the `ports:` lines of postgres and redis. The apps stay on both networks.
- Retest evidence: NGINX is only on frontend and gets `bad address` for postgres and redis (it cannot even resolve the names). Only nginx publishes a port (127.0.0.1:8080). `/ready` is 200 and the split is still 5/5. My script printed "redis answered" for the error text, which was wrong wording, so I added a correction note to the file. Files: `19-isolation-proof.txt`, `20-isolation-retest.txt`.
- Related commit: c7eef02 (proof), ff7de2b (fix)
- Remaining uncertainty: None.

## Entry 11 / 2026-09-27 15:38 to 15:54 / Redis counter resets
- Symptom: The counter went 1, 2, 3. After `up -d --force-recreate redis` it started again at 1.
- Hypothesis: The redis command was `redis-server --save "" --appendonly no`, so persistence was switched off.
- Command or test: The counter test above, plus `docker inspect redis` for its mounts.
- Actual output: The counter was reset. Redis had one mount, an anonymous `/data` volume from the image, but nothing was saved to it.
- Failed attempt and what changed your thinking: In my first commit message I wrote that redis had no volume, but the output showed one anonymous volume. I corrected the message before pushing. When I added the new `volumes` line I indented it with 8 spaces, and `docker compose config -q` failed with "did not find expected key". I fixed the indentation before starting anything.
- Root cause: Redis persistence was disabled.
- Fix: `redis-server --appendonly yes` and a named volume `redis-data:/data`. AOF writes the changes to disk about every second, which is enough for a counter.
- Retest evidence: `CONFIG GET appendonly` returns yes, and AOF files exist in `/data/appendonlydir`. Counter 1, 2, 3, recreate redis, and the next value was 4. Files: `21-redis-persistence-proof.txt`, `22-redis-persistence-retest.txt`.
- Related commit: 0889dd6 (proof), 668f163 (fix)
- Remaining uncertainty: With AOF every second, up to about 1 second of counts could be lost in a crash.

## Entry 12 / 2026-09-27 15:57 to 16:35 / Database password exposed
- Symptom: The postgres password was in plain text in several places.
- Hypothesis: It is in the committed files, in the image and in the logs.
- Command or test: `git grep -n "BarqLabOnly"`, `docker run --rm --entrypoint cat barq-assessment-app-01 /srv/app.env`, and `docker logs app-01 | grep configuration_loaded`.
- Actual output: It was in `config/app.env` and `docker-compose.yml`. The image still had the old wrong values (K8d, 5433) from before my fix, which also proved the app never used that file. The log printed the full `DATABASE_URL` with the password.
- Failed attempt and what changed your thinking: After moving the values to `.env`, plain `up -d` did not recreate anything, because the final values were the same. I used `--force-recreate` to be sure it really worked from scratch.
- Root cause: The Dockerfile copied `config/app.env` into the image, the app logged the whole URL, and the credentials were committed.
- Fix (four steps):
  1. Removed `COPY config/app.env` from the Dockerfile (c26861d).
  2. Added `redact_url()` in `app/server.py` so the log shows `barq_app:***@` (7545b57).
  3. Moved the credentials to the git-ignored `.env`, used `${VAR:?message}` in compose, updated `.env.example` with a placeholder and deleted `config/app.env` (7236e9a).
  4. The repository is public, so the old password is still visible in the git history. I did not rewrite the history. I rotated the password with `ALTER ROLE` instead. The new random password is only in `.env` (b5489bd).
- Retest evidence: `/srv/app.env` no longer exists in the image. The log shows `***` and contains the password 0 times. The 8 supplied unit tests pass. No committed file outside `evidence/` has the password. With an empty `POSTGRES_PASSWORD`, compose refuses to start with a clear message. The old password now fails and the new one works. Files: `23-secrets-proof.txt` to `27-password-rotation.txt`.
- Related commit: 623b9f9 (proof), c26861d, 7545b57, 7236e9a, b5489bd
- Remaining uncertainty: The old password can still be read in the history and in older evidence files, but it no longer works.

## Entry 13 / 2026-09-27 16:41 to 16:53 / App runs as root
- Symptom: The Dockerfile creates a user `app` but then sets `USER root`.
- Hypothesis: The app process runs as root.
- Command or test: `docker exec app-01 id` and a list of processes with their user IDs.
- Actual output: `uid=0(root)`, and `python -m app.server` runs as uid 0.
- Failed attempt and what changed your thinking: None.
- Root cause: `USER root` in the Dockerfile.
- Fix: `USER app`. The app listens on 8080 (not a privileged port) and does not write files, so it does not need root.
- Retest evidence: Both apps run as `uid=10001(app)`, are healthy, and `/ready`, `/counter` and POST `/records` work. Files: `28-root-user-proof.txt`, `29-non-root-retest.txt`.
- Related commit: 647e78c (proof), d14c2e7 (fix)
- Remaining uncertainty: The postgres, redis and nginx images use their own default users. I did not change those.

## Entry 14 / 2026-09-27 16:55 to 17:15 / No restart policy and no resource limits
- Symptom: All containers had `restart: "no"` and no CPU or memory limits.
- Hypothesis: A crashed container would stay down, and one container could use all the memory of the machine.
- Command or test: `docker inspect` for the restart policy and limits, then I killed the app process inside app-02 to simulate a crash and checked it after 20 seconds.
- Actual output: `restart=no memory=0 nanocpus=0` on all five containers. app-02 was still `Exited (137)` 20 seconds after the crash.
- Failed attempt and what changed your thinking: I forgot the redis block on my first edit. I noticed it by checking the file before starting the containers.
- Root cause: No restart policy and no limits in `docker-compose.yml`.
- Fix: `restart: unless-stopped` on all services. Limits: apps 0.5 CPU and 256 MB, postgres 1 CPU and 512 MB, redis and nginx 0.5 CPU and 128 MB each. I chose `unless-stopped` because it restarts after a crash but not after a deliberate `docker stop`, which the failure test needs.
- Retest evidence: `docker inspect` shows the policy and limits on all five containers. After killing the process, app-02 was up again within about 2 seconds and healthy after about 5 seconds (`RestartCount=1`). After `docker stop app-02` it stayed stopped, and after `docker start` the split was 5/5 again. Files: `30-restart-limits-proof.txt`, `31-restart-limits-retest.txt`.
- Related commit: fa7cf77 (proof), b8e3381 (fix)
- Remaining uncertainty: The limit values are my estimates and were not tested under real load.

## Open points (not fixed yet)
- NGINX looks up the app names only when it starts. After recreating the apps I restarted NGINX so it used the new addresses.
- The app runs with the Flask development server. `gunicorn` is in `requirements.txt` but not used.

Do not fabricate a failed attempt just to fill the template. Record actual attempts.
