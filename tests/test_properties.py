#!/usr/bin/env python3
"""Property tests for seat-usage's pace, headroom, capped and ranking rules. No network.

A small seeded generator loop, no extra dependency. Every run uses the same
seed, so a failure reproduces. On a failure the message names the seed, the
case number and the generated input:

    PROPERTY_SEED=20261026 PROPERTY_CASES=300 python3 tests/test_properties.py

tests/test_seat_usage.py runs these too, so the normal suite includes them.
"""
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
BIN = Path(os.environ["SEAT_USAGE_BIN"]) if os.environ.get("SEAT_USAGE_BIN") else HERE.parents[0] / "bin" / "seat-usage"

SEED = int(os.environ.get("PROPERTY_SEED") or 20261026)
CASES = int(os.environ.get("PROPERTY_CASES") or 300)

NOW = 1_800_000_000
HOUR = 3600
WEEK = 7 * 86400
MONTH = 30 * 86400


def _load():
    loader = importlib.machinery.SourceFileLoader("seat_usage_properties", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


su = _load()
_SCRATCH = tempfile.mkdtemp(prefix="seat-usage-props-")
# This module's own copy reads no real renewals file.
su.RENEWALS_FILE = os.path.join(_SCRATCH, "no-renewals.json")


@contextlib.contextmanager
def _isolated():
    """Point every log the routes read at files that do not exist, then restore."""
    keys = {
        "SEAT_USAGE_FAILURES": "no-failures.jsonl",
        "SEAT_USAGE_OUTCOMES": "no-outcomes.jsonl",
        "SEAT_USAGE_RUNS": "no-runs.jsonl",
        "SEAT_USAGE_REVIEW_LOG": "no-review-log.jsonl",
    }
    old = {k: os.environ.get(k) for k in keys}
    try:
        for k, name in keys.items():
            os.environ[k] = os.path.join(_SCRATCH, name)
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


TESTS = []


def prop(fn):
    """Run fn(rng, case) CASES times with a generator seeded from SEED and the name."""
    name = fn.__name__

    def run():
        rng = random.Random(f"{SEED}:{name}")
        with _isolated():
            for i in range(CASES):
                case = {}
                try:
                    fn(rng, case)
                except AssertionError as e:
                    shown = json.dumps(case, sort_keys=True, default=repr)
                    raise AssertionError(
                        f"seed={SEED} case={i} counterexample={shown} :: {e} "
                        f"(rerun: PROPERTY_SEED={SEED} python3 tests/test_properties.py)") from None

    run.__name__ = f"property_{name}"
    TESTS.append(run)
    return run


# ---- generators -------------------------------------------------------------

_EDGE_USED = (0, 0.4, 0.5, 9.5, 10, 10.5, 49.5, 50, 89.5, 90, 94.9, 95, 99.4, 99.5, 99.9, 100)


def gen_used(rng, lo=0.0, hi=100.0):
    if rng.random() < 0.3:
        edges = [u for u in _EDGE_USED if lo <= u <= hi]
        if edges:
            return rng.choice(edges)
    return round(rng.uniform(lo, hi), rng.choice((0, 1, 3)))


def gen_window(rng):
    return rng.choice((5 * HOUR, WEEK, MONTH, rng.randint(HOUR, 40 * 86400)))


def gen_left(rng, window):
    """Seconds until reset, inside the window, edges included."""
    pick = rng.random()
    if pick < 0.1:
        return window
    if pick < 0.2:
        return 1
    return rng.randint(1, window)


def gen_snapshot(rng, hi=100.0):
    """A fresh SUMMARY-shaped dict: every seat read now, every pool reset ahead."""
    s = {"v": 1, "ts": NOW,
         "as_of": {seat: NOW - rng.randint(0, 300) for seat in
                   ("claude", "chatgpt", "cursor", "grokbot", "grok", "muse", "google")},
         "codex_window_s": WEEK, "cursor_window_s": MONTH, "bot_window_s": WEEK, "grok_window_s": WEEK,
         "grok_exhausted": False, "bot_exhausted": False}
    for meta in su.POOLS.values():
        window = meta["window"] if isinstance(meta["window"], (int, float)) else s.get(meta["window"], WEEK)
        if meta["reset"] not in s:
            s[meta["reset"]] = NOW + gen_left(rng, window)
        s[meta["used"]] = gen_used(rng, 0.0, hi)
    s["cursor_pct"] = max(s.get("cursor_auto_pct", 0), s.get("cursor_api_pct", 0))
    return s


def states_of(s):
    return {pid: su.pool_status(pid, s, NOW) for pid in su.POOLS}


# ---- pace ---------------------------------------------------------------------

_PACE_RANK = {"under": 0, "on": 1, "ahead": 2}


@prop
def pace_code_never_drops_as_usage_rises(rng, case):
    """More used at the same time is never further under pace."""
    window = gen_window(rng)
    left = gen_left(rng, window)
    a, b = sorted((gen_used(rng), gen_used(rng)))
    case.update(window=window, reset_ts=NOW + left, now=NOW, used_lo=a, used_hi=b)
    lo = su.pace_code(a, NOW + left, window, NOW)
    hi = su.pace_code(b, NOW + left, window, NOW)
    case.update(code_lo=lo, code_hi=hi)
    assert lo in _PACE_RANK and hi in _PACE_RANK, (lo, hi)
    assert _PACE_RANK[hi] >= _PACE_RANK[lo], f"{b}% reads {hi} but {a}% reads {lo}"


# ---- headroom -----------------------------------------------------------------

def _headroom(pid, states):
    st = states[pid][0]
    if st in ("capped", "unknown"):
        return 0.0
    return su._review_headroom(st, pid, states)


@prop
def headroom_never_rises_as_one_pool_uses_more(rng, case):
    """Same snapshot, one pool's used raised: its state and headroom never improve."""
    s = gen_snapshot(rng)
    pid = rng.choice(sorted(su.POOLS))
    key = su.POOLS[pid]["used"]
    a, b = sorted((gen_used(rng), gen_used(rng, 0.0, 120.0)))
    case.update(pool=pid, used_lo=a, used_hi=b, snapshot=s)
    lo_s, hi_s = dict(s, **{key: a}), dict(s, **{key: b})
    lo_states, hi_states = states_of(lo_s), states_of(hi_s)
    case.update(state_lo=lo_states[pid], state_hi=hi_states[pid])
    assert lo_states[pid][0] != "unknown" and hi_states[pid][0] != "unknown", (lo_states[pid], hi_states[pid])
    h_lo, h_hi = _headroom(pid, lo_states), _headroom(pid, hi_states)
    case.update(headroom_lo=h_lo, headroom_hi=h_hi)
    assert h_hi <= h_lo, f"headroom {h_hi} at {b}% is above {h_lo} at {a}%"


@prop
def headroom_stays_between_zero_and_full(rng, case):
    """Every headroom factor the routes use is in [0, 1]; burn's left% is in [0, 100]."""
    s = gen_snapshot(rng, hi=150.0)
    case["snapshot"] = s
    states = states_of(s)
    for pid in su.POOLS:
        st = states[pid][0]
        for value, where in ((su._review_headroom(st, pid, states), "review"), (su._ask_headroom(st), "ask")):
            assert 0.0 <= value <= 1.0, f"{where} headroom {value} for {pid} ({st})"
        parts = su._score_parts(pid, "builder", s, NOW, states, 1.0)
        if parts is not None:
            assert 0.0 <= parts["headroom"] <= 1.0, f"score headroom {parts['headroom']} for {pid}"
    for e in su.burn_entries(s, NOW):
        assert 0 <= e["left_pct"] <= 100, f"burn left {e['left_pct']}% for {e}"
        assert 0 <= e["used_pct"] <= 100, f"burn used {e['used_pct']}% for {e}"
        assert 0 <= e["hours"] <= su.BURN_HOURS, f"burn hours {e['hours']} for {e}"


@prop
def caps_early_is_inside_the_window_and_grows_with_usage(rng, case):
    """A projected early cap is after now and before the reset; more used caps no later."""
    window = gen_window(rng)
    left = gen_left(rng, window)
    a, b = sorted((gen_used(rng), gen_used(rng)))
    case.update(window=window, reset_ts=NOW + left, used_lo=a, used_hi=b)
    ea = su.caps_early_s(a, NOW + left, window, NOW)
    eb = su.caps_early_s(b, NOW + left, window, NOW)
    case.update(early_lo=ea, early_hi=eb)
    for e in (ea, eb):
        assert e is None or 0 < e <= left, f"caps early {e}s with {left}s left"
    if ea is not None and b < 100:
        assert eb is not None and eb >= ea, f"{b}% caps {eb}s early but {a}% caps {ea}s early"


# ---- capped -------------------------------------------------------------------

@prop
def a_full_window_is_always_capped(rng, case):
    """used >= 100 with a reset ahead, or an exhausted flag, is capped and never scored."""
    s = gen_snapshot(rng)
    pid = rng.choice(sorted(su.POOLS))
    meta = su.POOLS[pid]
    how = rng.choice(("full", "flag", "muse_quota", "codex_gate"))
    if how == "flag" and not meta.get("exhausted"):
        how = "full"
    if how == "muse_quota" and meta["seat"] != "muse":
        how = "full"
    if how == "codex_gate" and pid != "codex":
        how = "full"
    if how == "full":
        s[meta["used"]] = gen_used(rng, 100.0, 250.0)
    elif how == "flag":
        s[meta["exhausted"]] = True
    elif how == "muse_quota":
        s["muse_exhausted"] = True
        s["muse_exhausted_reset_ts"] = NOW + rng.randint(1, WEEK)
        if rng.random() < 0.5:
            s.pop(meta["used"], None)
    else:
        s["codex_5h"] = gen_used(rng, 100.0, 150.0)
        s["codex_5h_reset_ts"] = NOW + rng.randint(1, 5 * HOUR)
    case.update(pool=pid, how=how, snapshot=s)
    states = states_of(s)
    case["state"] = states[pid]
    assert states[pid][0] == "capped", f"{pid} reads {states[pid]}"
    for role in sorted(su.ROLES):
        assert su._score_parts(pid, role, s, NOW, states, 1.0) is None, f"{pid} scored for {role}"
    for vendor in ("xai", "anthropic", "openai"):
        row = su.route_review(s, NOW, vendor)
        pools = [r.get("pool") for r in row.get("reviewers") or []]
        assert pid not in pools, f"review for {vendor} seated capped {pid}: {pools}"


# ---- ranking ------------------------------------------------------------------

def _shuffled_snapshot(rng, s):
    items = list(s.items())
    rng.shuffle(items)
    out = dict(items)
    as_of = list(s["as_of"].items())
    rng.shuffle(as_of)
    out["as_of"] = dict(as_of)
    return out


def _canon(row):
    return json.dumps(row, sort_keys=True, default=repr)


@prop
def ranking_is_deterministic_and_ignores_input_order(rng, case):
    """Same snapshot in any key order picks the same review seat and the same route rows."""
    s = gen_snapshot(rng)
    t = _shuffled_snapshot(rng, s)
    vendor = rng.choice(("xai", "anthropic", "openai", "grok"))
    case.update(vendor=vendor, snapshot=s)
    first = _canon(su.route_review(s, NOW, vendor))
    assert _canon(su.route_review(s, NOW, vendor)) == first, "two runs disagree"
    assert _canon(su.route_review(t, NOW, vendor)) == first, "key order changed the review pick"
    for role in sorted(su.ROLES):
        states = states_of(s)
        backwards = dict(reversed(list(states.items())))
        a = _canon(su.score_route(role, s, NOW, states))
        assert _canon(su.score_route(role, t, NOW, backwards)) == a, f"{role} depends on input order"


@prop
def review_rank_is_a_stable_total_order(rng, case):
    """Rows with equal pace and used sort by roster order, whatever order they arrive in."""
    paces = ("under", "open", "on", "ahead", "near_cap", None)
    rows = [{"pace": rng.choice(paces), "used_pct": rng.choice((None, 0, 10, 10, 50, 50, 99)), "_index": i}
            for i in range(rng.randint(1, 8))]
    case["rows"] = rows
    want = [r["_index"] for r in sorted(rows, key=su._review_rank_key)]
    mixed = list(rows)
    rng.shuffle(mixed)
    got = [r["_index"] for r in sorted(mixed, key=su._review_rank_key)]
    assert got == want, f"{got} != {want}"
    s = gen_snapshot(rng)
    states = states_of(s)
    ready = [pid for pid in su.POOLS if states[pid][0] in su._PACE_ORDER]
    if ready:
        pick = su._pick_optimal(ready, states, s)
        rng.shuffle(ready)
        case.update(ready=ready, snapshot=s)
        assert su._pick_optimal(ready, states, s) == pick, "optimal pick depends on order"


def main():
    passed = failed = 0
    print(f"property seed {SEED}, {CASES} cases each")
    for fn in TESTS:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        else:
            passed += 1
            print(f"ok {fn.__name__}")
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
