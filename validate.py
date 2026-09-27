#!/usr/bin/env python3
"""Validate the running lab: public access, endpoints, all backends, dependencies, isolation and ports.

Usage: python3 validate.py [--url http://127.0.0.1:8080] [--project barq-assessment] [--timeout 90]
Exit code: 0 = all checks passed, 1 = at least one check failed, 2 = could not run (e.g. no docker).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def docker(*args, timeout=20):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def http(method, url, body=None, timeout=5):
    """Return (status, headers, parsed JSON or text). status 0 means no HTTP answer."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, headers, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, headers, raw = error.code, error.headers, error.read()
    except (urllib.error.URLError, OSError) as error:
        return 0, {}, str(error)
    try:
        return status, headers, json.loads(raw)
    except ValueError:
        return status, headers, raw.decode(errors="replace")


def compose_containers(project):
    """Map service name -> container inspect data for the compose project."""
    names = docker("ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Names}}")
    if names.returncode != 0:
        print(f"ERROR: docker ps failed: {names.stderr.strip()}")
        sys.exit(2)
    ids = names.stdout.split()
    if not ids:
        return {}
    data = json.loads(docker("inspect", *ids).stdout)
    return {c["Config"]["Labels"]["com.docker.compose.service"]: c for c in data}


def networks_of(container):
    return set(container["NetworkSettings"]["Networks"])


