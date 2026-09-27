# Log analysis

Use all three supplied logs. Answer every question with commands/scripts and actual output.

All numbers below come from `scripts/analyze_logs.py`. Its full output is saved in
`evidence/32-log-analysis-output.txt`. All times are UTC (as stated in `logs/README.md`).

## Commands / scripts

```bash
# 1. Record checksums of the original logs (they are only read, never modified)
sha256sum logs/*.log

# 2. Run the analysis (Python 3, standard library only)
python3 scripts/analyze_logs.py | tee evidence/32-log-analysis-output.txt

# 3. Check the checksums again at the end (identical = originals unchanged)
sha256sum logs/*.log
```

Checksums before and after the analysis (identical):

```
f8562d6ee86b7e7aa67e8c3474ca16eca8a1e5f521f78f3808d784be62efa754  logs/access.log
483d06cf431faa1d04a7264b015798bcde4bed1ba618f87426e79fb0d5caea05  logs/application.log
940588d00bafd6c5c7cad8a4d8a0c39b665d1fd64928d93a5d1f1810c3c6f175  logs/error.log
```

How the script handles the data:
- **Malformed lines**: a line that is not valid JSON (access, application) or does not match the NGINX error format (error) is counted and skipped.
- **Duplicates**: exact duplicate lines are counted once. The script also checks for a `request_id` that repeats with *different* content in access.log (there are none).
- **Unit of counting**: one client request = one unique `request_id` in **access.log**. NGINX writes one access line per client request, even when it tried two upstreams, so retries are not counted twice. The application and error logs are only used to explain what happened to each request, never to count requests.
- **Backends**: the upstream addresses were mapped to instances with requests that had one upstream attempt and also appear in application.log: `172.23.0.11` = app-01 (360 of 360), `172.23.0.12` = app-02 (301 of 301).

## Results

### 1. What UTC interval is covered? How many valid, malformed and duplicate lines are in each file?

The logs cover **2026-08-20 11:00:00.015 to 11:30:00 UTC** (30 minutes).

| File | Lines | Valid | Malformed | Exact duplicates | First | Last |
|---|---|---|---|---|---|---|
| access.log | 726 | 725 | 1 (line 311) | 5 | 11:00:00.015 | 11:29:57.578 |
| application.log | 730 | 729 | 1 (line 401) | 2 | 11:00:00.015 | 11:29:57.578 |
| error.log | 68 | 68 | 0 | 0 | 11:05:02 | 11:30:00 |

"Valid" includes the duplicates. Both malformed lines are cut off in the middle of the JSON (for example `{"timestamp":"2026-08-20T11:12:48Z","request_id":`). The last error.log line is a `[notice]` ("log collector rotated stream"), not an error.

### 2. How many distinct client requests occurred? How did you deduplicate and avoid counting retries twice?

**720 distinct client requests.**

- 725 valid access lines minus 5 exact duplicates = 720 unique `request_id`s. No `request_id` repeats with different content.
- 19 of these requests have two upstreams (for example `"172.23.0.12:8080, 172.23.0.11:8080"`). That is 739 upstream attempts, but still 720 client requests, because each client request has one access line.
- Cross-check with application.log: 680 unique `request_id`s have an `http_request` event. The other 40 are exactly the 40 requests that got 502 because app-02 refused the connection, so the app never saw them (see Q9). 680 + 40 = 720.
- The 47 `dependency_error` events in application.log share a `request_id` with an `http_request` event of the same request, so they are not extra requests.

### 3. What are the final client status counts and error rate? State your denominator.

Final status = the `status` field in access.log (what the client received).

| Status | Count | Share |
|---|---|---|
| 200 | 615 | 85.42% |
| 404 | 10 | 1.39% |
| 502 | 40 | 5.56% |
| 503 | 47 | 6.53% |
| 504 | 8 | 1.11% |

**Denominator: 720 distinct client requests.**
- **Server error rate (5xx): 95 / 720 = 13.19%**
- Client errors (4xx): 10 / 720 = 1.39%. These are all requests to `/missing`, which does not exist, so I do not count them as service failures.

### 4. Which paths, time windows and backends account for the failures?

There are four separate failure windows, each with one type of error:

