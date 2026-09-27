import os, socket
def port(host, p):
    try:
        socket.create_connection((host, p), timeout=2).close(); return "OPEN"
    except OSError as e:
        return f"closed ({e.__class__.__name__})"
print("[ports] as seen from app-01")
for h, p in [("postgres", 5433), ("postgres", 5432), ("redis", 6380), ("redis", 6379)]:
    print(f"  {h}:{p} -> {port(h, p)}")
import psycopg
url = os.environ["DATABASE_URL"]
print("[postgres login on the correct port 5432]")
for label, u in [("password from app.env (...K8d)", url.replace(":5433", ":5432")),
                 ("password from compose (...K8c)", url.replace(":5433", ":5432").replace("K8d@", "K8c@"))]:
    try:
        psycopg.connect(u, connect_timeout=3).close(); print(f"  {label}: LOGIN OK")
    except Exception as e:
        print(f"  {label}: FAILED - {str(e).strip().splitlines()[-1][:110]}")
