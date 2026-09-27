#!/usr/bin/env python3
"""Failure test: stop one app backend under traffic, restore it and prove it serves requests again.

Usage: python3 failure_test.py [--target app-02] [--url http://127.0.0.1:8080] [--project barq-assessment]
                               [--down 15] [--max-error-rate 0.01]
Exit code: 0 = pass, 1 = a check failed, 2 = refused to run (unsafe target or missing docker).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def get_instance(url):
    """One request to /instance. Returns (ok, instance_id or error text, seconds)."""
    start = time.monotonic()
    try:
        with urllib.request.urlopen(f"{url}/instance", timeout=5) as response:
            body = json.loads(response.read())
            return response.status == 200, body.get("instance_id"), time.monotonic() - start
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}", time.monotonic() - start
    except (urllib.error.URLError, OSError, ValueError) as error:
        return False, type(error).__name__, time.monotonic() - start


def traffic(url, seconds=None, until=None, limit=60, interval=0.1):
    """Send requests for `seconds`, or until `until(samples)` is true (at most `limit` seconds)."""
    samples, deadline = [], time.monotonic() + (seconds if seconds is not None else limit)
    while time.monotonic() < deadline:
        samples.append(get_instance(url))
        if until and until(samples):
            break
        time.sleep(interval)
    return samples


def summary(label, samples):
    errors = [s for s in samples if not s[0]]
    served = {}
    for ok, who, _ in samples:
        if ok:
            served[who] = served.get(who, 0) + 1
    times = sorted(s[2] for s in samples)
    p95 = times[max(1, math.ceil(0.95 * len(times))) - 1] if times else 0
    error_kinds = {}
    for _, who, _ in errors:
        error_kinds[who] = error_kinds.get(who, 0) + 1
    rate = len(errors) / len(samples) if samples else 1.0
    print(f"  [{label}] requests={len(samples)} errors={len(errors)} error_rate={rate:.2%} "
          f"p95={p95:.3f}s max={(times[-1] if times else 0):.3f}s served_by={served}"
          + (f" errors={error_kinds}" if errors else ""), flush=True)
    return rate, served


def state(name):
    result = docker("inspect", "-f", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", name)
    return result.stdout.strip() if result.returncode == 0 else "missing"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=f"http://127.0.0.1:{os.environ.get('PUBLIC_PORT', '8080')}")
    parser.add_argument("--project", default="barq-assessment")
    parser.add_argument("--target", default="app-02")
    parser.add_argument("--down", type=int, default=15, help="seconds to keep the target stopped")
    parser.add_argument("--recovery-timeout", type=int, default=60)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    args = parser.parse_args()
    url = args.url.rstrip("/")

    # Safety: only a running app container of this compose project, and never the last app.
    try:
        listing = docker("ps", "--filter", f"label=com.docker.compose.project={args.project}", "--format", "{{.Names}}")
    except FileNotFoundError:
        print("ERROR: docker CLI not found")
        return 2
    running_apps = sorted(n for n in listing.stdout.split() if n.startswith("app-"))
    if args.target not in running_apps:
        print(f"REFUSED: {args.target} is not a running app container of project {args.project} ({running_apps})")
        return 2
    if len(running_apps) < 2:
        print(f"REFUSED: {args.target} is the only running app; stopping it would take the service down")
        return 2
    others = [a for a in running_apps if a != args.target]
    print(f"Failure test on {url}: target={args.target}, remaining={others}, down for {args.down}s\n")

    stopped = False
    try:
        print("Phase 1: baseline (5s)")
        rate, served = summary("baseline", traffic(url, seconds=5))
        check("baseline: no errors", rate == 0, f"{rate:.2%}")
        check("baseline: every app serves", set(running_apps) <= set(served), f"{served}")

        print(f"\nPhase 2: docker stop {args.target}, traffic for {args.down}s")
        stop_started = time.monotonic()
        result = docker("stop", args.target)
        stopped = result.returncode == 0
        check(f"{args.target} stopped", stopped and state(args.target).startswith("exited"),
              f"{state(args.target)}, took {time.monotonic() - stop_started:.1f}s")
        rate, served = summary("during failure", traffic(url, seconds=args.down))
        check(f"service stays available (error rate <= {args.max_error_rate:.0%})", rate <= args.max_error_rate,
              f"{rate:.2%}")
        check(f"stopped {args.target} serves nothing", args.target not in served, f"{served}")
        check("remaining apps keep serving", set(others) <= set(served), f"{served}")

        print(f"\nPhase 3: docker start {args.target}, wait until healthy and serving again")
        docker("start", args.target)
        stopped = False
        start = time.monotonic()
        while state(args.target) != "running healthy" and time.monotonic() - start < args.recovery_timeout:
            time.sleep(1)
        healthy_after = time.monotonic() - start
        check(f"{args.target} healthy again within {args.recovery_timeout}s", state(args.target) == "running healthy",
              f"{state(args.target)} after {healthy_after:.1f}s")
        samples = traffic(url, until=lambda s: any(ok and who == args.target for ok, who, _ in s),
                          limit=args.recovery_timeout)
        rate, served = summary("recovery", samples)
        check(f"{args.target} serves requests again through nginx", args.target in served,
              f"first answer {time.monotonic() - start:.1f}s after start")
        check("no errors while recovering", rate <= args.max_error_rate, f"{rate:.2%}")

        print("\nPhase 4: after recovery (5s)")
        rate, served = summary("after", traffic(url, seconds=5))
        check("after: no errors", rate == 0, f"{rate:.2%}")
        check("after: every app serves", set(running_apps) <= set(served), f"{served}")
    finally:
        if stopped or not state(args.target).startswith("running"):
            print(f"\nCleanup: starting {args.target} again")
            docker("start", args.target)

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    print("RESULT: PASS" if failed == 0 else f"RESULT: FAIL ({failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted (cleanup already ran)")
        sys.exit(1)