| Window (UTC) | Failures | Status | Paths | Backend |
|---|---|---|---|---|
| 11:05:02 to 11:09:57 | 40 | 502 | `/`, `/health`, `/records`, `/counter` (10 each) | all app-02 |
| 11:12:09 to 11:15:52 | 31 | 503 (Redis) | `/ready` 15, `/counter` 16 | both apps |
| 11:20:07 to 11:21:45 | 16 | 503 (Postgres) | `/ready` 8, `/records` 8 | both apps |
| 11:25:14 to 11:26:47 | 8 | 504 | `/records` (8) | app-01 4, app-02 4 |

By path over the whole period: `/records` 26 (10 × 502, 8 × 503, 8 × 504), `/counter` 26 (10 × 502, 16 × 503), `/ready` 23 (503), `/` 10 (502), `/health` 10 (502).

By backend: all 40 × 502 were on app-02. The 503s were split evenly (app-01 23, app-02 24) and so were the 504s (4 and 4). So only the 502 window was a single-instance problem. The 503 and 504 windows affected both apps equally, which points to something they share (Redis, Postgres).

### 5. What are the median and p95 client latencies? State the percentile method and units.

Source: `request_time` in access.log, in **seconds**, one value per distinct client request.
**Method: nearest-rank**. The values are sorted, and the p-th percentile is the value at rank `ceil(p/100 × n)`.

| Requests | n | Median | p95 | Max |
|---|---|---|---|---|
| All | 720 | 0.054 s | 2.001 s | 2.025 s |
| 2xx only | 615 | 0.055 s | 0.093 s | 0.120 s |
| 5xx only | 95 | 0.041 s | 2.025 s | 2.025 s |

Normal requests are fast (p95 under 0.1 s). The high overall p95 comes from the failures: the Redis 503s all took 2.025 s (the app waited for Redis until it timed out) and the 504s about 2.001 s (NGINX timeout). The Postgres 503s took only 0.041 s and the 502s only 0.003 s, because those failed immediately.

### 6. Which requests retried upstream? How many succeeded after retrying?

**19 requests were retried, and all 19 succeeded.**
- All of them went app-02 first (`502`, connection refused) and then app-01 (`200`): `upstream_status "502, 200"`.
- All are between 11:05:07 and 11:09:37, inside the app-02 outage.
- Only two paths were retried: `/ready` (10) and `/instance` (9). The other paths that went to app-02 in the same window (`/`, `/health`, `/records`, `/counter`) were not retried and returned 502. So the NGINX configuration at that time seems to have allowed retries only for some locations (see Q10).
- IDs: lab-000124, 130, 136, 142, 148, 154, 160, 166, 172, 178, 184, 190, 196, 202, 208, 214, 220, 226, 232.
- All 19 also appear in error.log as "connect() failed (111: Connection refused)" to `172.23.0.12` (19/19).

## Timeline and correlated examples

### 7. Incident timeline (access, error AND application logs)

Per-minute counts from the script (access = what the client saw, error = NGINX errors, app = dependency errors). Minutes without problems are shortened.

```
UTC   reqs  2xx  4xx  502  503  504 retry | refused timeout | dep_pg dep_rd
11:00   24   23    1    0    0    0     0 |       0       0 |      0      0
...     (11:00-11:04 normal)
11:05   24   16    0    8    0    0     4 |      12       0 |      0      0
11:06   24   15    1    8    0    0     4 |      12       0 |      0      0
11:07   24   16    0    8    0    0     4 |      12       0 |      0      0
11:08   24   16    0    8    0    0     4 |      12       0 |      0      0
11:09   24   15    1    8    0    0     3 |      11       0 |      0      0
11:10   24   24    0    0    0    0     0 |       0       0 |      0      0
11:11   24   24    0    0    0    0     0 |       0       0 |      0      0
11:12   24   16    0    0    8    0     0 |       0       0 |      0      8
11:13   24   16    1    0    7    0     0 |       0       0 |      0      7
11:14   24   16    0    0    8    0     0 |       0       0 |      0      8
11:15   24   16    0    0    8    0     0 |       0       0 |      0      8
11:16   24   23    1    0    0    0     0 |       0       0 |      0      0
...     (11:16-11:19 normal)
11:20   24   16    0    0    8    0     0 |       0       0 |      8      0
11:21   24   16    0    0    8    0     0 |       0       0 |      8      0
11:22   24   24    0    0    0    0     0 |       0       0 |      0      0
...     (11:22-11:24 normal)
11:25   24   20    0    0    0    4     0 |       0       4 |      0      0
11:26   24   19    1    0    0    4     0 |       0       4 |      0      0
11:27   24   24    0    0    0    0     0 |       0       0 |      0      0
...     (11:27-11:29 normal)
11:30  error.log [notice] "log collector rotated stream"
```