def wait_until_ready(url, project, timeout):
    """Bounded wait: /ready must return 200 and every container with a health check must be healthy."""
    deadline = time.monotonic() + timeout
    while True:
        status, _, _ = http("GET", f"{url}/ready", timeout=3)
        containers = compose_containers(project)
        unhealthy = [s for s, c in containers.items()
                     if c["State"].get("Health", {}).get("Status", "healthy") != "healthy" or not c["State"]["Running"]]
        if status == 200 and containers and not unhealthy:
            return True, f"ready after {timeout - (deadline - time.monotonic()):.0f}s"
        if time.monotonic() >= deadline:
            state = (f"not healthy/running: {unhealthy}" if unhealthy else
                     "all containers healthy" if containers else "no containers found")
            return False, f"gave up after {timeout}s: /ready={status}, {state}"
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_port = os.environ.get("PUBLIC_PORT", "8080")
    parser.add_argument("--url", default=f"http://127.0.0.1:{default_port}")
    parser.add_argument("--project", default="barq-assessment")
    parser.add_argument("--timeout", type=int, default=90, help="max seconds to wait for readiness")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    try:
        docker("version", "--format", "{{.Server.Version}}")
    except FileNotFoundError:
        print("ERROR: docker CLI not found")
        return 2

    print(f"Validating {url} (compose project {args.project})\n")

    # 1. Readiness (bounded wait)
    ok, detail = wait_until_ready(url, args.project, args.timeout)
    check("stack becomes ready within the time limit", ok, detail)
    containers = compose_containers(args.project)
    apps = sorted(s for s in containers if s.startswith("app-"))

    # 2. Containers
    for name in ["nginx", "postgres", "redis", *apps]:
        c = containers.get(name)
        check(f"container {name} exists with that name", c is not None and c["Name"] == f"/{name}")
        if c:
            health = c["State"].get("Health", {}).get("Status", "no healthcheck")
            check(f"container {name} is running", c["State"]["Running"], f"health: {health}")
    check("at least two app instances", len(apps) >= 2, ", ".join(apps))
    for name in apps:
        user = docker("exec", name, "id", "-u").stdout.strip()
        check(f"{name} runs as a non-root user", user not in ("", "0"), f"uid {user}")

    # 3. Endpoints through NGINX
    status, headers, body = http("GET", f"{url}/")
    check("GET / returns 200 with message and instance_id",
          status == 200 and isinstance(body, dict) and "message" in body and "instance_id" in body, f"HTTP {status}")
    check("responses include a request ID header", bool(headers.get("X-Request-ID")), headers.get("X-Request-ID", "missing"))
    status, _, body = http("GET", f"{url}/health")
    check("GET /health returns 200", status == 200, f"HTTP {status}")
    status, _, body = http("GET", f"{url}/ready")
    deps = body.get("dependencies", {}) if isinstance(body, dict) else {}
    check("GET /ready returns 200", status == 200, f"HTTP {status}")
    check("PostgreSQL is ready", deps.get("postgres") == "ready", f"postgres: {deps.get('postgres')}")
    check("Redis is ready", deps.get("redis") == "ready", f"redis: {deps.get('redis')}")

    title = f"validate {uuid.uuid4().hex[:8]}"
    status, _, body = http("POST", f"{url}/records", {"title": title})
    check("POST /records creates a record (201)", status == 201, f"HTTP {status}")
    status, _, body = http("GET", f"{url}/records")
    titles = [r.get("title") for r in body.get("records", [])] if isinstance(body, dict) else []
    check("GET /records lists the new record", status == 200 and title in titles, f"HTTP {status}, {len(titles)} records")
    status, _, _ = http("POST", f"{url}/records", {"title": ""})
    check("POST /records with an empty title returns 400", status == 400, f"HTTP {status}")

    status1, _, first = http("GET", f"{url}/counter")
    status2, _, second = http("GET", f"{url}/counter")
    values = [b.get("counter") if isinstance(b, dict) else None for b in (first, second)]
    check("GET /counter increments", status1 == status2 == 200 and None not in values and values[1] > values[0],
          f"{values[0]} -> {values[1]}")
    status, _, _ = http("GET", f"{url}/does-not-exist")
    check("unknown path returns 404", status == 404, f"HTTP {status}")

    # 4. Every backend answers through NGINX, with distinct identities
    seen, header_mismatch = {}, 0
    for _ in range(max(10, 6 * len(apps))):
        status, headers, body = http("GET", f"{url}/instance")
        if status == 200 and isinstance(body, dict):
            seen[body["instance_id"]] = seen.get(body["instance_id"], 0) + 1
            header_mismatch += headers.get("X-Instance-ID") != body["instance_id"]
    check("every app instance answers through NGINX", set(apps) <= set(seen), f"seen {seen}, expected {apps}")
    check("X-Instance-ID header matches instance_id", header_mismatch == 0 and bool(seen), f"{header_mismatch} mismatches")

    # 5. Network isolation
    frontend = {n for c in containers.values() for n in networks_of(c) if n.endswith("frontend")}
    backend = {n for c in containers.values() for n in networks_of(c) if n.endswith("backend")}
    check("one network ending in 'frontend' and one ending in 'backend'",
          len(frontend) == 1 and len(backend) == 1, f"frontend={sorted(frontend)} backend={sorted(backend)}")
    expected = {"nginx": frontend, "postgres": backend, "redis": backend, **{a: frontend | backend for a in apps}}
    for name, nets in expected.items():
        if name in containers:
            actual = networks_of(containers[name])
            check(f"{name} is only on {sorted(n.rsplit('_', 1)[-1] for n in nets)}", actual == nets, f"on {sorted(actual)}")
    if apps:
        control = docker("exec", "nginx", "nc", "-z", "-w", "2", apps[0], "8080")
        check(f"control: nginx can reach {apps[0]}:8080", control.returncode == 0)
    for name, port in [("postgres", 5432), ("redis", 6379)]:
        probe = docker("exec", "nginx", "nc", "-z", "-w", "2", name, str(port))
        check(f"nginx cannot reach {name}:{port}", probe.returncode != 0, (probe.stderr or probe.stdout).strip() or "no answer")

    # 6. Host ports: only nginx, only on 127.0.0.1, on the expected port
    expected_port = str(urlsplit(url).port or 80)
    for name, c in sorted(containers.items()):
        bindings = [(b["HostIp"], b["HostPort"]) for p in (c["NetworkSettings"]["Ports"] or {}).values() if p for b in p]
        if name == "nginx":
            check("nginx publishes only 127.0.0.1:" + expected_port,
                  bindings and all(ip == "127.0.0.1" and port == expected_port for ip, port in bindings), f"{bindings}")
        else:
            check(f"{name} publishes no host port", not bindings, f"{bindings}")

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    print("RESULT: PASS" if failed == 0 else f"RESULT: FAIL ({failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
