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


@test
def cache_copy_round_trips_read_state_and_drops_anything_else():
    until = NOW + 540
    raw = google_cache(
        read_state={"claude": "rate_limited", "chatgpt": "usage_failed", "session": "fresh",
                    "claude_plan": "max_5x"},
        rate_limited_until={"claude": until, "chatgpt": until, "session": until},
    )
    # A free-text reason and a non-enum value never survive. chatgpt is usage_failed,
    # so its epoch is not a rate-limit deadline and is dropped too.
    raw["read_state"]["grok"] = "HTTPError 429 fixture-token"
    copy = sp.build_cache_copy(raw)
    assert copy["read_state"] == {"claude": "rate_limited", "chatgpt": "usage_failed"}, copy.get("read_state")
    assert copy["rate_limited_until"] == {"claude": until}, copy.get("rate_limited_until")
    blob = json.dumps(copy)
    assert "fixture-token" not in blob and "max_5x" not in blob and "HTTPError" not in blob, blob
    # Pull filters again, so a hand-edited gist cannot widen the enum.
    import tempfile
    saved = (sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get)
    with tempfile.TemporaryDirectory() as t:
        try:
            _with_paths(t)
            sp.write_json(sp.CONFIG, {"id": "abc123"})
            sp.gh_get = lambda path: {"files": {sp.CACHE_FILE: {"content": json.dumps(raw)}}}
            assert sp.pull() == 0
            landed = json.load(open(sp.CACHE))
            assert landed["read_state"] == copy["read_state"], landed.get("read_state")
            assert landed["rate_limited_until"] == {"claude": until}, landed.get("rate_limited_until")
            assert "fixture-token" not in json.dumps(landed)
        finally:
            sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get = saved


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
def pull_lands_an_exhausted_muse_without_inventing_a_percent():
    import tempfile
    saved = (sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_get)
    with tempfile.TemporaryDirectory() as t:
        try:
            _with_paths(t)
            sp.write_json(sp.CONFIG, {"id": "abc123"})
            raw = google_cache()
            for key in ("muse_5h", "muse_5h_reset_ts", "muse_week", "muse_week_reset_ts"):
                raw.pop(key)
            raw["muse_exhausted"] = True
            raw["muse_exhausted_reset_ts"] = NOW + 86400
            raw["as_of"] = dict(raw["as_of"], muse=NOW - 5)
            # An older gist has no flag. It still copies, and the new flag is absent.
            old = sp.build_cache_copy(google_cache())
            assert "muse_exhausted" not in old and "muse_exhausted_reset_ts" not in old, old
            assert old["muse_week"] == 50, old
            junk = sp.build_cache_copy(google_cache(muse_exhausted="yes", muse_exhausted_reset_ts="soon"))
            assert "muse_exhausted" not in junk and "muse_exhausted_reset_ts" not in junk, junk
            copy = sp.build_cache_copy(raw)
            assert copy["muse_exhausted"] is True and copy["muse_exhausted_reset_ts"] == NOW + 86400, copy
            assert "muse_5h" not in copy and "muse_week" not in copy, copy
            doc = sp.muse_doc(copy)
            assert doc["exhausted"] is True and doc["exhausted_resets_at"] == NOW + 86400, doc
            assert "window" not in doc and "used_percent" not in json.dumps(doc), doc
            sp.gh_get = lambda path: {"files": {sp.CACHE_FILE: {"content": json.dumps(raw)}}}
            assert sp.pull() == 0
            landed = json.load(open(sp.CACHE))
            assert landed["muse_exhausted"] is True, landed
            assert landed["muse_exhausted_reset_ts"] == NOW + 86400, landed
            assert landed["as_of"]["muse"] == NOW - 5, landed["as_of"]
            assert "muse_5h" not in landed and "muse_week" not in landed, landed
            m = json.load(open(sp.MUSE_FILE))
            assert m["exhausted"] is True and m["exhausted_resets_at"] == NOW + 86400, m
            assert "used_percent" not in json.dumps(m), m
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


# --- refresh: one journal line per decision, wait on the status line's lock, stale by ts ---

import contextlib, fcntl, io, tempfile, threading