| Time (UTC) | Event | Evidence |
|---|---|---|
| 11:00:00 to 11:04:59 | Normal traffic, 24 requests/minute, only 404s for `/missing` | access |
| **11:05:02** | **app-02 stops accepting connections.** First 502 (lab-000122, `/health`) | access 502 + error "Connection refused" to .12; no app-02 line |
| 11:05:07 to 11:09:37 | 19 requests to `/ready` and `/instance` are retried on app-01 and succeed | access `"502, 200"` + error refused + app-01 `http_request` 200 |
| 11:09:52 | app-02 answers one request (lab-000238, `/missing`, 404) | access + app-02 `http_request` |
| 11:09:57 | app-02 refuses again (lab-000240, `/`, 502), the last 502 | access + error refused |
| **11:10:02** | **app-02 serves normally again** (lab-000242, `/health`, 200) | access + app |
| **11:12:09 to 11:15:52** | **Redis timeouts on both apps**: 31 × 503 on `/ready` and `/counter`, each 2.025 s | access 503 + app `dependency_error redis TimeoutError`; nothing in error.log |
| **11:20:07 to 11:21:45** | **Postgres errors on both apps**: 16 × 503 on `/ready` and `/records`, each 0.041 s (failed fast) | access 503 + app `dependency_error postgres` |
| **11:25:14 to 11:26:47** | **`/records` responses take 2.7 s; NGINX gives up after about 2 s**: 8 × 504 | access 504 + error "upstream timed out ... reading response header" + app `http_request` 200 with `duration_ms` 2700 |
| 11:27 to 11:29:57 | Normal again | access |
| 11:30:00 | Log collector rotated the stream | error.log `[notice]` |

### 8. One correlated failed request and one successful request

**Failed: lab-000122 (502, app-02 down)**
```
access.log line 123: 2026-08-20T11:05:02.503Z GET /health status=502 upstream=172.23.0.12:8080 upstream_status=502 request_time=0.003s
error.log  line 1:   2026/08/20 11:05:02 [error] 31#31: *122 connect() failed (111: Connection refused) while connecting to upstream, request_id=lab-000122, request: "GET /health HTTP/1.1", upstream: "http://172.23.0.12:8080/health"
application.log:     (no line: the request never reached app-02)
```

**Failed: lab-000606 (504, timeout while the app succeeded)**
```
access.log line 612: 2026-08-20T11:25:14.501Z GET /records status=504 upstream=172.23.0.12:8080 upstream_status=504 request_time=2.001s
error.log  line 60:  2026/08/20 11:25:14 [error] 31#31: *606 upstream timed out (110: Operation timed out) while reading response header from upstream, request_id=lab-000606, request: "GET /records ..."
app.log    line 616: 2026-08-20T11:25:15.200Z http_request instance_id=app-02 status=200 duration_ms=2700
```

**Failed: lab-000292 (503, Redis)**
```
access.log line 294: 2026-08-20T11:12:09.525Z GET /ready status=503 upstream=172.23.0.12:8080 upstream_status=503 request_time=2.025s
app.log    line 253: 2026-08-20T11:12:09.524Z dependency_error instance_id=app-02 dependency=redis error_type=TimeoutError
app.log    line 254: 2026-08-20T11:12:09.525Z http_request instance_id=app-02 status=503 duration_ms=2025.0
```

**Retried, then successful: lab-000124**
```
access.log line 125: 2026-08-20T11:05:07.620Z GET /ready status=200 upstream=172.23.0.12:8080, 172.23.0.11:8080 upstream_status=502, 200 request_time=0.12s
error.log  line 2:   2026/08/20 11:05:07 [error] 31#31: *124 connect() failed (111: Connection refused) while connecting to upstream, request_id=lab-000124, request: "GET /ready HTTP/1.1"
app.log    line 123: 2026-08-20T11:05:07.620Z http_request instance_id=app-01 status=200 duration_ms=120.0
```

