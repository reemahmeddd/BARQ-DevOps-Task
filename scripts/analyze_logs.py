#!/usr/bin/env python3
"""Answer the log_analysis.md questions from logs/ (read-only). Usage: python3 scripts/analyze_logs.py"""
import collections
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "logs"
ERROR_RE = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] \d+#\d+: (?:\*\d+ )?(.*)$")


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_lines(name):
    return (LOGS / name).read_text(encoding="utf-8", errors="replace").splitlines()


def load_json_log(name):
    """Return (records, malformed line numbers, duplicate line count). Exact duplicate lines are kept once."""
    records, malformed, seen, duplicates = [], [], set(), 0
    for number, line in enumerate(read_lines(name), 1):
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or "timestamp" not in record:
                raise ValueError
        except ValueError:
            malformed.append(number)
            continue
        if line in seen:
            duplicates += 1
            continue
        seen.add(line)
        record["_line"] = number
        records.append(record)
    return records, malformed, duplicates


def load_error_log():
    records, malformed, seen, duplicates = [], [], set(), 0
    for number, line in enumerate(read_lines("error.log"), 1):
        match = ERROR_RE.match(line)
        if not match:
            malformed.append(number)
            continue
        if line in seen:
            duplicates += 1
            continue
        seen.add(line)
        when, level, message = match.groups()
        rid = re.search(r"request_id=([^,\s]+)", message)
        upstream = re.search(r'upstream: "http://([^/"]+)', message)
        request = re.search(r'request: "(\w+) (\S+)', message)
        kind = ("connection_refused" if "Connection refused" in message else
                "upstream_timeout" if "timed out" in message else level)
        records.append({"_line": number, "time": datetime.strptime(when, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc),
                        "level": level, "kind": kind, "request_id": rid.group(1) if rid else None,
                        "upstream": upstream.group(1) if upstream else None,
                        "path": request.group(2) if request else None, "raw": line})
    return records, malformed, duplicates


