#!/usr/bin/env python3
"""Payload tests for seat-usage-push. No network, no gh."""
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
BIN = Path(os.environ["SEAT_USAGE_PUSH_BIN"]) if os.environ.get("SEAT_USAGE_PUSH_BIN") else HERE.parent / "bin" / "seat-usage-push"

loader = importlib.machinery.SourceFileLoader("seat_usage_push", str(BIN))
spec = importlib.util.spec_from_loader(loader.name, loader)
sp = importlib.util.module_from_spec(spec)
loader.exec_module(sp)

NOW = 1_800_000_000
WEEK = 7 * 86400
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def full_cache(**over):
    c = {
        "ts": NOW, "v": 1,
        "as_of": {"claude": NOW - 60, "chatgpt": NOW - 120, "cursor": NOW - 30, "grokbot": NOW - 30, "grok": NOW - 90},
        "claude_5h": 12, "claude_5h_reset_ts": NOW + 3600,
        "claude_week": 40, "claude_week_pace": "on", "claude_week_reset_ts": NOW + 3 * 86400,
        "fable": 55, "fable_pace": "ahead", "fable_reset_ts": NOW + 3 * 86400,
        "codex": 20, "codex_pace": "under", "codex_reset_ts": NOW + 86400, "codex_window_s": WEEK,
        "codex_plan": "pro", "lines": ["claude | max_5x active | secret-ish text"],
        "cursor": "pro+", "cursor_auto_pct": 5.5, "cursor_api_pct": 61.2, "cursor_auto_pace": "under",
        "cursor_api_pace": "ahead", "cursor_reset_ts": NOW + 10 * 86400, "cursor_window_s": 30 * 86400,
        "bot_pct": 9.0, "bot_pace": "on", "bot_reset_ts": NOW + 2 * 86400, "bot_window_s": WEEK, "bot_exhausted": False,
        "grok_pct": 33, "grok_pace": "on", "grok_reset_ts": NOW + 4 * 86400, "grok_window_s": WEEK, "grok_exhausted": False,
        "grok_cookie_ok": True,
    }
    c.update(over)
    return c


def seat(payload, sid):
    for p in payload["pools"]:
        for s in p["seats"]:
            if s["id"] == sid:
                return s
    return None


@test
def pools_follow_status_line_order():
    p = sp.build_payload(full_cache())
    assert [x["id"] for x in p["pools"]] == ["cl", "cx", "cu", "gk"], p
    assert [s["id"] for s in p["pools"][0]["seats"]] == ["claude_5h", "claude_week", "claude_fable"]
    assert [s["id"] for s in p["pools"][2]["seats"]] == ["cursor_models", "cursor_other", "grokbot"]
    assert [x["hue"] for x in p["pools"]] == [208, 141, 43, 75]


@test
def only_allowlisted_fields_leave():
    body = json.dumps(sp.build_payload(full_cache()))
    for leaked in ("lines", "secret-ish", "max_5x", "codex_plan", "grok_cookie_ok", "pro+"):
        assert leaked not in body, leaked


@test
def seat_values_and_windows():
    p = sp.build_payload(full_cache())
    wk = seat(p, "claude_week")
    assert wk["pct"] == 40 and wk["window_s"] == WEEK and wk["pace"] == "on" and wk["as_of"] == NOW - 60, wk
    assert seat(p, "claude_5h")["window_s"] == 5 * 3600
    other = seat(p, "cursor_other")
    assert other["pct"] == 61.2 and other["window_s"] == 30 * 86400 and other["as_of"] == NOW - 30, other
    assert seat(p, "grokbot")["as_of"] == NOW - 30
    assert set(wk) == {"id", "short", "label", "pct", "reset_ts", "window_s", "pace", "exhausted", "as_of"}, sorted(wk)


@test
def missing_claude_seats_drop_but_vendor_seats_stay_unknown():
    p = sp.build_payload(full_cache(claude_5h=None, fable="", codex=None, grok_pct=None))
    assert seat(p, "claude_5h") is None and seat(p, "claude_fable") is None
    assert seat(p, "codex")["pct"] is None
    assert seat(p, "supergrok")["pct"] is None


@test
def no_claude_at_all_drops_the_pool():
    p = sp.build_payload(full_cache(claude_5h=None, claude_week=None, fable=None))
    assert [x["id"] for x in p["pools"]] == ["cx", "cu", "gk"], p


@test
def exhausted_reads_full():
    p = sp.build_payload(full_cache(grok_exhausted=True, grok_pct=None, bot_exhausted=True))
    assert seat(p, "supergrok")["exhausted"] is True and seat(p, "supergrok")["pct"] == 100.0
    assert seat(p, "grokbot")["exhausted"] is True and seat(p, "grokbot")["pct"] == 100.0


@test
def bad_values_are_none():
    p = sp.build_payload(full_cache(codex="n/a", codex_window_s=0, codex_pace="weird"))
    cx = seat(p, "codex")
    assert cx["pct"] is None and cx["window_s"] is None and cx["pace"] == "", cx


@test
def empty_cache_still_has_vendor_seats():
    p = sp.build_payload({})
    assert [x["id"] for x in p["pools"]] == ["cx", "cu", "gk"], p
    assert p["ts"] is None


def main():
    passed = failed = 0
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
