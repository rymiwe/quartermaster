#!/usr/bin/env python3
"""Show what fable_conserve does to a saved reading. Reads a fixture, writes nothing.

Usage: tools/fable_pace_sim.py [reading.json]
Every line comes from the real routes in bin/seat-usage.
"""
import copy
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BIN = os.environ.get("SEAT_USAGE_BIN") or str(ROOT / "bin" / "seat-usage")
FIXTURE = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tests" / "fixtures" / "fable-pace-reading.json")
sys.argv = ["seat-usage"]
loader = importlib.machinery.SourceFileLoader("su", BIN)
spec = importlib.util.spec_from_loader("su", loader)
su = importlib.util.module_from_spec(spec)
loader.exec_module(su)

OVER_CAP = {"#50": 240838, "#54": 206305}   # measured prompt sizes


def seat(v):
    return f"{v.get('family')} on {v.get('pool') or v.get('seat')}"


def run(label, s, now):
    states = {pid: su.pool_status(pid, s, now) for pid in su.POOLS}
    cons = su.fable_conserve(s, now, states)
    print(f"\n### {label}")
    print("conserve:", json.dumps({k: cons.get(k) for k in (
        "active", "state", "used", "elapsed_pct", "projected_pct", "keep", "keep_seats", "skip")}))
    print("Opus pools:", {o: su._opus_group(o, states)[0] for o in su.OPUS_ORDER})
    for name, row in (("(a) review council, xAI builder", su.route_lineup(s, now, kind="review", builder_vendor="xai")),
                      ("(b) design council", su.route_lineup(s, now, kind="design"))):
        voters = (row.get("lineup") or {}).get("voters") or []
        print(f"{name}: mode={row['mode']} voters={[seat(v) for v in voters]}")
        if row["mode"] == "ask":
            print(f"  reason: {row['reason']}")
    gate = su.route_review(s, now, "xai")
    print(f"(c) gate, xAI builder: mode={gate['mode']} reviewer={[seat(v) for v in gate['reviewers']]}")
    for pr, size in OVER_CAP.items():
        big = su.route_review(s, now, "xai", exclude_vendors=("google",), prompt_bytes=size)
        print(f"    over-cap {pr} ({size} B): mode={big['mode']} reviewer={[seat(v) for v in big['reviewers']]}")
        if big["mode"] == "ask":
            print(f"      reason: {big['reason']}")


base = json.load(open(FIXTURE))
now = base["ts"] + 720
m = su.POOLS[su.FABLE_CONSERVE["pool"]]
run("today", copy.deepcopy(base), now)
win = su._window(m, base)
at = copy.deepcopy(base)
at[m["used"]] = round(100 * (1 - (at[m["reset"]] - now) / win), 1)
run(f"what-if: Fable pool at pace ({m['used']}={at[m['used']]})", at, now)
cap = copy.deepcopy(base)
cap[m["used"]] = 100
run(f"what-if: Fable pool capped ({m['used']}=100)", cap, now)
# About Nov 15: no more Fable burn, so used stays put and elapsed reaches 84.4%.
# Other clocks move with now, so their readings stay as fresh as today's.
later = base[m["reset"]] - (1 - 0.844) * win
shift = later - now
nov = copy.deepcopy(base)
for key, value in base.items():
    if key.endswith("_reset_ts") and key != m["reset"] and isinstance(value, (int, float)):
        nov[key] = value + shift
nov["ts"] += shift
nov["pulled_ts"] += shift
nov["as_of"] = {k: v + shift for k, v in base["as_of"].items()}
run(f"Nov 15 row: elapsed 84.4%, {m['used']}={nov[m['used']]}, reset Nov 20", nov, later)