def percentile(values, p):
    """Nearest-rank percentile: the value at rank ceil(p/100 * n) of the sorted list."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def minute(dt):
    return dt.strftime("%H:%M")


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main():
    access_raw, access_bad, access_dup = load_json_log("access.log")
    app_raw, app_bad, app_dup = load_json_log("application.log")
    errors, error_bad, error_dup = load_error_log()

    # One client request = one request_id in access.log. Report ids that repeat with different content.
    requests, conflicts = {}, []
    for record in access_raw:
        rid = record["request_id"]
        if rid in requests:
            conflicts.append(rid)
            continue
        record["time"] = ts(record["timestamp"])
        record["request_time"] = float(record["request_time"])
        record["attempts"] = [u.strip() for u in str(record["upstream"]).split(",")]
        record["attempt_status"] = [s.strip() for s in str(record["upstream_status"]).split(",")]
        requests[rid] = record
    reqs = sorted(requests.values(), key=lambda r: r["time"])

    app_http, app_dep = collections.defaultdict(list), collections.defaultdict(list)
    for record in app_raw:
        record["time"] = ts(record["timestamp"])
        (app_http if record["event"] == "http_request" else app_dep)[record["request_id"]].append(record)
    err_by_id = collections.defaultdict(list)
    for record in errors:
        if record["request_id"]:
            err_by_id[record["request_id"]].append(record)

    # Map upstream address -> instance using single-attempt requests that the app also logged.
    ip_votes = collections.defaultdict(collections.Counter)
    for r in reqs:
        if len(r["attempts"]) == 1 and r["request_id"] in app_http:
            ip_votes[r["attempts"][0]][app_http[r["request_id"]][0]["instance_id"]] += 1
    backend = {ip: votes.most_common(1)[0][0] for ip, votes in ip_votes.items()}
    name = lambda ip: f"{ip} ({backend.get(ip, '?')})"

    section("Q1. UTC interval, valid / malformed / duplicate lines per file")
    all_times = [r["time"] for r in reqs] + [r["time"] for r in app_raw] + [r["time"] for r in errors]
    print(f"Interval covered (all files): {min(all_times).isoformat()} -> {max(all_times).isoformat()}")
    for label, total, valid, bad, dup in [
            ("access.log", len(read_lines("access.log")), len(access_raw) + access_dup, access_bad, access_dup),
            ("application.log", len(read_lines("application.log")), len(app_raw) + app_dup, app_bad, app_dup),
            ("error.log", len(read_lines("error.log")), len(errors) + error_dup, error_bad, error_dup)]:
        print(f"  {label:16} lines={total:4}  valid={valid:4}  malformed={len(bad)} {bad}  exact_duplicates={dup}")
    for label, recs in [("access.log", reqs), ("application.log", app_raw), ("error.log", errors)]:
        times = [r["time"] for r in recs]
        print(f"  {label:16} first={min(times).isoformat()}  last={max(times).isoformat()}")

    section("Q2. Distinct client requests and deduplication")
    print(f"Distinct client requests (unique request_id in access.log): {len(reqs)}")
    print(f"  valid access lines {len(access_raw) + access_dup} - exact duplicates {access_dup} = {len(access_raw)}")
    print(f"  request_ids repeated with DIFFERENT content: {len(conflicts)} {conflicts}")
    retried = [r for r in reqs if len(r["attempts"]) > 1]
    print(f"  requests with more than one upstream attempt (still ONE client request each): {len(retried)}")
    print(f"  upstream attempts in total: {sum(len(r['attempts']) for r in reqs)}")
    ids_app = set(app_http)
    print(f"Cross-check: unique request_ids with an http_request event in application.log: {len(ids_app)}")
    print(f"  in access but not in app log: {len(set(requests) - ids_app)}   in app but not in access: {len(ids_app - set(requests))}")
    print(f"  app log records sharing a request_id with an http_request (dependency_error): "
          f"{sum(len(v) for v in app_dep.values())}")

    section("Q3. Final client status counts and error rate")
    status = collections.Counter(r["status"] for r in reqs)
    for code, count in sorted(status.items()):
        print(f"  {code}: {count:4}  ({count / len(reqs):.2%})")
    server_err = sum(c for s, c in status.items() if s >= 500)
    client_err = sum(c for s, c in status.items() if 400 <= s < 500)
    print(f"Denominator: {len(reqs)} distinct client requests (final status from access.log)")
    print(f"  5xx error rate: {server_err}/{len(reqs)} = {server_err / len(reqs):.2%}")
    print(f"  4xx rate:       {client_err}/{len(reqs)} = {client_err / len(reqs):.2%} (client errors, e.g. unknown path)")

    section("Q4. Failures (5xx) by path, time window and backend")
    fails = [r for r in reqs if r["status"] >= 500]
    print("By path and status:")
    for (path, code), count in sorted(collections.Counter((r["path"], r["status"]) for r in fails).items()):
        print(f"  {path:10} {code}: {count}")
    print("By backend attempted (every attempt of a failed request):")
    for (ip, code), count in sorted(collections.Counter((a, r["status"]) for r in fails for a in r["attempts"]).items()):
        print(f"  {name(ip):28} status {code}: {count}")
    print("Time windows (consecutive minutes containing 5xx):")
    by_min = collections.defaultdict(list)
    for r in fails:
        by_min[r["time"].replace(second=0, microsecond=0)].append(r)
    window = []
    for m in sorted(by_min):
        if window and (m - window[-1]).seconds > 60:
            wf = [r for w in window for r in by_min[w]]
            print(f"  {minute(window[0])}-{minute(window[-1])} UTC: {len(wf)} failures {dict(collections.Counter(r['status'] for r in wf))}")
            window = []
        window.append(m)
    if window:
        wf = [r for w in window for r in by_min[w]]
        print(f"  {minute(window[0])}-{minute(window[-1])} UTC: {len(wf)} failures {dict(collections.Counter(r['status'] for r in wf))}")
    for code in sorted({r["status"] for r in fails}):
        times = [r["time"] for r in fails if r["status"] == code]
        print(f"  status {code}: first {min(times).strftime('%H:%M:%S')}  last {max(times).strftime('%H:%M:%S')}")

    section("Q5. Client latency (access.log request_time, seconds)")
    method = "nearest-rank: sorted values, p-th percentile = value at rank ceil(p/100 * n)"
    for label, subset in [("all requests", reqs), ("2xx only", [r for r in reqs if r["status"] < 300]),
                          ("5xx only", fails)]:
        values = [r["request_time"] for r in subset]
        print(f"  {label:13} n={len(values):4}  median={percentile(values, 50):.3f}s  p95={percentile(values, 95):.3f}s  "
              f"max={max(values):.3f}s")
    print(f"  Method: {method}. Units: seconds (request_time). Denominator: distinct requests.")

    section("Q6. Requests retried upstream")
    outcome = collections.Counter((" -> ".join(r["attempt_status"]), r["status"]) for r in retried)
    for (chain, final), count in outcome.items():
        print(f"  attempts {chain:12} final client status {final}: {count}")
    ok_after = sum(1 for r in retried if r["status"] < 400)
    print(f"Retried: {len(retried)}   succeeded after retry: {ok_after}   failed after retry: {len(retried) - ok_after}")
    print(f"Attempt order: {collections.Counter(' -> '.join(name(a) for a in r['attempts']) for r in retried)}")
    print(f"Time range: {retried[0]['time'].strftime('%H:%M:%S')} -> {retried[-1]['time'].strftime('%H:%M:%S')}" if retried else "")
    print("IDs:", ", ".join(r["request_id"] for r in retried))

    section("Q7. Timeline per minute (access = client view, error = nginx, app = dependency errors)")
    minutes = sorted({r["time"].replace(second=0, microsecond=0) for r in reqs + app_raw + errors})
    print(f"  {'UTC':5} {'reqs':>4} {'2xx':>4} {'4xx':>4} {'502':>4} {'503':>4} {'504':>4} {'retry':>5} | "
          f"{'refused':>7} {'timeout':>7} | {'dep_pg':>6} {'dep_rd':>6}")
    for m in minutes:
        rm = [r for r in reqs if r["time"].replace(second=0, microsecond=0) == m]
        em = [e for e in errors if e["time"].replace(second=0, microsecond=0) == m]
        dm = [d for d in app_raw if d["event"] == "dependency_error" and d["time"].replace(second=0, microsecond=0) == m]
        c = collections.Counter(r["status"] for r in rm)
        print(f"  {minute(m):5} {len(rm):4} {sum(v for k, v in c.items() if k < 300):4} "
              f"{sum(v for k, v in c.items() if 400 <= k < 500):4} {c[502]:4} {c[503]:4} {c[504]:4} "
              f"{sum(1 for r in rm if len(r['attempts']) > 1):5} | "
              f"{sum(1 for e in em if e['kind'] == 'connection_refused'):7} {sum(1 for e in em if e['kind'] == 'upstream_timeout'):7} | "
              f"{sum(1 for d in dm if d['dependency'] == 'postgres'):6} {sum(1 for d in dm if d['dependency'] == 'redis'):6}")
    for e in errors:
        if e["level"] != "error":
            print(f"  non-error line {e['_line']}: {e['raw']}")

    section("Q8. Correlated examples (same request_id in all logs)")
    def show(rid):
        r = requests[rid]
        print(f"  access.log line {r['_line']}: {r['timestamp']} {r['method']} {r['path']} status={r['status']} "
              f"upstream={r['upstream']} upstream_status={r['upstream_status']} request_time={r['request_time']}s")
        for e in err_by_id.get(rid, []):
            print(f"  error.log  line {e['_line']}: {e['raw'][:170]}")
        for a in app_http.get(rid, []) + app_dep.get(rid, []):
            extra = {k: a[k] for k in ("instance_id", "status", "duration_ms", "dependency", "error_type") if k in a}
            print(f"  app.log    line {a['_line']}: {a['timestamp']} {a['event']} {extra}")
    for label, pick in [
            ("Failed 502 (connection refused)", lambda r: r["status"] == 502 and r["request_id"] in err_by_id),
            ("Failed 503 (dependency)", lambda r: r["status"] == 503 and r["request_id"] in app_dep),
            ("Failed 504 (timeout)", lambda r: r["status"] == 504 and r["request_id"] in err_by_id),
            ("Retried then succeeded", lambda r: len(r["attempts"]) > 1 and r["status"] < 400),
            ("Successful", lambda r: r["status"] == 200 and r["request_id"] in app_http and r["time"].minute >= 5)]:
        match = next((r for r in reqs if pick(r)), None)
        if match:
            print(f"-- {label}: {match['request_id']}")
            show(match["request_id"])

    section("Q9. Proxy/connectivity vs dependency/application failures")
    rows = collections.Counter()
    for r in fails:
        kinds = sorted({e["kind"] for e in err_by_id.get(r["request_id"], [])}) or ["-"]
        deps = sorted({d["dependency"] for d in app_dep.get(r["request_id"], [])}) or ["-"]
        app_status = sorted({a["status"] for a in app_http.get(r["request_id"], [])}) or ["no app record"]
        rows[(r["status"], ",".join(kinds), ",".join(deps), ",".join(map(str, app_status)))] += 1
    print(f"  {'client':6} {'error.log':20} {'app dependency_error':22} {'app http status':16} count")
    for (code, kinds, deps, app_status), count in sorted(rows.items()):
        print(f"  {code:<6} {kinds:20} {deps:22} {app_status:16} {count}")
    timeouts = [r for r in fails if r["status"] == 504]
    if timeouts:
        print("  504 details (request_time vs app duration_ms):")
        for r in timeouts:
            app = app_http.get(r["request_id"], [{}])[0]
            print(f"    {r['request_id']} {r['path']} request_time={r['request_time']}s upstream={name(r['attempts'][-1])} "
                  f"app duration_ms={app.get('duration_ms')} app status={app.get('status')}")
    print(f"  retried requests also listed in error.log as connection_refused: "
          f"{sum(1 for r in retried if any(e['kind'] == 'connection_refused' for e in err_by_id.get(r['request_id'], [])))}/{len(retried)}")

    section("Backend mapping used above (from single-attempt requests also in application.log)")
    for ip, votes in sorted(ip_votes.items()):
        print(f"  {ip} -> {dict(votes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