FAKE = """#!/usr/bin/env python3
import fcntl, json, os, sys, time
mark, cache, lock, rc, write, err = {args!r}
held = open(lock, "a")
try:
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = "free"
except OSError:
    state = "locked"
with open(mark, "a") as f:
    f.write(state + " " + " ".join(sys.argv[1:]) + "\\n")
if write:
    with open(cache, "w") as f:
        json.dump({{"ts": time.time(), "v": 1}}, f)
sys.stdout.write("stdout cookie=sso-STDOUTSECRET\\n")
sys.stderr.write(err)
sys.exit(rc)
"""


@contextlib.contextmanager
def refresh_env(rc=0, write=True, err="", seat_usage=True):
    names = ("CACHE_DIR", "CACHE", "LOCK", "SEAT_USAGE", "LOCK_WAIT_S", "RUN_TIMEOUT_S", "KILL_AFTER_S")
    saved = {n: getattr(sp, n, None) for n in names}
    with tempfile.TemporaryDirectory() as t:
        try:
            sp.CACHE_DIR = f"{t}/cache"
            os.makedirs(sp.CACHE_DIR)
            sp.CACHE, sp.LOCK = f"{sp.CACHE_DIR}/latest.json", f"{sp.CACHE_DIR}/.lock"
            sp.SEAT_USAGE, mark = f"{t}/seat-usage", f"{t}/runs"
            if seat_usage:
                with open(sp.SEAT_USAGE, "w") as f:
                    f.write(FAKE.format(args=(mark, sp.CACHE, sp.LOCK, rc, write, err)))
                os.chmod(sp.SEAT_USAGE, 0o755)
            sp.LOCK_WAIT_S = 5
            def runs(self):
                if not os.path.exists(mark):
                    return []
                with open(mark) as f:
                    return f.read().split("\n")[:-1]
            yield type("Env", (), {"runs": runs})()
        finally:
            for n, v in saved.items():
                if v is None:
                    if hasattr(sp, n):
                        delattr(sp, n)
                else:
                    setattr(sp, n, v)


def write_cache_at(ts, mtime=None):
    sp.write_json(sp.CACHE, {"ts": ts, "v": 1})
    if mtime is not None:
        os.utime(sp.CACHE, (mtime, mtime))


def run_refresh():
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        sp.refresh_if_stale()
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return lines


def field(line, name):
    """The number in name=<n>s, e.g. field(line, "new_age") -> 0.0."""
    return float(line.split(f" {name}=")[1].split("s")[0])


def one_line(lines):
    assert len(lines) == 1, lines
    assert lines[0].startswith("seat-usage-push: refresh"), lines
    return lines[0]


def hold_lock(seconds, then=None):
    """Hold the cache lock from this process (a separate open file, so it conflicts) for a while."""
    h = open(sp.LOCK, "a")
    fcntl.flock(h, fcntl.LOCK_EX)

    def release():
        time.sleep(seconds)
        if then:
            then()
        fcntl.flock(h, fcntl.LOCK_UN)
        h.close()
    th = threading.Thread(target=release)
    th.start()
    return th


@test
def refresh_fresh_cache_logs_a_skip_with_its_age():
    with refresh_env() as env:
        write_cache_at(time.time() - 100)
        line = one_line(run_refresh())
        assert env.runs() == [], env.runs()
        assert "skip" in line and "fresh" in line and "age=100s" in line and "by=ts" in line, line


@test
def refresh_stale_by_ts_runs_even_when_mtime_is_fresh():
    with refresh_env() as env:
        write_cache_at(time.time() - 1650, mtime=time.time())   # file touched, reading old
        lines = run_refresh()
        assert env.runs() == ["locked --quiet"], env.runs()     # ran once, under the lock
        line = one_line(lines)
        assert "ran" in line and "age=1650s" in line and "by=ts" in line and field(line, "mtime_age") <= 2, line
        assert "rc=0" in line and "lock=got" in line and field(line, "new_age") <= 2, line


@test
def refresh_without_ts_falls_back_to_mtime():
    with refresh_env() as env:
        sp.write_json(sp.CACHE, {"v": 1})
        os.utime(sp.CACHE, (time.time() - 700, time.time() - 700))
        lines = run_refresh()
        assert env.runs() == ["locked --quiet"], env.runs()
        line = one_line(lines)
        assert "age=700s" in line and "by=mtime" in line, line


@test
def refresh_waits_for_a_held_lock_then_runs():
    with refresh_env() as env:
        write_cache_at(time.time() - 1650)
        th = hold_lock(1.5)                       # the status line's run, which did not land a reading
        lines = run_refresh()
        th.join()
        assert env.runs() == ["locked --quiet"], (env.runs(), lines)
        line = one_line(lines)
        assert "ran" in line and "lock=got" in line, line
        assert 1.0 <= field(line, "wait") < 5, line


