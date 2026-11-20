import json
import sys

d = json.load(sys.stdin)
print("  routes inventoried : %d  (roles: player, instructor, admin)" % d["routes"])
for k, v in d["role_matrix"].items():
    print("  role matrix [%-6s]: %d/%d conform, %d violations"
          % (k, v["conform"], v["checks"], v["violations"]))
print("  forged/invalid tokens (401 on every route expected):")
for name, r in d["forged"].items():
    e, x = r["edge"], r["direct"]
    print("    %-20s edge %3d/%d   direct %3d/%d" % (
        name, e["rejected_401"], e["rejected_401"] + e["not_rejected"],
        x["rejected_401"], x["rejected_401"] + x["not_rejected"]))
s = d["compromised_service_forgery"]
print("  compromised-service forgery (%s): %s -> %s" % (
    s["route"], s["status"], "ACCEPTED" if s["accepted"] else "all rejected"))
print("  /internal/* through the edge: %s (404 = never proxied)" % d["internal_paths"])
if d["violations"]:
    print("  violations:")
    for v in d["violations"]:
        print("    -", v)
