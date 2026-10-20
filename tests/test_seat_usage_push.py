#!/usr/bin/env python3
"""Payload tests for seat-usage-push. No network, no gh."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
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


# --- reader copy: latest.json cut to numbers for the shared VM ---

def strings_in(v, path="$"):
    if isinstance(v, str):
        return [path]
    if isinstance(v, dict):
        return [x for k, w in v.items() for x in strings_in(w, f"{path}.{k}")]
    if isinstance(v, list):
        return [path + "[]"]
    return []


def google_cache(**over):
    c = full_cache(**{
        "claude_plan": "max_5x", "claude_status": "active", "cursor": "pro+", "codex_source": "api",
        "errors": {"google": "agy: not signed in as someone@example.com", "grok": "cookie expired"},
        "burn": [{"seat": "claude", "pool": "fable"}], "grok_cookie_ok": True,
        "google_gemini_week": 40, "google_gemini_week_reset_ts": NOW + 86400, "google_gemini_week_pace": "on",
        "google_gemini_5h": 20, "google_gemini_5h_reset_ts": NOW + 3600,
        "google_argon_week": 10, "google_argon_week_reset_ts": NOW + 5 * 86400,
        "muse_5h": 35, "muse_5h_reset_ts": NOW + 7200, "muse_week": 50, "muse_week_reset_ts": NOW + 3 * 86400,
        "cookie": "sso=SECRET", "token": "sk-SECRET", "email": "ryan@example.com",
    })
    c["as_of"] = dict(c["as_of"], google=NOW - 10, muse=NOW - 20, session="SECRET", bogus=NOW)
    c.update(over)
    return c


@test
def cache_copy_is_an_allowlist_of_numbers_and_flags():
    c = sp.build_cache_copy(google_cache())
    assert strings_in(c) == [], strings_in(c)
    allowed = sp.CACHE_NUMBERS | sp.CACHE_FLAGS | {"as_of"}
    assert set(c) <= allowed, sorted(set(c) - allowed)
    for k, v in c.items():
        if k == "as_of":
            continue
        if k in sp.CACHE_FLAGS:
            assert isinstance(v, bool), (k, v)
        else:
            assert isinstance(v, (int, float)) and not isinstance(v, bool), (k, v)
    body = json.dumps(c)
    for leaked in ("SECRET", "example.com", "max_5x", "active", "pro+", "cookie", "token", "email", "lines",
                   "errors", "burn", "codex_plan", "grok_cookie_ok", "_pace", "not signed in", "session"):
        assert leaked not in body, leaked


@test
def cache_copy_carries_claude_max_as_a_bool_only():
    for flag in (True, False):
        c = sp.build_cache_copy(google_cache(claude_max=flag))
        assert c["claude_max"] is flag, c
        assert "max_5x" not in json.dumps(c) and "claude_plan" not in c
    for junk in ("yes", 1, 0, None, "max_5x"):
        assert "claude_max" not in sp.build_cache_copy(google_cache(claude_max=junk)), junk
    assert "claude_max" not in sp.build_cache_copy(google_cache())   # unknown stays absent


@test
def cache_copy_keeps_the_numbers_meters_and_routing_need():
    c = sp.build_cache_copy(google_cache())
    assert c["claude_week"] == 40 and c["claude_week_reset_ts"] == NOW + 3 * 86400
    assert c["google_gemini_week"] == 40 and c["google_gemini_5h_reset_ts"] == NOW + 3600
    assert c["google_argon_week"] == 10 and c["cursor_api_pct"] == 61.2 and c["codex_window_s"] == WEEK
    assert c["bot_exhausted"] is False and c["muse_week"] == 50 and c["ts"] == NOW and c["v"] == 1
    assert c["as_of"] == {"claude": NOW - 60, "chatgpt": NOW - 120, "cursor": NOW - 30, "grokbot": NOW - 30,
                          "grok": NOW - 90, "google": NOW - 10, "muse": NOW - 20}, c["as_of"]


@test
def only_pull_marks_a_copy_as_pulled():
    assert "pulled_ts" not in sp.build_cache_copy(google_cache(pulled_ts=NOW))


@test
def cache_copy_drops_wrong_types():
    c = sp.build_cache_copy(google_cache(claude_week="40", codex=True, grok_pct=float("nan"),
                                         bot_exhausted="yes", cursor_api_pct=None))
    for k in ("claude_week", "codex", "grok_pct", "bot_exhausted", "cursor_api_pct"):
        assert k not in c, (k, c.get(k))
    assert sp.build_cache_copy("not a dict") == {"as_of": {}, "v": 1}


def _with_paths(t):
    sp.CACHE, sp.MUSE_FILE = f"{t}/c/latest.json", f"{t}/q/muse-usage.json"
    sp.CONFIG, sp.STATE = f"{t}/gist.json", f"{t}/push.json"


@test
def pull_lands_a_refiltered_copy_and_muse_file():
    import tempfile
    saved = (sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get)
    with tempfile.TemporaryDirectory() as t:
        try:
            _with_paths(t)
            sp.write_json(sp.CONFIG, {"id": "abc123"})
            hand_edited = google_cache()   # strings put straight into the gist file
            calls = []
            sp.gh_get = lambda path: calls.append(path) or {"files": {sp.CACHE_FILE: {"content": json.dumps(hand_edited)}}}
            assert sp.pull() == 0
            assert calls == ["gists/abc123"], calls
            landed = json.load(open(sp.CACHE))
            assert strings_in(landed) == [], strings_in(landed)
            pulled = landed.pop("pulled_ts")
            assert isinstance(pulled, float) and abs(pulled - time.time()) < 60, pulled
            assert landed == sp.build_cache_copy(hand_edited)
            m = json.load(open(sp.MUSE_FILE))
            assert m["window"]["used_percent"] == 35 and m["window"]["window_duration_mins"] == 300
            assert m["weekly"]["used_percent"] == 50
            from datetime import datetime
            for ts, want in ((m["window"]["resets_at"], NOW + 7200), (m["weekly"]["resets_at"], NOW + 3 * 86400),
                             (m["captured_at"], NOW - 20)):
                assert datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() == want, (ts, want)
        finally:
            sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get = saved


@test
def pull_without_a_pushed_copy_changes_nothing():
    import tempfile
    saved = (sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get)
    with tempfile.TemporaryDirectory() as t:
        try:
            _with_paths(t)
            assert sp.pull() == 1   # no gist id
            sp.write_json(sp.CONFIG, {"id": "abc123"})
            sp.gh_get = lambda path: {"files": {"seats.json": {"content": "{}"}}}
            assert sp.pull() == 1
            assert not os.path.exists(sp.CACHE) and not os.path.exists(sp.MUSE_FILE)
        finally:
            sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get = saved


@test
def push_sends_the_widget_file_and_the_numbers_copy():
    import tempfile
    saved = (sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_api, sp.refresh_if_stale)
    with tempfile.TemporaryDirectory() as t:
        try:
            _with_paths(t)
            sp.write_json(sp.CONFIG, {"id": "abc123"})
            sp.write_json(sp.CACHE, google_cache())
            sent = []
            sp.gh_api = lambda method, path, body: sent.append((method, path, body)) or {}
            sp.refresh_if_stale = lambda: None
            assert sp.push() == 0
            (method, path, body), = sent
            assert (method, path) == ("PATCH", "gists/abc123")
            assert set(body["files"]) == {"seats.json", "latest.json"}, body
            copy = json.loads(body["files"]["latest.json"]["content"])
            assert copy == sp.build_cache_copy(google_cache())
            assert "SECRET" not in json.dumps(body) and "example.com" not in json.dumps(body)
            assert sp.push() == 0 and len(sent) == 1   # unchanged: no second push inside the heartbeat
        finally:
            sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_api, sp.refresh_if_stale = saved


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
