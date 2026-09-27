"""Draw architecture.png (final setup: three app instances, public port 8090). Needs matplotlib.

Usage: python docs/architecture.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = Path(__file__).resolve().parent.parent / "architecture.png"
fig, ax = plt.subplots(figsize=(16, 10))
ax.set_xlim(0, 16)
ax.set_ylim(0, 10)
ax.axis("off")


def box(x, y, w, h, text, face, edge="#333333", size=10, weight="normal"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.12",
                                facecolor=face, edgecolor=edge, linewidth=1.4, zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, weight=weight, zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#1f4e79", style="-|>", dashed=False, lx=0.0, ly=0.0):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), zorder=2,
                arrowprops=dict(arrowstyle=style, color=color, lw=1.6, linestyle="--" if dashed else "-"))
    if label:
        ax.text((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, fontsize=8.5, color=color, ha="center",
                va="center", zorder=5, bbox=dict(facecolor="white", edgecolor="none", pad=1.2))


# Network zones
ax.add_patch(FancyBboxPatch((0.4, 4.35), 11.2, 3.35, boxstyle="round,pad=0.02,rounding_size=0.2",
                            facecolor="#e8f1fb", edgecolor="#4a7ab5", linewidth=1.2, zorder=0))
ax.text(0.6, 7.45, "frontend network  (barq-assessment_frontend)", fontsize=10, color="#2d5b94", weight="bold")
ax.add_patch(FancyBboxPatch((0.4, 0.3), 11.2, 4.0, boxstyle="round,pad=0.02,rounding_size=0.2",
                            facecolor="#fdf1e3", edgecolor="#c47f2c", linewidth=1.2, zorder=0))
ax.text(0.6, 0.45, "backend network  (barq-assessment_backend, internal: true - no outside access)",
        fontsize=10, color="#9a5f16", weight="bold")

# Client and published port
box(3.6, 8.75, 4.8, 0.85, "Client (browser / curl) on the laptop", "#ffffff", weight="bold")
arrow(6.0, 8.75, 6.0, 7.05, "HTTP  127.0.0.1:8090  ->  nginx:80\n(the only published port)", lx=1.9)

# nginx
box(3.1, 6.05, 5.8, 1.0,
    "nginx 1.28  (container: nginx)\nround robin, shared zone, failover, re-resolves app names every 5 s",
    "#cfe2f7", size=9.5, weight="bold")

# Apps straddle both networks
apps_x = [0.9, 4.55, 8.2]
for i, x in enumerate(apps_x, 1):
    box(x, 3.55, 2.9, 1.55, f"app-0{i}  (Flask, port 8080)\nuser app (uid 10001)\n/health = liveness\n/ready = readiness",
        "#d9ead3", size=9)
    arrow(6.0, 6.05, x + 1.45, 5.1, f"app-0{i}:8080" if i != 2 else "app-02:8080", lx=0.0, ly=0.1)

# Dependencies and storage
box(1.6, 1.55, 3.6, 1.1, "postgres 16  (port 5432)\nhealth: pg_isready", "#fce5cd", size=9.5, weight="bold")
box(6.8, 1.55, 3.6, 1.1, "redis 7.4  (port 6379)\nhealth: redis-cli ping", "#fce5cd", size=9.5, weight="bold")
box(1.8, 0.75, 3.2, 0.55, "volume postgres-data", "#eeeeee", size=8.5)
box(7.0, 0.75, 3.2, 0.55, "volume redis-data (AOF, fsync 1 s)", "#eeeeee", size=8.5)
for x in apps_x:
    arrow(x + 1.2, 3.55, 3.4, 2.65, color="#7f6000")
    arrow(x + 1.7, 3.55, 8.6, 2.65, color="#7f6000")
ax.text(2.2, 3.05, "SQL 5432", fontsize=8.5, color="#7f6000")
ax.text(9.3, 3.05, "Redis 6379", fontsize=8.5, color="#7f6000")

# Blocked path
ax.plot([3.1, 0.62, 0.62, 1.45], [6.55, 6.55, 2.1, 2.1], color="#c00000", lw=1.6, linestyle="--", zorder=2)
ax.plot([1.45], [2.1], marker="x", markersize=14, markeredgewidth=3, color="#c00000", zorder=5)
ax.text(0.75, 5.25, "blocked: nginx is not on the\nbackend network, it cannot\nreach postgres or redis",
        fontsize=8, color="#c00000", zorder=5)

# Side notes
notes = (
    "Health and startup\n"
    "- postgres, redis healthy  ->  apps start\n"
    "- apps healthy  ->  nginx starts (depends_on: service_healthy)\n"
    "- nginx health: wget 127.0.0.1:8081/nginx-health\n"
    "  (inside the container only)\n"
    "- restart: unless-stopped, CPU/memory limits\n\n"
    "Failover (nginx)\n"
    "- connect timeout 2 s, read timeout 10 s\n"
    "- retry once on the next app if unreachable\n"
    "- max_fails=2, fail_timeout=10s\n\n"
    "Secrets\n"
    "- credentials only in git-ignored .env\n\n"
    "Single points of failure\n"
    "- one nginx, one postgres, one redis,\n"
    "  one host (only the app layer is redundant)"
)
ax.text(11.95, 9.55, notes, fontsize=9, va="top", family="monospace",
        bbox=dict(facecolor="#f7f7f7", edgecolor="#999999", boxstyle="round,pad=0.6"))

ax.set_title("BARQ DevOps task - final architecture (3 app instances, public port 8090)", fontsize=14,
             weight="bold", pad=6)
fig.savefig(OUT, dpi=150, bbox_inches="tight")
print(f"wrote {OUT}")
