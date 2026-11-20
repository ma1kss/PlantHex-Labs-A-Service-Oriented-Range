import csv
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = list(csv.DictReader(open(os.path.join(HERE, "traceability", "requirements.csv"), encoding="utf-8")))

# Table 1, Status column (F full, P partial, - not implemented).
STATUS = {"F01": "F", "F02": "F", "F03": "P", "F04": "F", "F05": "F", "F06": "F",
          "F07": "F", "F08": "P", "F09": "P", "F10": "F", "F11": "F", "F12": "F",
          "F13": "F", "F14": "F", "F15": "F", "F16": "F", "F17": "P", "F18": "F",
          "F19": "-"}
FUNCS = [f"F{i:02d}" for i in range(1, 20)]

fr = [r for r in ROWS if r["id"].startswith("SR-FR-")]
sec = [r for r in ROWS if r["id"].startswith("SEC-")]
errors = []
if len(fr) != 49: errors.append(f"expected 49 functional requirements, found {len(fr)}")
if len(sec) != 27: errors.append(f"expected 27 security requirements, found {len(sec)}")
if len({r['id'] for r in ROWS}) != len(ROWS): errors.append("duplicate requirement ids")

per_func = collections.defaultdict(list)
for r in ROWS:
    fs = [f.strip() for f in r["functions"].split(";") if f.strip()]
    if not fs: errors.append(f"{r['id']} is not allocated")
    for f in fs:
        if f not in FUNCS: errors.append(f"{r['id']} allocated to unknown {f}")
        per_func[f].append(r["id"])
empty = [f for f in FUNCS if not per_func[f]]
if empty: errors.append("functions without requirements: " + ", ".join(empty))

print("=== Allocation check (Table 1) ===")
print(f"requirements: {len(fr)} functional + {len(sec)} security = {len(ROWS)}")
print("every requirement allocated  :", "yes" if not any("not allocated" in e for e in errors) else "NO")
print("every function has a req.    :", "yes" if not empty else "NO")
multi = [r["id"] for r in ROWS if ";" in r["functions"]]
print(f"allocated to >1 function     : {len(multi)} ({', '.join(multi)})")
only19 = [r["id"] for r in ROWS if r["functions"].strip() == "F19"]
print(f"allocated only to F19        : {len(only19)} ({', '.join(only19)})")

c = collections.Counter(STATUS.values())
print(f"\nimplementation coverage      : {c['F'] + c['P']}/19 functions realised "
      f"({c['F']} fully, {c['P']} in part); not implemented: "
      + ", ".join(f for f, s in STATUS.items() if s == "-"))

print("\n=== Satisfaction (S satisfied · P partly · N not · NV not verified) ===")
for label, rows in (("functional", fr), ("security", sec), ("all", ROWS)):
    v = collections.Counter(r["verdict"] for r in rows)
    print(f"{label:10s} n={len(rows):2d}  S={v['S']:2d}  P={v['P']:2d}  N={v['N']:2d}  NV={v['NV']:2d}")
bad = [r["id"] for r in ROWS if r["verdict"] not in ("S", "P", "N", "NV")]
if bad: errors.append("invalid verdicts: " + ", ".join(bad))

print("\nper function (requirements: S/P/N/NV):")
for f in FUNCS:
    v = collections.Counter(next(r["verdict"] for r in ROWS if r["id"] == i) for i in per_func[f])
    print(f"  {f} [{STATUS[f]}] n={len(per_func[f]):2d}  S={v['S']} P={v['P']} N={v['N']} NV={v['NV']}")

if errors:
    print("\nERRORS:"); [print("  -", e) for e in errors]
    sys.exit(1)
print("\nallocation invariants: OK")
