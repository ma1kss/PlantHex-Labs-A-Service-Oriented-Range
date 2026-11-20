import re
import statistics
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rep, out = Path(sys.argv[1]), Path(sys.argv[2])
plt.rcParams.update({"font.family": "serif", "font.size": 11})
BLUE, RED, CYAN = "#4477AA", "#EE6677", "#66CCEE"

# Fig. latency: p50/p95 per endpoint (02_api_latency)
rows = {}
for line in (rep / "02_api_latency.txt").read_text().splitlines():
    m = re.match(r"(.+?)\s+n=\d+.*p50=([\d.]+)ms\s+p95=([\d.]+)ms", line)
    if m:
        rows[m.group(1).strip()] = (float(m.group(2)), float(m.group(3)))
pick = [("GET /health (edge)", "Edge health (no proxying)"),
        ("GET /iam/health (proxied)", "Proxied service health"),
        ("GET /iam/auth/validate", "Token validation (IAM)"),
        ("GET /iam/profile", "User profile (IAM)"),
        ("GET /learning/leaderboard", "Leaderboard (Learning)"),
        ("GET /scenario/challenges", "Challenge catalogue (Scenario)"),
        ("POST /iam/auth/login (bcrypt)", "Login (IAM, bcrypt)")]
labels = [lab for key, lab in pick][::-1]
p50 = [rows[key][0] for key, _ in pick][::-1]
p95 = [rows[key][1] for key, _ in pick][::-1]
fig, ax = plt.subplots(figsize=(9.2, 4.6))
y = range(len(labels)); h = 0.38
b1 = ax.barh([i + h / 2 for i in y], p50, h, color=BLUE, label="p50")
b2 = ax.barh([i - h / 2 for i in y], p95, h, color=RED, label="p95")
for bars in (b1, b2):
    for b in bars:
        ax.text(b.get_width() * 1.06, b.get_y() + b.get_height() / 2,
                f"{b.get_width():.1f}", va="center", fontsize=9.5, color="#333")
ax.set_xscale("log"); ax.set_xlim(5, 900)
ax.set_xticks([5, 10, 20, 50, 100, 200, 400, 800]); ax.set_xticklabels(["5", "10", "20", "50", "100", "200", "400", "800"])
ax.set_yticks(list(y)); ax.set_yticklabels(labels)
ax.set_xlabel("Latency (ms, log scale; n = 100 per endpoint)")
ax.grid(axis="x", color="#ddd"); ax.set_axisbelow(True)
for s in ("top", "right"): ax.spines[s].set_visible(False)
ax.legend(frameon=False, loc="upper right")
fig.tight_layout(); fig.savefig(out / "endpoint_latency.png", dpi=300); plt.close(fig)

# Fig. lab lifecycle: per-run launch / first request via VPN / teardown
L, V, T = [], [], []
for line in (rep / "05_lab_provision_time.txt").read_text().splitlines():
    m = re.match(r"run \d+: launch (\d+) ms .* via VPN tunnel (\d+)ms · teardown (\d+) ms", line)
    if m:
        L.append(int(m.group(1))); V.append(int(m.group(2))); T.append(int(m.group(3)))
series = [("Launch →\nrunning", L, BLUE), ("First request\nthrough VPN tunnel", V, CYAN),
          ("Teardown\n(full reclaim)", T, RED)]
fig, ax = plt.subplots(figsize=(7.2, 4.6))
import random
random.seed(1)
for i, (lab, xs, col) in enumerate(series):
    m, sd = statistics.mean(xs), statistics.stdev(xs)
    ax.bar(i, m, 0.55, color=col)
    ax.errorbar(i, m, yerr=sd, color="#111", capsize=6, lw=1.4)
    ax.scatter([i + random.uniform(-0.14, 0.14) for _ in xs], xs, s=16, facecolor="white",
               edgecolor="#222", lw=0.8, zorder=3)
    ax.text(i, m * 1.45, f"{m:.0f} ± {sd:.0f} ms", ha="center", fontsize=10.5)
ax.set_yscale("log"); ax.set_ylim(150, 30000)
ax.set_yticks([200, 500, 1000, 2000, 5000, 10000, 20000]); ax.set_yticklabels(["200", "500", "1000", "2000", "5000", "10000", "20000"])
ax.set_xticks(range(3)); ax.set_xticklabels([s[0] for s in series])
ax.set_ylabel("Time (ms, log scale)")
ax.set_title(f"Two-service laboratory exercise, n = {len(L)} cycles", fontsize=10.5)
ax.grid(axis="y", color="#ddd"); ax.set_axisbelow(True)
for s in ("top", "right"): ax.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(out / "lab_lifecycle.png", dpi=300); plt.close(fig)
print(f"figures written to {out}  (latency endpoints {len(pick)}, lab cycles {len(L)})")