@test
def refresh_waits_then_skips_when_the_other_run_refreshed():
    with refresh_env() as env:
        write_cache_at(time.time() - 1650)
        th = hold_lock(1.5, then=lambda: write_cache_at(time.time()))
        line = one_line(run_refresh())
        th.join()
        assert env.runs() == [], env.runs()       # no double read
        assert "skip" in line and "fresh-after-wait" in line and "lock=got" in line, line


@test
def refresh_gives_up_on_the_lock_after_the_wait_and_says_so():
    with refresh_env() as env:
        sp.LOCK_WAIT_S = 1
        write_cache_at(time.time() - 1650)
        th = hold_lock(3)
        line = one_line(run_refresh())
        th.join()
        assert env.runs() == [], env.runs()
        assert "skip" in line and "lock=timeout" in line, line


@test
def refresh_logs_exit_code_and_a_scrubbed_stderr_tail():
    err = ("seat-usage: first line\n"
           "RuntimeError: op failed token=ops_eyJhbGciOiJIUzI1NiJ9abcdefSECRET1 "
           "Cookie: sso=SECRETCOOKIE123; Authorization: Bearer sk-ant-SECRETBEARER4567890abc\n")
    with refresh_env(rc=3, write=False, err=err) as env:
        write_cache_at(time.time() - 1650)
        line = one_line(run_refresh())
        assert env.runs() == ["locked --quiet"], env.runs()
        assert "ran" in line and "rc=3" in line and "new_age=1650s" in line, line
        assert "RuntimeError: op failed" in line and "first line" not in line, line
        for secret in ("SECRET", "eyJ", "sso=", "sk-ant", "STDOUT"):
            assert secret not in line, (secret, line)


@test
def refresh_logs_a_timed_out_run():
    with refresh_env():
        sp.RUN_TIMEOUT_S = 1
        with open(sp.SEAT_USAGE, "w") as f:
            f.write("#!/bin/sh\nsleep 5\n")
        write_cache_at(time.time() - 1650)
        start = time.monotonic()
        line = one_line(run_refresh())
        assert time.monotonic() - start < 4, line
        assert "rc=124" in line and "timed-out" in line, line


@test
def refresh_kills_a_run_that_ignores_sigterm_and_frees_the_lock():
    with refresh_env():
        sp.RUN_TIMEOUT_S, sp.KILL_AFTER_S = 1, 1
        with open(sp.SEAT_USAGE, "w") as f:
            f.write("#!/bin/sh\ntrap '' TERM\nsleep 8\n")
        write_cache_at(time.time() - 1650)
        start = time.monotonic()
        line = one_line(run_refresh())
        assert time.monotonic() - start < 5, line
        assert ("rc=137" in line or "rc=-9" in line) and "killed" in line, line
        with open(sp.LOCK, "a") as h:      # nothing left holding it
            fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)


@test
def refresh_treats_a_future_ts_as_unusable():
    with refresh_env() as env:
        write_cache_at(time.time() * 1000, mtime=time.time() - 1650)   # milliseconds by mistake
        lines = run_refresh()
        assert env.runs() == ["locked --quiet"], (env.runs(), lines)
        assert "by=mtime(bad-ts)" in one_line(lines) and "age=1650s" in lines[0], lines


@test
def refresh_reports_a_missing_flock_as_a_lock_error():
    saved = os.environ["PATH"]
    with refresh_env() as env:
        try:
            os.environ["PATH"] = os.path.dirname(sp.SEAT_USAGE)   # no flock, no timeout
            write_cache_at(time.time() - 1650)
            line = one_line(run_refresh())
        finally:
            os.environ["PATH"] = saved
        assert env.runs() == [] and "why=lock-error" in line and "FileNotFoundError" in line, line


@test
def refresh_logs_an_unexpected_error_and_does_not_raise():
    saved = sp.cache_age
    try:
        sp.cache_age = lambda now=None: 1 / 0
        line = one_line(run_refresh())
    finally:
        sp.cache_age = saved
    assert "why=error" in line and "ZeroDivisionError" in line, line