**Successful: lab-000121**
```
access.log line 121: 2026-08-20T11:05:00.055Z GET / status=200 upstream=172.23.0.11:8080 upstream_status=200 request_time=0.055s
app.log    line 121: 2026-08-20T11:05:00.055Z http_request instance_id=app-01 status=200 duration_ms=55.0
```

### 9. Which errors are proxy/connectivity issues versus dependency/application issues? What proves it?

Cross-tab from the script (every 5xx request, by what the other two logs say about the same `request_id`):

```
client error.log            app dependency_error   app http status  count
502    connection_refused   -                      no app record    40
503    -                    postgres               503              16
503    -                    redis                  503              31
504    upstream_timeout     -                      200              8
```

**Proxy / connectivity (48 requests):**
- **502 (40)**: NGINX logged "connect() failed (111: Connection refused)" for every one, and the app has **no record** of them. The request never reached an application process, so this is a connectivity problem between NGINX and app-02 (app-02 was not listening).
- **504 (8)**: NGINX logged "upstream timed out ... while reading response header", but the app logged the same requests as **200** after 2700 ms. The app worked, only too slowly for NGINX's timeout. The error came from the proxy.

**Dependency / application (47 requests):**
- **503 (47)**: NGINX logged **nothing** in error.log, because for NGINX a 503 is a normal response. The app itself logged a `dependency_error` (redis `TimeoutError` or postgres) followed by its own 503. So the app was reachable and working, but one of its dependencies failed.

### 10. What do the logs not prove? What would you check next in a running environment?

What the logs do **not** prove:
- **Why app-02 went down at 11:05**, and why it flapped (answered at 11:09:52, refused at 11:09:57, fine from 11:10:02). There are no container events, exit codes or restart counts in these logs.
- **Why Redis and Postgres failed.** We only see the apps' side (timeouts/errors). There are no Redis or Postgres logs.
- **Why the `/records` responses became slow (2.7 s)** at 11:25. It happened on both apps, which points to something shared such as the database, but this is not proven.
- **Why only `/ready` and `/instance` were retried.** The NGINX configuration from that time is not in the logs. The 504s at about 2.0 s also suggest a 2-second read timeout at that time, which is different from the current `proxy_read_timeout 3s`, so the historical config was probably not the same as the starter config.
- **Whether data was lost.** The 504 requests were `GET /records` (reads), so no write was repeated or lost, but the logs cannot show what was stored.
- **Whether the logs are complete.** There are malformed and duplicate lines, and the log stream was rotated at 11:30. All requests come from a single client (`192.0.2.24`, a documentation address), so this looks like a synthetic test load, not real users.
- The error.log timestamps have only second precision, so ordering within the same second is not certain.

What I would check next in a running environment:
- `docker ps -a`, `docker inspect app-02` (`State.ExitCode`, `State.OOMKilled`, `RestartCount`) and `docker events` for the time of the outage.
- `docker logs redis` and `docker logs postgres` for the dependency windows, plus `docker stats` for CPU and memory.
- The NGINX configuration (`proxy_next_upstream`, `proxy_read_timeout`) that was in use, to confirm the retry and timeout behaviour.
- The slow `/records` query in Postgres (for example with `pg_stat_statements`).

## Conclusions and limits

- 720 client requests in 30 minutes, 95 failures (13.19%), in **four separate incidents**: app-02 unreachable (502), Redis timeouts (503), Postgres errors (503), and slow `/records` responses cut off by NGINX (504).
- Retrying on the other instance worked: every retried request (19/19) succeeded. But retries were only enabled for two paths, so 40 other requests failed that could have been saved. This is the same weakness I found and fixed in the starter environment (`proxy_next_upstream off`, Entry 8 in `troubleshooting.md`).
- The 504s are a **timeout mismatch**: the app answered 200, but the client got an error, because NGINX waited less than the app needed.
- The 503 windows hit both apps at the same time, so adding more app instances would not help. The shared dependencies (Redis, Postgres) are single points of failure.
- Limits: the numbers depend on my parsing rules (skip malformed lines, count exact duplicates once, one request per access-log `request_id`). The causes in Q10 are hypotheses, not proven by these logs.