@test
def scrub_redacts_quoted_pairs_cookie_headers_and_token_prefixes():
    cases = [
        '{"token": "abc123"} failed',
        "{'access_token': 'x9f'} failed",
        "Cookie: a=1; sess2=SHORT; other=PRIV",
        "set-cookie: sso=SHORTV; Path=/",
        "Authorization: token SHORTGH",
        "key=SHORTK sid=SHORTS api_key=SHORTA",
        "got sk-ant-api03-SHORT and ghp_SHORTGH and ops_SHORTOP and xoxb-SHORTX",
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIx.SHORTSIG",
        "https://x.example/cb?code=SHORTC&state=1",
        "fetch https://ryan:SHORTPW@host.example/x failed",
    ]
    for c in cases:
        out = sp.scrub(c)
        for bad in ("abc123", "x9f", "SHORT", "PRIV", "eyJ"):
            assert bad not in out, (c, out)
    keep = "HTTPError: HTTP Error 429: Too Many Requests; skipped options for author"
    assert sp.scrub(keep) == keep, sp.scrub(keep)
    assert len(sp.scrub("x" * 10 + " y" * 200)) <= 160


@test
def refresh_drops_a_cut_stderr_line():
    long_line = "Cookie: sso=" + "v; other=PRIVATE " * 400 + "\n"     # far over the 4 KiB tail
    with refresh_env(rc=1, write=False, err=long_line) as env:
        write_cache_at(time.time() - 1650)
        line = one_line(run_refresh())
        assert env.runs() == ["locked --quiet"], env.runs()
        assert 'stderr="<truncated>"' in line and "PRIVATE" not in line, line


@test
def refresh_without_seat_usage_says_so():
    with refresh_env(seat_usage=False):
        write_cache_at(time.time() - 1650)
        line = one_line(run_refresh())
        assert "skip" in line and "not-executable" in line, line


@test
def refresh_hands_seat_usage_the_lock_it_waited_for():
    """The real seat-usage adopts the inherited lock instead of waiting 30 s on itself."""
    real = HERE.parent / "bin" / "seat-usage"
    with refresh_env() as env:
        out = os.path.join(os.path.dirname(sp.SEAT_USAGE), "adopt")
        with open(sp.SEAT_USAGE, "w") as f:
            f.write("#!/usr/bin/env python3\n"
                    "import importlib.machinery, importlib.util, os, sys\n"
                    "sys.dont_write_bytecode = True\n"
                    f"os.environ['SEAT_USAGE_CACHE_DIR'] = {sp.CACHE_DIR!r}\n"
                    f"l = importlib.machinery.SourceFileLoader('su', {str(real)!r})\n"
                    "m = importlib.util.module_from_spec(importlib.util.spec_from_loader(l.name, l))\n"
                    "l.exec_module(m)\n"
                    "h = m._acquire_lock(1)\n"
                    f"open({out!r}, 'w').write('none' if h is None else 'owned=%s' % h.owned)\n")
        write_cache_at(time.time() - 1650)
        lines = run_refresh()
        got = open(out).read() if os.path.exists(out) else "not run"
        assert got == "owned=False", (got, lines)
        one_line(lines)


@test
def push_carries_the_reading_age():
    saved = (sp.CACHE, sp.MUSE_FILE, sp.CONFIG, sp.STATE, sp.gh_api, sp.refresh_if_stale)
    with tempfile.TemporaryDirectory() as t:
        try:
            _with_paths(t)
            sp.write_json(sp.CONFIG, {"id": "abc123"})
            ts = time.time() - 1650
            sp.write_json(sp.CACHE, full_cache(ts=ts))
            sent = []
            sp.gh_api = lambda method, path, body: sent.append(body) or {}
            sp.refresh_if_stale = lambda: None
            assert sp.push() == 0
            p = json.loads(sent[0]["files"]["seats.json"]["content"])
            assert isinstance(p.get("age_s"), (int, float)) and not isinstance(p["age_s"], bool), p.get("age_s")
            assert abs(p["age_s"] - (p["pushed_at"] - ts)) <= 1, (p["age_s"], p["pushed_at"] - ts)
            assert sp.push() == 0 and len(sent) == 1   # the age alone does not defeat the heartbeat
            os.remove(sp.STATE)
            sp.write_json(sp.CACHE, full_cache(ts=time.time() * 1000))   # milliseconds by mistake
            assert sp.push() == 0
            assert json.loads(sent[-1]["files"]["seats.json"]["content"])["age_s"] is None
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
