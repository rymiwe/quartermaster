#!/usr/bin/env python3
"""Route and hook tests for seat-usage. No network."""
import contextlib
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
BIN = Path(os.environ["SEAT_USAGE_BIN"]) if os.environ.get("SEAT_USAGE_BIN") else HERE.parents[0] / "bin" / "seat-usage"
HOOK = HERE.parents[0] / "hooks" / "seat-route-hook"
if not HOOK.exists():   # installed copy: ~/.local/share/seat-usage, hook in ~/.claude/hooks
    HOOK = Path.home() / ".claude" / "hooks" / "seat-route-hook"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


su = load("seat_usage", BIN)
# Never hand a real ~/.config/seat-usage/op-token to a fake op; tests that need one set their own.
os.environ["SEAT_USAGE_OP_TOKEN_FILE"] = "/nonexistent/seat-usage-test/op-token"
hook = load("seat_route_hook", HOOK)

NOW = 1_800_000_000
WEEK = 7 * 86400
MONTH = 30 * 86400


def snap(**kw):
    as_of = kw.pop("as_of", None)
    s = {
        "v": 1,
        "ts": NOW,
        "as_of": {"claude": NOW, "chatgpt": NOW, "cursor": NOW, "grokbot": NOW, "grok": NOW, "muse": NOW, "google": NOW},
        "claude_5h": 10,
        "claude_5h_reset_ts": NOW + 4 * 3600,
        "claude_week": 10,
        "claude_week_reset_ts": NOW + 3 * 86400,
        "fable": 10,
        "fable_reset_ts": NOW + 3 * 86400,
        "codex": 10,
        "codex_reset_ts": NOW + 3 * 86400,
        "codex_window_s": WEEK,
        "cursor_auto_pct": 15,
        "cursor_api_pct": 10,
        "cursor_pct": 15,
        "cursor_reset_ts": NOW + 15 * 86400,
        "cursor_window_s": MONTH,
        "grok_pct": 10,
        "grok_reset_ts": NOW + 3 * 86400,
        "grok_window_s": WEEK,
        "grok_exhausted": False,
        "bot_pct": 10,
        "bot_reset_ts": NOW + 3 * 86400,
        "bot_window_s": WEEK,
        "muse_5h": 10,
        "muse_5h_reset_ts": NOW + 4 * 3600,
        "muse_week": 10,
        "muse_week_reset_ts": NOW + 3 * 86400,
        "google_claudegpt_5h": 10,
        "google_claudegpt_5h_reset_ts": NOW + 4 * 3600,
        "google_claudegpt_week": 10,
        "google_claudegpt_week_reset_ts": NOW + 3 * 86400,
    }
    s.update(kw)
    if as_of is not None:
        s["as_of"] = as_of
    return s


def without_as_of(s, seat):
    s = dict(s)
    s["as_of"] = {k: v for k, v in s["as_of"].items() if k != seat}
    return s


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def absent_seat_no_as_of():
    s = snap()
    s["as_of"] = {k: v for k, v in s["as_of"].items() if k != "claude"}
    for key in ("fable", "fable_reset_ts", "claude_week", "claude_week_reset_ts", "claude_5h", "claude_5h_reset_ts"):
        s.pop(key, None)
    r = su.route("bounded-question", s, NOW)
    assert r["mode"] == "default", r
    assert r["seat"] == "claude" and r["family"] == "fable", r
    assert r["reason"] == "Fable: no reading", r
    assert r["reading_age_s"] is None, r


@test
def failed_read_numbers_without_as_of():
    s = without_as_of(snap(fable=12, claude_week=40), "claude")
    r = su.route("bounded-question", s, NOW)
    assert r["mode"] == "default", r
    assert r["family"] == "fable", r
    assert r["reason"] == "Fable: no reading", r
    assert r["reading_age_s"] is None, r


@test
def passed_reset():
    r = su.route("bounded-question", snap(fable_reset_ts=NOW - 1), NOW)
    assert r["mode"] == "default", r
    assert r["reason"] == "Fable: reset passed", r
    assert r["family"] == "fable", r


@test
def stale_reading_11_min():
    s = snap()
    s["as_of"] = dict(s["as_of"])
    s["as_of"]["claude"] = NOW - 11 * 60
    r = su.route("bounded-question", s, NOW)
    assert r["mode"] == "default", r
    assert r["reason"] == "Fable: reading 11m old", r
    assert r["reading_age_s"] == 11 * 60, r


@test
def absent_key_is_never_zero():
    zero = su.route("bounded-question", snap(fable=0), NOW)
    assert zero["mode"] == "suggest", zero
    assert "under pace" in zero["reason"], zero
    missing = snap()
    del missing["fable"]
    r = su.route("bounded-question", missing, NOW)
    assert r["mode"] == "default", r
    assert r["reason"] == "Fable: no used %", r
    assert r["family"] == "fable", r


@test
def fable_under_on_ahead():
    under = su.route("bounded-question", snap(fable=10), NOW)
    assert under["mode"] == "suggest" and under["family"] == "fable" and under["seat"] == "claude", under
    assert under["effort"] == "-" and under["variant"] == "-", under
    assert under["reason"] == "Fable under pace: score 0.87 (fit 0.90, headroom 1.00, cost 3, lose-it 0.00)", under
    on = su.route("bounded-question", snap(fable=57), NOW)
    assert on["mode"] == "default" and on["family"] == "fable" and on["effort"] == "-", on
    ahead = su.route("bounded-question", snap(fable=80), NOW)
    assert ahead["mode"] == "suggest" and ahead["family"] == "opus" and ahead["seat"] == "claude", ahead
    assert ahead["reason"] == "claude weekly all under pace: score 0.58 (fit 0.62, headroom 1.00, cost 4, lose-it 0.00)", ahead
    assert su.pace_code(10, NOW + 3 * 86400, WEEK, NOW) == "under"
    assert su.pace_code(57, NOW + 3 * 86400, WEEK, NOW) == "on"
    assert su.pace_code(80, NOW + 3 * 86400, WEEK, NOW) == "ahead"


@test
def codex_under_on_ahead():
    under = su.route("hard-bug", snap(codex=10), NOW)
    assert under["mode"] == "suggest" and under["family"] == "astra" and under["effort"] == "low", under
    assert under["reason"] == "Codex under pace: score 0.85 (fit 0.88, headroom 1.00, cost 3, lose-it 0.00)", under
    on = su.route("hard-bug", snap(codex=57), NOW)
    assert on["mode"] == "default" and on["family"] == "astra" and on["effort"] == "low", on
    ahead = su.route("hard-bug", snap(codex=80), NOW)
    assert ahead["mode"] == "suggest" and ahead["family"] == "sol" and ahead["effort"] == "-", ahead
    assert ahead["seat"] == "chatgpt", ahead
    assert ahead["reason"] == "Codex ahead of pace: score 0.45 (fit 0.88, headroom 0.55, cost 3, lose-it 0.00)", ahead


@test
def builder_spends_the_under_pool_and_keeps_cursor_when_both_are_on():
    # Default grok is under, so an on-pace Cursor still loses to it.
    under = su.route("builder", snap(cursor_auto_pct=15), NOW)
    assert under["mode"] == "default" and under["seat"] == "cursor" and under["family"] == "grok", under
    assert under["effort"] == "high" and under["variant"] == "fast", under
    assert under["reason"] == "Cursor Models under pace: score 0.78 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00)", under
    assert under["reading_age_s"] == 0, under
    moved = su.route("builder", snap(cursor_auto_pct=50), NOW)
    assert moved["seat"] == "grok" and moved["variant"] == "-" and moved["effort"] == "high", moved
    assert moved["reason"] == "grok under pace: score 0.74 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00)", moved
    both_on = su.route("builder", snap(cursor_auto_pct=50, grok_pct=57), NOW)
    assert both_on["mode"] == "default" and both_on["seat"] == "cursor" and both_on["variant"] == "fast", both_on
    assert both_on["reason"] == "Cursor Models on pace: score 0.70 (fit 0.80, headroom 0.90, cost 2, lose-it 0.00)", both_on
    ahead = su.route("builder", snap(cursor_auto_pct=80), NOW)
    assert ahead["seat"] == "grok" and ahead["effort"] == "high" and ahead["mode"] == "default", ahead
    chair = su.route("chair", snap(cursor_auto_pct=15), NOW)
    assert chair["reason"] == under["reason"], chair
    assert chair["seat"] == "cursor" and chair["family"] == "grok" and chair["variant"] == "fast", chair
    assert chair["effort"] == "high", chair


@test
def fable_capped_goes_to_opus_on_max():
    r = su.route("bounded-question", snap(fable=100, claude_week=10, claude_5h=10), NOW)
    assert r["mode"] == "suggest" and r["seat"] == "claude" and r["family"] == "opus", r
    assert r["effort"] == "-" and r["variant"] == "-", r
    assert r["reason"] == "claude weekly all under pace: score 0.58 (fit 0.62, headroom 1.00, cost 4, lose-it 0.00)", r


@test
def max_capped_goes_to_cursor_fable():
    week = su.route("bounded-question", snap(fable=100, claude_week=100, claude_5h=10, cursor_api_pct=10), NOW)
    assert week["mode"] == "suggest" and week["seat"] == "cursor" and week["family"] == "fable", week
    assert week["reason"] == "Other Models under pace: score 0.22 (fit 0.25, headroom 1.00, cost 3, lose-it 0.00)", week
    session = su.route("bounded-question", snap(fable=100, claude_week=10, claude_5h=100, cursor_api_pct=10), NOW)
    assert session["mode"] == "suggest" and session["seat"] == "cursor" and session["family"] == "fable", session
    blocked = su.route("bounded-question", snap(fable=100, claude_week=100, cursor_api_pct=100), NOW)
    assert blocked["mode"] == "ask" and blocked["family"] == "fable" and blocked["seat"] == "claude", blocked
    assert blocked["reason"] == "Other Models capped: ask Ryan", blocked
    unknown = snap(fable=100, claude_week=100)
    del unknown["cursor_api_pct"]
    missing = su.route("bounded-question", unknown, NOW)
    assert missing["mode"] == "ask" and missing["reason"] == "Other Models reading unknown: fix the failed reading, then ask Ryan", missing


@test
def max_capped_wins_over_fable_pace():
    # Fable under pace does not help when Max itself is session-limited or weekly-capped.
    for kw in ({"claude_5h": 100}, {"claude_week": 100}):
        r = su.route("bounded-question", snap(fable=5, cursor_api_pct=10, **kw), NOW)
        assert r["seat"] == "cursor" and r["family"] == "fable" and r["mode"] == "suggest", (kw, r)
    r = su.route("bounded-question", snap(fable=5, claude_5h=100, cursor_api_pct=100), NOW)
    assert r["mode"] == "ask" and r["reason"] == "Other Models capped: ask Ryan", r


@test
def builder_cursor_capped_uses_supergrok_until_that_pool_is_capped_too():
    native = su.route("builder", snap(cursor_auto_pct=100, claude_week=10, claude_5h=10), NOW)
    assert native["mode"] == "default" and native["seat"] == "grok" and native["family"] == "grok", native
    assert native["effort"] == "high" and native["variant"] == "-", native
    ok = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_week=10, claude_5h=10), NOW)
    assert ok["mode"] == "suggest" and ok["seat"] == "claude" and ok["family"] == "opus", ok
    assert ok["effort"] == "low" and ok["variant"] == "-", ok
    assert ok["reason"] == "claude weekly all under pace: score 0.36 (fit 0.40, headroom 1.00, cost 4, lose-it 0.00)", ok
    agy = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_week=100, claude_5h=10), NOW)
    assert (agy["mode"], agy["seat"], agy["family"], agy["effort"]) == ("suggest", "google", "opus", "low"), agy
    blocked = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_week=100, claude_5h=10,
                                       google_claudegpt_week=100), NOW)
    assert blocked["mode"] == "ask" and blocked["seat"] == "cursor" and blocked["family"] == "grok", blocked
    assert blocked["effort"] == "high" and blocked["variant"] == "fast", blocked
    assert blocked["reason"] == "Google Claude/GPT weekly capped: ask Ryan", blocked
    unknown = snap(cursor_auto_pct=100, grok_pct=100, claude_5h=10, google_claudegpt_week=100)
    del unknown["claude_week"]
    u = su.route("builder", unknown, NOW)
    assert u["mode"] == "ask" and u["reason"].endswith("fix the failed reading, then ask Ryan"), u


@test
def builder_reads_auto_not_the_max_bar():
    r = su.route("builder", snap(cursor_auto_pct=10, cursor_api_pct=100, cursor_pct=100), NOW)
    assert r["mode"] == "default" and r["seat"] == "cursor" and r["family"] == "grok", r
    assert r["effort"] == "high", r
    assert "cursor_pct" not in su.POOLS["cursor_models"]["used"]


@test
def chair_uses_supergrok_when_cursor_is_capped_and_opus_only_when_both_are_dead():
    native = su.route("chair", snap(cursor_auto_pct=100, grok_pct=10, grok_exhausted=False, claude_week=10, claude_5h=10), NOW)
    assert native["mode"] == "default" and native["seat"] == "grok" and native["family"] == "grok", native
    assert native["effort"] == "high" and native["variant"] == "-", native
    agy = su.route("chair", snap(cursor_auto_pct=100, grok_pct=100, claude_week=100, claude_5h=100), NOW)
    assert (agy["seat"], agy["family"], agy["effort"]) == ("google", "opus", "high"), agy
    none = su.route("chair", snap(cursor_auto_pct=100, grok_pct=100, claude_week=100, claude_5h=100,
                                  google_claudegpt_week=100), NOW)
    assert none["mode"] == "ask" and none["seat"] == "cursor", none
    assert none["reason"] == "Google Claude/GPT weekly capped: ask Ryan", none


@test
def hard_bug_capped_asks():
    r = su.route("hard-bug", snap(codex=100), NOW)
    assert r["mode"] == "ask" and r["family"] == "astra" and r["seat"] == "chatgpt", r
    assert r["reason"].startswith("ChatGPT capped: ask Ryan"), r


@test
def burn_mode_raises_effort():
    soon = NOW + 12 * 3600
    fable = snap(fable=10, fable_reset_ts=soon)
    assert any(e["pool"] == "weekly Fable" for e in su.burn_entries(fable, NOW))
    rf = su.route("bounded-question", fable, NOW)
    assert rf["mode"] == "burn" and rf["effort"] == "max" and rf["family"] == "fable", rf
    codex = snap(codex=10, codex_reset_ts=soon)
    rc = su.route("hard-bug", codex, NOW)
    assert rc["mode"] == "burn" and rc["effort"] == "medium" and rc["family"] == "astra", rc
    cursor = snap(cursor_auto_pct=10, cursor_reset_ts=soon, cursor_window_s=MONTH)
    rb = su.route("builder", cursor, NOW)
    assert rb["mode"] == "burn" and rb["effort"] == "xhigh" and rb["family"] == "grok" and rb["variant"] == "fast", rb
    chair = su.route("chair", cursor, NOW)
    assert chair["mode"] == "burn" and chair["effort"] == "xhigh" and chair["seat"] == "cursor", chair


@test
def term_end_before_reset_shortens_the_window():
    # The 2026-10-08 miss: Codex at 0% read "resets in 6.9d" on the day its term ended.
    ends = snap(codex=0, codex_reset_ts=NOW + 6 * 86400, codex_renews_ts=NOW + 6 * 3600, codex_renews=False)
    burn = [e for e in su.burn_entries(ends, NOW) if e["seat"] == "chatgpt"]
    assert burn and burn[0]["hours"] == 6.0, burn
    r = su.route("hard-bug", ends, NOW)
    assert r["mode"] == "burn" and r["family"] == "astra", r
    # Renewing, or a hand-kept date with renews unknown, leaves the reset alone.
    for renews in (True, None):
        keep = dict(ends, codex_renews=renews)
        assert not [e for e in su.burn_entries(keep, NOW) if e["seat"] == "chatgpt"], renews
        assert su.route("hard-bug", keep, NOW)["mode"] == "suggest", renews
    # A term end after the reset changes nothing.
    later = dict(ends, codex_renews_ts=NOW + 8 * 86400)
    assert su.route("hard-bug", later, NOW)["mode"] == "suggest"
    # A term that already ended is capped, not "resets in 6d".
    ended = dict(ends, codex_renews_ts=NOW - 60)
    assert su.pool_status("codex", ended, NOW) == ("capped", "term ended")
    assert su.route("hard-bug", ended, NOW)["mode"] == "ask"
    # The term end is a billing fact, so an old usage reading does not hide it.
    old = dict(ended, as_of=dict(ended["as_of"], chatgpt=NOW - 3600))
    assert su.pool_status("codex", old, NOW) == ("capped", "term ended")
    claude = snap(claude_renews_ts=NOW - 60, claude_renews=None)
    assert su.pool_status("fable", claude, NOW)[0] == "under"


@test
def asks_are_logged_and_settled_routes_are_not():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "asks.jsonl")
        old, su.ASKS = su.ASKS, path
        try:
            su.log_asks([su.route("hard-bug", snap(codex=10), NOW)], snap(codex=10), NOW)
            assert not os.path.exists(path)
            capped = snap(codex=100)
            su.log_asks([su.route("hard-bug", capped, NOW)], capped, NOW)
            rows = [json.loads(ln) for ln in open(path)]
            assert len(rows) == 1 and rows[0]["row"]["mode"] == "ask", rows
            assert rows[0]["pools"]["codex"] == ["capped", 100], rows[0]["pools"]
        finally:
            su.ASKS = old


@test
def merge_cursor_keeps_claude_and_failed_read_drops_as_of():
    base = {
        "v": 1,
        "ts": NOW - 50,
        "as_of": {"claude": NOW - 20, "cursor": NOW - 20, "grokbot": NOW - 20},
        "claude_week": 12,
        "claude_week_reset_ts": NOW + 3 * 86400,
        "fable": 15,
        "fable_reset_ts": NOW + 3 * 86400,
        "claude_5h": 3,
        "claude_5h_reset_ts": NOW + 3600,
        "cursor_auto_pct": 40,
        "cursor_api_pct": 22,
        "cursor_reset_ts": NOW + 10 * 86400,
        "cursor_window_s": MONTH,
        "lines": ["claude   | old claude", "cursor   | old cursor", "grokbot  | old bot"],
    }
    failed = {"ts": NOW, "as_of": {}, "cursor": "pro+", "lines": ["cursor   | usage failed"]}
    merged = su.merge_summary(base, failed, ["cursor", "grokbot"], now=NOW)
    assert merged["as_of"]["claude"] == NOW - 20, merged["as_of"]
    assert merged["claude_week"] == 12 and merged["fable"] == 15, merged
    assert "cursor" not in merged["as_of"] and "grokbot" not in merged["as_of"], merged["as_of"]
    assert merged["cursor_auto_pct"] == 40, merged
    assert merged["v"] == 1
    assert any(ln.startswith("claude") for ln in merged["lines"])
    fresh = {
        "ts": NOW,
        "as_of": {"cursor": NOW, "grokbot": NOW},
        "cursor_auto_pct": 7,
        "cursor_api_pct": 8,
        "cursor_reset_ts": NOW + 10 * 86400,
        "cursor_window_s": MONTH,
        "bot_pct": 4,
        "lines": ["cursor   | new cursor", "grokbot  | new bot"],
    }
    ok = su.merge_summary(base, fresh, ["cursor", "grokbot"], now=NOW)
    assert ok["cursor_auto_pct"] == 7 and ok["as_of"]["cursor"] == NOW, ok
    assert ok["as_of"]["claude"] == NOW - 20 and ok["claude_week"] == 12 and ok["fable"] == 15, ok
    assert any("new cursor" in ln for ln in ok["lines"]) and any("old claude" in ln for ln in ok["lines"])


@test
def route_voter_exits_2_before_network():
    proc = subprocess.run([sys.executable, str(BIN), "--route", "voter"], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 2, (proc.returncode, proc.stderr, proc.stdout)
    assert "voters do not route" in proc.stderr, proc.stderr
    assert proc.stdout == "", proc.stdout


@test
def unknown_role_exits_2_with_the_list():
    proc = subprocess.run([sys.executable, str(BIN), "--route", "nope"], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 2, proc.returncode
    for name in ("bounded-question", "hard-bug", "builder", "chair"):
        assert name in proc.stderr, proc.stderr


@test
def cached_silent_when_council_voter():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump({"ts": NOW, "v": 1, "as_of": {"claude": NOW}, "lines": ["claude   | should not print"], "burn": []}, f)
        env = os.environ.copy()
        env["COUNCIL_VOTER"] = "1"
        env["SEAT_USAGE_CACHE_DIR"] = d
        proc = subprocess.run([sys.executable, str(BIN), "--cached"], capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, proc.returncode
    assert proc.stdout == "" and proc.stderr == "", (proc.stdout, proc.stderr)


@test
def hook_agent_deny():
    cur = [{"role": "builder", "seat": "cursor", "family": "grok", "effort": "high", "variant": "fast", "reason": "under pace"}]
    opus = [{"role": "builder", "seat": "claude", "family": "opus", "effort": "low", "variant": "-", "reason": "Cursor capped"}]
    build = {"subagent_type": "general-purpose", "prompt": "Implement fix 6, then commit and open a PR."}
    assert "grok on cursor" in hook.agent_deny_reason(build, cur)
    assert hook.agent_deny_reason({"prompt": "implement it"}, cur), "missing type counts as general-purpose"
    assert hook.agent_deny_reason(dict(build, subagent_type="fork"), cur)
    assert hook.agent_deny_reason(build, opus) is None, "Opus fallback is a Claude builder"
    assert hook.agent_deny_reason(dict(build, prompt="route-override: Cursor down\nImplement it"), cur) is None
    assert hook.agent_deny_reason(dict(build, subagent_type="Explore"), cur) is None
    assert hook.agent_deny_reason(dict(build, subagent_type="cursor:cursor-runner"), cur) is None
    assert hook.agent_deny_reason({"subagent_type": "general-purpose", "prompt": "Find where X is defined"}, cur) is None
    assert hook.agent_deny_reason(build, []) is None, "fails open with no builder row"
    assert hook.agent_deny_reason("bad", cur) is None


@test
def hook_role_mapping():
    assert hook.command_role("FOO=1 codex exec") == "hard-bug"
    assert hook.command_role("FOO=1 BAR=2 codex exec -m a") == "hard-bug"
    assert hook.command_role("/x/bin/cursor-agent") == "builder"
    assert hook.command_role("agy --model claude-opus-5-5-low -p 'fix it'") == "builder"
    assert hook.command_role("agy --model claude-opus-5-5-low -p='fix it'") == "builder"
    assert hook.command_role("agy models") is None
    assert hook.command_role("claude -p --model fable") == "bounded-question"
    assert hook.command_role("claude -p --permission-mode bypassPermissions --model=fable") == "bounded-question"
    assert hook.command_role("claude -p --model opus") == "builder"
    assert hook.command_role("git status") is None
    assert hook.command_role("/usr/bin/grok -p hi") == "chair"
    assert hook.command_role("/usr/bin/codex exec") == "hard-bug"
    assert hook.command_role("codex review") is None
    assert hook.command_role("env FOO=1 codex exec") is None


@test
def hook_prints_nothing_for_council_voter():
    env = os.environ.copy()
    env["COUNCIL_VOTER"] = "1"
    proc = subprocess.run([sys.executable, str(HOOK)], input='{"tool_name": "Agent"}',
                          capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, proc.returncode
    assert proc.stdout == "" and proc.stderr == "", (proc.stdout, proc.stderr)


@test
def f1_unknown_fable_still_overflows_capped_max():
    week = snap(claude_week=100, cursor_api_pct=31)
    del week["fable"]
    r = su.route("bounded-question", week, NOW)
    assert r["mode"] == "suggest" and r["seat"] == "cursor" and r["family"] == "fable", r
    assert r["reason"] == "Other Models under pace: score 0.22 (fit 0.25, headroom 1.00, cost 3, lose-it 0.00)", r
    session = snap(claude_5h=100, cursor_api_pct=31, fable_reset_ts=NOW)
    r = su.route("bounded-question", session, NOW)
    assert r["mode"] == "suggest" and r["reason"] == "Other Models under pace: score 0.22 (fit 0.25, headroom 1.00, cost 3, lose-it 0.00)", r
    ask = snap(claude_week=100, cursor_api_pct=100, fable_reset_ts=NOW)
    r = su.route("bounded-question", ask, NOW)
    assert r["mode"] == "ask" and r["reason"] == "Other Models capped: ask Ryan", r
    missing = snap()
    del missing["cursor_auto_pct"]
    b = su.route("builder", missing, NOW)
    assert b["mode"] == "default" and b["seat"] == "grok" and b["family"] == "grok", b
    assert "failed" not in b["reason"], b


@test
def f2_model_flag_ignores_prompt_and_quotes():
    assert hook.command_role("claude -p 'Explain --model fable usage' --model opus") == "builder"
    assert hook.command_role('claude -p --model opus "explain why --model fable was slow"') == "builder"
    assert hook.command_role('claude -p --model "fable"') == "bounded-question"
    assert hook.command_role("claude -p --model 'fable'") == "bounded-question"
    assert hook.command_role("claude --model fable") is None
    assert hook.command_role("claude -p --model opus; echo --model fable") == "builder"
    assert hook.command_role("claude -p --model opus\necho --model fable") == "builder"
    assert hook.command_role("claude -p --model=fable-extended") == "builder"


@test
def f3_chair_asks_when_the_grok_reading_is_missing():
    unknown = without_as_of(snap(cursor_auto_pct=100, claude_week=10, claude_5h=10), "grok")
    r = su.route("chair", unknown, NOW)
    assert r["mode"] == "ask" and r["seat"] == "cursor" and r["family"] == "grok", r
    assert r["reason"] == "grok reading unknown: fix the failed reading, then ask Ryan", r
    capped = su.route("chair", snap(cursor_auto_pct=100, grok_pct=100, claude_week=10, claude_5h=10), NOW)
    assert capped["mode"] == "suggest" and capped["seat"] == "claude" and capped["family"] == "opus", capped
    assert capped["effort"] == "high", capped
    assert capped["reason"] == "claude weekly all under pace: score 0.36 (fit 0.40, headroom 1.00, cost 4, lose-it 0.00)", capped
    same = su.route_lineup(snap(cursor_auto_pct=100, grok_pct=100, claude_week=10, claude_5h=10), NOW)
    assert same["lineup"]["chair"]["effort"] == capped["effort"] == "high", same
    assert same["lineup"]["builder"]["effort"] == "low", same


@test
def f4_zero_5h_without_reset_is_open():
    # Both Grok pools are capped, so the builder is Opus and the 5h state matters.
    r = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=0, claude_5h_reset_ts=None), NOW)
    assert r["mode"] == "suggest", r
    assert r["seat"] == "claude" and r["family"] == "opus" and r["effort"] == "low", r
    assert r["reason"] == "claude weekly all under pace: score 0.36 (fit 0.40, headroom 1.00, cost 4, lose-it 0.00)", r
    # A passed 5h clock, or a used% under 100 with no clock, does not block Max.
    past = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=0, claude_5h_reset_ts=NOW - 1), NOW)
    assert past["mode"] == "suggest" and past["family"] == "opus", past
    used = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=1, claude_5h_reset_ts=None), NOW)
    assert used["mode"] == "suggest" and used["family"] == "opus", used
    full = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=100, claude_5h_reset_ts=None,
                                    google_claudegpt_5h=100), NOW)
    assert full["mode"] == "ask" and "5h" in full["reason"], full


@test
def f5_partial_merge_keeps_full_run_ts():
    base = snap(codex=40)
    base["ts"] = NOW - 7200
    base["as_of"] = {k: NOW - 7200 for k in base["as_of"]}
    fresh = {"ts": NOW, "as_of": {"chatgpt": NOW}, "codex": 55, "lines": ["chatgpt  | new"]}
    merged = su.merge_summary(base, fresh, ["chatgpt"], now=NOW)
    assert merged["ts"] == NOW - 7200, merged["ts"]
    no_ts = dict(base)
    del no_ts["ts"]
    assert su.merge_summary(no_ts, fresh, ["chatgpt"], now=NOW)["ts"] == NOW
    assert su.merge_summary({"ts": 0, "as_of": {}, "v": 1, "lines": []}, fresh, ["chatgpt"], now=NOW)["ts"] == 0
    with tempfile.TemporaryDirectory() as d:
        old_cache, old_dir, old_summary = su.CACHE, su.CACHE_DIR, su.SUMMARY
        saved = {name: getattr(su, name) for name in ("claude", "codex", "cursor", "grok")}
        su.CACHE = os.path.join(d, "latest.json")
        su.CACHE_DIR = d
        try:
            assert su._load_cache()["ts"] == 0
            with open(su.CACHE, "w") as f:
                json.dump({"ts": NOW - 7200, "as_of": {}, "v": 1, "lines": []}, f)
            for name in saved:
                setattr(su, name, lambda: [])
            out = su.refresh(["chatgpt"])
            assert out["ts"] == NOW - 7200, out.get("ts")
            assert abs(os.stat(su.CACHE).st_mtime - (NOW - 7200)) < 1, os.stat(su.CACHE).st_mtime
        finally:
            su.CACHE, su.CACHE_DIR, su.SUMMARY = old_cache, old_dir, old_summary
            for name, fn in saved.items():
                setattr(su, name, fn)


@test
def f6_older_fresh_as_of_keeps_base():
    base = {
        "v": 1,
        "ts": NOW - 300,
        "as_of": {"chatgpt": NOW - 300},
        "codex": 40,
        "lines": ["chatgpt  | base line"],
    }
    fresh = {
        "ts": NOW,
        "as_of": {"chatgpt": NOW - 80000},
        "codex": 95,
        "lines": ["chatgpt  | old rollout"],
    }
    merged = su.merge_summary(base, fresh, ["chatgpt"], now=NOW)
    assert merged["codex"] == 40, merged.get("codex")
    assert merged["as_of"]["chatgpt"] == NOW - 300, merged["as_of"]
    assert merged["lines"] == ["chatgpt  | base line"], merged["lines"]
    empty = {"v": 1, "ts": NOW, "as_of": {}, "codex": 1, "lines": ["chatgpt  | prior"]}
    took = su.merge_summary(empty, fresh, ["chatgpt"], now=NOW)
    assert took["codex"] == 95 and took["as_of"]["chatgpt"] == NOW - 80000, took


@test
def failed_reread_keeps_numbers_and_takes_the_new_read_state():
    until = NOW + 600
    base = {
        "v": 1, "ts": NOW - 50,
        "as_of": {"claude": NOW - 20},
        "claude_week": 12, "claude_week_reset_ts": NOW + 3 * 86400,
        "read_state": {"claude": "fresh"},
        "lines": ["claude   | old claude"],
    }
    # No as_of: the numbers stay, and the failure code still replaces fresh.
    expired = {
        "ts": NOW, "as_of": {},
        "read_state": {"claude": "token_expired"},
        "lines": ["claude   | token expired 1.0h ago; run `claude` once to refresh"],
    }
    merged = su.merge_summary(base, expired, ["claude"], now=NOW)
    assert merged["claude_week"] == 12, merged
    assert "claude" not in merged["as_of"], merged["as_of"]
    assert merged["read_state"] == {"claude": "token_expired"}, merged.get("read_state")
    assert "rate_limited_until" not in merged
    # Older stamp than the cache (kept_base): numbers stay, a failure code still wins.
    limited = {
        "ts": NOW, "as_of": {"claude": NOW - 100},
        "claude_week": 99,
        "read_state": {"claude": "rate_limited"},
        "rate_limited_until": {"claude": until},
        "lines": ["claude   | old numbers"],
    }
    held = su.merge_summary(base, limited, ["claude"], now=NOW)
    assert held["claude_week"] == 12 and held["as_of"]["claude"] == NOW - 20, held
    assert held["read_state"] == {"claude": "rate_limited"}, held.get("read_state")
    assert held["rate_limited_until"] == {"claude": until}, held.get("rate_limited_until")
    # An older successful read does not wipe a newer failure code.
    newer = dict(base, read_state={"claude": "usage_failed"})
    older_ok = {
        "ts": NOW, "as_of": {"claude": NOW - 100},
        "claude_week": 99,
        "read_state": {"claude": "fresh"},
        "lines": ["claude   | stale success"],
    }
    kept = su.merge_summary(newer, older_ok, ["claude"], now=NOW)
    assert kept["claude_week"] == 12 and kept["as_of"]["claude"] == NOW - 20, kept
    assert kept["read_state"] == {"claude": "usage_failed"}, kept.get("read_state")


@test
def f7_failed_grok_reread_ors_exhausted():
    base = {
        "v": 1,
        "ts": NOW - 20,
        "as_of": {"grok": NOW - 20},
        "grok_exhausted": False,
        "grok_pct": 10,
        "grok_cookie_ok": True,
        "grok_402_h": 1,
        "lines": ["grok     | old"],
    }
    fresh = {
        "ts": NOW,
        "as_of": {},
        "grok_exhausted": True,
        "grok_cookie_ok": False,
        "grok_402_h": 4,
        "lines": ["grok     | usage failed"],
    }
    merged = su.merge_summary(base, fresh, ["grok"], now=NOW)
    assert merged["grok_exhausted"] is True, merged.get("grok_exhausted")
    assert merged["grok_cookie_ok"] is False, merged.get("grok_cookie_ok")
    assert merged["grok_402_h"] == 4, merged.get("grok_402_h")
    assert merged["grok_pct"] == 10, merged.get("grok_pct")
    assert "grok" not in merged["as_of"], merged["as_of"]
    held = dict(base)
    held["grok_exhausted"] = True
    fresh_clear = {"ts": NOW, "as_of": {}, "grok_exhausted": False}
    assert su.merge_summary(held, fresh_clear, ["grok"], now=NOW)["grok_exhausted"] is True


@test
def f8_assignment_value_command_substitution():
    assert hook.command_role("SOL=$(jq -r '.x' ~/.codex/models_cache.json | tail -1)\ncodex exec -m sol") == "hard-bug"
    assert hook.command_role('P=$(cat /tmp/p.md) claude -p --model fable "$P"') == "bounded-question"
    assert hook.command_role("FOO=1 codex exec") == "hard-bug"
    assert hook.command_role("env FOO=1 codex exec") is None


@test
def f9_prompt_flag_anywhere_in_argv():
    assert hook.command_role("grok --prompt-file /tmp/p.md --model grok-4.7 --always-approve") == "chair"
    assert hook.command_role("grok --prompt-file=/tmp/p.md") == "chair"
    assert hook.command_role('claude --resume 1234-abcd -p "finish"') == "builder"
    assert hook.command_role('claude --model fable -p "q"') == "bounded-question"
    assert hook.command_role('claude --print --model fable "q"') == "bounded-question"


@test
def f11_missing_as_of_prints_no_reading():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump({
                "ts": now,
                "v": 1,
                "as_of": {"cursor": now - 4 * 3600},
                "lines": ["grokbot  | usage failed: URLError", "cursor   | models"],
                "burn": [],
            }, f)
        env = os.environ.copy()
        env.pop("COUNCIL_VOTER", None)
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        env["SEAT_USAGE_CACHE_DIR"] = d
        proc = subprocess.run([sys.executable, str(BIN), "--cached"], capture_output=True, text=True, env=env, timeout=15)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "grokbot  | usage failed: URLError | no reading" in proc.stdout, proc.stdout
    assert "cursor   | models | stale (reading 4.0h old)" in proc.stdout, proc.stdout
    assert "3.0h" not in proc.stdout, proc.stdout
    assert su.seat_age_s({"ts": NOW, "as_of": {"cursor": NOW}}, "grok", NOW) is None
    assert su.seat_age_s({"ts": NOW - 10}, "grok", NOW) == 10


@test
def full_run_merges_failed_seat_under_lock():
    old_ts = time.time() - 500
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "latest.json")
        seed = {
            "v": 1,
            "ts": old_ts,
            "as_of": {seat: old_ts for seat in ("claude", "chatgpt", "cursor", "grokbot", "grok")},
            "claude_week": 11,
            "codex": 12,
            "cursor_auto_pct": 21,
            "cursor_api_pct": 22,
            "bot_pct": 23,
            "grok_pct": 16,
            "lines": [
                "claude   | old claude",
                "chatgpt  | old codex",
                "cursor   | old cursor",
                "grokbot  | old bot",
                "grok     | old grok",
            ],
        }
        with open(cache, "w") as f:
            json.dump(seed, f)
        old_cache, old_dir, old_summary = su.CACHE, su.CACHE_DIR, su.SUMMARY
        saved = {name: getattr(su, name) for name in ("claude", "codex", "cursor", "grok")}
        state = {"claude_week": 81, "line": "claude   | new claude"}

        def claude():
            su.SUMMARY["claude_week"] = state["claude_week"]
            su.SUMMARY["as_of"]["claude"] = time.time()
            return [state["line"]]

        def codex():
            su.SUMMARY["codex"] = 82
            su.SUMMARY["as_of"]["chatgpt"] = time.time()
            return ["chatgpt  | new codex"]

        def cursor():
            raise RuntimeError("boom")

        def grok():
            su.SUMMARY["grok_pct"] = 83
            su.SUMMARY["as_of"]["grok"] = time.time()
            return ["grok     | new grok"]

        su.CACHE = cache
        su.CACHE_DIR = d
        su.claude, su.codex, su.cursor, su.grok = claude, codex, cursor, grok
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                su.full_run(quiet=False, timeout=2)
            text = buf.getvalue()
            assert "claude   | new claude" in text, text
            assert "cursor   | error RuntimeError" in text, text
            assert "grokbot  | old bot" in text, text
            written = json.load(open(cache))
            assert written["claude_week"] == 81, written
            assert written["codex"] == 82, written
            assert written["grok_pct"] == 83, written
            assert written["cursor_auto_pct"] == 21 and written["cursor_api_pct"] == 22, written
            assert written["bot_pct"] == 23, written
            assert "cursor" not in written["as_of"] and "grokbot" not in written["as_of"], written["as_of"]
            assert "claude" in written["as_of"] and "chatgpt" in written["as_of"] and "grok" in written["as_of"], written["as_of"]
            assert written["ts"] != old_ts and abs(written["ts"] - time.time()) < 5, written["ts"]
            assert abs(os.stat(cache).st_mtime - written["ts"]) < 1, os.stat(cache).st_mtime
            held = open(cache).read()
            state["claude_week"] = 999
            state["line"] = "claude   | should-not-cache"
            lock_fh = open(os.path.join(d, ".lock"), "a+")
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
                blocked = io.StringIO()
                with contextlib.redirect_stdout(blocked):
                    su.full_run(quiet=False, timeout=0)
                assert "should-not-cache" in blocked.getvalue(), blocked.getvalue()
            finally:
                lock_fh.close()
            assert open(cache).read() == held, open(cache).read()
        finally:
            su.CACHE, su.CACHE_DIR, su.SUMMARY = old_cache, old_dir, old_summary
            for name, fn in saved.items():
                setattr(su, name, fn)

    # Inherited descriptor: parent holds the lock and passes that fd. The child
    # must write without unlocking the parent's open file description.
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "latest.json")
        with open(cache, "w") as f:
            json.dump({"v": 1, "ts": 1, "as_of": {"claude": 1}, "claude_week": 1,
                       "lines": ["claude   | old"], "burn": []}, f)
        lock_path = os.path.join(d, ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            env = os.environ.copy()
            env["SEAT_USAGE_CACHE_DIR"] = d
            proc = subprocess.run(
                [sys.executable, "-c", _INHERITED_LOCK_CHILD, str(BIN)],
                pass_fds=(fd,),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
            written = json.load(open(cache))
            assert written["claude_week"] == 42, written
            probe = os.open(lock_path, os.O_RDWR)
            try:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    still_held = True
                else:
                    still_held = False
            finally:
                os.close(probe)
            assert still_held, "full run released the inherited lock"
        finally:
            os.close(fd)


_INHERITED_LOCK_CHILD = r"""
import importlib.machinery
import importlib.util
import sys
sys.dont_write_bytecode = True
bin_path = sys.argv[1]
loader = importlib.machinery.SourceFileLoader("seat_usage_inherited", bin_path)
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

def claude():
    mod.SUMMARY["claude_week"] = 42
    mod.SUMMARY["as_of"]["claude"] = mod.time.time()
    return ["claude   | from inherited lock"]

def empty():
    return []

mod.claude = claude
mod.codex = empty
mod.cursor = empty
mod.grok = empty
mod.full_run(quiet=True, timeout=0)
"""


@test
def cursor_fable_overflow_effort_xhigh():
    week = su.route("bounded-question", snap(fable=100, claude_week=100, claude_5h=10, cursor_api_pct=10), NOW)
    assert week["seat"] == "cursor" and week["family"] == "fable" and week["effort"] == "xhigh", week
    assert week["reason"] == "Other Models under pace: score 0.22 (fit 0.25, headroom 1.00, cost 3, lose-it 0.00)", week
    session = su.route("bounded-question", snap(fable=100, claude_week=10, claude_5h=100, cursor_api_pct=10), NOW)
    assert session["seat"] == "cursor" and session["family"] == "fable" and session["effort"] == "xhigh", session
    assert session["reason"] == "Other Models under pace: score 0.22 (fit 0.25, headroom 1.00, cost 3, lose-it 0.00)", session


_PROFILE_OK = json.dumps({
    "organization": {"rate_limit_tier": "default_max", "subscription_status": "active"},
}).encode()
_USAGE_OK = json.dumps({"limits": [
    {"kind": "session", "percent": 11, "resets_at": "2099-01-01T00:00:00Z"},
    {"kind": "weekly_all", "percent": 22, "resets_at": "2099-01-01T00:00:00Z"},
    {"kind": "weekly_scoped", "percent": 33, "resets_at": "2099-01-01T00:00:00Z",
     "scope": {"model": {"display_name": "Fable"}}},
]}).encode()
_RATE_BODY = json.dumps({
    "type": "error",
    "error": {"type": "rate_limit_error", "message": "Rate limited. Please try again later."},
}).encode()
_EXPIRED_BODY = json.dumps({
    "type": "error",
    "error": {"type": "authentication_error", "message": "token has expired"},
}).encode()


class _AnthropicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, headers, body):
        self.send_response(code)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _note_auth(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or auth == "Bearer ":
            self.server.bad_auth += 1

    def do_GET(self):
        self._note_auth()
        if self.path.endswith("/api/oauth/profile"):
            with self.server.hits_lock:
                self.server.profile_hits += 1
                mode = self.server.profile_mode
            if mode == "expired":
                self._send(401, {"Content-Type": "application/json"}, _EXPIRED_BODY)
            elif mode == "error":
                self._send(500, {"Content-Type": "application/json"}, b"{}")
            else:
                self._send(200, {"Content-Type": "application/json"}, self.server.profile_body or _PROFILE_OK)
            return
        if self.path.endswith("/api/oauth/usage"):
            with self.server.hits_lock:
                self.server.usage_hits += 1
                mode = self.server.usage_mode
            if mode == "hold":
                self.server.hit_gate.set()
                self.server.release_gate.wait(5)
                self._send_limited()
            elif mode == "limited":
                self._send_limited()
            elif mode == "bare429":
                # 429 with the rate_limit_error body and no Retry-After.
                self._send(429, {"Content-Type": "application/json"}, _RATE_BODY)
            elif mode == "zero429":
                # The live usage endpoint sends Retry-After: 0. That is not a wait.
                self._send(429, {"Retry-After": "0", "Content-Type": "application/json"}, _RATE_BODY)
            elif mode == "error":
                self._send(500, {"Content-Type": "application/json"}, b"{}")
            elif mode == "denied":
                self._send(401, {"Content-Type": "application/json"}, _EXPIRED_BODY)
            else:
                self._send(200, {"Content-Type": "application/json"}, self.server.usage_body or _USAGE_OK)
            return
        self._send(404, {}, b"")

    def _send_limited(self):
        # Delay-seconds only. No anthropic-ratelimit-* headers.
        self.server.retry_sent.append("1705")
        self._send(429, {"Retry-After": "1705", "Content-Type": "application/json"}, _RATE_BODY)


class _AnthropicServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextlib.contextmanager
def anthropic_server():
    server = _AnthropicServer(("127.0.0.1", 0), _AnthropicHandler)
    server.hits_lock = threading.Lock()
    server.profile_hits = 0
    server.usage_hits = 0
    server.bad_auth = 0
    server.profile_mode = "ok"
    server.usage_mode = "ok"
    server.profile_body = None
    server.usage_body = None
    server.retry_sent = []
    server.hit_gate = threading.Event()
    server.release_gate = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release_gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def server_urls(server):
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    return base + "/api/oauth/profile", base + "/api/oauth/usage"


@contextlib.contextmanager
def seat_http_sandbox(profile_url, usage_url):
    if not profile_url.startswith("http://127.0.0.1:") or not usage_url.startswith("http://127.0.0.1:"):
        raise AssertionError("refusing to point seat-usage at a non-local endpoint")
    saved = {
        "CACHE": su.CACHE, "CACHE_DIR": su.CACHE_DIR, "SUMMARY": su.SUMMARY, "NOW": su.NOW,
        "H": su.H, "HISTORY": su.HISTORY,
        "ANTHROPIC_PROFILE_URL": su.ANTHROPIC_PROFILE_URL,
        "ANTHROPIC_USAGE_URL": su.ANTHROPIC_USAGE_URL,
        "USAGE_MIN_INTERVAL_S": su.USAGE_MIN_INTERVAL_S,
        "RENEWALS_FILE": su.RENEWALS_FILE,
    }
    fns = {name: getattr(su, name) for name in ("codex", "cursor", "grok")}
    old_env = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy", "SEAT_USAGE_SKIP_GOOGLE")}
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    # Never run the real agy from a test.
    os.environ["SEAT_USAGE_SKIP_GOOGLE"] = "1"
    try:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cache:
            os.makedirs(os.path.join(home, ".claude"))
            with open(os.path.join(home, ".claude", ".credentials.json"), "w") as f:
                json.dump({"claudeAiOauth": {"accessToken": "fixture-token"}}, f)
            su.H = home
            su.HISTORY = os.path.join(home, "history.jsonl")
            # RENEWALS_FILE is resolved from the real home at import; a real term end would cap every Claude pool.
            su.RENEWALS_FILE = os.path.join(home, ".config", "seat-usage", "renewals.json")
            su.CACHE_DIR = cache
            su.CACHE = os.path.join(cache, "latest.json")
            su.ANTHROPIC_PROFILE_URL = profile_url
            su.ANTHROPIC_USAGE_URL = usage_url
            su.NOW = time.time()
            # Tests that count calls opt into the floor themselves.
            su.USAGE_MIN_INTERVAL_S = 0
            su.codex = lambda: []
            su.cursor = lambda: []
            su.grok = lambda: []
            yield home, cache
    finally:
        for key, value in saved.items():
            setattr(su, key, value)
        for name, fn in fns.items():
            setattr(su, name, fn)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _quiet_cache():
    su.full_run(quiet=True, timeout=5)
    with open(su.CACHE) as f:
        return json.load(f)


_PROFILE_PRO = json.dumps({
    "organization": {"rate_limit_tier": "default_claude_ai", "subscription_status": "active"},
}).encode()
_USAGE_PRO = json.dumps({"limits": [
    {"kind": "session", "percent": 11, "resets_at": "2099-01-01T00:00:00Z"},
    {"kind": "weekly_all", "percent": 22, "resets_at": "2099-01-01T00:00:00Z"},
]}).encode()


@test
def pro_reading_sets_claude_max_false_and_the_pulled_copy_routes_off_claude():
    push = load("seat_usage_push", HERE.parents[0] / "bin" / "seat-usage-push")
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.profile_body, server.usage_body = _PROFILE_PRO, _USAGE_PRO
            cache = _quiet_cache()
    assert cache["claude_plan"] == "claude_ai" and cache["claude_max"] is False, cache
    assert "fable" not in cache, cache
    copy = push.build_cache_copy(cache)
    assert copy["claude_max"] is False and "claude_plan" not in copy, copy
    pulled = snap(claude_max=False, cursor_api_pct=10)
    pulled.update({k: v for k, v in copy.items() if k.startswith("claude_")})
    pulled.pop("fable", None); pulled.pop("fable_reset_ts", None); pulled["pulled_ts"] = NOW
    r = su.route("bounded-question", pulled, NOW)
    assert r["seat"] == "cursor" and r["family"] == "fable", r


@test
def failed_profile_with_a_fable_slice_is_max():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.profile_mode = "error"
            cache = _quiet_cache()
    assert cache["claude_plan"] == "?" and cache["claude_max"] is True, cache


@test
def caps_projection_is_linear_from_the_window_start():
    f = su.caps_early_s
    D = 86400
    # 7d window, 4d left: 3d elapsed at 60% is 20%/day, so 100% in 2 more days, 2d before the reset.
    assert f(60, NOW + 4 * D, WEEK, NOW) == 2 * D
    assert f(90, NOW + 4 * D, WEEK, NOW) == round(4 * D - 10 * 3 * D / 90)
    assert f(40, NOW + 4 * D, WEEK, NOW) is None          # under pace: reaches 100% after the reset
    assert f(3 / 7 * 100, NOW + 4 * D, WEEK, NOW) is None  # exactly on pace: caps at the reset, not early
    assert f(0, NOW + 4 * D, WEEK, NOW) is None            # nothing used
    assert f(100, NOW + 4 * D, WEEK, NOW) is None and f(120, NOW + 4 * D, WEEK, NOW) is None  # already capped
    assert f(30, NOW + 6.5 * D, WEEK, NOW) is None         # 7% of the window: too early to project
    assert f(30, NOW + 6.3 * D, WEEK, NOW) is not None     # 10%: projected
    assert f(50, NOW - 1, WEEK, NOW) is None and f(50, NOW + 8 * D, WEEK, NOW) is None   # reset passed, or > a window off
    for bad in (None, 0, -5, "x", True):
        assert f(50, NOW + 4 * D, bad, NOW) is None, bad
    assert f(None, NOW + 4 * D, WEEK, NOW) is None and f(50, None, WEEK, NOW) is None and f(True, NOW + D, WEEK, NOW) is None
    # 5h window, 2h left: 3h elapsed at 90% is 30%/h, so 100% in 20 minutes, 1h 40m early.
    assert f(90, NOW + 2 * 3600, 5 * 3600, NOW) == 100 * 60


@test
def apply_caps_writes_one_key_per_projected_pool_and_drops_old_ones():
    s = snap(claude_week=60, claude_week_reset_ts=NOW + 4 * 86400, bot_pct=10,
             google_gemini_5h=90, google_gemini_5h_reset_ts=NOW + 2 * 3600, codex_caps_early_s=999)
    su.apply_caps(s, NOW)
    assert s["claude_week_caps_early_s"] == 2 * 86400, s
    assert s["google_gemini_5h_caps_early_s"] == 6000, s
    assert "codex_caps_early_s" not in s and "grokbot_caps_early_s" not in s, s   # stale key gone; under pace
    # A carried-over reading keeps its projection: measured at its as_of, not at write time.
    old = snap(claude_week=60, claude_week_reset_ts=NOW + 4 * 86400)
    old["as_of"] = dict(old["as_of"], claude=NOW - 3600)
    su.apply_caps(old, NOW)
    assert old["claude_week_caps_early_s"] == su.caps_early_s(60, NOW + 4 * 86400, WEEK, NOW - 3600), old
    gone = snap(claude_week=60, claude_week_reset_ts=NOW + 4 * 86400)
    gone["as_of"] = dict(gone["as_of"], claude=NOW - 5 * 86400)
    su.apply_caps(gone, NOW)
    assert "claude_week_caps_early_s" not in gone, gone   # at that reading the reset was > one window off
    push = load("seat_usage_push", HERE.parents[0] / "bin" / "seat-usage-push")
    keys = {f"{name}_caps_early_s" for name, *_ in su.caps_sources()}
    assert keys <= push.CACHE_NUMBERS, sorted(keys - push.CACHE_NUMBERS)
    copy = push.build_cache_copy({**s, "fable_caps_early_s": "soon"})
    assert copy["claude_week_caps_early_s"] == 2 * 86400 and "fable_caps_early_s" not in copy, copy


_RATE_CHILD = r"""
import importlib.machinery
import importlib.util
import os
import sys
sys.dont_write_bytecode = True
bin_path, cache_dir, home, profile, usage = sys.argv[1:6]
if not profile.startswith("http://127.0.0.1:") or not usage.startswith("http://127.0.0.1:"):
    sys.stderr.write("refusing non-local anthropic url\n")
    sys.exit(2)
os.environ["HOME"] = home
os.environ["SEAT_USAGE_CACHE_DIR"] = cache_dir
os.environ["SEAT_USAGE_ANTHROPIC_PROFILE"] = profile
os.environ["SEAT_USAGE_ANTHROPIC_USAGE"] = usage
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
loader = importlib.machinery.SourceFileLoader("seat_usage_rate_child", bin_path)
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
if not mod.ANTHROPIC_USAGE_URL.startswith("http://127.0.0.1:"):
    sys.stderr.write("usage url was not overridden\n")
    sys.exit(2)
mod.codex = lambda: []
mod.cursor = lambda: []
mod.grok = lambda: []
mod.full_run(quiet=True, timeout=5)
"""


@test
def retry_after_countdown_is_one_deadline():
    t0 = 1_700_000_000.0
    first = su._retry_after_deadline("1705", t0)
    later = su._retry_after_deadline("1432", t0 + (1705 - 1432))
    assert first == t0 + 1705, first
    assert later == first, later
    assert su._retry_after_deadline(" 1705 ", t0) == first
    assert su._retry_after_deadline(None, t0) is None
    assert su._retry_after_deadline("token has expired", t0) is None
    assert su._retry_after_deadline("-5", t0) is None
    assert su._retry_after_deadline("0", t0) is None
    assert su._retry_after_deadline("0.0", t0) is None


@test
def usage_429_keeps_as_of_until_retry_after():
    # Fake server only. The live usage and profile endpoints are not called.
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage) as (home, cache):
            server.usage_mode = "limited"
            t0 = time.time()
            missed = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert server.retry_sent == ["1705"], server.retry_sent
            assert "claude" not in missed["as_of"], missed["as_of"]
            assert any("usage failed: HTTPError" in ln for ln in missed["lines"]), missed["lines"]
            assert missed["read_state"]["claude"] == "rate_limited", missed.get("read_state")
            stored = json.load(open(su._rate_limit_path()))
            assert stored[f"{usage}#429s"] == 1, stored
            delta = stored[usage] - t0
            assert 1700 <= delta <= 1720, delta
            again = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert server.retry_sent == ["1705"], server.retry_sent
            assert "claude" not in again["as_of"], again["as_of"]

            past = time.time() - 1
            with open(su._rate_limit_path(), "w") as f:
                json.dump({usage: past}, f)
            server.usage_mode = "ok"
            su.NOW = time.time()
            fresh = _quiet_cache()
            success_as_of = fresh["as_of"]["claude"]
            assert server.usage_hits == 2, server.usage_hits
            assert su.NOW <= success_as_of <= time.time(), fresh["as_of"]
            assert fresh["fable"] == 33 and fresh["claude_week"] == 22 and fresh["claude_5h"] == 11, fresh
            assert fresh["read_state"]["claude"] == "fresh", fresh.get("read_state")
            assert "rate_limited_until" not in fresh, fresh.get("rate_limited_until")
            assert any("weekly Fable 33%" in ln for ln in fresh["lines"]), fresh["lines"]
            cleared = json.load(open(su._rate_limit_path()))
            assert usage not in cleared, cleared

            server.usage_mode = "hold"
            server.hit_gate.clear()
            server.release_gate.clear()
            errors = []

            def call():
                try:
                    su.claude()
                except Exception as e:
                    errors.append(repr(e))

            t_a = threading.Thread(target=call, daemon=True)
            t_b = None
            try:
                t_a.start()
                assert server.hit_gate.wait(5), "timed out waiting for the usage request"
                t_b = threading.Thread(target=call, daemon=True)
                t_b.start()
                time.sleep(0.2)
                held_hits = server.usage_hits
            finally:
                server.usage_mode = "limited"
                server.release_gate.set()
                t_a.join(10)
                if t_b is not None:
                    t_b.join(10)
            assert not t_a.is_alive() and (t_b is None or not t_b.is_alive()), "claude callers did not finish"
            assert not errors, errors
            assert held_hits == 3, held_hits
            assert server.usage_hits == 3, server.usage_hits
            assert server.retry_sent == ["1705", "1705"], server.retry_sent
            deadline_blob = open(su._rate_limit_path(), "rb").read()

            blocked = _quiet_cache()
            assert server.usage_hits == 3, server.usage_hits
            assert blocked["as_of"]["claude"] == success_as_of, blocked["as_of"]
            assert blocked["fable"] == 33, blocked
            assert any("weekly Fable 33%" in ln for ln in blocked["lines"]), blocked["lines"]
            assert not any("usage failed" in ln for ln in blocked["lines"]), blocked["lines"]
            stale = su.route("bounded-question", blocked, success_as_of + 11 * 60)
            assert stale["reason"] == "Fable: reading 11m old", stale
            assert blocked["as_of"]["claude"] == success_as_of

            proc = subprocess.run(
                [sys.executable, "-c", _RATE_CHILD, str(BIN), cache, home, profile, usage],
                capture_output=True, text=True, timeout=20,
            )
            assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
            assert server.usage_hits == 3, server.usage_hits
            assert open(su._rate_limit_path(), "rb").read() == deadline_blob
            after_spawn = json.load(open(su.CACHE))
            assert after_spawn["as_of"]["claude"] == success_as_of, after_spawn["as_of"]

            argv = sys.argv
            sys.argv = [str(BIN), "--route", "bounded-question"]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    su.cmd_route()
            finally:
                sys.argv = argv
            assert server.usage_hits == 3, server.usage_hits
            assert json.load(open(su.CACHE))["as_of"]["claude"] == success_as_of

            with open(su._rate_limit_path(), "w") as f:
                json.dump({usage: time.time() - 1}, f)
            server.usage_mode = "ok"
            su.NOW = time.time()
            renewed = _quiet_cache()
            assert server.usage_hits == 4, server.usage_hits
            # The stamp is the clock that armed the floor, after this call started.
            assert su.NOW <= renewed["as_of"]["claude"] <= time.time(), renewed["as_of"]
            assert renewed["as_of"]["claude"] != success_as_of
            assert server.bad_auth == 0, server.bad_auth


def _assert_no_secret(text):
    assert "fixture-token" not in text and "fixture-refresh" not in text, "token leaked"


@test
def expired_claude_token_makes_no_http_call():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage) as (home, _cache):
            cred = os.path.join(home, ".claude", ".credentials.json")
            future = int((time.time() + 3600) * 1000)
            with open(cred, "w") as f:
                json.dump({"claudeAiOauth": {"accessToken": "fixture-token", "expiresAt": future}}, f)
            fresh = _quiet_cache()
            assert server.profile_hits == 1 and server.usage_hits == 1, (server.profile_hits, server.usage_hits)
            cached = json.load(open(su.CACHE))
            cached["as_of"]["claude"] = su.NOW - 2 * 3600
            with open(su.CACHE, "w") as f:
                json.dump(cached, f)
            past = int((time.time() - 6 * 3600) * 1000)
            with open(cred, "w") as f:
                json.dump({
                    "claudeAiOauth": {
                        "accessToken": "fixture-token",
                        "refreshToken": "fixture-refresh",
                        "expiresAt": past,
                    },
                }, f)
            expired = _quiet_cache()
            assert server.profile_hits == 1 and server.usage_hits == 1, (server.profile_hits, server.usage_hits)
            line = expired["lines"][0]
            _assert_no_secret(line)
            assert line.startswith("claude   | token expired 6.0h ago; run `claude` once to refresh | "), line
            assert "weekly Fable 33%" in line, line
            assert line.endswith(" | stale (reading 2.0h old)"), line
            assert expired["as_of"]["claude"] == su.NOW - 2 * 3600, expired["as_of"]
            again = _quiet_cache()
            assert server.profile_hits == 1 and server.usage_hits == 1, (server.profile_hits, server.usage_hits)
            os.remove(su.CACHE)
            bare = _quiet_cache()
            assert server.profile_hits == 1 and server.usage_hits == 1, (server.profile_hits, server.usage_hits)
            assert bare["lines"] == ["claude   | token expired 6.0h ago; run `claude` once to refresh"], bare["lines"]
            assert "claude" not in bare["as_of"], bare["as_of"]
            assert bare["read_state"]["claude"] == "token_expired", bare.get("read_state")
            assert "fixture-token" not in json.dumps(bare["read_state"])
            _assert_no_secret(json.dumps(bare))


@test
def claude_401_sets_backoff_and_the_next_run_does_not_call():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.profile_mode = "expired"
            server.usage_mode = "ok"
            t0 = time.time()
            first = _quiet_cache()
            assert server.profile_hits == 1, server.profile_hits
            assert server.usage_hits == 0, server.usage_hits
            assert any("profile failed: HTTPError 401" in ln for ln in first["lines"]), first["lines"]
            _assert_no_secret(json.dumps(first))
            stored = json.load(open(su._rate_limit_path()))
            assert stored[f"{usage}#401s"] == 1, stored
            assert f"{usage}#429s" not in stored, stored
            assert 295 <= stored[usage] - t0 <= 310, stored[usage] - t0
            _assert_no_secret(json.dumps(stored))
            second = _quiet_cache()
            assert server.profile_hits == 1, server.profile_hits
            assert server.usage_hits == 0, server.usage_hits
            _assert_no_secret(json.dumps(second))

            with open(su._rate_limit_path(), "w") as f:
                json.dump({}, f)
            server.profile_mode = "ok"
            server.usage_mode = "denied"
            t1 = time.time()
            denied = _quiet_cache()
            assert server.profile_hits == 2, server.profile_hits
            assert server.usage_hits == 1, server.usage_hits
            assert any("usage failed: HTTPError 401" in ln for ln in denied["lines"]), denied["lines"]
            stored = json.load(open(su._rate_limit_path()))
            assert stored[f"{usage}#401s"] == 1, stored
            assert f"{usage}#429s" not in stored, stored
            assert 295 <= stored[usage] - t1 <= 310, stored[usage] - t1
            held = _quiet_cache()
            assert server.profile_hits == 2, server.profile_hits
            assert server.usage_hits == 1, server.usage_hits
            _assert_no_secret(json.dumps(held))
            assert server.bad_auth == 0, server.bad_auth


@test
def usage_429_with_cache_shows_stale_reading():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.usage_mode = "ok"
            fresh = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert "weekly Fable 33%" in fresh["lines"][0], fresh["lines"]
            cached = json.load(open(su.CACHE))
            cached["as_of"]["claude"] = su.NOW - 2 * 3600
            with open(su.CACHE, "w") as f:
                json.dump(cached, f)
            server.usage_mode = "bare429"
            limited = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            line = next(ln for ln in limited["lines"] if su.line_seat(ln) == "claude")
            _assert_no_secret(line)
            assert "usage failed" not in line, line
            assert "weekly Fable 33%" in line, line
            assert line.endswith(" | stale (reading 2.0h old)"), line
            assert limited["as_of"]["claude"] == su.NOW - 2 * 3600, limited["as_of"]
            assert limited["fable"] == 33, limited
            assert limited["read_state"]["claude"] == "rate_limited", limited.get("read_state")
            assert server.profile_hits == 1, server.profile_hits
            until = limited["rate_limited_until"]["claude"]
            assert su._is_number(until) and until > time.time(), until
            line_state = su.claude_read_state_line(limited, su.NOW)
            assert line_state.startswith("claude stale 120m: rate_limited until "), line_state
            assert line_state.endswith(su._clock(until)), line_state
            _assert_no_secret(line_state)


@test
def usage_429_without_retry_after_backs_off_and_doubles():
    # anthropics/claude-code#30930: the live endpoint sends 429 with no usable
    # Retry-After. Fake server only.
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.usage_mode = "ok"
            fresh = _quiet_cache()
            good_as_of = fresh["as_of"]["claude"]
            assert server.usage_hits == 1, server.usage_hits
            server.usage_mode = "bare429"
            t0 = time.time()
            limited = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert limited["as_of"]["claude"] == good_as_of, limited["as_of"]
            assert limited["fable"] == 33, limited
            assert not any("usage failed" in ln for ln in limited["lines"]), limited["lines"]
            stored = json.load(open(su._rate_limit_path()))
            assert 295 <= stored[usage] - t0 <= 310, stored[usage] - t0
            _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits

            waits = []
            for _ in range(6):
                stored = json.load(open(su._rate_limit_path()))
                stored[usage] = time.time() - 1
                with open(su._rate_limit_path(), "w") as f:
                    json.dump(stored, f)
                t = time.time()
                _quiet_cache()
                waits.append(round((json.load(open(su._rate_limit_path()))[usage] - t) / 100) * 100)
            assert waits == [600, 1200, 2400, 4800, 7200, 7200], waits
            assert server.usage_hits == 8, server.usage_hits

            stored = json.load(open(su._rate_limit_path()))
            stored[usage] = time.time() - 1
            with open(su._rate_limit_path(), "w") as f:
                json.dump(stored, f)
            server.usage_mode = "ok"
            su.NOW = time.time()
            renewed = _quiet_cache()
            assert su.NOW <= renewed["as_of"]["claude"] <= time.time(), renewed["as_of"]
            assert json.load(open(su._rate_limit_path())) == {}


@test
def usage_good_read_sets_a_floor():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            su.USAGE_MIN_INTERVAL_S = 300
            server.usage_mode = "ok"
            first = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert server.profile_hits == 1, server.profile_hits
            su.NOW = time.time() + 5
            second = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert server.profile_hits == 1, server.profile_hits
            assert second["as_of"]["claude"] == first["as_of"]["claude"], second["as_of"]
            assert second["fable"] == 33, second
            assert not any("stale (reading" in ln for ln in second["lines"]), second["lines"]
            stored = json.load(open(su._rate_limit_path()))
            assert 295 <= stored[usage] - time.time() <= 300, stored
            assert stored[profile] > time.time(), stored
            # Usage window has passed; profile is still inside its own floor.
            stored[usage] = time.time() - 1
            with open(su._rate_limit_path(), "w") as f:
                json.dump(stored, f)
            third = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert server.profile_hits == 1, server.profile_hits
            assert third["claude_plan"] == "max" and third["claude_status"] == "active", third
            assert third["claude_max"] is True, third


@test
def usage_min_interval_default_is_600():
    env = os.environ.copy()
    env.pop("SEAT_USAGE_MIN_INTERVAL_S", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    script = (
        "import importlib.machinery, importlib.util, sys\n"
        "loader = importlib.machinery.SourceFileLoader('seat_usage_default', sys.argv[1])\n"
        "spec = importlib.util.spec_from_loader(loader.name, loader)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "loader.exec_module(mod)\n"
        "assert mod.USAGE_MIN_INTERVAL_S == 600, mod.USAGE_MIN_INTERVAL_S\n"
        "assert mod.USAGE_MIN_INTERVAL_S <= mod.ROUTE_FRESH_S, (mod.USAGE_MIN_INTERVAL_S, mod.ROUTE_FRESH_S)\n"
        "assert mod.USAGE_BACKOFF_MAX_S == 7200, mod.USAGE_BACKOFF_MAX_S\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(BIN)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


@test
def retry_after_zero_uses_the_backoff_not_a_zero_wait():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.usage_mode = "zero429"
            t0 = time.time()
            limited = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert limited["read_state"]["claude"] == "rate_limited", limited.get("read_state")
            stored = json.load(open(su._rate_limit_path()))
            assert stored[f"{usage}#429s"] == 1, stored
            assert 295 <= stored[usage] - t0 <= 310, stored[usage] - t0
            again = _quiet_cache()
            assert server.usage_hits == 1, server.usage_hits
            assert again["read_state"]["claude"] == "rate_limited", again.get("read_state")
            stored[usage] = time.time() - 1
            with open(su._rate_limit_path(), "w") as f:
                json.dump(stored, f)
            t1 = time.time()
            _quiet_cache()
            nxt = json.load(open(su._rate_limit_path()))
            assert nxt[f"{usage}#429s"] == 2, nxt
            assert 590 <= nxt[usage] - t1 <= 620, nxt[usage] - t1


@test
def usage_exception_sets_usage_failed_and_does_not_hammer():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            su.USAGE_MIN_INTERVAL_S = 300
            fresh = _quiet_cache()
            assert fresh["read_state"]["claude"] == "fresh", fresh.get("read_state")
            assert server.profile_hits == 1 and server.usage_hits == 1
            stored = json.load(open(su._rate_limit_path()))
            stored[su.ANTHROPIC_USAGE_URL] = time.time() - 1
            with open(su._rate_limit_path(), "w") as f:
                json.dump(stored, f)
            server.usage_mode = "error"
            failed = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert server.profile_hits == 1, server.profile_hits
            assert failed["read_state"]["claude"] == "usage_failed", failed.get("read_state")
            assert failed["as_of"]["claude"] == fresh["as_of"]["claude"], failed["as_of"]
            assert "HTTPError" not in json.dumps(failed["read_state"])
            line = su.claude_read_state_line(failed, time.time())
            assert line.startswith("claude stale ") and line.endswith(": usage_failed"), line
            held = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert held["read_state"]["claude"] == "usage_failed", held.get("read_state")


@test
def cached_plan_skips_profile_after_both_floors_expire():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            su.USAGE_MIN_INTERVAL_S = 300
            first = _quiet_cache()
            assert first["read_state"]["claude"] == "fresh"
            assert server.profile_hits == 1 and server.usage_hits == 1
            stored = json.load(open(su._rate_limit_path()))
            stored[usage] = time.time() - 1
            stored[profile] = time.time() - 1
            with open(su._rate_limit_path(), "w") as f:
                json.dump(stored, f)
            second = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert server.profile_hits == 1, server.profile_hits
            assert second["claude_plan"] == "max" and second["read_state"]["claude"] == "fresh", second
            # The usage read holds the profile URL too, so the next call is quiet.
            quiet = _quiet_cache()
            assert server.usage_hits == 2 and server.profile_hits == 1, (server.usage_hits, server.profile_hits)
            assert quiet["read_state"]["claude"] == "fresh", quiet.get("read_state")


@test
def cache_without_read_state_renders_as_before():
    assert su.claude_read_state_line({"as_of": {"claude": NOW}, "claude_week": 10}) is None
    assert su.claude_read_state_line({"read_state": {"claude": "fresh"}, "as_of": {"claude": NOW}}) is None
    assert su.claude_read_state_line({"read_state": {"claude": "token has expired, leak"}, "as_of": {}}) is None
    now = time.time()
    cache = snap()
    cache["ts"] = now
    cache["as_of"] = {"claude": now - 120, "chatgpt": now, "cursor": now, "grokbot": now, "grok": now}
    cache["lines"] = ["claude   | max active | weekly all 10%"]
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(cache, f)
        env = os.environ.copy()
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        shown = subprocess.run([sys.executable, str(BIN), "--cached"], capture_output=True, text=True, env=env, timeout=15)
        route = subprocess.run([sys.executable, str(BIN), "--route", "bounded-question", "--cached"],
                               capture_output=True, text=True, env=env, timeout=15)
    assert shown.returncode == 0, shown.stderr
    assert "read_state" not in shown.stdout and "rate_limited" not in shown.stdout, shown.stdout
    assert "claude   | max active | weekly all 10%" in shown.stdout, shown.stdout
    assert route.returncode == 0, route.stderr
    assert route.stdout.startswith("route: bounded-question |"), route.stdout
    assert "rate_limited" not in route.stdout and route.stderr == "", (route.stdout, route.stderr)
    # A pulled-style cache that does carry the code shows it on the status line and on --route.
    cache["read_state"] = {"claude": "rate_limited"}
    cache["rate_limited_until"] = {"claude": now + 600}
    cache["as_of"]["claude"] = now - 300 * 60
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(cache, f)
        env = os.environ.copy()
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        shown = subprocess.run([sys.executable, str(BIN), "--cached"], capture_output=True, text=True, env=env, timeout=15)
        route = subprocess.run([sys.executable, str(BIN), "--route", "bounded-question", "--cached"],
                               capture_output=True, text=True, env=env, timeout=15)
    clock = datetime.fromtimestamp(now + 600).strftime("%I:%M %p").lstrip("0")
    expect = f"claude stale 300m: rate_limited until {clock}"
    assert shown.returncode == 0 and expect in shown.stdout, shown.stdout
    assert route.returncode == 0 and expect in route.stdout, route.stdout
    assert route.stdout.startswith("route: bounded-question |"), route.stdout


def grok_builder():
    return {"role": "builder", "seat": "cursor", "family": "grok", "effort": "high",
            "variant": "fast", "mode": "suggest", "reason": "already built"}


def opus_builder():
    return {"role": "builder", "seat": "claude", "family": "opus", "effort": "low",
            "variant": "-", "mode": "suggest", "reason": "builder fallback"}


@test
def lineup_default_is_xai_with_fable_and_astra():
    r = su.route_lineup(snap(), NOW)
    assert r["mode"] == "default", r
    chair, builder = r["lineup"]["chair"], r["lineup"]["builder"]
    assert chair["family"] == "grok" and chair["seat"] == "cursor" and chair["vendor"] == "xai", chair
    assert chair["effort"] == "high" and chair["variant"] == "fast", chair
    assert builder["family"] == "grok" and builder["seat"] == "cursor" and builder["vendor"] == "xai", builder
    assert builder["effort"] == "high", builder
    assert [(v["family"], v["effort"], v["vendor"], v["seat"]) for v in r["lineup"]["voters"]] == [
        ("fable", "high", "anthropic", "claude"),
        ("astra", "high", "openai", "chatgpt"),
        ("wonder-full", "high", "meta", "muse"),
    ], r["lineup"]["voters"]
    assert r["alternatives"] == [], r
    assert "xAI chairs and builds" in r["rule"], r
    assert r["reading_age_s"] == 0, r


@test
def lineup_burn_raises_chair_and_builder_effort_not_voters():
    soon = NOW + 12 * 3600
    r = su.route_lineup(snap(cursor_auto_pct=10, cursor_reset_ts=soon, cursor_window_s=MONTH), NOW)
    assert r["mode"] == "burn", r
    assert r["lineup"]["chair"]["effort"] == "xhigh", r["lineup"]["chair"]
    assert r["lineup"]["builder"]["effort"] == "xhigh", r["lineup"]["builder"]
    assert [v["effort"] for v in r["lineup"]["voters"]] == ["high", "high", "high"], r["lineup"]["voters"]
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "astra", "wonder-full"], r


@test
def lineup_cursor_ahead_moves_to_supergrok_while_that_pool_is_under():
    r = su.route_lineup(snap(cursor_auto_pct=80), NOW)
    assert r["mode"] == "default", r
    assert r["lineup"]["chair"]["seat"] == "grok" and r["lineup"]["chair"]["effort"] == "high", r
    assert r["lineup"]["builder"]["seat"] == "grok" and r["lineup"]["builder"]["effort"] == "high", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "astra", "wonder-full"], r


@test
def lineup_builder_family_follows_the_builder_route_and_drops_a_capped_seat():
    soon = NOW + 12 * 3600
    burning = snap(cursor_auto_pct=10, cursor_reset_ts=soon, cursor_window_s=MONTH)
    routed = su.route("builder", burning, NOW)
    r = su.route_lineup(burning, NOW, builder=grok_builder())
    assert r["mode"] == "burn", r
    assert r["lineup"]["builder"]["effort"] == routed["effort"] == "xhigh", r
    assert r["lineup"]["builder"]["seat"] == routed["seat"] == "cursor", r
    assert r["lineup"]["builder"]["mode"] == "burn", r
    capped = su.route_lineup(snap(cursor_auto_pct=100), NOW, builder=grok_builder())
    assert capped["mode"] != "ask" and capped["lineup"] is not None, capped
    assert capped["lineup"]["builder"]["seat"] == "grok", capped
    assert capped["lineup"]["builder"]["seat"] != "cursor", capped
    assert capped["lineup"]["chair"]["seat"] == "grok", capped
    assert "grok-build" not in json.dumps(capped)


@test
def lineup_opus_builder_uses_opus_chair_and_grok_voter():
    r = su.route_lineup(snap(), NOW, builder=opus_builder())
    assert r["mode"] != "ask", r
    assert r["lineup"]["chair"]["family"] == "opus" and r["lineup"]["chair"]["effort"] == "high", r
    assert r["lineup"]["chair"]["seat"] == "claude" and r["lineup"]["chair"]["vendor"] == "anthropic", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["astra", "grok", "wonder-full"], r
    grok = r["lineup"]["voters"][1]
    assert grok["effort"] == "high" and grok["seat"] == "grok" and grok["vendor"] == "xai", grok
    assert r["alternatives"] == [], r


@test
def lineup_cursor_capped_stays_on_supergrok_with_fable_and_astra():
    r = su.route_lineup(snap(cursor_auto_pct=100), NOW)
    assert r["mode"] == "default", r
    assert r["lineup"]["chair"]["family"] == "grok" and r["lineup"]["chair"]["seat"] == "grok", r
    assert r["lineup"]["builder"]["family"] == "grok" and r["lineup"]["builder"]["seat"] == "grok", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "astra", "wonder-full"], r
    assert all(v["effort"] == "high" for v in r["lineup"]["voters"]), r


@test
def lineup_both_grok_pools_capped_degrades_to_opus_and_one_voter():
    r = su.route_lineup(snap(cursor_auto_pct=100, grok_exhausted=True), NOW)
    assert r["mode"] == "suggest" and r["lineup"] is not None, r
    assert r["lineup"]["chair"]["family"] == "opus" and r["lineup"]["builder"]["family"] == "opus", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["astra", "wonder-full"], r
    assert "weaker:" in r["reason"], r
    assert all(v["family"] != "grok" for v in r["lineup"]["voters"]), r


@test
def lineup_unknown_cursor_uses_supergrok():
    r = su.route_lineup(without_as_of(snap(), "cursor"), NOW)
    assert r["mode"] == "default" and r["lineup"] is not None, r
    assert r["lineup"]["chair"]["seat"] == "grok", r
    both = su.route_lineup(without_as_of(without_as_of(snap(), "cursor"), "grok"), NOW)
    assert both["mode"] == "ask" and both["lineup"] is None, both
    assert "unknown" in both["reason"], both


@test
def lineup_unknown_fable_asks():
    s = snap()
    del s["fable"]
    r = su.route_lineup(s, NOW)
    assert r["mode"] == "ask" and r["lineup"] is None, r
    assert "Fable" in r["reason"], r


@test
def lineup_codex_capped_keeps_muse_at_low_when_that_pool_is_ahead():
    r = su.route_lineup(snap(codex=100), NOW)
    assert r["mode"] == "default" and r["lineup"] is not None, r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "wonder-full"], r
    ahead = su.route_lineup(snap(codex=100, muse_week=80), NOW)
    assert ahead["mode"] == "suggest" and ahead["lineup"] is not None, ahead
    assert [v["family"] for v in ahead["lineup"]["voters"]] == ["fable", "wonder-full"], ahead
    muse = next(v for v in ahead["lineup"]["voters"] if v["family"] == "wonder-full")
    assert muse["effort"] == "low", muse
    capped = su.route_lineup(snap(codex=100, muse_week=100), NOW)
    assert [v["family"] for v in capped["lineup"]["voters"]] == ["fable"], capped
    assert "one voter" in capped["reason"], capped


@test
def lineup_unknown_builder_family_asks():
    bad = dict(grok_builder(), family="composer")
    r = su.route_lineup(snap(), NOW, builder=bad)
    assert r["mode"] == "ask" and r["lineup"] is None, r
    assert "no vendor" in r["reason"], r
    assert "grok-build" not in json.dumps(r)


@test
def lineup_chairs_on_the_grok_seat_when_cursor_is_capped():
    r = su.route_lineup(snap(cursor_auto_pct=100, grok_pct=0), NOW)
    assert r["lineup"]["chair"]["seat"] == "grok", r
    assert r["lineup"]["builder"]["seat"] == "grok", r
    assert "grok-build" not in json.dumps(r)


@test
def host_suggests_bot_then_opus_and_does_not_select():
    r = su.route_host(snap(), NOW)
    assert r["mode"] == "suggest" and r["suggestion"] == "grokbot", r
    assert r["selects"] is False and "never" not in r, r
    assert [h["id"] for h in r["hosts"]] == ["grokbot", "opus"], r
    assert r["hosts"][0]["available"] is True and r["hosts"][1]["available"] is True, r
    assert r["hosts"][1]["family"] == "opus" and r["hosts"][1]["effort"] == "high", r
    assert r["hosts"][1]["seat"] == "claude", r
    assert r["seat"] == "grokbot" and r["effort"] == "-", r


@test
def host_bot_capped_suggests_opus_and_keeps_bot_state():
    r = su.route_host(snap(bot_pct=100), NOW)
    assert r["suggestion"] == "opus" and r["mode"] == "suggest", r
    assert r["seat"] == "claude" and r["family"] == "opus" and r["effort"] == "high", r
    assert r["hosts"][0]["id"] == "grokbot" and r["hosts"][0]["available"] is False, r
    assert r["hosts"][1]["available"] is True, r
    assert r["selects"] is False, r


@test
def host_unknown_bot_asks_and_still_reports_opus():
    r = su.route_host(without_as_of(snap(), "grokbot"), NOW)
    assert r["mode"] == "ask" and r["suggestion"] is None, r
    assert "unknown" in r["reason"], r
    assert [h["id"] for h in r["hosts"]] == ["grokbot", "opus"], r
    assert r["hosts"][1]["family"] == "opus", r
    assert r["selects"] is False, r


@test
def host_unknown_opus_does_not_hide_an_open_bot():
    r = su.route_host(without_as_of(snap(), "claude"), NOW)
    assert r["suggestion"] == "grokbot" and r["mode"] == "suggest", r
    assert r["hosts"][1]["state"] == "unknown" and r["hosts"][1]["available"] is False, r


@test
def host_unknown_max_does_not_hide_an_ahead_bot():
    r = su.route_host(without_as_of(snap(bot_pct=93), "claude"), NOW)
    assert r["suggestion"] == "grokbot" and r["mode"] == "suggest", r
    assert r["reason"] == "over pace", r
    assert "reading unknown" not in r["reason"], r
    assert r["hosts"][1]["state"] == "unknown", r
    _assert_host_reason(r)


@test
def host_burn_spends_opus_before_the_bot():
    soon = NOW + 12 * 3600
    r = su.route_host(snap(claude_week=5, claude_week_reset_ts=soon, fable=5, fable_reset_ts=soon), NOW)
    assert r["suggestion"] == "opus" and r["mode"] == "burn", r
    assert r["hosts"][1]["effort"] == "xhigh", r
    assert r["effort"] == "xhigh", r
    assert "never" not in r, r


@test
def host_exhausted_bot_is_not_the_suggestion():
    r = su.route_host(snap(bot_exhausted=True, bot_pct=10), NOW)
    assert r["hosts"][0]["available"] is False, r
    assert r["suggestion"] == "opus", r


@test
def host_both_capped_asks():
    r = su.route_host(snap(bot_pct=100, claude_week=100), NOW)
    assert r["mode"] == "ask" and r["suggestion"] is None, r
    assert [h["id"] for h in r["hosts"]] == ["grokbot", "opus"], r
    assert "grok-build" not in json.dumps(r["hosts"]), r


def _real_state_stamps():
    home = Path.home()
    paths = [
        home / ".cache/seat-usage/latest.json",
        home / ".local/state/seat-usage/asks.jsonl",
        home / ".local/state/seat-usage/history.jsonl",
        home / ".local/state/seat-usage/runs.jsonl",
    ]
    out = {}
    for path in paths:
        out[str(path)] = path.stat().st_mtime_ns if path.exists() else None
    return out


def _shift_snap(now, **kw):
    s = snap(**kw)
    delta = now - NOW
    s["ts"] = now
    s["as_of"] = {k: now for k in s["as_of"]}
    for key, val in list(s.items()):
        if key.endswith("_ts") and isinstance(val, (int, float)) and not isinstance(val, bool):
            s[key] = val + delta
    return s


@test
def cached_route_calls_no_vendor_and_writes_no_file():
    stamps = _real_state_stamps()
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "latest.json")
        with open(cache, "w") as f:
            json.dump(_shift_snap(now), f)
        asks = os.path.join(d, "asks.jsonl")
        history = os.path.join(d, "history.jsonl")
        home = os.path.join(d, "home")
        os.makedirs(home)
        env = os.environ.copy()
        env["HOME"] = home
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = asks
        before = set(os.listdir(d))
        proc = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--cached", "--json"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        rows = json.loads(proc.stdout)
        assert rows[0]["role"] == "lineup" and rows[0]["mode"] == "default", rows
        assert rows[0]["lineup"]["chair"]["vendor"] == "xai", rows
        host = subprocess.run(
            [sys.executable, str(BIN), "--route", "host", "--cached", "--json"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert host.returncode == 0, (host.returncode, host.stderr, host.stdout)
        hrows = json.loads(host.stdout)
        assert hrows[0]["role"] == "host" and hrows[0]["selects"] is False, hrows
        assert [x["id"] for x in hrows[0]["hosts"]] == ["grokbot", "opus"], hrows
        gap = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--cached", "--json", "--builder-family", "grok"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        # The open cache still has a legal pair. A capped cache must ask.
        capped = os.path.join(d, "latest.json")
        with open(capped, "w") as f:
            json.dump(_shift_snap(now, cursor_auto_pct=100), f)
        gap = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--cached", "--json", "--builder-family", "grok"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert gap.returncode == 0, (gap.returncode, gap.stderr, gap.stdout)
        grow = json.loads(gap.stdout)[0]
        assert grow["mode"] != "ask" and grow["lineup"]["builder"]["seat"] == "grok", grow
        assert grow["lineup"]["builder"]["effort"] == "high", grow
        assert not os.path.exists(asks), "cached route wrote the ask log"
        assert not os.path.exists(history), "cached route wrote usage history"
        assert set(os.listdir(d)) == before or set(os.listdir(d)) <= before | {"latest.json"}, os.listdir(d)
        written = []
        for root, dirs, files in os.walk(home):
            written.extend(files)
        assert written == [], written
    assert _real_state_stamps() == stamps, (_real_state_stamps(), stamps)


@test
def cached_route_in_process_does_not_refresh_or_log():
    called = []

    def boom(*_a, **_k):
        called.append("called")
        raise AssertionError("cached route called a vendor or a writer")

    names = ("refresh", "log_asks", "record_pool", "write_cache", "claude", "codex", "cursor", "grok", "full_run")
    saved = {name: getattr(su, name) for name in names}
    old_cache, old_asks, old_history = su.CACHE, su.ASKS, su.HISTORY
    argv = sys.argv
    try:
        for name in names:
            setattr(su, name, boom)
        with tempfile.TemporaryDirectory() as d:
            su.CACHE = os.path.join(d, "latest.json")
            su.ASKS = os.path.join(d, "asks.jsonl")
            su.HISTORY = os.path.join(d, "history.jsonl")
            with open(su.CACHE, "w") as f:
                json.dump(_shift_snap(time.time(), codex=100), f)
            sys.argv = [str(BIN), "--route", "lineup", "--cached", "--json"]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                su.cmd_route()
            row = json.loads(buf.getvalue())[0]
            assert row["mode"] == "default" and row["lineup"] is not None, row
            assert [v["family"] for v in row["lineup"]["voters"]] == ["fable", "wonder-full"], row
            assert not os.path.exists(su.ASKS)
            assert not os.path.exists(su.HISTORY)
            assert called == [], called
    finally:
        for name, fn in saved.items():
            setattr(su, name, fn)
        su.CACHE, su.ASKS, su.HISTORY = old_cache, old_asks, old_history
        sys.argv = argv


@test
def bare_route_lists_the_four_launch_roles():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(_shift_snap(now), f)
        env = os.environ.copy()
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        proc = subprocess.run(
            [sys.executable, str(BIN), "--route", "--json", "--cached"],
            capture_output=True, text=True, env=env, timeout=15,
        )
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    roles = [row["role"] for row in json.loads(proc.stdout)]
    assert roles == ["bounded-question", "hard-bug", "builder", "chair"], roles


@test
def usage_age_covers_every_pool_in_the_snapshot():
    s = snap()
    s["as_of"] = dict(s["as_of"])
    s["as_of"]["grok"] = NOW - 540
    legal = su.route_lineup(s, NOW)
    assert legal["mode"] != "ask", legal
    assert legal["usage"]["grok"]["state"] == "under", legal["usage"]["grok"]
    assert legal["usage_age_s"] == 540, legal["usage_age_s"]
    assert legal["reading_age_s"] == 0, legal["reading_age_s"]
    asked = snap()
    asked["as_of"] = dict(asked["as_of"])
    asked["as_of"]["grokbot"] = NOW - 590
    del asked["fable"]
    denied = su.route_lineup(asked, NOW)
    assert denied["mode"] == "ask", denied
    assert denied["usage_age_s"] == 590, denied["usage_age_s"]
    assert "grokbot" in denied["usage"], denied["usage"]


@test
def host_aged_reading_says_old_and_not_failed():
    s = snap()
    s["as_of"] = dict(s["as_of"])
    s["as_of"]["grokbot"] = NOW - 15 * 60
    r = su.route_host(s, NOW)
    assert r["mode"] == "ask" and r["suggestion"] is None, r
    assert "15m old" in r["reason"], r
    assert "failed" not in r["reason"], r
    assert "never" not in r, r
    missing = su.route_host(without_as_of(snap(), "grokbot"), NOW)
    assert "fix the failed reading" in missing["reason"], missing


@test
def host_pulled_copy_15m_old_does_not_ask():
    s = snap(pulled_ts=NOW - 60)
    s["as_of"] = {k: NOW - 15 * 60 for k in s["as_of"]}
    r = su.route_host(s, NOW)
    assert r["mode"] != "ask" and r["suggestion"], r
    assert "old" not in r["reason"], r


@test
def host_pulled_copy_25m_old_asks():
    s = snap(pulled_ts=NOW - 60)
    s["as_of"] = dict(s["as_of"])
    s["as_of"]["grokbot"] = NOW - 25 * 60
    r = su.route_host(s, NOW)
    assert r["mode"] == "ask" and r["suggestion"] is None, r
    assert "25m old" in r["reason"], r


@test
def host_live_cache_11m_old_still_asks():
    s = snap()
    assert "pulled_ts" not in s
    s["as_of"] = dict(s["as_of"])
    s["as_of"]["grokbot"] = NOW - 11 * 60
    r = su.route_host(s, NOW)
    assert r["mode"] == "ask" and "11m old" in r["reason"], r


@test
def route_freshness_comes_from_the_pool_config():
    def host(age_m, pulled):
        s = snap(pulled_ts=NOW - 60) if pulled else snap()
        s["as_of"] = {k: NOW - age_m * 60 for k in s["as_of"]}
        return su.route_host(s, NOW)
    with tempfile.TemporaryDirectory() as d:
        src = json.loads(Path(su.pools_file()).read_text())
        path = os.path.join(d, "pools.json")
        try:
            src["route_freshness_s"] = {"live": 30 * 60, "pulled": 5 * 60}
            Path(path).write_text(json.dumps(src))
            su.load_config(pools_path=path)
            live = host(15, pulled=False)
            assert live["mode"] != "ask" and live["suggestion"], live
            pulled = host(15, pulled=True)
            assert pulled["mode"] == "ask" and "15m old" in pulled["reason"], pulled
            del src["route_freshness_s"]   # missing key: 10 min live, 20 min pulled
            Path(path).write_text(json.dumps(src))
            su.load_config(pools_path=path)
            live = host(11, pulled=False)
            assert live["mode"] == "ask" and "11m old" in live["reason"], live
            pulled = host(15, pulled=True)
            assert pulled["mode"] != "ask" and pulled["suggestion"], pulled
            assert host(25, pulled=True)["mode"] == "ask"
        finally:
            su.load_config()


@test
def live_merge_drops_the_pulled_marker():
    base = snap(pulled_ts=NOW - 60)
    fresh = {"ts": NOW, "as_of": {"claude": NOW}, "v": 1, "claude_week": 41}
    out = su.merge_summary(base, fresh, ["claude"], NOW)
    assert "pulled_ts" not in out, sorted(out)


@test
def f2_fable_uses_other_models_when_the_5h_window_is_full():
    seated = su.route_lineup(snap(claude_5h=100, cursor_api_pct=10), NOW)
    assert seated["mode"] != "ask" and seated["lineup"] is not None, seated
    fable = next(v for v in seated["lineup"]["voters"] if v["family"] == "fable")
    assert fable["seat"] == "cursor" and fable["effort"] == "high", fable
    assert "weaker:" in seated["reason"], seated
    soon = NOW + 12 * 3600
    burning = su.route_lineup(snap(
        claude_5h=100, cursor_api_pct=10, cursor_auto_pct=50, grok_pct=57,
        cursor_reset_ts=soon, cursor_window_s=MONTH,
    ), NOW)
    # The shared Cursor reset is 12h out on a 30-day window, so elapsed is
    # about 98%. Cursor Models at 50% is under and burning. SuperGrok at 57%
    # stays on its default window and is on pace. Other Models at 10% shares
    # that reset, so it burns, and Fable's effort follows that burn.
    fable_burn = next(v for v in burning["lineup"]["voters"] if v["family"] == "fable")
    assert fable_burn["seat"] == "cursor" and fable_burn["effort"] == "xhigh", burning
    blocked = su.route_lineup(snap(claude_5h=100, cursor_api_pct=100), NOW)
    assert blocked["mode"] != "ask" and blocked["lineup"] is not None, blocked
    assert "fable" not in [v["family"] for v in blocked["lineup"]["voters"]], blocked
    assert "astra" in [v["family"] for v in blocked["lineup"]["voters"]], blocked
    assert "resets in 4.0h" in blocked["downgrade"]["reason"], blocked
    assert "claude 5h full" in blocked["downgrade"]["reason"], blocked
    assert "approve a downgraded leg" in blocked["downgrade"]["reason"], blocked
    assert "fix the failed reading" not in blocked["downgrade"]["reason"], blocked


@test
def both_grok_pools_over_pace_seats_opus_and_grok_at_low():
    s = snap(cursor_auto_pct=80, grok_pct=80)
    chair = su.route("chair", s, NOW)
    builder = su.route("builder", s, NOW)
    assert chair["family"] == "opus" and chair["effort"] == "high", chair
    assert builder["family"] == "opus" and builder["effort"] == "low", builder
    r = su.route_lineup(s, NOW)
    assert r["lineup"]["chair"]["effort"] == chair["effort"], r
    assert r["lineup"]["builder"]["effort"] == builder["effort"], r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["astra", "grok", "wonder-full"], r
    grok = next(v for v in r["lineup"]["voters"] if v["family"] == "grok")
    assert grok["effort"] == "low", grok
    assert "weaker:" in r["reason"], r
    assert "kind" not in r and "excluded" not in r, r
    # An explicit Grok builder still falls back while both pools are ahead.
    # The pre-launch call has no kind, so the pair stays one vendor.
    kept = su.route_lineup(s, NOW, builder=grok_builder())
    assert kept["lineup"]["chair"]["family"] == "opus", kept
    assert kept["lineup"]["builder"]["family"] == "opus", kept
    assert [v["family"] for v in kept["lineup"]["voters"]] == ["astra", "grok", "wonder-full"], kept


def _voter_families(row):
    return [v["family"] for v in row["lineup"]["voters"]]


@test
def lineup_kind_excludes_the_author_not_the_chair():
    # Claude chairs. Grok built the diff. Fable votes. Grok sits out.
    ahead = snap(cursor_auto_pct=80, grok_pct=80)
    review = su.route_lineup(ahead, NOW, builder=grok_builder(), kind="review")
    assert review["mode"] != "ask" and review["lineup"] is not None, review
    assert review["lineup"]["chair"]["seat"] == "claude", review["lineup"]["chair"]
    assert review["lineup"]["chair"]["family"] == "opus", review["lineup"]["chair"]
    assert review["lineup"]["builder"]["family"] == "grok", review["lineup"]["builder"]
    assert review["lineup"]["builder"]["effort"] == "low", review["lineup"]["builder"]
    assert _voter_families(review) == ["fable", "astra"], review
    assert review["author"] == "grok", review
    assert review["excluded"]["vendor"] == "xai", review["excluded"]
    assert review["excluded"]["why"] == "Grok built the diff", review["excluded"]
    assert review["rule"].startswith("review excludes xAI:"), review["rule"]
    assert "Fable" in review["rule"], review["rule"]
    text = su.format_lineup(review)
    assert "excluded: xAI | grok | Grok built the diff" in text, text

    mixed = su.route_lineup(ahead, NOW, builder=grok_builder(), kind="mixed")
    assert _voter_families(mixed) == ["fable", "astra", "wonder-full"], mixed
    assert mixed["excluded"]["why"] == "Grok built the diff", mixed["excluded"]
    assert mixed["rule"].startswith("mixed excludes xAI:"), mixed["rule"]

    # The brief's author wins over the lineup builder.
    named = su.route_lineup(ahead, NOW, builder=grok_builder(), kind="review", author="fable")
    assert named["lineup"]["builder"]["family"] == "grok", named
    assert named["author"] == "fable" and named["excluded"]["vendor"] == "anthropic", named
    assert _voter_families(named) == ["astra", "wonder"], named
    assert named["excluded"]["why"] == "Fable built the diff", named["excluded"]

    # Design seats the chair's vendor. Audit seats everybody until an author is named.
    design = su.route_lineup(snap(), NOW, kind="design")
    assert design["lineup"]["chair"]["vendor"] == "xai", design
    assert _voter_families(design) == ["fable", "astra", "grok", "wonder-full"], design
    named_design = su.route_lineup(snap(), NOW, kind="design", author="grok")
    assert named_design["excluded"]["vendor"] is None, named_design
    assert _voter_families(named_design) == _voter_families(design), named_design
    assert design["excluded"]["vendor"] is None, design["excluded"]
    assert design["excluded"]["why"] == "design judges a packet the chair framed", design
    assert "excluded: none | design judges a packet the chair framed" in su.format_lineup(design)

    audit = su.route_lineup(ahead, NOW, kind="audit")
    assert audit["lineup"]["chair"]["family"] == "opus", audit
    assert audit["lineup"]["builder"]["family"] == "opus", audit
    assert _voter_families(audit) == ["fable", "astra", "grok", "wonder-full"], audit
    assert audit["author"] is None and audit["excluded"]["why"] == "audit author unknown", audit
    wrote = su.route_lineup(ahead, NOW, kind="audit", author="grok")
    assert wrote["lineup"]["builder"]["family"] == "opus", wrote
    assert wrote["author"] == "grok" and wrote["excluded"]["vendor"] == "xai", wrote
    assert _voter_families(wrote) == ["fable", "astra", "wonder-full"], wrote
    assert wrote["excluded"]["why"] == "Grok wrote it", wrote["excluded"]

    # Fable is required when xAI is excluded, not when xAI chairs.
    blocked = snap(claude_5h=100, cursor_api_pct=100)
    design_skip = su.route_lineup(blocked, NOW, kind="design")
    assert design_skip["mode"] != "ask" and design_skip["lineup"] is not None, design_skip
    assert "fable" not in _voter_families(design_skip), design_skip
    assert "approve a downgraded leg" in design_skip["downgrade"]["reason"], design_skip
    # No author and no caller builder: the pace-picked builder is not the author.
    # Fable is still skipped because both frontier seats are full. Grok can vote.
    review_ask = su.route_lineup(blocked, NOW, kind="review")
    assert review_ask["mode"] != "ask" and review_ask["lineup"] is not None, review_ask
    assert review_ask["excluded"]["vendor"] is None, review_ask
    assert "fable" not in _voter_families(review_ask), review_ask
    assert "astra" in _voter_families(review_ask), review_ask
    assert "grok" in _voter_families(review_ask), review_ask
    assert "downgrade" not in review_ask, review_ask
    assert any(item["family"] == "fable" for item in review_ask["skipped"]), review_ask
    assert not any(item.get("code") == "builder_vendor" for item in review_ask["skipped"]), review_ask

    missing = su.route_lineup(snap(), NOW, kind="review", author="composer")
    assert missing["mode"] == "ask" and "no vendor" in missing["reason"], missing
    design_typo = su.route_lineup(snap(), NOW, kind="design", author="composer")
    assert design_typo["mode"] == "ask" and "no vendor" in design_typo["reason"], design_typo
    bad = su.route_lineup(snap(), NOW, kind="host")
    assert bad["mode"] == "ask" and "unknown kind" in bad["reason"], bad


def _review_eligible_families(row):
    """Seated review voters, plus seats that lost only on rank."""
    names = set(_voter_families(row))
    for item in row.get("skipped") or []:
        if item.get("code") == "lower_rank" and item.get("family"):
            names.add(item["family"])
    return names


@test
def review_without_author_or_builder_excludes_nobody():
    def check(s):
        row = su.route_lineup(s, NOW, kind="review")
        assert row["mode"] != "ask", row
        excl = row["excluded"]
        assert excl["vendor"] is None and excl["name"] is None and excl["family"] is None, excl
        assert excl["why"] == "review author unknown", excl
        assert row["author"] is None, row
        assert not any(item.get("code") == "builder_vendor" for item in row.get("skipped") or []), row
        assert "grok" in _review_eligible_families(row), row
        return row

    plain = check(snap())
    assert plain["lineup"]["builder"]["family"] == "grok", plain
    ahead = check(snap(cursor_auto_pct=80, grok_pct=80))
    assert ahead["lineup"]["builder"]["family"] == "opus", ahead
    # Mixed follows the same rule: whoever won the builder slot is not the author.
    mixed = su.route_lineup(snap(), NOW, kind="mixed")
    assert mixed["lineup"]["builder"]["family"] == "grok", mixed
    assert mixed["excluded"]["vendor"] is None and mixed["author"] is None, mixed
    assert "grok" in _voter_families(mixed), mixed
    mixed_ahead = su.route_lineup(snap(cursor_auto_pct=80, grok_pct=80), NOW, kind="mixed")
    assert mixed_ahead["lineup"]["builder"]["family"] == "opus", mixed_ahead
    assert mixed_ahead["excluded"]["vendor"] is None and mixed_ahead["author"] is None, mixed_ahead
    assert "fable" in _voter_families(mixed_ahead), mixed_ahead


@test
def review_author_human_and_none_exclude_nobody():
    for author in ("human", "none"):
        row = su.route_lineup(snap(), NOW, kind="review", author=author)
        assert row["mode"] != "ask", row
        assert row["author"] == author, row
        excl = row["excluded"]
        assert excl["vendor"] is None and excl["name"] is None and excl["family"] is None, excl
        assert not any(item.get("code") == "builder_vendor" for item in row.get("skipped") or []), row
        assert "grok" in _review_eligible_families(row), row
        # A named human or none wins over a caller-passed builder.
        named = su.route_lineup(snap(), NOW, builder=grok_builder(), kind="review", author=author)
        assert named["mode"] != "ask" and named["excluded"]["vendor"] is None, named
        assert named["lineup"]["builder"]["family"] == "grok", named
    ahead = su.route_lineup(snap(cursor_auto_pct=80, grok_pct=80), NOW, kind="review", author="human")
    assert ahead["mode"] != "ask" and ahead["excluded"]["vendor"] is None, ahead
    assert ahead["lineup"]["builder"]["family"] == "opus", ahead
    assert "grok" in _review_eligible_families(ahead), ahead
    # A caller-passed builder vendor still wins, including over human.
    forced = su.route_lineup(snap(), NOW, kind="review", author="human", builder_vendor="xai")
    assert forced["mode"] != "ask" and forced["excluded"]["vendor"] == "xai", forced
    assert any(item.get("family") == "grok" and item.get("code") == "builder_vendor"
               for item in forced["skipped"]), forced
    # Audit and design do not treat human or none as "exclude nobody".
    for kind in ("audit", "design"):
        for author in ("human", "none"):
            asked = su.route_lineup(snap(), NOW, kind=kind, author=author)
            assert asked["mode"] == "ask" and "no vendor" in asked["reason"], (kind, author, asked)

    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(_shift_snap(now), f)
        env = os.environ.copy()
        env["HOME"] = d
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        for author in ("human", "none"):
            proc = subprocess.run(
                [sys.executable, str(BIN), "--route", "lineup", "--kind", "review",
                 "--author", author, "--cached", "--json"],
                capture_output=True, text=True, env=env, timeout=15,
            )
            assert proc.returncode == 0, (author, proc.returncode, proc.stderr, proc.stdout)
            row = json.loads(proc.stdout)[0]
            assert row["mode"] != "ask" and row["author"] == author, row
            assert row["excluded"]["vendor"] is None, row
            assert not any(item.get("code") == "builder_vendor" for item in row.get("skipped") or []), row


@test
def mixed_without_author_or_builder_excludes_nobody():
    for s, builder_family in ((snap(), "grok"), (snap(cursor_auto_pct=80, grok_pct=80), "opus")):
        for author, why in ((None, "mixed author unknown"), ("human", "human author"),
                            ("none", "author none")):
            row = su.route_lineup(s, NOW, kind="mixed", author=author)
            assert row["mode"] != "ask", (author, row)
            assert row["kind"] == "mixed" and row["author"] == author, row
            assert row["lineup"]["builder"]["family"] == builder_family, row
            excl = row["excluded"]
            assert excl == {"vendor": None, "name": None, "family": None, "why": why}, excl
            assert _voter_families(row) == ["fable", "astra", "grok", "wonder-full"], row
            assert row["rule"].startswith("mixed excludes nobody:"), row["rule"]
            assert f"excluded: none | {why}" in su.format_lineup(row), su.format_lineup(row)
    # A caller-passed builder is still excluded when no author is named.
    built = su.route_lineup(snap(), NOW, builder=grok_builder(), kind="mixed")
    assert built["excluded"]["vendor"] == "xai" and built["author"] == "grok", built
    assert "grok" not in _voter_families(built), built
    # A named family still sits out. human and none win over a caller builder.
    named = su.route_lineup(snap(), NOW, kind="mixed", author="fable")
    assert named["excluded"]["vendor"] == "anthropic", named
    for author in ("human", "none"):
        over = su.route_lineup(snap(), NOW, builder=grok_builder(), kind="mixed", author=author)
        assert over["mode"] != "ask" and over["excluded"]["vendor"] is None, over
        assert "grok" in _voter_families(over), over

    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(_shift_snap(now), f)
        env = os.environ.copy()
        env["HOME"] = d
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        for extra, author in (([], None), (["--author", "human"], "human"),
                              (["--author", "none"], "none")):
            proc = subprocess.run(
                [sys.executable, str(BIN), "--route", "lineup", "--kind", "mixed",
                 *extra, "--cached", "--json"],
                capture_output=True, text=True, env=env, timeout=15,
            )
            assert proc.returncode == 0, (extra, proc.returncode, proc.stderr, proc.stdout)
            row = json.loads(proc.stdout)[0]
            assert row["mode"] != "ask" and row["kind"] == "mixed", row
            assert row["author"] == author and row["excluded"]["vendor"] is None, row
        proc = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--kind", "mixed",
             "--builder-family", "grok", "--cached", "--json"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        row = json.loads(proc.stdout)[0]
        assert row["excluded"]["vendor"] == "xai", row
        # Audit is unchanged: human still asks.
        proc = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--kind", "audit",
             "--author", "human", "--cached", "--json"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        row = json.loads(proc.stdout)[0]
        assert row["mode"] == "ask" and "no vendor" in row["reason"], row


def _absent_fable(cursor=10, **kw):
    s = snap(cursor_api_pct=cursor, claude_plan="claude_ai", **kw)
    s.pop("fable", None)
    s.pop("fable_reset_ts", None)
    return s


@test
def absent_fable_slice_seats_the_optimal_frontier_pool():
    seated = su.route_lineup(_absent_fable(), NOW, kind="design")
    assert seated["mode"] != "ask" and seated["lineup"] is not None, seated
    assert "downgrade" not in seated, seated
    fable = next(v for v in seated["lineup"]["voters"] if v["family"] == "fable")
    assert fable["seat"] == "cursor", fable
    assert _voter_families(seated) == ["fable", "astra", "grok", "wonder-full"], seated
    native = su.route_lineup(snap(fable=10, cursor_api_pct=80, claude_plan="claude_ai"), NOW, kind="design")
    native_fable = next(v for v in native["lineup"]["voters"] if v["family"] == "fable")
    assert native_fable["seat"] == "claude", native_fable
    other = su.route_lineup(snap(fable=80, cursor_api_pct=10, claude_plan="claude_ai"), NOW, kind="design")
    other_fable = next(v for v in other["lineup"]["voters"] if v["family"] == "fable")
    assert other_fable["seat"] == "cursor", other_fable


def _off_max(**kw):
    s = snap(**kw)
    s.pop("fable", None)
    s.pop("fable_reset_ts", None)
    return s


@test
def bounded_question_off_max_never_seats_fable_on_claude():
    """Claude Pro has no Fable slice. The role's hold default used to seat Fable on Claude anyway."""
    for s in (_off_max(claude_max=False), _off_max(claude_plan="claude_ai"),
              snap(claude_max=False, fable=10)):  # the flag wins over a stray Fable number
        r = su.route("bounded-question", s, NOW)
        assert r["seat"] == "cursor" and r["family"] == "fable", r
        assert r["mode"] in ("default", "suggest") and r["reason"].startswith("Other Models"), r
    capped = su.route("bounded-question", _off_max(claude_max=False, cursor_api_pct=100), NOW)
    assert capped["mode"] == "ask" and capped["reason"] == "Other Models capped: ask Ryan", capped
    unknown = _off_max(claude_max=False)
    del unknown["cursor_api_pct"]
    asked = su.route("bounded-question", unknown, NOW)
    assert asked["mode"] == "ask" and "Other Models reading unknown" in asked["reason"], asked
    # Plan unknown (no flag, no plan name): unchanged, the hold default stays on Claude.
    held = su.route("bounded-question", _off_max(), NOW)
    assert held["seat"] == "claude" and held["family"] == "fable" and held["reason"] == "Fable: no used %", held
    on_max = su.route("bounded-question", _off_max(claude_max=True), NOW)
    assert on_max["seat"] == "claude" and on_max["reason"] == "Fable: no used %", on_max


@test
def claude_max_flag_from_plan_and_fable_slice():
    f = su.claude_max_flag
    assert f("claude_max_5x", False) is True and f("default_claude_max_20x", False) is True
    assert f("claude_ai", False) is False and f("default_claude_ai", False) is False
    assert f("claude_ai", True) is True   # a Fable slice in the reading is Max, whatever the name
    assert f("?", True) is True           # profile failed, but this reading had a weekly Fable slice
    assert f("?", False) is None and f(None, False) is None and f("", False) is None
    assert f("claude_team", False) is None and f("claude_enterprise", False) is None   # not a known tier
    assert su.claude_max(snap(claude_plan="claude_team")) is None
    assert su.claude_max(snap(claude_max=False, claude_plan="claude_max_5x")) is True   # plan name wins
    assert su.claude_max(snap(claude_plan="claude_ai")) is False
    assert su.claude_max(snap(claude_plan="?")) is None and su.claude_max(snap()) is None
    assert su.claude_max(snap(claude_max=1)) is None    # flags are booleans only
    absent = _off_max(claude_max=False)
    absent.pop("claude_plan", None)
    lineup = su.route_lineup(absent, NOW, kind="design")   # a pulled copy: flag, no plan name
    fable = next(v for v in lineup["lineup"]["voters"] if v["family"] == "fable")
    assert fable["seat"] == "cursor", fable


@test
def claude_max_reads_tier_then_plan_before_the_flag():
    """A tier or plan name decides. The bool is only the fallback."""
    assert su.claude_max({"claude_tier": "claude_ai"}) is False
    named = {"claude_plan": "default_claude_max_5x", "claude_max": False}
    assert su.claude_max(named) is True and su._claude_tier_word(named) == "Max"


@test
def absent_fable_slice_prompts_instead_of_a_failed_reading():
    prompted = su.route_lineup(_absent_fable(cursor=100), NOW, kind="design")
    assert prompted["mode"] != "ask" and prompted["lineup"] is not None, prompted
    assert _voter_families(prompted) == ["astra", "grok", "wonder-full"], prompted
    assert prompted["downgrade"]["family"] == "fable", prompted
    assert "approve a downgraded leg" in prompted["downgrade"]["reason"], prompted
    assert "fix the failed reading" not in prompted["downgrade"]["reason"], prompted
    more = _absent_fable(cursor=100)
    more["downgrade_approved"] = "grok"
    more["grok_pct"] = 10
    less = _absent_fable(cursor=100)
    less["downgrade_approved"] = "grok"
    less["grok_pct"] = 80
    more_row = su.route_lineup(more, NOW, kind="design")
    less_row = su.route_lineup(less, NOW, kind="design")
    more_effort = next(v for v in more_row["lineup"]["voters"] if v.get("downgraded"))["effort"]
    less_effort = next(v for v in less_row["lineup"]["voters"] if v.get("downgraded"))["effort"]
    rank = {"high": 0, "xhigh": 1, "max": 2}
    assert more_effort in rank and less_effort in rank, (more_effort, less_effort)
    assert rank[more_effort] >= rank[less_effort], (more_effort, less_effort)
    assert su.downgrade_effort("under") == "max"
    assert su.downgrade_effort("ahead") == "high"
    max_plan = _absent_fable()
    max_plan["claude_plan"] = "default_claude_max"
    # A missing Fable percent on Max is still a failed reading. Design skips it.
    # Audit still asks.
    skipped_max = su.route_lineup(max_plan, NOW, kind="design")
    assert skipped_max["mode"] != "ask" and skipped_max["lineup"] is not None, skipped_max
    assert _voter_families(skipped_max) == ["astra", "grok", "wonder-full"], skipped_max
    max_skip = next(item for item in skipped_max["skipped"] if item["family"] == "fable")
    assert max_skip["code"] == "unknown" and "fix the failed reading" in max_skip["reason"], max_skip
    audit_max = su.route_lineup(max_plan, NOW, kind="audit")
    assert audit_max["mode"] == "ask" and audit_max["lineup"] is None, audit_max
    assert "fix the failed reading" in audit_max["reason"], audit_max
    # A stale non-Max stamp is an absent slice, so Fable sits on Cursor.
    stale = _absent_fable()
    stale["as_of"] = dict(stale["as_of"])
    stale["as_of"]["claude"] = NOW - 11 * 60
    stale_row = su.route_lineup(stale, NOW, kind="design")
    assert stale_row["mode"] != "ask" and stale_row["lineup"] is not None, stale_row
    stale_fable = next(v for v in stale_row["lineup"]["voters"] if v["family"] == "fable")
    assert stale_fable["seat"] == "cursor", stale_fable
    missing = without_as_of(_absent_fable(), "claude")
    missing_row = su.route_lineup(missing, NOW, kind="design")
    assert missing_row["mode"] != "ask" and missing_row["lineup"] is not None, missing_row
    assert "fable" not in _voter_families(missing_row), missing_row
    missing_skip = next(item for item in missing_row["skipped"] if item["family"] == "fable")
    assert missing_skip["code"] == "unknown" and "fix the failed reading" in missing_skip["reason"], missing_skip
    cursor_stale = _absent_fable()
    cursor_stale["as_of"] = dict(cursor_stale["as_of"])
    cursor_stale["as_of"]["cursor"] = NOW - 11 * 60
    cursor_row = su.route_lineup(cursor_stale, NOW, kind="design")
    assert cursor_row["mode"] != "ask" and cursor_row["lineup"] is not None, cursor_row
    assert "fable" not in _voter_families(cursor_row), cursor_row
    cursor_skip = next(item for item in cursor_row["skipped"] if item["family"] == "fable")
    assert cursor_skip["code"] == "stale", cursor_skip
    sibling = without_as_of(snap(fable=10, claude_plan="claude_ai"), "cursor")
    sibling_row = su.route_lineup(sibling, NOW, kind="design")
    assert sibling_row["mode"] != "ask" and sibling_row["lineup"] is not None, sibling_row
    sibling_fable = next(v for v in sibling_row["lineup"]["voters"] if v["family"] == "fable")
    assert sibling_fable["seat"] == "claude", sibling_fable


@test
def design_skips_a_stale_unknown_plan_and_seats_non_max_fable_on_cursor():
    """A fresh design still seats four voters. Stale seats are skipped, not an ask.

    Claude stale and token_expired with claude_max False seats Fable on Cursor
    when Cursor is fresh. An unknown plan with a stale reading leaves Fable in
    skipped and seats the other voters.
    """
    fresh = su.route_lineup(snap(), NOW, kind="design")
    assert fresh["mode"] != "ask" and _voter_families(fresh) == [
        "fable", "astra", "grok", "wonder-full"], fresh
    assert "skipped" not in fresh, fresh
    assert len(su.VOTER_ROSTER) == 4, su.VOTER_ROSTER

    off = snap(claude_max=False)
    off["as_of"] = dict(off["as_of"])
    off["as_of"]["claude"] = NOW - 11 * 60
    off["read_state"] = {"claude": "token_expired"}
    seated = su.route_lineup(off, NOW, kind="design")
    assert seated["mode"] != "ask" and seated["lineup"] is not None, seated
    fable = next(v for v in seated["lineup"]["voters"] if v["family"] == "fable")
    assert fable["seat"] == "cursor", fable
    assert _voter_families(seated) == ["fable", "astra", "grok", "wonder-full"], seated
    assert not any(item["family"] == "fable" for item in seated.get("skipped") or []), seated

    unknown = snap(claude_plan="?")
    unknown.pop("claude_max", None)
    unknown["as_of"] = dict(unknown["as_of"])
    unknown["as_of"]["claude"] = NOW - 11 * 60
    held = su.route_lineup(unknown, NOW, kind="design")
    assert held["mode"] != "ask" and held["lineup"] is not None, held
    assert _voter_families(held) == ["astra", "grok", "wonder-full"], held
    item = next(x for x in held["skipped"] if x["family"] == "fable")
    assert item["code"] == "stale" and item["seat"] == "claude", item
    assert "11m old" in item["reason"], item


@test
def lineup_kind_flags_reject_a_bad_kind_before_a_refresh():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(_shift_snap(now, cursor_auto_pct=80, grok_pct=80), f)
        home = os.path.join(d, "home")
        os.makedirs(home)
        env = os.environ.copy()
        env["HOME"] = home
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        env["QUARTERMASTER_NO_UPDATE"] = "1"

        def run(args):
            return subprocess.run(
                [sys.executable, str(BIN), *args],
                capture_output=True, text=True, env=env, timeout=15,
            )

        bad = run(["--route", "lineup", "--cached", "--kind", "host"])
        assert bad.returncode == 2, (bad.returncode, bad.stderr, bad.stdout)
        assert "unknown kind" in bad.stderr, bad.stderr
        lone = run(["--route", "lineup", "--cached", "--author", "grok"])
        assert lone.returncode == 2 and "--author needs --kind" in lone.stderr, lone.stderr
        other = run(["--route", "chair", "--cached", "--kind", "review"])
        assert other.returncode == 2 and "apply to lineup" in other.stderr, other.stderr
        ok = run([
            "--route", "lineup", "--cached", "--json",
            "--kind", "review", "--builder-family", "grok",
        ])
        assert ok.returncode == 0, (ok.returncode, ok.stderr, ok.stdout)
        row = json.loads(ok.stdout)[0]
        assert row["lineup"]["chair"]["family"] == "opus", row
        assert row["lineup"]["builder"]["family"] == "grok", row
        assert [v["family"] for v in row["lineup"]["voters"]] == ["fable", "astra"], row
        assert row["excluded"]["vendor"] == "xai", row


@test
def supergrok_burn_is_spent_before_cursor():
    soon = NOW + 12 * 3600
    r = su.route("builder", snap(grok_pct=10, grok_reset_ts=soon), NOW)
    assert r["seat"] == "grok" and r["effort"] == "xhigh" and r["mode"] == "burn", r
    assert r["variant"] == "-", r
    chair = su.route("chair", snap(grok_pct=10, grok_reset_ts=soon), NOW)
    assert chair["seat"] == "grok" and chair["effort"] == "xhigh", chair


def _assert_host_reason(r):
    reason = r.get("reason") or ""
    assert "(" not in reason and ")" not in reason, reason
    low = reason.lower()
    assert "xhigh" not in low and "at high" not in low and "at low" not in low, reason
    if "reading" not in reason and "old" not in reason:
        assert "Opus" not in reason and "Grok Bot" not in reason, reason
    assert r["selects"] is False and isinstance(r.get("lean"), list), r


@test
def host_both_ahead_suggests_the_bot_and_names_over_pace():
    r = su.route_host(snap(bot_pct=80, claude_week=80), NOW)
    assert r["mode"] == "suggest" and r["suggestion"] == "grokbot", r
    assert r["reason"] == "both over pace", r
    assert r["selects"] is False and "never" not in r, r
    assert [h["id"] for h in r["hosts"]] == ["grokbot", "opus"], r
    _assert_host_reason(r)


@test
def host_burn_reason_names_the_pool_and_not_the_host():
    soon = NOW + 12 * 3600
    r = su.route_host(snap(claude_week=5, claude_week_reset_ts=soon, fable=5, fable_reset_ts=soon), NOW)
    assert r["suggestion"] == "opus" and r["mode"] == "burn", r
    assert r["reason"] == "weekly all-models pool burning", r
    assert r["hosts"][1]["binding"] == "claude_week" and r["hosts"][1]["used_pct"] == 5, r
    _assert_host_reason(r)


@test
def host_short_window_ahead_drops_max_and_keeps_default_effort():
    soon = NOW + 12 * 3600
    r = su.route_host(snap(
        claude_5h=91, claude_week=5, claude_week_reset_ts=soon, fable=5, fable_reset_ts=soon,
    ), NOW)
    assert r["suggestion"] == "grokbot" and r["mode"] == "suggest", r
    assert r["hosts"][1]["state"] == "ahead" and r["hosts"][1]["binding"] == "claude_5h", r
    assert r["hosts"][1]["used_pct"] == 91 and r["hosts"][1]["windows"]["claude_week"] == 5, r
    assert r["hosts"][1]["effort"] == "high" and r["effort"] == "-", r
    _assert_host_reason(r)


@test
def host_ask_names_the_binding_window_and_leans():
    r = su.route_host(snap(bot_pct=93, claude_5h=100, claude_week=34), NOW)
    assert r["mode"] == "ask" and r["suggestion"] is None, r
    assert su._pool_facts("claude_5h", snap(claude_tier="claude_ai"))["limits"] == "Pro session window"
    assert su._pool_facts("claude_week", snap(claude_max=True))["limits"] == "Max weekly, all models"
    assert su._pool_facts("fable", snap())["limits"] == "Claude weekly Fable slice"
    assert "{tier}" not in su._pool_facts("claude_5h", snap())["limits"]
    assert su._opus_phrase("builder", "low", snap(claude_max=False)) == "Opus at low on Pro"
    assert su._opus_phrase("chair", "high", snap(claude_tier="claude_max_5x")) == "Opus on Max at high"
    assert r["reason"] == "Bot over pace, Claude 5h full", r
    pro = su.route_host(snap(bot_pct=93, claude_5h=100, claude_week=34, claude_max=False), NOW)
    assert pro["reason"] == "Bot over pace, Pro 5h full", pro
    mx = su.route_host(snap(bot_pct=93, claude_5h=100, claude_week=34, claude_tier="claude_max_5x"), NOW)
    assert mx["reason"] == "Bot over pace, Max 5h full", mx
    assert r["lean"] == ["cursor_models", "grok"], r
    assert r["hosts"][1]["binding"] == "claude_5h" and r["hosts"][1]["used_pct"] == 100, r
    assert r["hosts"][1]["windows"]["claude_week"] == 34, r
    _assert_host_reason(r)


@test
def host_lean_puts_supergrok_first_only_when_it_would_expire():
    soon = NOW + 12 * 3600
    burning = su.route_host(snap(
        bot_pct=100, claude_5h=100, grok_pct=10, grok_reset_ts=soon,
    ), NOW)
    assert burning["lean"][0] == "grok", burning["lean"]
    empty = su.route_host(snap(
        bot_pct=100, claude_week=100, cursor_auto_pct=100, grok_pct=100,
    ), NOW)
    assert empty["lean"] == [], empty
    _assert_host_reason(burning)
    _assert_host_reason(empty)


@test
def codex_5h_ahead_does_not_burn_the_weekly_pool():
    soon = NOW + 12 * 3600
    blocked = snap(codex=5, codex_reset_ts=soon, codex_5h=91, codex_5h_reset_ts=NOW + 4 * 3600)
    assert su.pool_status("codex_5h", blocked, NOW)[0] == "ahead"
    assert su._pool_burning("codex", blocked, NOW) is False
    assert su.pool_status("codex_5h", snap(), NOW)[0] == "open"


@test
def claude_renewal_date_only_keeps_the_calendar_day():
    old = su.RENEWALS_FILE
    saved = dict(su.SUMMARY)
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "renewals.json")
            with open(path, "w") as f:
                json.dump({"claude": {"at": "2026-10-14", "renews": None}}, f)
            su.RENEWALS_FILE = path
            su.claude_renewal()
            assert su.SUMMARY["claude_renews_date_only"] is True
            assert su.SUMMARY["claude_renews_on"] == "2026-10-14"
            assert su.SUMMARY["claude_renews"] is None
            with open(path, "w") as f:
                json.dump({"claude": {"at": "2026-10-14T15:00:00-07:00", "renews": False}}, f)
            su.claude_renewal()
            assert su.SUMMARY["claude_renews_date_only"] is False
            assert su.SUMMARY["claude_renews_on"] is None
            assert su.SUMMARY["claude_renews"] is False
    finally:
        su.RENEWALS_FILE = old
        su.SUMMARY.clear()
        su.SUMMARY.update(saved)


@test
def claude_renewal_drops_a_passed_term_while_the_profile_is_active():
    old, old_now = su.RENEWALS_FILE, su.NOW
    saved = dict(su.SUMMARY)
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "renewals.json")
            with open(path, "w") as f:
                json.dump({"claude": {"at": "2026-10-14", "renews": False}}, f)
            su.RENEWALS_FILE = path
            su.NOW = su.iso_to_ts("2026-10-20T12:00:00-07:00")
            su.SUMMARY.clear()
            su.claude_renewal("active")
            assert "claude_renews_ts" not in su.SUMMARY, su.SUMMARY
            su.claude_renewal("canceled")
            assert su.SUMMARY["claude_renews"] is False, su.SUMMARY
            su.SUMMARY.clear()
            su.NOW = su.iso_to_ts("2026-10-10T12:00:00-07:00")
            su.claude_renewal("active")
            assert su.SUMMARY["claude_renews"] is False, "a term still ahead is kept"
    finally:
        su.RENEWALS_FILE, su.NOW = old, old_now
        su.SUMMARY.clear()
        su.SUMMARY.update(saved)


UNDER_HARD_BUG = (
    "route: hard-bug | chatgpt | astra | low | - | suggest | "
    "Codex under pace: score 0.85 (fit 0.88, headroom 1.00, cost 3, lose-it 0.00) | reading 0m\n"
)


def _cached_hard_bug(now, **kw):
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        os.makedirs(home)
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(_shift_snap(now, **kw), f)
        env = os.environ.copy()
        env["HOME"] = home
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        env["SEAT_USAGE_RENEWALS"] = os.path.join(d, "renewals.json")
        return subprocess.run(
            [sys.executable, str(BIN), "--route", "hard-bug", "--cached"],
            capture_output=True, text=True, env=env, timeout=15,
        )


@test
def hard_bug_full_5h_window_asks_with_that_reset():
    proc = _cached_hard_bug(time.time(), codex=5, codex_5h=100, codex_5h_reset_ts=NOW + 4 * 3600)
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stderr == "", proc.stderr
    assert proc.stdout != UNDER_HARD_BUG, proc.stdout
    assert "| ask |" in proc.stdout, proc.stdout
    assert "resets in 4.0h" in proc.stdout, proc.stdout
    assert "ChatGPT capped: ask Ryan" in proc.stdout, proc.stdout


@test
def hard_bug_past_5h_reset_stays_on_the_weekly_route():
    proc = _cached_hard_bug(time.time(), codex=5, codex_5h=100, codex_5h_reset_ts=NOW - 60)
    assert proc.returncode == 0 and proc.stderr == "", (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == UNDER_HARD_BUG, proc.stdout


@test
def hard_bug_open_5h_window_matches_the_weekly_route():
    proc = _cached_hard_bug(time.time(), codex=5, codex_5h=0, codex_5h_reset_ts=NOW + 4 * 3600)
    assert proc.returncode == 0 and proc.stderr == "", (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == UNDER_HARD_BUG, proc.stdout


@test
def hard_bug_without_a_5h_window_matches_the_weekly_route():
    proc = _cached_hard_bug(time.time(), codex=5)
    assert proc.returncode == 0 and proc.stderr == "", (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == UNDER_HARD_BUG, proc.stdout


MUSE_FIXTURES = HERE / "fixtures" / "muse-usage"
FRESH_MUSE_UTC = (
    "muse     | Current 42% (resets 2099-01-01 00:00 UTC)"
    " | Weekly 18.5% (resets 2099-01-08 00:00 UTC)"
    " | as of 2026-10-12 15:04 UTC"
)
FRESH_MUSE_LA = (
    "muse     | Current 42% (resets 2098-12-31 16:00 PST)"
    " | Weekly 18.5% (resets 2099-01-07 16:00 PST)"
    " | as of 2026-10-12 08:04 PDT"
)
STALE_MUSE_UTC = (
    "muse     | Current 90% (resets 2020-01-01 05:00 UTC)"
    " | Weekly 10% (resets 2099-01-08 00:00 UTC)"
    " | as of 2020-01-01 00:00 UTC | stale (window reset)"
)
NO_MUSE = "muse     | no data yet"


@contextlib.contextmanager
def local_tz(name):
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def load_muse_fixture(name):
    return json.loads((MUSE_FIXTURES / name).read_text())


def _string_consts(fn):
    found = []
    stack = [fn.__code__]
    seen = set()
    while stack:
        code = stack.pop()
        if id(code) in seen:
            continue
        seen.add(id(code))
        for const in code.co_consts:
            if isinstance(const, str):
                found.append(const)
            elif hasattr(const, "co_consts"):
                stack.append(const)
    return found


@test
def muse_fixture_prints_current_and_weekly_in_local_time():
    data = load_muse_fixture("fresh.json")
    with local_tz("UTC"):
        assert su.format_muse_line(data, NOW) == FRESH_MUSE_UTC
    with local_tz("America/Los_Angeles"):
        assert su.format_muse_line(data, NOW) == FRESH_MUSE_LA
    # An offset in the file is converted. The line does not echo the source zone.
    shifted = dict(data, captured_at="2026-10-12T08:04:00-07:00")
    with local_tz("UTC"):
        line = su.format_muse_line(shifted, NOW)
    assert "as of 2026-10-12 15:04 UTC" in line, line
    assert "-07:00" not in line, line
    assert "300" not in line, line
    assert len(line.split("|", 1)[0]) == len("grok     "), line


@test
def muse_fixture_marks_stale_when_the_window_reset_has_passed():
    data = load_muse_fixture("stale-window.json")
    reset = datetime.fromisoformat("2020-01-01T05:00:00+00:00").timestamp()
    with local_tz("UTC"):
        before = su.format_muse_line(data, reset - 1)
        at_reset = su.format_muse_line(data, reset)
        later = su.format_muse_line(data, NOW)
    assert before == STALE_MUSE_UTC.replace(" | stale (window reset)", ""), before
    assert at_reset == STALE_MUSE_UTC, at_reset
    assert later == STALE_MUSE_UTC, later
    # The weekly reset is still in the future. Stale is the window that ended since capture.
    assert "2099-01-08" in later, later


@test
def muse_weekly_reset_alone_does_not_mark_the_line_stale():
    data = load_muse_fixture("weekly-reset-passed.json")
    with local_tz("UTC"):
        line = su.format_muse_line(data, NOW)
    assert line == (
        "muse     | Current 12% (resets 2099-06-01 00:00 UTC)"
        " | Weekly 80% (resets 2020-01-01 00:00 UTC)"
        " | as of 2026-10-12 15:04 UTC"
    ), line
    assert "stale" not in line, line


@test
def muse_missing_or_broken_fixture_says_no_data_yet():
    missing = MUSE_FIXTURES / "no-such.json"
    assert su.muse_line(now=NOW, path=str(missing)) == NO_MUSE
    assert su.muse_line(now=NOW, path=str(MUSE_FIXTURES / "broken.json")) == NO_MUSE
    assert su.muse_line(now=NOW, path=str(MUSE_FIXTURES / "missing-fields.json")) == NO_MUSE
    assert su.format_muse_line(None, NOW) is None
    assert su.format_muse_line([], NOW) is None
    assert su.format_muse_line({"captured_at": "2026-10-12T15:04:00Z"}, NOW) is None
    bad = load_muse_fixture("fresh.json")
    bad["window"] = dict(bad["window"], used_percent="42")
    assert su.format_muse_line(bad, NOW) is None
    bad = load_muse_fixture("fresh.json")
    bad["window"] = dict(bad["window"], window_duration_mins=-5)
    assert su.format_muse_line(bad, NOW) is None
    bad = load_muse_fixture("fresh.json")
    bad["window"] = dict(bad["window"], used_percent=True)
    assert su.format_muse_line(bad, NOW) is None
    bad = load_muse_fixture("fresh.json")
    bad["captured_at"] = "yesterday"
    assert su.format_muse_line(bad, NOW) is None
    # A named model in the file is not copied onto the line.
    named = load_muse_fixture("fresh.json")
    named["model"] = "muse-spark-1.3"
    with local_tz("UTC"):
        line = su.format_muse_line(named, NOW)
    assert line == FRESH_MUSE_UTC, line
    assert "spark" not in line, line


@test
def muse_pools_seat_a_voter_and_do_not_change_launch_routes():
    assert su.SEAT_KEYS["muse"] == (
        "muse_5h", "muse_5h_reset_ts", "muse_week", "muse_week_reset_ts",
        "muse_exhausted", "muse_exhausted_reset_ts",
    )
    assert "muse_5h" in su.POOLS and "muse_week" in su.POOLS
    assert su.FAMILY_VENDOR["muse"] == "meta"
    assert su.FAMILY_VENDOR["wonder-full"] == "meta"
    assert su.FAMILY_VENDOR["wonder"] == "meta"
    assert su._FAMILY_NAME["muse"] == "Muse"
    assert su._FAMILY_NAME["wonder-full"] == "Wonder full"
    assert su._FAMILY_NAME["wonder"] == "Wonder"
    week_meta = su.POOLS["muse_week"]
    assert week_meta["family"] == "wonder-full"
    assert list(week_meta["serves"]) == ["wonder-full", "wonder"]
    assert su._family_for(week_meta, "ahead") == "wonder"
    assert su._family_for(week_meta, "under") == "wonder-full"
    assert any(spec["family"] == "wonder-full" for spec in su.VOTER_ROSTER)
    assert "muse" not in su.ROLES
    for role in ("bounded-question", "hard-bug", "builder", "chair"):
        assert su.POOLS["muse_week"].get("strengths", {}).get(role) is None
        assert su.route(role, snap(), NOW)["family"] not in ("wonder-full", "wonder")
    seated = su.route_lineup(snap(), NOW)
    assert [v["family"] for v in seated["lineup"]["voters"]] == ["fable", "astra", "wonder-full"], seated
    ahead = su.route_lineup(snap(muse_5h=80), NOW)
    assert [v["family"] for v in ahead["lineup"]["voters"]] == ["fable", "astra", "wonder-full"], ahead
    muse = next(v for v in ahead["lineup"]["voters"] if v["family"] == "wonder-full")
    assert muse["effort"] == "high", muse
    week = su.route_lineup(snap(muse_week=80), NOW)
    muse_week = next(v for v in week["lineup"]["voters"] if v["family"] == "wonder-full")
    assert muse_week["effort"] == "low", muse_week
    capped = su.route_lineup(snap(muse_5h=100), NOW)
    assert "wonder-full" not in [v["family"] for v in capped["lineup"]["voters"]], capped
    missing = su.route_lineup(without_as_of(snap(), "muse"), NOW)
    assert missing["mode"] == "ask" and missing["lineup"] is None, missing
    assert missing["reason"] == "muse: no usage source", missing
    # A named model on the snapshot is not a route input.
    extra = snap(model="muse-spark-1.3")
    for role in ("bounded-question", "hard-bug", "builder", "chair"):
        assert su.route(role, snap(), NOW) == su.route(role, extra, NOW), role
    assert su.route_host(snap(), NOW) == su.route_host(extra, NOW)
    before = json.dumps(su.SUMMARY, sort_keys=True, default=str)
    with local_tz("UTC"):
        assert su.muse_line(now=NOW, path=str(MUSE_FIXTURES / "fresh.json")) == FRESH_MUSE_UTC
    assert json.dumps(su.SUMMARY, sort_keys=True, default=str) == before


@test
def route_ask_picks_two_vendors_by_headroom():
    r = su.route_ask(snap(), NOW)
    assert r["role"] == "ask" and r["mode"] == "default", r
    assert [(v["family"], v["effort"], v["vendor"]) for v in r["voters"]] == [
        ("fable", "high", "anthropic"),
        ("astra", "high", "openai"),
    ], r
    assert [v["family"] for v in r["candidates"]] == ["fable", "astra", "grok", "wonder-full"], r
    # Grok stays a candidate. Ask mode does not drop the chair's vendor.
    assert any(v["family"] == "grok" for v in r["candidates"]), r
    full = su.route_ask(snap(fable=100, claude_week=100, claude_5h=100, cursor_api_pct=100, codex=100), NOW)
    assert [v["family"] for v in full["voters"]] == ["grok", "wonder-full"], full
    assert len({v["vendor"] for v in full["voters"]}) == 2, full
    over = su.route_ask(snap(muse_week=90), NOW)
    assert "wonder-full" not in [v["family"] for v in over["candidates"]], over
    burning = su.route_ask(snap(muse_week=10, muse_week_reset_ts=NOW + 12 * 3600), NOW)
    muse = next(v for v in burning["candidates"] if v["family"] == "wonder-full")
    assert muse["effort"] == "xhigh" and muse["mode"] == "burn" and muse["pace"] == "under", muse


@test
def muse_numeric_reset_prints_and_a_past_window_is_stale():
    # Conclave stores whichever type the API sent. The live file's 5h reset is a unix time.
    data = load_muse_fixture("fresh.json")
    data["window"] = dict(data["window"], resets_at=4102444800)  # 2100-01-01T00:00:00Z
    with local_tz("UTC"):
        line = su.format_muse_line(data, NOW)
    assert line is not None and line.startswith("muse     | Current 42% (resets 2100-01-01 00:00 UTC)"), line
    assert "stale" not in line, line
    data["window"] = dict(data["window"], resets_at=1759910000)  # 2025-10-08
    with local_tz("UTC"):
        stale = su.format_muse_line(data, NOW)
    assert stale is not None and stale.endswith("| stale (window reset)"), stale


@test
def muse_cache_stores_a_parsed_snapshot_even_after_the_window_reset():
    captured = datetime.fromisoformat("2026-10-12T15:04:00+00:00").timestamp()
    current = {"v": 1, "as_of": {}, "errors": {"muse": "no data yet"}, "muse_week": 9}
    su.apply_muse(current, NOW, path=str(MUSE_FIXTURES / "fresh.json"))
    assert current["muse_5h"] == 42, current
    assert current["muse_week"] == 18.5, current
    assert current["as_of"]["muse"] == captured, current["as_of"]
    assert abs(current["muse_5h_reset_ts"] - datetime.fromisoformat("2099-01-01T00:00:00+00:00").timestamp()) < 1
    assert "muse" not in current["errors"], current

    stale_file = load_muse_fixture("stale-window.json")
    stale = {"v": 1, "as_of": {}, "errors": {"muse": "no data yet"}, "muse_week": 9, "muse_5h": 1}
    su.apply_muse(stale, NOW, path=str(MUSE_FIXTURES / "stale-window.json"))
    assert stale["muse_5h"] == stale_file["window"]["used_percent"], stale
    assert stale["muse_week"] == stale_file["weekly"]["used_percent"], stale
    assert stale["as_of"]["muse"] == su._muse_epoch(stale_file["captured_at"]), stale["as_of"]
    assert "muse" not in stale["errors"], stale

    missing = {"v": 1, "as_of": {"muse": NOW}, "muse_week": 4}
    su.apply_muse(missing, NOW, path=str(MUSE_FIXTURES / "no-such.json"))
    assert "muse" not in missing["as_of"], missing
    assert missing["errors"]["muse"] == "no data yet", missing
    assert missing["muse_week"] == 4, missing


def _muse_stub(path, body):
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)


@test
def muse_refresh_leaves_a_current_file_alone():
    now = time.time()
    captured = now - 60
    window_reset = now + 4 * 3600
    doc = {
        "captured_at": su.datetime.fromtimestamp(captured, su.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": {"used_percent": 4, "window_duration_mins": 300, "resets_at": window_reset},
        "weekly": {"used_percent": 11, "resets_at": now + 3 * 86400},
    }
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        dest = d / "muse-usage.json"
        dest.write_text(json.dumps(doc))
        before = dest.read_text()
        assert su.refresh_muse_usage(now, path=str(dest), binary=str(d / "no-muse")) is False
        assert dest.read_text() == before


@test
def muse_refresh_replaces_an_ancient_window_from_usage_read():
    observed = 1_791_994_305_857
    stub = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}), flush=True)
    elif msg.get("method") == "usage/read":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"usage": {
            "observedAtMs": %d,
            "tier": "t",
            "window": {"usedPercent": 0, "windowDurationMins": 300, "resetsAtMs": 1792012300000},
            "weekly": {"usedPercent": 10, "resetsAtMs": 1792368000000},
        }}}), flush=True)
        break
''' % observed
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        binary = d / "muse"
        _muse_stub(binary, stub)
        dest = d / "muse-usage.json"
        dest.write_text(json.dumps({
            "captured_at": "2026-10-14T04:04:45Z",
            "window": {"used_percent": 80, "window_duration_mins": 300, "resets_at": 1759910000},
            "weekly": {"used_percent": 3, "resets_at": "2026-10-19T00:00:00Z"},
        }))
        assert su.refresh_muse_usage(time.time(), path=str(dest), binary=str(binary), timeout=5) is True
        got = json.loads(dest.read_text())
        assert got["window"]["used_percent"] == 0, got
        assert got["weekly"]["used_percent"] == 10, got
        assert got["window"]["resets_at"] == 1792012300, got
        assert got["captured_at"] == "2026-10-14T16:11:45Z", got


@test
def muse_refresh_uses_the_changed_event_when_the_read_is_empty():
    stub = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}), flush=True)
    elif method == "usage/read":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}), flush=True)
    elif method == "session/start":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"session": {"sessionId": "s1"}}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"jsonrpc": "2.0", "method": "usage/changed", "params": {
            "observedAtMs": 1791994305857,
            "tier": "t",
            "window": {"usedPercent": 1, "windowDurationMins": 300, "resetsAtMs": 1792012300000},
            "weekly": {"usedPercent": 12, "resetsAtMs": 1792368000000},
        }}), flush=True)
        break
'''
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        binary = d / "muse"
        _muse_stub(binary, stub)
        dest = d / "muse-usage.json"
        assert su.refresh_muse_usage(time.time(), path=str(dest), binary=str(binary), timeout=5) is True
        got = json.loads(dest.read_text())
        assert got["window"]["used_percent"] == 1, got
        assert got["weekly"]["used_percent"] == 12, got


def _seed_route_cache(path, now):
    path.write_text(json.dumps({
        "v": 1,
        "ts": now,
        "as_of": {seat: now for seat in ("claude", "chatgpt", "cursor", "grokbot", "grok")},
        "claude_5h": 10,
        "claude_5h_reset_ts": now + 4 * 3600,
        "claude_week": 10,
        "claude_week_reset_ts": now + 3 * 86400,
        "fable": 10,
        "fable_reset_ts": now + 3 * 86400,
        "codex": 10,
        "codex_reset_ts": now + 3 * 86400,
        "codex_window_s": WEEK,
        "cursor_auto_pct": 15,
        "cursor_api_pct": 10,
        "cursor_pct": 15,
        "cursor_reset_ts": now + 15 * 86400,
        "cursor_window_s": MONTH,
        "grok_pct": 10,
        "grok_reset_ts": now + 3 * 86400,
        "grok_window_s": WEEK,
        "grok_exhausted": False,
        "bot_pct": 10,
        "bot_reset_ts": now + 3 * 86400,
        "bot_window_s": WEEK,
        "lines": ["claude   | max active"],
        "burn": [],
    }))


def _run_bin(home, cache_dir, args):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "UTC",
        "SEAT_USAGE_CACHE_DIR": str(cache_dir),
        "SEAT_USAGE_ASKS": str(Path(cache_dir) / "asks.jsonl"),
        "QUARTERMASTER_NO_UPDATE": "1",
    }
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, env=env, timeout=20,
    )


@test
def cached_cli_reads_muse_usage_json_and_route_ignores_it():
    with tempfile.TemporaryDirectory() as home:
        home = Path(home)
        cache_dir = home / "seat-cache"
        cache_dir.mkdir()
        _seed_route_cache(cache_dir / "latest.json", time.time())
        dest = home / ".cache" / "quartermaster" / "muse-usage.json"
        dest.parent.mkdir(parents=True)
        dest.write_text((MUSE_FIXTURES / "fresh.json").read_text())
        shown = _run_bin(home, cache_dir, ["--cached"])
        assert shown.returncode == 0 and shown.stderr == "", (shown.returncode, shown.stderr, shown.stdout)
        assert f"  {FRESH_MUSE_UTC}\n" in shown.stdout, shown.stdout
        assert "claude   | max active" in shown.stdout, shown.stdout
        routed = _run_bin(home, cache_dir, ["--route", "--cached"])
        assert routed.returncode == 0 and routed.stderr == "", (routed.returncode, routed.stderr, routed.stdout)
        assert "muse" not in routed.stdout.lower(), routed.stdout
        assert "route: builder | cursor | grok |" in routed.stdout, routed.stdout
        dest.write_text((MUSE_FIXTURES / "stale-window.json").read_text())
        stale = _run_bin(home, cache_dir, ["--cached"])
        assert f"  {STALE_MUSE_UTC}\n" in stale.stdout, stale.stdout
        dest.write_text((MUSE_FIXTURES / "broken.json").read_text())
        broken = _run_bin(home, cache_dir, ["--cached"])
        assert f"  {NO_MUSE}\n" in broken.stdout, broken.stdout
        dest.unlink()
        absent = _run_bin(home, cache_dir, ["--cached"])
        assert f"  {NO_MUSE}\n" in absent.stdout, absent.stdout
        dest.write_text((MUSE_FIXTURES / "fresh.json").read_text())
        empty = home / "empty-cache"
        empty.mkdir()
        missing_cache = _run_bin(home, empty, ["--cached"])
        assert missing_cache.returncode == 0 and missing_cache.stderr == "", (missing_cache.returncode, missing_cache.stderr)
        assert "no cached line" in missing_cache.stdout, missing_cache.stdout
        assert f"  {FRESH_MUSE_UTC}\n" in missing_cache.stdout, missing_cache.stdout
        # The seat cache is not where this line lives, and --route did not write asks.
        stored = json.loads((cache_dir / "latest.json").read_text())
        assert all(not str(ln).startswith("muse") for ln in stored.get("lines") or [])
        assert not any("muse" in str(key) for key in stored)
        assert not (cache_dir / "asks.jsonl").exists()


@test
def full_run_prints_muse_and_stores_it_beside_the_seat_lines():
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "latest.json")
        old = (su.CACHE, su.CACHE_DIR, su.SUMMARY, su.muse_usage_path)
        saved = {name: getattr(su, name) for name in ("claude", "codex", "cursor", "grok")}

        def claude():
            su.SUMMARY["as_of"]["claude"] = time.time()
            su.SUMMARY["claude_week"] = 1
            return ["claude   | ok"]

        def codex():
            su.SUMMARY["as_of"]["chatgpt"] = time.time()
            return ["chatgpt  | ok"]

        def cursor():
            su.SUMMARY["as_of"]["cursor"] = time.time()
            su.SUMMARY["as_of"]["grokbot"] = time.time()
            return ["cursor   | ok", "grokbot  | ok"]

        def grok():
            su.SUMMARY["as_of"]["grok"] = time.time()
            return ["grok     | ok"]

        su.CACHE, su.CACHE_DIR = cache, d
        su.SUMMARY = {"ts": time.time(), "as_of": {}, "v": 1}
        su.muse_usage_path = lambda: str(MUSE_FIXTURES / "fresh.json")
        su.claude, su.codex, su.cursor, su.grok = claude, codex, cursor, grok
        try:
            quiet_buf = io.StringIO()
            with contextlib.redirect_stdout(quiet_buf):
                quiet = su.full_run(quiet=True, timeout=5)
            assert quiet_buf.getvalue() == "", quiet_buf.getvalue()
            assert all(not ln.startswith("muse") for ln in quiet["lines"]), quiet["lines"]
            loud_buf = io.StringIO()
            with local_tz("UTC"):
                with contextlib.redirect_stdout(loud_buf):
                    loud = su.full_run(quiet=False, timeout=5)
            text = loud_buf.getvalue()
            muse_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("muse")]
            assert muse_lines == [FRESH_MUSE_UTC], text
            # The printed line stays out of the seat lines. The numbers live in the cache fields.
            assert all(not ln.startswith("muse") for ln in loud["lines"]), loud["lines"]
            captured = datetime.fromisoformat("2026-10-12T15:04:00+00:00").timestamp()
            assert loud["muse_5h"] == 42 and loud["muse_week"] == 18.5, loud
            assert loud["as_of"]["muse"] == captured, loud["as_of"]
            written = json.load(open(cache))
            assert all(not str(ln).startswith("muse") for ln in written["lines"]), written["lines"]
            assert written["muse_5h"] == 42 and written["as_of"]["muse"] == captured, written
            assert quiet["muse_5h"] == 42 and quiet["as_of"]["muse"] == captured, quiet
        finally:
            su.CACHE, su.CACHE_DIR, su.SUMMARY, su.muse_usage_path = old
            for name, fn in saved.items():
                setattr(su, name, fn)


@test
def blend_lets_track_outweigh_the_prior():
    # n = 0 keeps the mean of the seeds. A track value with n = 0 does not count.
    assert abs(su.blend_strength(0.8, 0.4, None, 0) - 0.6) < 1e-9
    assert abs(su.blend_strength(0.8, 0.4, 1.0, 0) - 0.6) < 1e-9
    # (8 * 0.8 + 8 * 0.4 + 80 * 1) / 96. The track record is most of the weight.
    got = su.blend_strength(0.8, 0.4, 1.0, 80)
    assert abs(got - (89.6 / 96)) < 1e-9, got
    assert got > 0.9
    # A missing prior is omitted. It is not a zero.
    assert su.blend_strength(0.5, None, None, 0) == 0.5
    assert su.blend_strength(None, None, None, 0) is None


RUNS_FIXTURE = HERE / "fixtures" / "conclave" / "runs.jsonl"


@test
def conclave_runs_fixture_counts_seat_agreement():
    assert not Path("/no/such/conclave/runs.jsonl").exists()
    assert su.read_track(["/no/such/conclave/runs.jsonl"]) == {}
    got = su.read_track([str(RUNS_FIXTURE)])
    assert got[("cursor_models", "builder")] == (1.0, 1), got
    assert got[("grok", "builder")] == (0.5, 2), got
    assert got[("fable", "builder")] == (1.0, 1), got
    assert got[("codex", "builder")] == (0.0, 1), got
    assert got[("cursor_models", "chair")] == (0.0, 1), got
    assert got[("claude_week", "chair")] == (1.0, 1), got
    assert ("claude_5h", "chair") not in got, got


@test
def conclave_runs_log_can_move_a_route():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "runs.jsonl")
        line = (
            '{"role":"builder","seats":[{"seat":"cursor","family":"grok","agreed":false}]}\n'
        )
        with open(path, "w") as f:
            f.write(line * 80)
        old = os.environ.get("SEAT_USAGE_RUNS")
        os.environ["SEAT_USAGE_RUNS"] = path
        try:
            moved = su.route("builder", snap(), NOW)
            assert moved["seat"] == "grok", moved
        finally:
            if old is None:
                os.environ.pop("SEAT_USAGE_RUNS", None)
            else:
                os.environ["SEAT_USAGE_RUNS"] = old
    restored = su.route("builder", snap(), NOW)
    assert restored["seat"] == "cursor", restored


@test
def track_reader_ignores_a_missing_file_and_counts_agreement():
    assert su.read_track(["/no/such/outcomes.jsonl"]) == {}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "outcomes.jsonl")
        with open(path, "w") as f:
            f.write('{"role": "builder", "pool": "spare", "agreed": true}\n')
            f.write('{"role": "builder", "pool": "spare", "agreed": false}\n')
            f.write("not json\n\n")
            f.write('{"role": "lineup", "votes": [{"pool": "fable", "agreed": true}, {"pool": "codex", "agreed": false}]}\n')
        got = su.read_track([path])
    assert got[("spare", "builder")] == (0.5, 2), got
    assert got[("fable", "lineup")] == (1.0, 1), got
    assert got[("codex", "lineup")] == (0.0, 1), got


@test
def prior_file_overrides_the_pool_seed():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "priors.json")
        with open(path, "w") as f:
            json.dump({
                "version": 1,
                "scores": {"fable": {"bounded-question": {"score": 0.0, "samples": 3, "source": "test"}}},
            }, f)
        try:
            su.load_config(priors_path=path)
            assert su.PRIORS["fable"]["bounded-question"] == 0.0
            blended = su.blend_strength(0.9, 0.0, None, 0)
            assert abs(blended - 0.45) < 1e-9, blended
            moved = su.route("bounded-question", snap(fable=10), NOW)
            assert moved["family"] == "opus", moved
        finally:
            su.load_config()
    restored = su.route("bounded-question", snap(fable=10), NOW)
    assert restored["family"] == "fable", restored


@test
def local_outcomes_move_a_route_once_the_sample_count_grows():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "outcomes.jsonl")
        with open(path, "w") as f:
            for _ in range(80):
                f.write('{"role": "builder", "pool": "cursor_models", "agreed": false}\n')
        old = os.environ.get("SEAT_USAGE_OUTCOMES")
        os.environ["SEAT_USAGE_OUTCOMES"] = path
        try:
            moved = su.route("builder", snap(), NOW)
            assert moved["seat"] == "grok", moved
        finally:
            if old is None:
                os.environ.pop("SEAT_USAGE_OUTCOMES", None)
            else:
                os.environ["SEAT_USAGE_OUTCOMES"] = old
    restored = su.route("builder", snap(), NOW)
    assert restored["seat"] == "cursor", restored


@test
def a_pool_added_only_in_config_can_win():
    assert "Spare" not in BIN.read_text()
    with tempfile.TemporaryDirectory() as d:
        src = json.loads(Path(su.pools_file()).read_text())
        src["pools"]["spare"] = {
            "enabled": True,
            "seat": "cursor",
            "label": "Spare",
            "family": "grok",
            "models": ["spare-model"],
            "serves": ["grok"],
            "only": [],
            "cost_tier": 1,
            "pace": True,
            "lose_it": False,
            "variant": "fast",
            "effort": "high",
            "usage": {
                "used": "cursor_auto_pct",
                "reset": "cursor_reset_ts",
                "window_key": "cursor_window_s",
                "as_of": "cursor",
            },
            "facts": {"context": 1000, "limits": "test pool", "window": "month"},
            "strengths": {
                "builder": {"provider": 0.99, "prior": 0.99},
                "chair": {"provider": 0.99, "prior": 0.99},
            },
        }
        path = os.path.join(d, "pools.json")
        Path(path).write_text(json.dumps(src))
        try:
            su.load_config(pools_path=path)
            assert "spare" in su.POOLS and "muse" not in su.POOLS
            routed = su.route("builder", snap(), NOW)
            assert routed["seat"] == "cursor" and routed["family"] == "grok", routed
            assert routed["reason"].startswith("Spare under pace: score "), routed
            assert "fit 0.99" in routed["reason"], routed
        finally:
            su.load_config()
    restored = su.route("builder", snap(), NOW)
    assert restored["reason"].startswith("Cursor Models under pace:"), restored


@test
def disabled_pool_is_config_only():
    raw = json.loads(Path(su.pools_file()).read_text())
    assert raw["pools"]["muse_week"]["enabled"] is True
    assert raw["pools"]["muse_5h"]["enabled"] is True
    assert "muse_week" in su.POOLS and "muse" not in su.ROLES
    with tempfile.TemporaryDirectory() as d:
        src = json.loads(Path(su.pools_file()).read_text())
        src["pools"]["spare"] = {
            "enabled": False,
            "seat": "cursor",
            "label": "Spare",
            "usage": {"used": "cursor_auto_pct", "reset": "cursor_reset_ts"},
        }
        path = os.path.join(d, "pools.json")
        Path(path).write_text(json.dumps(src))
        try:
            su.load_config(pools_path=path)
            assert "spare" not in su.POOLS
            assert "muse_week" in su.POOLS
        finally:
            su.load_config()


@test
def grok_cookie_reads_seat_usage_op_ref():
    old_ref = os.environ.get("SEAT_USAGE_OP_REF")
    old_op = os.environ.get("SEAT_USAGE_OP")
    os.environ.pop("SEAT_USAGE_OP_REF", None)
    assert su.op_ref() == "op://seat-usage/grok web cookie/credential"
    os.environ["SEAT_USAGE_OP_REF"] = ""
    assert su.op_ref() == "op://seat-usage/grok web cookie/credential"
    seen = {}

    def which(name):
        return "/tmp/fake-op" if name == "op.exe" else None

    class Result:
        returncode = 0
        stdout = "sso-cookie\r\n"

    def run(cmd, capture_output, text, timeout, **kw):
        seen["cmd"] = cmd
        return Result()

    import shutil
    import subprocess as sp
    old_which, old_run = shutil.which, sp.run
    old_tried = list(su.COOKIE_TRIED)
    old_cache_dir = su.CACHE_DIR
    os.environ["SEAT_USAGE_OP_REF"] = "op://other/item/credential"
    os.environ.pop("SEAT_USAGE_OP", None)
    shutil.which, sp.run = which, run
    su.COOKIE_TRIED = []
    tmp = tempfile.TemporaryDirectory()
    su.CACHE_DIR = tmp.name
    try:
        assert su.op_ref() == "op://other/item/credential"
        assert su.grok_cookie() == "sso-cookie"
        assert seen["cmd"] == ["/tmp/fake-op", "read", "op://other/item/credential"], seen
        cache = Path(su.cookie_cache_path())
        assert cache.read_text() == "sso-cookie"
        assert cache.stat().st_mode & 0o777 == 0o600, oct(cache.stat().st_mode)

        # A fresh cache answers without a 1Password read.
        seen.clear()
        assert su.grok_cookie() == "sso-cookie"
        assert "cmd" not in seen, seen

        # A stale cache triggers a read; a failed read still returns the stale copy.
        old = time.time() - su.COOKIE_TTL_S - 60
        os.utime(cache, (old, old))
        Result.returncode, Result.stdout = 1, ""
        assert su.grok_cookie() == "sso-cookie"
        assert seen["cmd"][1] == "read", seen
    finally:
        su.CACHE_DIR = old_cache_dir
        tmp.cleanup()
        shutil.which, sp.run = old_which, old_run
        su.COOKIE_TRIED[:] = old_tried
        if old_ref is None:
            os.environ.pop("SEAT_USAGE_OP_REF", None)
        else:
            os.environ["SEAT_USAGE_OP_REF"] = old_ref
        if old_op is None:
            os.environ.pop("SEAT_USAGE_OP", None)
        else:
            os.environ["SEAT_USAGE_OP"] = old_op


@test
def test_record_pool_caps_history_to_its_newest_whole_lines():
    saved = {n: getattr(su, n) for n in ("HISTORY", "HISTORY_MAX_BYTES", "HISTORY_KEEP_BYTES")}
    try:
        with tempfile.TemporaryDirectory() as d:
            su.HISTORY = os.path.join(d, "state", "history.jsonl")
            su.HISTORY_MAX_BYTES, su.HISTORY_KEEP_BYTES = 2000, 1000
            for i in range(40):
                su.record_pool("codex", "chatgpt weekly", i, NOW + WEEK, WEEK, "test")
            size = os.path.getsize(su.HISTORY)
            assert size <= 2000 + 400, size
            rows = [json.loads(line) for line in open(su.HISTORY)]
            assert rows[-1]["used_pct"] == 39, rows[-1]
            used = [r["used_pct"] for r in rows]
            assert used == list(range(used[0], 40)), used
            assert 0 not in used
            assert os.listdir(os.path.dirname(su.HISTORY)) == ["history.jsonl"]
            # Under the cap, the file is only appended to.
            su.HISTORY_MAX_BYTES = 10 ** 6
            before = open(su.HISTORY).read()
            su.record_pool("codex", "chatgpt weekly", 40, NOW + WEEK, WEEK, "test")
            after = open(su.HISTORY).read()
            assert after.startswith(before) and json.loads(after.splitlines()[-1])["used_pct"] == 40
    finally:
        for n, v in saved.items():
            setattr(su, n, v)


@test
def test_run_draws_diffs_history_around_a_run():
    hist = [
        {"pool": "chatgpt weekly", "used_pct": 10, "reset_ts": NOW + WEEK, "read_ts": NOW - 400},
        {"pool": "chatgpt weekly", "used_pct": 13, "reset_ts": NOW + WEEK, "read_ts": NOW + 30},
        {"pool": "grok week", "used_pct": 50, "reset_ts": NOW, "read_ts": NOW - 400},
        {"pool": "grok week", "used_pct": 0, "reset_ts": NOW + WEEK, "read_ts": NOW + 30},
    ]
    runs = [
        {"phase": "before", "run_id": "r", "ts": NOW - 300},
        {"phase": "after", "run_id": "r", "kind": "review", "ts": NOW, "elapsed_s": 300,
         "ran": {"voters": [{"name": "codex"}, {"name": "grok"}, {"name": "muse"}]}},
    ]
    got = su.run_draws(runs, hist)
    assert [(d["leg"], d["draw"]) for d in got] == [("codex", 3)], got


@test
def cursor_fresh_cycle_without_total_spend():
    # A fresh billing cycle omits planUsage.totalSpend (2026-10-15); it raised TypeError.
    import base64
    body = {"billingCycleStart": str((NOW - 3600) * 1000), "billingCycleEnd": str((NOW + MONTH) * 1000),
            "planUsage": {"limit": 7000, "remaining": 7000, "autoPercentUsed": 0, "apiPercentUsed": 0},
            "spendLimitUsage": {"individualLimit": 15000, "individualRemaining": 15000}}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    claims = base64.urlsafe_b64encode(json.dumps({"sub": "auth0|user_x"}).encode()).decode().rstrip("=")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".config" / "cursor").mkdir(parents=True)
        (Path(d) / ".config" / "cursor" / "auth.json").write_text(json.dumps({"accessToken": f"h.{claims}.s"}))
        saved = su.H, su.get, su.grok_bot, su.urllib.request.urlopen
        su.H = d
        su.get = lambda url, hdr=None, **kw: {"membershipType": "pro_plus", "subscriptionStatus": "active"}
        su.grok_bot = lambda tok: "grokbot  | stub"
        su.urllib.request.urlopen = lambda req, timeout=None: Resp(json.dumps(body).encode())
        try:
            out = su.cursor()
        finally:
            su.H, su.get, su.grok_bot, su.urllib.request.urlopen = saved
    assert "usage failed" not in out[0], out
    assert "$0.00 spent" in out[0], out


AGY_FIXTURE = HERE / "fixtures" / "agy-usage.tsv"
AGY_NOW = datetime.fromisoformat("2026-10-20T01:39:26+00:00").timestamp()


def _agy_epoch(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


@contextlib.contextmanager
def google_sandbox(interval=0):
    saved = {name: getattr(su, name) for name in ("CACHE", "CACHE_DIR", "HISTORY", "USAGE_MIN_INTERVAL_S", "SUMMARY")}
    try:
        with tempfile.TemporaryDirectory() as d:
            su.CACHE_DIR = d
            su.CACHE = os.path.join(d, "latest.json")
            su.HISTORY = os.path.join(d, "history.jsonl")
            su.USAGE_MIN_INTERVAL_S = interval
            su.SUMMARY = {"ts": AGY_NOW, "as_of": {}, "v": 1}
            yield d
    finally:
        for name, value in saved.items():
            setattr(su, name, value)


def _write_agy(directory, text, code=0):
    path = Path(directory) / "agy"
    path.write_text("#!/bin/sh\ncat <<'END'\n%s\nEND\nexit %d\n" % (text, code))
    path.chmod(0o755)
    return str(path)


@test
def agy_fixture_converts_remaining_to_used():
    text = AGY_FIXTURE.read_text()
    rows = {(item["pool"], item["window"]): item for item in su.parse_agy_usage(text)}
    assert set(rows) == {
        ("gemini", "week"), ("gemini", "5h"),
        ("claudegpt", "week"), ("claudegpt", "5h"),
        ("argon", "week"),
    }, rows
    assert rows[("gemini", "week")]["used"] == 0
    assert rows[("gemini", "5h")]["used"] == 0
    assert rows[("claudegpt", "week")]["used"] == 0
    assert rows[("claudegpt", "5h")]["used"] == 0
    assert rows[("argon", "week")]["used"] == 1
    assert rows[("gemini", "week")]["reset"] == _agy_epoch("2026-10-27T01:39:26Z")
    assert rows[("gemini", "5h")]["reset"] == _agy_epoch("2026-10-20T06:39:26Z")
    assert rows[("argon", "week")]["reset"] == _agy_epoch("2026-10-27T01:38:40Z")
    # A percent outside 0-100 is not a reading, and it does not replace a real row.
    junk = text + "Gemini Models\tWeekly Limit Remaining\t150%\t2026-10-27T01:39:26Z\n"
    again = {(item["pool"], item["window"]): item for item in su.parse_agy_usage(junk)}
    assert again[("gemini", "week")]["used"] == 0
    assert su.parse_agy_usage("") == []
    assert su.parse_agy_usage("not a table\n") == []


@test
def missing_agy_is_an_error_and_invents_no_number():
    with google_sandbox():
        fresh = {"v": 1, "as_of": {}, "errors": {}}
        su.apply_google(fresh, AGY_NOW, binary="/no/such/agy")
        assert fresh["errors"]["google"] == "agy not found", fresh
        assert "google" not in fresh["as_of"], fresh
        assert not any(key.startswith("google_") for key in fresh), fresh
        assert su.google_line(fresh, AGY_NOW) == "google   | usage failed: agy not found"
        # A later failure keeps the last real numbers and does not write a new one.
        kept = {"v": 1, "as_of": {"google": AGY_NOW}, "google_argon_week": 1, "google_gemini_week": 0}
        su.apply_google(kept, AGY_NOW, binary="/no/such/agy")
        assert kept["google_argon_week"] == 1 and kept["google_gemini_week"] == 0, kept
        assert "google" not in kept["as_of"], kept
        assert kept["errors"]["google"] == "agy not found", kept


@test
def agy_not_signed_in_and_nonzero_exit_are_errors():
    with google_sandbox() as d:
        signed_out = Path(d) / "signed-out"
        signed_out.write_text("#!/bin/sh\necho 'You are currently not signed in.' >&2\nexit 1\n")
        signed_out.chmod(0o755)
        summary = {"v": 1, "as_of": {}}
        su.apply_google(summary, AGY_NOW, binary=str(signed_out))
        assert summary["errors"]["google"] == "not signed in", summary
        assert not any(key.startswith("google_") for key in summary), summary
        failed = Path(d) / "failed"
        failed.write_text("#!/bin/sh\necho 'quota service down' >&2\nexit 3\n")
        failed.chmod(0o755)
        other = {"v": 1, "as_of": {}}
        su.apply_google(other, AGY_NOW, binary=str(failed))
        assert other["errors"]["google"] == "exit 3: quota service down", other
        assert not any(key.startswith("google_") for key in other), other


@test
def agy_fixture_fills_the_cache_and_the_floor_skips_the_next_call():
    with google_sandbox(interval=600) as d:
        calls = Path(d) / "calls"
        script = Path(d) / "agy"
        script.write_text("#!/bin/sh\necho x >> %s\ncat %s\n" % (calls, AGY_FIXTURE))
        script.chmod(0o755)
        summary = {"v": 1, "as_of": {}, "errors": {"google": "stale"}}
        su.apply_google(summary, AGY_NOW, binary=str(script))
        assert calls.read_text().count("x") == 1, calls.read_text()
        assert summary["google_argon_week"] == 1, summary
        assert summary["google_gemini_week"] == 0 and summary["google_gemini_5h"] == 0, summary
        assert summary["google_claudegpt_week"] == 0 and summary["google_claudegpt_5h"] == 0, summary
        assert summary["google_argon_week_reset_ts"] == _agy_epoch("2026-10-27T01:38:40Z")
        assert summary["google_gemini_5h_reset_ts"] == _agy_epoch("2026-10-20T06:39:26Z")
        assert summary["as_of"]["google"] == AGY_NOW, summary["as_of"]
        assert "google" not in summary["errors"], summary
        assert summary["google_argon_week_pace"] == "on", summary["google_argon_week_pace"]
        line = su.google_line(summary, AGY_NOW)
        assert line.startswith("google   | AI Pro | Argon weekly 1%:"), line
        assert "Gemini weekly 0%:" in line and "Gemini 5h 0% (resets in 5.0h)" in line, line
        assert "Claude/GPT weekly 0%:" in line and "Claude/GPT 5h 0% (resets in 5.0h)" in line, line
        su.apply_google(summary, AGY_NOW + 10, binary=str(script))
        assert calls.read_text().count("x") == 1, "live check ran inside the floor"
        stored = json.load(open(su._agy_rate_path()))
        stored["next"] = AGY_NOW - 1
        with open(su._agy_rate_path(), "w") as handle:
            json.dump(stored, handle)
        su.apply_google(summary, AGY_NOW + 20, binary=str(script))
        assert calls.read_text().count("x") == 2, calls.read_text()


@test
def cached_output_does_not_run_agy():
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / "agy"
        marker = Path(d) / "ran"
        script.write_text("#!/bin/sh\necho ran > %s\n" % marker)
        script.chmod(0o755)
        cache = Path(d) / "cache"
        cache.mkdir()
        (cache / "latest.json").write_text(json.dumps({
            "ts": time.time(), "v": 1, "as_of": {}, "lines": ["claude   | cached"], "burn": [],
        }))
        env = os.environ.copy()
        env["SEAT_USAGE_AGY"] = str(script)
        env["SEAT_USAGE_CACHE_DIR"] = str(cache)
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        env.pop("COUNCIL_VOTER", None)
        proc = subprocess.run([sys.executable, str(BIN), "--cached"], capture_output=True, text=True, env=env, timeout=15)
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        assert "claude   | cached" in proc.stdout, proc.stdout
        assert "google" not in proc.stdout, proc.stdout
        assert not marker.exists(), "cached run executed agy"


@test
def grok_cookie_op_cannot_be_stopped_by_a_terminal():
    """`op` that touches the terminal must not freeze a run started under a tty.

    The child runs as a background process group of a pty, as `timeout` puts
    it. A fake op that reads /dev/tty would get SIGTTIN and stop the whole
    group, so grok_cookie() would never return. Detached, op has no terminal
    and fails at once, and the stale cached cookie is returned.
    """
    import pty
    import select
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cache = tmp / "cache"
        cache.mkdir()
        cookie = cache / "grok-cookie"
        cookie.write_text("stale-cookie")
        old = time.time() - su.COOKIE_TTL_S - 60
        os.utime(cookie, (old, old))
        fake = tmp / "op"
        fake.write_text("#!/bin/sh\nread line < /dev/tty || exit 1\necho fresh-cookie\n")
        fake.chmod(0o755)
        helper = tmp / "helper.py"
        helper.write_text(
            "import importlib.machinery, importlib.util, os, sys, time\n"
            "pid = os.fork()\n"   # the pty child leads its session; its child can take a new group
            "if pid:\n    os._exit(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]))\n"
            "os.setpgid(0, 0)\n"   # a background group of the pty, like `timeout`
            "print('GROUP', os.getpid(), flush=True)\n"
            "loader = importlib.machinery.SourceFileLoader('su', sys.argv[1])\n"
            "spec = importlib.util.spec_from_loader('su', loader)\n"
            "m = importlib.util.module_from_spec(spec); loader.exec_module(m)\n"
            "t = time.monotonic(); v = m.grok_cookie()\n"
            "print('RETURNED', v, round(time.monotonic() - t, 2), m.COOKIE_TRIED, flush=True)\n")
        env = os.environ.copy()
        env.update(SEAT_USAGE_CACHE_DIR=str(cache), SEAT_USAGE_OP=str(fake), QUARTERMASTER_NO_UPDATE="1")
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execve(sys.executable, [sys.executable, str(helper), str(BIN)], env)
            finally:
                os._exit(127)
        out, deadline = b"", time.monotonic() + 8
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    out += chunk
                if b"RETURNED" in out and b"\n" in out.split(b"RETURNED", 1)[1]:
                    break
        finally:
            groups = [pid] + [int(g) for g in re.findall(rb"GROUP (\d+)", out)]
            for group in groups:
                try:
                    os.killpg(group, 9)
                except OSError:
                    pass
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.waitpid(pid, 0)
            os.close(fd)
        text = out.decode(errors="replace")
        assert "RETURNED stale-cookie" in text, f"grok_cookie did not return within 8s under a tty: {text!r}"
        assert "op read failed" in text, text


FAKE_OP_TOKEN = "ops_FAKE_TEST_TOKEN_not_a_secret_7c1e"


@contextlib.contextmanager
def _fake_op_run(token_file, returncode=0, stdout="sso-cookie\n"):
    """grok_cookie() with which/run faked, an empty cache and SEAT_USAGE_OP_TOKEN_FILE set."""
    import shutil
    import subprocess as sp
    seen = {}

    class Result:
        pass

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        r = Result()
        r.returncode, r.stdout, r.stderr = returncode, stdout, ""
        return r

    saved = {k: os.environ.get(k) for k in ("SEAT_USAGE_OP", "SEAT_USAGE_OP_REF", "SEAT_USAGE_OP_TOKEN_FILE", "OP_SERVICE_ACCOUNT_TOKEN")}
    old_which, old_run, old_tried, old_cache_dir = shutil.which, sp.run, list(su.COOKIE_TRIED), su.CACHE_DIR
    tmp = tempfile.TemporaryDirectory()
    try:
        for k in saved:
            os.environ.pop(k, None)
        if token_file is not None:
            os.environ["SEAT_USAGE_OP_TOKEN_FILE"] = str(token_file)
        shutil.which = lambda name: "/tmp/fake-op" if name == "op.exe" else None
        sp.run = run
        su.COOKIE_TRIED = []
        su.CACHE_DIR = tmp.name
        yield seen
    finally:
        su.CACHE_DIR = old_cache_dir
        tmp.cleanup()
        shutil.which, sp.run = old_which, old_run
        su.COOKIE_TRIED = old_tried
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@test
def grok_cookie_default_op_ref_is_the_seat_usage_vault():
    old = os.environ.pop("SEAT_USAGE_OP_REF", None)
    try:
        assert su.DEFAULT_OP_REF == "op://seat-usage/grok web cookie/credential", su.DEFAULT_OP_REF
        assert su.op_ref() == "op://seat-usage/grok web cookie/credential"
        os.environ["SEAT_USAGE_OP_REF"] = "op://other/item/credential"
        assert su.op_ref() == "op://other/item/credential"
    finally:
        if old is None:
            os.environ.pop("SEAT_USAGE_OP_REF", None)
        else:
            os.environ["SEAT_USAGE_OP_REF"] = old


@test
def grok_cookie_passes_token_file_to_op_env():
    with tempfile.TemporaryDirectory() as d:
        tok = Path(d) / "op-token"
        tok.write_text(f"  {FAKE_OP_TOKEN}\n")
        tok.chmod(0o600)
        err = io.StringIO()
        with _fake_op_run(tok) as seen, contextlib.redirect_stderr(err):
            assert su.grok_cookie() == "sso-cookie"
        kw = seen["kw"]
        env = kw.get("env")
        assert env is not None, "op ran without an env built from the token file"
        assert env["OP_SERVICE_ACCOUNT_TOKEN"] == FAKE_OP_TOKEN
        assert env.get("PATH") == os.environ.get("PATH"), "env must be a copy of os.environ"
        assert "OP_SERVICE_ACCOUNT_TOKEN" not in os.environ, "token leaked into this process's environ"
        assert kw.get("stdin") == subprocess.DEVNULL and kw.get("start_new_session") is True, kw
        assert FAKE_OP_TOKEN not in " ".join(seen["cmd"]), "token in argv"
        assert FAKE_OP_TOKEN not in err.getvalue()


@test
def grok_cookie_without_token_file_leaves_op_env_alone():
    with tempfile.TemporaryDirectory() as d:
        for case in ("missing", "empty"):
            tok = Path(d) / "op-token"
            if case == "empty":
                tok.write_text("  \n")
            with _fake_op_run(tok) as seen:
                assert su.grok_cookie() == "sso-cookie"
            kw = seen["kw"]
            assert kw.get("env") is None, f"{case}: env passed: {sorted(kw)}"
            assert kw.get("stdin") == subprocess.DEVNULL and kw.get("start_new_session") is True, kw
        # Default path is under HOME; with none there, op inherits the environment.
        old_home = os.environ.get("HOME")
        old_file = os.environ.pop("SEAT_USAGE_OP_TOKEN_FILE", None)
        os.environ["HOME"] = d
        try:
            assert su.op_token_file() == os.path.join(d, ".config", "seat-usage", "op-token")
            with _fake_op_run(None) as seen:
                assert su.grok_cookie() == "sso-cookie"
            assert seen["kw"].get("env") is None, "default token path absent, yet env passed"
            # ...and a token at the default path is used.
            dflt = Path(d) / ".config" / "seat-usage" / "op-token"
            dflt.parent.mkdir(parents=True)
            dflt.write_text(FAKE_OP_TOKEN)
            dflt.chmod(0o600)
            with _fake_op_run(None) as seen:
                assert su.grok_cookie() == "sso-cookie"
            assert seen["kw"]["env"]["OP_SERVICE_ACCOUNT_TOKEN"] == FAKE_OP_TOKEN
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_file is not None:
                os.environ["SEAT_USAGE_OP_TOKEN_FILE"] = old_file


@test
def grok_cookie_token_never_reaches_output():
    """A real child: fake op gets the token, fails, and nothing printed carries it."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tok = tmp / "op-token"
        tok.write_text(FAKE_OP_TOKEN + "\n")
        tok.chmod(0o644)   # loose mode: any warning must not echo the token
        marker = tmp / "marker"
        fake = tmp / "op"
        fake.write_text(
            "#!/bin/sh\n"
            f"if [ \"$OP_SERVICE_ACCOUNT_TOKEN\" = \"{FAKE_OP_TOKEN}\" ]; then echo token-ok > \"{marker}\"; fi\n"
            "echo \"[ERROR] token $OP_SERVICE_ACCOUNT_TOKEN rejected\" >&2\n"
            "exit 1\n")
        fake.chmod(0o755)
        helper = tmp / "helper.py"
        helper.write_text(
            "import importlib.machinery, importlib.util, sys\n"
            "loader = importlib.machinery.SourceFileLoader('su', sys.argv[1])\n"
            "spec = importlib.util.spec_from_loader('su', loader)\n"
            "m = importlib.util.module_from_spec(spec); loader.exec_module(m)\n"
            "print('RETURNED', m.grok_cookie(), m.COOKIE_TRIED, flush=True)\n")
        env = {k: v for k, v in os.environ.items() if k != "OP_SERVICE_ACCOUNT_TOKEN"}
        env.update(SEAT_USAGE_CACHE_DIR=str(tmp / "cache"), SEAT_USAGE_OP=str(fake),
                   SEAT_USAGE_OP_TOKEN_FILE=str(tok), HOME=str(tmp), QUARTERMASTER_NO_UPDATE="1")
        proc = subprocess.run([sys.executable, str(helper), str(BIN)], env=env, capture_output=True, text=True, timeout=20)
        out = proc.stdout + proc.stderr
        assert "RETURNED None" in proc.stdout, out
        assert "read failed" in proc.stdout, out
        assert marker.exists() and marker.read_text().strip() == "token-ok", "fake op did not receive the token"
        assert FAKE_OP_TOKEN not in out, "token printed"


@test
def op_token_file_bom_fifo_and_mode_warning():
    with tempfile.TemporaryDirectory() as d:
        tok = Path(d) / "op-token"
        saved = os.environ.get("SEAT_USAGE_OP_TOKEN_FILE")
        os.environ["SEAT_USAGE_OP_TOKEN_FILE"] = str(tok)
        try:
            tok.write_bytes(b"\xef\xbb\xbf" + FAKE_OP_TOKEN.encode() + b"\r\n")
            tok.chmod(0o600)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                env = su.op_env()
            assert env["OP_SERVICE_ACCOUNT_TOKEN"] == FAKE_OP_TOKEN, "BOM or CRLF kept"
            assert err.getvalue() == "", err.getvalue()
            tok.chmod(0o644)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                assert su.op_env()["OP_SERVICE_ACCOUNT_TOKEN"] == FAKE_OP_TOKEN
            assert "chmod 600" in err.getvalue() and "644" in err.getvalue(), err.getvalue()
            assert FAKE_OP_TOKEN not in err.getvalue()
            tok.unlink()
            os.mkfifo(tok)   # open() would block on a FIFO with no writer
            assert su.op_env() is None
        finally:
            if saved is None:
                os.environ.pop("SEAT_USAGE_OP_TOKEN_FILE", None)
            else:
                os.environ["SEAT_USAGE_OP_TOKEN_FILE"] = saved


@test
def wsl_env_names_the_token_for_op_exe_only():
    base = {"PATH": "/usr/bin", "OP_SERVICE_ACCOUNT_TOKEN": FAKE_OP_TOKEN}
    assert su.wsl_env(None, "/mnt/c/x/op.exe") is None
    assert su.wsl_env(base, "/usr/bin/op") is base
    e = su.wsl_env(base, "/mnt/c/x/op.exe")
    assert e["WSLENV"] == "OP_SERVICE_ACCOUNT_TOKEN" and "WSLENV" not in base, e
    e = su.wsl_env(dict(base, WSLENV="FOO/p:BAR"), "C:/op.EXE")
    assert e["WSLENV"] == "FOO/p:BAR:OP_SERVICE_ACCOUNT_TOKEN", e
    e = su.wsl_env(dict(base, WSLENV="OP_SERVICE_ACCOUNT_TOKEN/u"), "op.exe")
    assert e["WSLENV"] == "OP_SERVICE_ACCOUNT_TOKEN/u", e
    # Through grok_cookie: the faked op.exe path gets WSLENV.
    with tempfile.TemporaryDirectory() as d:
        tok = Path(d) / "op-token"
        tok.write_text(FAKE_OP_TOKEN)
        tok.chmod(0o600)
        import shutil
        with _fake_op_run(tok) as seen:
            old_which = shutil.which
            shutil.which = lambda name: "/mnt/c/fake/op.exe" if name == "op.exe" else None
            try:
                assert su.grok_cookie() == "sso-cookie"
            finally:
                shutil.which = old_which
        assert "OP_SERVICE_ACCOUNT_TOKEN" in seen["kw"]["env"]["WSLENV"].split(":"), seen["kw"]["env"].get("WSLENV")


def _pull_only_sandbox(d):
    """HOME, cache, and a PATH of trap binaries that record any call."""
    home = os.path.join(d, "home")
    cache_dir = os.path.join(home, ".cache", "seat-usage")
    os.makedirs(cache_dir)
    os.makedirs(os.path.join(home, ".config", "seat-usage"))
    traps = os.path.join(d, "traps")
    os.makedirs(traps)
    called = os.path.join(d, "called")
    for name in ("op", "op.exe", "codex", "claude", "cursor-agent", "agy", "muse", "curl", "gh"):
        path = os.path.join(traps, name)
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\necho {name} >> '{called}'\nexit 1\n")
        os.chmod(path, 0o755)
    cache = os.path.join(cache_dir, "latest.json")
    body = json.dumps(snap(ts=time.time() - 600, lines=["grok     | SuperGrok active | week 21%"]))
    with open(cache, "w") as f:
        f.write(body)
    os.utime(cache, (1_700_000_000, 1_700_000_000))
    env = {"PATH": traps + ":/usr/bin:/bin", "HOME": home, "PYTHONDONTWRITEBYTECODE": "1",
           "SEAT_USAGE_CACHE_DIR": cache_dir, "SEAT_USAGE_OP_TOKEN_FILE": "/nonexistent/op-token"}
    return env, cache, body, called, home


def _pull_only_runs(env, cache, body, called):
    argvs = ([], ["--quiet"], ["--route", "builder"], ["--route", "lineup", "--json", "--kind", "review"],
             ["--route", "ask"], ["--route", "host"], ["--route"], ["--cached"])
    outs = []
    for argv in argvs:
        r = subprocess.run([sys.executable, str(BIN)] + argv, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (argv, r.returncode, r.stderr[-400:])
        outs.append((argv, r.stdout))
        assert not os.path.exists(called), (argv, open(called).read())
        with open(cache) as f:
            assert f.read() == body, argv
        assert os.stat(cache).st_mtime == 1_700_000_000, argv
    return outs


@test
def pull_only_marker_never_reads_live_or_writes_cache():
    with tempfile.TemporaryDirectory() as d:
        env, cache, body, called, home = _pull_only_sandbox(d)
        open(os.path.join(home, ".config", "seat-usage", "pull-only"), "w").close()
        outs = dict((" ".join(a), o) for a, o in _pull_only_runs(env, cache, body, called))
        assert "[seat-usage] pull-only cached 10m ago" in outs[""], outs[""]
        assert outs["--quiet"] == "", outs["--quiet"]
        assert outs["--route builder"].startswith("route: builder"), outs["--route builder"]
        json.loads(outs["--route lineup --json --kind review"])
        env2 = dict(env, QUARTERMASTER_AUTO_UPDATE="1")
        r = subprocess.run([sys.executable, str(BIN), "--route", "builder"], env=env2, capture_output=True, text=True, timeout=60)
        assert "[seat-usage] pull-only cached 10m ago" in r.stderr, r.stderr
        assert not os.path.exists(os.path.join(home, ".cache", "quartermaster", "seat-usage-update"))
        os.remove(cache)
        r = subprocess.run([sys.executable, str(BIN)], env=env2, capture_output=True, text=True, timeout=60)
        assert "pull-only: no cached line" in r.stdout and "seat-usage-push --pull" in r.stdout, r.stdout
        assert not os.path.exists(called), open(called).read()


@test
def pull_only_env_never_reads_live_or_writes_cache():
    with tempfile.TemporaryDirectory() as d:
        env, cache, body, called, _ = _pull_only_sandbox(d)
        env["SEAT_USAGE_PULL_ONLY"] = "1"
        outs = dict((" ".join(a), o) for a, o in _pull_only_runs(env, cache, body, called))
        assert "pull-only cached" in outs[""], outs[""]


@test
def pull_only_in_process_skips_readers_and_op():
    calls = []
    saved = {n: getattr(su, n) for n in ("claude", "codex", "cursor", "grok", "write_cache")}
    old_env = os.environ.get("SEAT_USAGE_PULL_ONLY")
    old_run = subprocess.run
    def trap(name):
        def f(*a, **k):
            calls.append(name)
            return []
        f.__name__ = name
        return f
    try:
        for n in saved:
            setattr(su, n, trap(n))
        subprocess.run = trap("subprocess.run")
        os.environ["SEAT_USAGE_PULL_ONLY"] = "1"
        su.refresh(["claude", "chatgpt", "cursor", "grok"])
        su.full_run(quiet=True)
        assert su.grok_cookie() is None
        assert su.refresh_muse_usage() is False
        assert calls == [], calls
    finally:
        subprocess.run = old_run
        for n, f in saved.items():
            setattr(su, n, f)
        if old_env is None:
            os.environ.pop("SEAT_USAGE_PULL_ONLY", None)
        else:
            os.environ["SEAT_USAGE_PULL_ONLY"] = old_env


@test
def pull_only_off_still_runs_readers():
    calls = []
    saved = {n: getattr(su, n) for n in ("claude", "codex", "cursor", "grok")}
    old_env = os.environ.pop("SEAT_USAGE_PULL_ONLY", None)
    old_file = os.environ.get("SEAT_USAGE_PULL_ONLY_FILE")
    with tempfile.TemporaryDirectory() as d:
        old_cache, old_dir, old_summary = su.CACHE, su.CACHE_DIR, su.SUMMARY
        os.environ["SEAT_USAGE_PULL_ONLY_FILE"] = os.path.join(d, "no-marker")
        su.CACHE, su.CACHE_DIR = os.path.join(d, "latest.json"), d
        def trap(name):
            def f(*a, **k):
                calls.append(name)
                return []
            f.__name__ = name
            return f
        try:
            for n in saved:
                setattr(su, n, trap(n))
            assert su.pull_only() is False
            su.refresh(["claude", "chatgpt", "cursor", "grok"])
            assert calls == ["claude", "codex", "cursor", "grok"], calls
            assert os.path.exists(su.CACHE)
        finally:
            for n, f in saved.items():
                setattr(su, n, f)
            su.CACHE, su.CACHE_DIR, su.SUMMARY = old_cache, old_dir, old_summary
            if old_env is not None:
                os.environ["SEAT_USAGE_PULL_ONLY"] = old_env
            if old_file is None:
                os.environ.pop("SEAT_USAGE_PULL_ONLY_FILE", None)
            else:
                os.environ["SEAT_USAGE_PULL_ONLY_FILE"] = old_file


def _cached_out(d, extra_env=None):
    with tempfile.TemporaryDirectory() as td:
        cache = os.path.join(td, "latest.json")
        with open(cache, "w") as f:
            json.dump(d, f)
        env = {"PATH": "/usr/bin:/bin", "HOME": td, "PYTHONDONTWRITEBYTECODE": "1", "SEAT_USAGE_CACHE_DIR": td,
               "SEAT_USAGE_OP_TOKEN_FILE": "/nonexistent/op-token"}
        env.update(extra_env or {})
        r = subprocess.run([sys.executable, str(BIN), "--cached"], env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        with open(cache) as f:
            assert json.load(f) == d
        return r.stdout


def _numbers_only(now):
    return {"v": 1, "ts": now - 60, "as_of": {k: now - 60 for k in ("claude", "chatgpt", "cursor", "grokbot", "grok")},
            "claude_5h": 3, "claude_5h_reset_ts": now + 3600, "claude_week": 64, "claude_week_reset_ts": now + 17 * 3600,
            "codex": 9, "codex_reset_ts": now + 6 * 86400, "codex_window_s": WEEK, "codex_5h": 15, "codex_5h_reset_ts": now + 4 * 3600,
            "cursor_pct": 30.7, "cursor_auto_pct": 6.8, "cursor_api_pct": 30.7, "cursor_reset_ts": now + 26 * 86400, "cursor_window_s": 31 * 86400,
            "bot_pct": 96.7, "bot_reset_ts": now + 2 * 86400, "bot_window_s": WEEK,
            "grok_pct": 21, "grok_reset_ts": now + 2 * 86400, "grok_window_s": WEEK,
            "grok_renews_ts": now + 46 * 86400, "grok_renews": False, "grok_exhausted": False, "bot_exhausted": False}


@test
def cached_numbers_only_renders_rows():
    out = _cached_out(_numbers_only(time.time()))
    assert "KeyError" not in out and "no cached line" not in out, out
    rows = {l.split("|")[0].strip(): l for l in out.splitlines()[1:]}
    assert rows["grok"].startswith("  grok     | sub ends in 46.0d | week 21% (resets in 2.0d; "), rows["grok"]
    assert rows["grok"].endswith("| no 402 since the last weekly reset"), rows["grok"]
    assert "weekly all 64%:" in rows["claude"] and "5h 3% (resets in 1.0h)" in rows["claude"], rows["claude"]
    assert rows["chatgpt"].startswith("  chatgpt  | weekly 9%: ") and "| 5h 15%: " in rows["chatgpt"], rows["chatgpt"]
    assert rows["cursor"].startswith("  cursor   | cursor models 7%, other models 31%: "), rows["cursor"]
    assert rows["grokbot"].startswith("  grokbot  | week 96.7%: "), rows["grokbot"]
    assert "spent" not in out and "SuperGrok" not in out and "Pro+" not in out, out


@test
def cached_numbers_only_pull_only_renders_rows_and_omits_absent_seats():
    d = _numbers_only(time.time())
    for k in [k for k in d if k.startswith(("claude", "codex", "cursor", "bot_"))]:
        d.pop(k)
    out = _cached_out(d, {"SEAT_USAGE_PULL_ONLY": "1"})
    assert out.startswith("[seat-usage] pull-only cached 1m ago"), out
    seats = [l.split("|")[0].strip() for l in out.splitlines()[1:]]
    assert "grok" in seats and "claude" not in seats and "chatgpt" not in seats and "cursor" not in seats, out
    assert "KeyError" not in out, out


@test
def cached_lines_present_unchanged():
    d = _numbers_only(time.time())
    d["lines"] = ["grok     | SuperGrok active | week 21% (live text)", "claude   | max active | weekly all 64%"]
    out = _cached_out(d)
    body = out.splitlines()[1:3]
    assert body == ["  grok     | SuperGrok active | week 21% (live text)", "  claude   | max active | weekly all 64%"], out
    assert "chatgpt" not in out and "cursor   |" not in out, out


@test
def cached_numbers_only_odd_values_and_flags():
    now = time.time()
    d = _numbers_only(now)
    d.update({"codex_window_s": None, "codex_reset_ts": now + 4 * 3600, "claude_week": float("nan"), "bot_exhausted": True,
              "grok_exhausted": True, "grok_402_h": 2.5, "cursor_pct": None, "bot_reset_ts": True})
    rows = {l.split("|")[0].strip(): l for l in lines_with(d, now)}
    assert "weekly" not in rows["chatgpt"].split("|")[1] and "elapsed" not in rows["chatgpt"].split("|")[1], rows["chatgpt"]
    assert "weekly all" not in rows["claude"], rows["claude"]
    assert rows["grokbot"].endswith("| ALLOWANCE USED") and "elapsed" not in rows["grokbot"], rows["grokbot"]
    assert rows["grok"].endswith("| EXHAUSTED: 402 2.5h ago, until the weekly reset"), rows["grok"]
    assert rows["cursor"].startswith("cursor   | cursor models 7%, other models 31%: "), rows["cursor"]
    d2 = _numbers_only(now)
    d2.pop("grok_exhausted")
    assert "402" not in [l for l in lines_with(d2, now) if l.startswith("grok ")][0]
    d3 = _numbers_only(now)
    d3.update({"codex_5h_reset_ts": now + 4 * 3600, "codex_renews": False, "codex_renews_ts": now + 3600})
    row = [l for l in lines_with(d3, now) if l.startswith("chatgpt")][0]
    assert "5h 15%: under pace" in row and "term ends in 1.0h" in row, row
    d4 = _numbers_only(now)
    d4.update({"codex_renews": False, "codex_renews_ts": float("-inf"), "claude_renews": False, "claude_renews_ts": now + 3600})
    rows4 = {l.split("|")[0].strip(): l for l in lines_with(d4, now)}
    assert "term ends" not in rows4["chatgpt"] and "term ends" not in rows4["claude"], rows4


def lines_with(d, now):
    return su.lines_from_numbers(d, now)


@test
def cached_empty_lines_list_unchanged():
    d = _numbers_only(time.time())
    d["lines"] = []
    out = _cached_out(d)
    assert "grok     |" not in out and "chatgpt  |" not in out, out


@test
def pull_only_plain_run_renders_pulled_copy():
    push = load("seat_usage_push_lines", HERE.parents[0] / "bin" / "seat-usage-push")
    now = time.time()
    d = _numbers_only(now)
    d["lines"] = ["grok     | SuperGrok active | live text"]
    copy = push.build_cache_copy(d) if hasattr(push, "build_cache_copy") else None
    assert isinstance(copy, dict) and "lines" not in copy, copy
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "latest.json"), "w") as f:
            json.dump(copy, f)
        env = {"PATH": "/usr/bin:/bin", "HOME": td, "PYTHONDONTWRITEBYTECODE": "1", "SEAT_USAGE_CACHE_DIR": td,
               "SEAT_USAGE_PULL_ONLY": "1", "SEAT_USAGE_OP_TOKEN_FILE": "/nonexistent/op-token"}
        r = subprocess.run([sys.executable, str(BIN)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("[seat-usage] pull-only cached"), r.stdout
    assert "KeyError" not in r.stdout and "| week 21% (resets in 2.0d; " in r.stdout, r.stdout

@test
def plans_line_names_each_vendor_plan():
    now = 1_800_000_000
    assert su.plans_line({}, now) is None, "no plan known, no line"
    s = {"claude_plan": "claude_ai", "codex_plan": "plus", "codex_renews": False, "codex_renews_ts": now + 86400,
         "cursor": "pro+", "cursor_renews": True, "grok": "SuperGrok", "grok_renews": False, "grok_renews_ts": now - 1,
         "google_argon_week": 0}
    ends = datetime.fromtimestamp(now + 86400).strftime("%Y-%m-%d")
    assert su.plans_line(s, now) == ("plans    | claude Pro | chatgpt Plus, ends " + ends
                                     + " | cursor Pro+ | grok SuperGrok | google AI Pro (label, not read)"), su.plans_line(s, now)
    line = su.plans_line({"claude_plan": "claude_max_5x", "cursor": "x_super", "codex_plan": "?"}, now)
    assert line == "plans    | claude Max 5x | cursor x_super", line


@test
def seat_rows_mirror_the_status_line_groups():
    s = snap(claude_max=False, codex_5h=5, codex_5h_reset_ts=NOW + 3600, bot_exhausted=True,
             google_argon_week=4, google_argon_week_reset_ts=NOW + 86400)
    s["as_of"] = dict(s["as_of"], google=NOW)
    rows = {g["id"]: g for g in su.seat_rows(s, NOW)}
    assert list(rows) == ["cl", "cx", "cu", "gk", "mu", "gg"], list(rows)
    assert rows["cl"]["tag"] == "pro"
    assert [b["name"] for b in rows["cl"]["bars"]] == ["5h", "wk", "fable"], rows["cl"]
    assert rows["cl"]["bars"][0]["stdin"] == "five_hour"
    assert [b["name"] for b in rows["cx"]["bars"]] == ["cx 5h", "cx"]
    assert rows["cx"]["bars"][0]["stale_name"] == "cx" and "stale_name" not in rows["cx"]["bars"][1]
    assert rows["cu"]["bars"][-1] == {"kind": "empty", "name": "bot", "seat": "grokbot", "reset_ts": s["bot_reset_ts"]}
    assert [(b["name"], b["pct"]) for b in rows["gg"]["bars"]] == [("argon", 4), ("c/g", 10)], rows["gg"]
    assert [g["label"] for g in rows.values()] == ["Claude", "Codex", "Cursor", "SuperGrok", "Muse", "Google"]
    assert [b["label"] for b in rows["gg"]["bars"]] == ["Argon weekly", "Claude/GPT weekly"]
    assert rows["gg"]["bars"][0]["families"] == ["Argon"], "chips come from pools.json's google_ai_pro block"
    assert "Opus" in rows["gg"]["bars"][1]["families"]
    # A failed read, an ended ChatGPT term, and no Google keys at all.
    s2 = {k: v for k, v in snap(codex_renews=False, codex_renews_ts=NOW - 60).items() if not k.startswith("google_")}
    s2["as_of"] = {k: v for k, v in s2["as_of"].items() if k != "grok"}
    rows = {g["id"]: g for g in su.seat_rows(s2, NOW)}
    assert rows["cx"]["bars"] == [{"kind": "off", "name": "cx", "seat": "chatgpt"}], rows["cx"]
    assert rows["gk"]["bars"] == [{"kind": "failed", "name": "gk", "seat": "grok"}], rows["gk"]
    assert "gg" not in rows


def _argon(s, used=10, **kw):
    s = dict(s)
    s["google_argon_week"] = used
    s["google_argon_week_reset_ts"] = kw.get("reset", NOW + 3 * 86400)
    return s


@test
def review_roster_excludes_spark_and_muse_contributor():
    for spec in su.REVIEW_ROSTER:
        model = spec["model"].lower()
        assert "spark" not in model and "contributor" not in model, spec
    assert su._review_model_barred("muse-spark-1")
    assert su._review_model_barred("wonder-contributor")
    assert su._review_model_barred("Muse Spark")
    assert not su._review_model_barred("wonder")
    assert not su._review_model_barred("gpt-6-astra")
    families = [spec["family"] for spec in su.REVIEW_ROSTER]
    assert families == ["fable", "astra", "grok", "argon", "wonder"], families
    assert "wonder-full" not in families
    muse = next(spec for spec in su.REVIEW_ROSTER if spec["family"] == "wonder")
    assert muse["contributor_mode"] == "off" and muse["model"] == "wonder", muse
    # Design's roster is unchanged and still has no Argon.
    assert [spec["family"] for spec in su.VOTER_ROSTER] == ["fable", "astra", "grok", "wonder-full"]


@test
def review_lineup_picks_two_by_pace_and_skips_unknown():
    fresh = _argon(snap(codex=50), 40)
    row = su.route_lineup(fresh, NOW, builder=grok_builder(), kind="review")
    assert row["mode"] != "ask" and len(row["lineup"]["voters"]) == 2, row
    assert _voter_families(row) == ["fable", "wonder"], row
    vendors = [v["vendor"] for v in row["lineup"]["voters"]]
    assert len(set(vendors)) == 2 and "xai" not in vendors, row
    assert all(v["pace"] == "under" and v["headroom"] == 1.0 for v in row["lineup"]["voters"]), row
    assert "ranked by pace then headroom" in row["why"], row
    skipped = {item["family"]: item["reason"] for item in row["skipped"]}
    assert "grok" in skipped and "builder vendor" in skipped["grok"], skipped
    assert "argon" in skipped and "astra" in skipped, skipped
    # A stale Fable reading is skipped. The other seats still fill the council.
    stale = _argon(snap(), 80)
    stale["as_of"] = dict(stale["as_of"])
    stale["as_of"]["claude"] = NOW - 222 * 60
    held = su.route_lineup(stale, NOW, builder_vendor="xai", kind="review")
    assert held["mode"] != "ask", held
    assert "fable" not in _voter_families(held), held
    assert _voter_families(held) == ["astra", "wonder"], held
    fable_skip = next(item for item in held["skipped"] if item["family"] == "fable")
    assert "222m old" in fable_skip["reason"], fable_skip
    # Ask only when fewer than two eligible seats remain.
    dead = snap(codex=100, muse_week=100, muse_5h=100, google_argon_week=100,
                google_argon_week_reset_ts=NOW + 3 * 86400)
    dead["as_of"] = dict(dead["as_of"])
    dead["as_of"]["claude"] = NOW - 222 * 60
    asked = su.route_lineup(dead, NOW, kind="review", builder_vendor="grok")
    assert asked["mode"] == "ask" and asked["lineup"] is None, asked
    assert asked["builder_vendor"] == "xai", asked
    assert "need 2" in asked["reason"], asked
    # grok is an alias for xai. Design output does not grow an Argon voter.
    design = su.route_lineup(fresh, NOW, kind="design")
    assert _voter_families(design) == ["fable", "astra", "grok", "wonder-full"], design
    assert "skipped" not in design and "why" not in design, design
    text = su.format_lineup(design)
    assert "pace " not in text and "skipped:" not in text, text


@test
def review_gate_picks_one_other_vendor():
    fresh = _argon(snap(cursor_api_pct=80, fable=80), 5)
    row = su.route_review(fresh, NOW, "xai")
    assert row["role"] == "review" and row["mode"] != "ask", row
    assert len(row["reviewers"]) == 1, row
    chosen = row["reviewers"][0]
    assert chosen["vendor"] != "xai" and chosen["family"] == "argon", chosen
    assert chosen["model"] == "gemini-4-argon-high", chosen
    assert chosen["pace"] == "under" and chosen["headroom"] == 1.0, chosen
    assert "spark" not in json.dumps(row).lower()
    assert "contributor" not in json.dumps(chosen).lower()
    text = su.format_review(row)
    assert text.startswith("route: review |"), text
    assert "pace under" in text and "headroom 1.00" in text, text
    assert any(item["family"] == "grok" for item in row["skipped"]), row
    none = su.route_review(snap(codex=100, muse_week=100, muse_5h=100, fable=100,
                                cursor_api_pct=100, claude_5h=100), NOW, "anthropic")
    # grok may still be eligible; this only checks the ask shape when forced empty below.
    capped = snap(codex=100, muse_week=100, muse_5h=100, fable=100, cursor_api_pct=100,
                  claude_5h=100, grok_pct=100, google_argon_week=100,
                  google_argon_week_reset_ts=NOW + 86400)
    asked = su.route_review(capped, NOW, "xai")
    assert asked["mode"] == "ask" and asked["reviewers"] == [], asked


@test
def review_argon_matches_pool_pace_and_vendor_flag_wins():
    hot = _argon(snap(), 96)
    pace, why, _used = su._argon_reading(hot, NOW)
    assert pace == "near_cap" and why is None, (pace, why)
    row = su.route_lineup(hot, NOW, kind="review", builder_vendor="xai")
    assert [v["family"] for v in row["lineup"]["voters"]] == ["fable", "astra"], row["why"]
    expired = _argon(snap(), 100, reset=NOW - 60)
    pace, why, _used = su._argon_reading(expired, NOW)
    assert pace == "unknown" and why == "reset passed", (pace, why)
    named = su.route_lineup(snap(), NOW, kind="review", author="argon", builder_vendor="openai")
    assert named["mode"] != "ask", named
    assert named["excluded"]["vendor"] == "openai" and named["excluded"]["family"] is None, named["excluded"]
    stale = _argon(snap(codex=80), 6)
    stale["as_of"] = dict(stale["as_of"])
    stale["as_of"]["claude"] = NOW - 222 * 60
    shown = su.route_lineup(stale, NOW, kind="review", builder_vendor="xai")
    assert [v["family"] for v in shown["lineup"]["voters"]] == ["argon", "wonder"], shown["why"]
    assert "Argon" in shown["rule"] and "Wonder" in shown["rule"], shown["rule"]
    asked = su.route_review(snap(), NOW, "not-a-vendor")
    assert asked["role"] == "review" and asked["mode"] == "ask" and asked["reviewers"] == [], asked
    assert su._parse_findings("VERDICT: approve\nNo findings.\n", "A") == []
    kept = su._parse_findings("VERDICT: request changes\n1. P1: first line\nstill the finding\n", "A")
    assert kept[0]["text"] == "first line\nstill the finding", kept


def _pick_without_ages(text):
    def scrub(obj):
        if isinstance(obj, dict):
            return {k: scrub(v) for k, v in obj.items() if k not in ("reading_age_s", "usage_age_s")}
        if isinstance(obj, list):
            return [scrub(v) for v in obj]
        return obj
    return scrub(json.loads(text))


@test
def review_routing_ignores_a_log_full_of_grades():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "latest.json")
        with open(cache, "w") as handle:
            body = _shift_snap(now)
            body["google_argon_week"] = 12
            body["google_argon_week_reset_ts"] = now + 3 * 86400
            json.dump(body, handle)
        empty = os.path.join(d, "empty.jsonl")
        graded = os.path.join(d, "graded.jsonl")
        open(empty, "w").close()
        graded_row = {
            "ts": 1, "id": "1.000000", "repo": "rymiwe/example", "pr": "9", "ballot": None,
            "builder_vendor": "xai", "route": "review",
            "reviewers": [{"seat": "claude", "family": "fable", "model": "fable", "vendor": "anthropic",
                           "pace": "under", "headroom": 1.0, "used_pct": 1, "effort": "high"}],
            "why": "ranked by pace then headroom: fable pace under headroom 1.00 used 1",
            "findings": [{"id": "f1", "reviewer": "claude", "text": "fable on the claude seat", "grade": "false"},
                         {"id": "f2", "reviewer": "chatgpt", "text": "astra from openai", "grade": "real"}],
        }
        with open(graded, "w") as handle:
            handle.write(json.dumps(graded_row) + "\n")
        home = os.path.join(d, "home")
        os.makedirs(home)

        def run(log_path, args):
            env = os.environ.copy()
            env["HOME"] = home
            env["SEAT_USAGE_CACHE_DIR"] = d
            env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
            env["SEAT_USAGE_REVIEW_LOG"] = log_path
            env["QUARTERMASTER_NO_UPDATE"] = "1"
            return subprocess.run(
                [sys.executable, str(BIN), *args],
                capture_output=True, text=True, env=env, timeout=20,
            )

        args = ["--route", "lineup", "--cached", "--json", "--kind", "review", "--builder-vendor", "xai"]
        first = run(empty, args)
        second = run(graded, args)
        assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
        # reading_age_s moves with the clock between the two runs. The pick does not.
        assert _pick_without_ages(first.stdout) == _pick_without_ages(second.stdout)
        gate_args = ["--route", "review", "--cached", "--json", "--builder-vendor", "grok",
                     "--repo", "rymiwe/example", "--pr", "4"]
        gate_empty = run(empty, gate_args)
        gate_full = run(graded, gate_args)
        assert gate_empty.returncode == 0 and gate_full.returncode == 0, (gate_empty.stderr, gate_full.stderr)
        assert _pick_without_ages(gate_empty.stdout) == _pick_without_ages(gate_full.stdout)
        picked = json.loads(gate_empty.stdout)[0]
        assert picked["builder_vendor"] == "xai" and len(picked["reviewers"]) == 1, picked
        assert picked["reviewers"][0]["vendor"] != "xai"
        # The grade log was not an input: both files gained a pick, and the graded
        # findings are unchanged.
        with open(graded) as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        assert lines[0]["findings"][0]["grade"] == "false", lines[0]
        assert len(lines) >= 2


@test
def review_grade_hides_model_vendor_and_seat_names():
    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "picks.jsonl")
        home = os.path.join(d, "home")
        os.makedirs(home)
        env = os.environ.copy()
        env["HOME"] = home
        env["SEAT_USAGE_REVIEW_LOG"] = log_path
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        for key in ("SEAT_USAGE_ROLE", "CONCLAVE_CHAIR", "COUNCIL_CHAIR", "SEAT_USAGE_GRADER"):
            env.pop(key, None)
        record = {
            "ts": 10, "id": "10.000000", "repo": "rymiwe/example", "pr": None, "ballot": "b1",
            "builder_vendor": "xai", "route": "lineup",
            "reviewers": [
                {"seat": "claude", "family": "fable", "model": "fable", "vendor": "anthropic",
                 "pace": "under", "headroom": 1.0, "used_pct": 10, "effort": "high"},
                {"seat": "chatgpt", "family": "astra", "model": "gpt-6-astra", "vendor": "openai",
                 "pace": "on", "headroom": 0.9, "used_pct": 40, "effort": "high"},
            ],
            "why": "ranked by pace then headroom: fable pace under headroom 1.00 used 10",
            "findings": [],
        }
        with open(log_path, "w") as handle:
            handle.write(json.dumps(record) + "\n")
        findings = os.path.join(d, "findings.md")
        with open(findings, "w") as handle:
            handle.write("VERDICT: request changes\n1. P1 fable on the claude seat leaks anthropic\n"
                         "2. P2 gpt-6-astra from openai on chatgpt\n")
        attach = subprocess.run(
            [sys.executable, str(BIN), "--review-findings", "--pick", "latest",
             "--reviewer", "claude", "--file", findings],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert attach.returncode == 0, attach.stderr
        with open(findings, "w") as handle:
            handle.write("1. grok cursor muse wonder argon gemini spark contributor\n")
        attach_b = subprocess.run(
            [sys.executable, str(BIN), "--review-findings", "--pick", "10.000000",
             "--reviewer", "cursor", "--file", findings],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert attach_b.returncode == 0, attach_b.stderr
        before_blind = open(log_path, "rb").read()
        shown = subprocess.run(
            [sys.executable, str(BIN), "--blind-findings", "--pick", "latest"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert shown.returncode == 0, shown.stderr
        assert open(log_path, "rb").read() == before_blind
        out = shown.stdout
        assert "Reviewer A" in out and "Reviewer B" in out, out
        assert "recorded" not in out, out
        banned = list(su._GRADE_REDACTIONS)
        with open(log_path) as handle:
            picked = json.loads(handle.read().strip().splitlines()[-1])
        for rev in picked["reviewers"]:
            for key in ("model", "vendor", "seat", "family"):
                banned.append(rev[key])
        for item in picked["findings"]:
            banned.append(item["reviewer"])
        for word in banned:
            assert word.lower() not in out.lower(), (word, out)
        graded = subprocess.run(
            [sys.executable, str(BIN), "--grade-findings", "--pick", "latest",
             "--grader", "blind-grader", "--grade", "f1=real", "--grade", "f2=false"],
            capture_output=True, text=True, env=env, timeout=15,
        )
        assert graded.returncode == 0, graded.stderr
        assert "recorded f1=real" in graded.stdout and "f2=false" in graded.stdout, graded.stdout
        assert "Reviewer A" not in graded.stdout, graded.stdout
        with open(log_path) as handle:
            saved = json.loads(handle.read().strip().splitlines()[-1])
        by_id = {item["id"]: item for item in saved["findings"]}
        assert by_id["f1"]["grade"] == "real" and by_id["f2"]["grade"] == "false", saved
        assert by_id["f1"]["grader"] == "blind-grader" and by_id["f1"]["grade_status"] == "graded", by_id["f1"]
        assert by_id["f1"]["graded_at"], by_id["f1"]
        # The second reviewer's finding is still blank, and attaching did not grade it.
        assert any(item["grade"] == "" for item in saved["findings"]), saved


@test
def google_opus_pool_wins_only_when_the_pools_ahead_of_it_cannot():
    s = snap()
    assert su.route("builder", s, NOW)["seat"] != "google"
    capped = dict(s, grok_exhausted=True, cursor_auto_pct=100, claude_week=99)
    r = su.route("builder", capped, NOW)
    assert (r["seat"], r["family"], r["effort"]) == ("google", "opus", "low"), r
    held = dict(capped, google_claudegpt_5h=100)
    assert su.route("builder", held, NOW)["seat"] != "google", "the 5h window blocks the weekly pool"


def _codes(skipped):
    return {item["family"]: item.get("code") for item in skipped}


@test
def review_exclusions_are_coded_and_argon_leads_when_healthy():
    # Argon at 4% leads the non-builder seats. Muse contributor/spark is not a roster row.
    fresh = _argon(snap(codex=80, grok_pct=70, muse_week=60, muse_5h=20, fable=90), 4)
    row = su.route_lineup(fresh, NOW, kind="review", builder_vendor="anthropic")
    assert row["mode"] != "ask", row
    families = [v["family"] for v in row["lineup"]["voters"]]
    assert families[0] == "argon", row["why"]
    assert "wonder" in families, families
    assert all("spark" not in (v.get("model") or "") and "contributor" not in (v.get("model") or "")
               for v in row["lineup"]["voters"])
    wonder = next(v for v in row["lineup"]["voters"] if v["family"] == "wonder")
    assert wonder["contributor_mode"] == "off" and wonder["model"] == "wonder", wonder
    codes = _codes(row["skipped"])
    assert codes["fable"] == "builder_vendor", codes
    assert codes.get("astra") == "lower_rank", codes
    text = su.format_lineup(row)
    assert "skipped: fable | builder_vendor |" in text, text
    assert "skipped: argon" not in text, text
    # The one-reviewer route shares the exclusion codes and still seats one.
    gate = su.route_review(fresh, NOW, "anthropic")
    assert len(gate["reviewers"]) == 1 and gate["reviewers"][0]["family"] == "argon", gate
    assert _codes(gate["skipped"])["fable"] == "builder_vendor"
    # A 365m Muse reading is an exclusion with code stale, in text and json.
    stale = _argon(snap(), 10)
    stale["as_of"] = dict(stale["as_of"])
    stale["as_of"]["muse"] = NOW - 365 * 60
    held = su.route_lineup(stale, NOW, kind="review", builder_vendor="xai")
    muse = next(item for item in held["skipped"] if item["family"] == "wonder")
    assert muse["code"] == "stale", muse
    assert "365m old" in muse["reason"] and "Muse 5h" in muse["reason"], muse
    shown = su.format_lineup(held)
    assert "skipped: wonder | stale |" in shown and "365m old" in shown, shown
    # Capped, read_state, and a recent failure each carry their own code.
    capped = _argon(snap(grok_pct=100), 12)
    capped_row = su.route_lineup(capped, NOW, kind="review", builder_vendor="anthropic")
    grok = next(item for item in capped_row["skipped"] if item["family"] == "grok")
    assert grok["code"] == "capped", grok
    limited = _argon(snap(), 8)
    limited["read_state"] = {"claude": "rate_limited", "google": "fresh"}
    limited["cursor_api_pct"] = 100
    limited_row = su.route_lineup(limited, NOW, kind="review", builder_vendor="xai")
    fable = next(item for item in limited_row["skipped"] if item["family"] == "fable")
    assert fable["code"] == "read_state:rate_limited", fable
    assert "fable" not in [v["family"] for v in limited_row["lineup"]["voters"]], limited_row["why"]
    # A cache with no read_state still seats Fable. The field is optional.
    bare = su.route_lineup(_argon(snap(), 8), NOW, kind="review", builder_vendor="xai")
    assert "fable" in [v["family"] for v in bare["lineup"]["voters"]], bare["why"]
    assert "read_state" not in json.dumps(bare["lineup"]["voters"])


@test
def review_failure_record_round_trip_and_expiry():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        cache = os.path.join(d, "latest.json")
        body = _shift_snap(now, google_argon_week=6, google_argon_week_reset_ts=now + 3 * 86400)
        with open(cache, "w") as handle:
            json.dump(body, handle)
        fail = os.path.join(d, "failures.jsonl")
        log = os.path.join(d, "picks.jsonl")
        home = os.path.join(d, "home")
        os.makedirs(home)
        env = os.environ.copy()
        env["HOME"] = home
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_FAILURES"] = fail
        env["SEAT_USAGE_REVIEW_LOG"] = log
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        env.pop("OP_SERVICE_ACCOUNT_TOKEN", None)

        def run(args, extra=None):
            this = dict(env)
            if extra:
                this.update(extra)
            return subprocess.run(
                [sys.executable, str(BIN), *args],
                capture_output=True, text=True, env=this, timeout=20,
            )

        recorded = run(["--record-failure", "grok", "--reason", "max_turns"])
        assert recorded.returncode == 0, recorded.stderr
        assert recorded.stdout.strip() == "recorded grok max_turns", recorded.stdout
        rejected = run(["--record-failure", "grok", "--reason", "fixture-token-secret"])
        assert rejected.returncode == 2, rejected.stderr
        assert "fixture-token-secret" not in rejected.stderr and "fixture-token-secret" not in rejected.stdout
        with open(fail) as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        assert lines == [{"ts": lines[0]["ts"], "seat": "grok", "reason": "max_turns"}], lines
        assert "fixture" not in json.dumps(lines)
        args = ["--route", "lineup", "--cached", "--json", "--kind", "review",
                "--builder-vendor", "anthropic", "--repo", "rymiwe/example", "--pr", "3"]
        routed = run(args)
        assert routed.returncode == 0, routed.stderr
        row = json.loads(routed.stdout)[0]
        grok = next(item for item in row["skipped"] if item["family"] == "grok")
        assert grok["code"] == "failed_recently:max_turns", grok
        assert all(v["family"] != "grok" for v in row["lineup"]["voters"]), row["why"]
        text = run(["--route", "lineup", "--cached", "--kind", "review", "--builder-vendor", "anthropic"])
        assert "skipped: grok | failed_recently:max_turns |" in text.stdout, text.stdout
        with open(log) as handle:
            logged = [json.loads(line) for line in handle if line.strip()]
        assert logged and any(
            item.get("code") == "failed_recently:max_turns" and item.get("family") == "grok"
            for item in logged[-1].get("exclusions") or []
        ), logged[-1]
        # credits on the cursor seat is no_credits, and a spark model is never a voter.
        credits = run(["--record-failure", "cursor", "--reason", "credits"])
        assert credits.returncode == 0, credits.stderr
        cursor_body = _shift_snap(now, fable=100, cursor_api_pct=10,
                                  google_argon_week=6, google_argon_week_reset_ts=now + 3 * 86400)
        with open(cache, "w") as handle:
            json.dump(cursor_body, handle)
        # Drop the grok failure so the cursor case is the one under test.
        with open(fail, "w") as handle:
            handle.write(json.dumps({"ts": now, "seat": "cursor", "reason": "credits"}) + "\n")
        seated = run(["--route", "lineup", "--cached", "--json", "--kind", "review",
                      "--builder-vendor", "xai"])
        assert seated.returncode == 0, seated.stderr
        cursor_row = json.loads(seated.stdout)[0]
        fable = next(item for item in cursor_row["skipped"] if item["family"] == "fable")
        assert fable["code"] == "no_credits", fable
        assert all("spark" not in (v.get("model") or "") for v in (cursor_row["lineup"] or {}).get("voters") or [])
        # An expired line does not exclude. TTL is 1s and the stamp is an hour old.
        with open(fail, "w") as handle:
            handle.write(json.dumps({"ts": now - 3600, "seat": "chatgpt", "reason": "auth"}) + "\n")
        healthy = _shift_snap(now, google_argon_week=40, google_argon_week_reset_ts=now + 3 * 86400)
        with open(cache, "w") as handle:
            json.dump(healthy, handle)
        expired = run(args, {"SEAT_USAGE_FAILURE_TTL_S": "1"})
        assert expired.returncode == 0, expired.stderr
        expired_row = json.loads(expired.stdout)[0]
        assert all(item.get("code") != "failed_recently:auth" for item in expired_row["skipped"]), expired_row["skipped"]
        astra = next(item for item in expired_row["skipped"] if item["family"] == "astra") if any(
            item["family"] == "astra" for item in expired_row["skipped"]) else None
        if astra is not None:
            assert astra["code"] == "lower_rank", astra
        else:
            assert any(v["family"] == "astra" for v in expired_row["lineup"]["voters"]), expired_row["why"]
        unknown = run(["--record-failure", "not-a-seat", "--reason", "auth"])
        assert unknown.returncode == 2 and "not-a-seat" not in unknown.stderr


def _fable_outcome(row):
    voters = [v for v in (row.get("lineup") or {}).get("voters") or [] if v["family"] == "fable"]
    skipped = [item for item in row.get("skipped") or [] if item["family"] == "fable"]
    return voters, skipped


@test
def review_blocked_fable_falls_through_and_an_ask_is_not_graded():
    # A stale Claude reading plus a block must not hide a healthy Cursor seat.
    stale = snap(cursor_api_pct=10)
    stale["as_of"] = dict(stale["as_of"])
    stale["as_of"]["claude"] = NOW - 400 * 60
    stale["read_state"] = {"claude": "rate_limited"}
    voters, skipped = _fable_outcome(su.route_lineup(stale, NOW, kind="review", builder_vendor="xai"))
    if voters:
        assert voters[0]["seat"] == "cursor", voters
    else:
        assert skipped and skipped[0]["code"] == "lower_rank" and skipped[0].get("seat") == "cursor", skipped
    assert all(item.get("code") not in ("stale", "unknown") for item in skipped), skipped
    # Both frontier seats closed: the block code wins over the stale ask.
    shut = snap(cursor_api_pct=100, fable=100, claude_week=100, claude_5h=100)
    shut["as_of"] = dict(shut["as_of"])
    shut["as_of"]["claude"] = NOW - 400 * 60
    shut["read_state"] = {"claude": "rate_limited"}
    held_voters, held_skipped = _fable_outcome(su.route_lineup(shut, NOW, kind="review", builder_vendor="xai"))
    assert not held_voters, held_voters
    assert held_skipped and held_skipped[0]["code"] == "read_state:rate_limited", held_skipped
    # --exclude-seat is the same block when the reading itself is stale.
    aged = snap(cursor_api_pct=10)
    aged["as_of"] = dict(aged["as_of"])
    aged["as_of"]["claude"] = NOW - 400 * 60
    su._REVIEW_FORCE_EXCLUDE.add("claude")
    try:
        forced_voters, forced_skipped = _fable_outcome(
            su.route_lineup(aged, NOW, kind="review", builder_vendor="xai"))
    finally:
        su._REVIEW_FORCE_EXCLUDE.clear()
    if forced_voters:
        assert forced_voters[0]["seat"] == "cursor", forced_voters
    else:
        assert forced_skipped and forced_skipped[0]["code"] == "lower_rank", forced_skipped
    assert all(item.get("code") not in ("stale", "unknown") for item in forced_skipped), forced_skipped
    # A log of only exclusion asks is not a pick. A later ask does not steal the last real one.
    assert su._pick_index([{"reviewers": [], "id": "ask"}], "latest") is None
    assert su._pick_index([
        {"reviewers": [{"family": "argon"}], "id": "settled"},
        {"reviewers": [], "id": "ask"},
    ], "latest") == 0


def _grade_env(tmp, log_path):
    env = os.environ.copy()
    env["HOME"] = tmp
    env["SEAT_USAGE_REVIEW_LOG"] = log_path
    env["QUARTERMASTER_NO_UPDATE"] = "1"
    for key in ("SEAT_USAGE_ROLE", "CONCLAVE_CHAIR", "COUNCIL_CHAIR", "SEAT_USAGE_GRADER",
                "SEAT_USAGE_REVIEW_BACKUP_DIR"):
        env.pop(key, None)
    return env


def _run_seat(env, args):
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True, text=True, env=env, timeout=20,
    )


@test
def chair_cannot_write_a_grade():
    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "picks.jsonl")
        record = {
            "ts": 10, "id": "10.000000", "repo": "rymiwe/example", "pr": "1", "ballot": None,
            "builder_vendor": "xai", "route": "review",
            "reviewers": [{"seat": "seat-alpha", "family": "family-alpha", "model": "model-alpha",
                           "vendor": "vendor-alpha", "pace": "pace-alpha", "headroom": "headroom-alpha",
                           "used_pct": "used-alpha", "effort": "effort-alpha"}],
            "why": "model-alpha", "findings": [
                {"id": "f1", "reviewer": "seat-alpha", "text": "model-alpha is wrong", "grade": ""},
            ],
        }
        with open(log_path, "w") as handle:
            handle.write(json.dumps(record) + "\n")
        original = open(log_path, "rb").read()
        env = _grade_env(d, log_path)
        markers = (
            {"SEAT_USAGE_ROLE": "chair"},
            {"CONCLAVE_CHAIR": "1"},
            {"COUNCIL_CHAIR": "true"},
        )
        for marker in markers:
            child = dict(env)
            child.update(marker)
            result = _run_seat(child, ["--grade-findings", "--pick", "10.000000",
                                       "--grader", "ryan", "--grade", "f1=real"])
            assert result.returncode != 0, (marker, result.returncode, result.stderr)
            assert open(log_path, "rb").read() == original, marker
            assert "model-alpha" not in result.stdout, (marker, result.stdout)
        # The same call without a chair marker records provenance.
        ok = _run_seat(env, ["--grade-findings", "--pick", "latest",
                             "--grader", "ryan", "--grade", "f1=false"])
        assert ok.returncode == 0, ok.stderr
        saved = json.loads(open(log_path).read())
        item = saved["findings"][0]
        assert item["grade"] == "false" and item["grader"] == "ryan", item
        assert item["grade_status"] == "graded" and item["graded_at"], item


@test
def blind_findings_hide_every_reviewer_value_and_unblind_waits():
    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "review-picks.jsonl")
        out_path = os.path.join(d, "blind.txt")
        reviewers = [
            {"seat": "seat-alpha", "family": "family-alpha", "model": "model-alpha",
             "vendor": "vendor-alpha", "pace": "pace-alpha", "headroom": "headroom-alpha",
             "used_pct": "used-alpha", "effort": "effort-alpha"},
            {"seat": "seat-beta", "family": "family-beta", "model": "model-beta",
             "vendor": "vendor-beta", "pace": "pace-beta", "headroom": "headroom-beta",
             "used_pct": "used-beta", "effort": "effort-beta"},
        ]
        record = {
            "ts": 11, "id": "11.000000", "repo": "rymiwe/example", "pr": "2", "ballot": "b",
            "builder_vendor": "xai", "route": "lineup", "reviewers": reviewers,
            "why": "model-alpha then model-beta", "findings": [],
        }
        with open(log_path, "w") as handle:
            handle.write(json.dumps(record) + "\n")
        env = _grade_env(d, log_path)
        names = [value for rev in reviewers for value in rev.values()]
        names.extend(su._GRADE_REDACTIONS)
        first = os.path.join(d, "a.json")
        second = os.path.join(d, "b.json")
        with open(first, "w") as handle:
            json.dump([{
                "id": "keep-me", "reviewer": "not-the-flag",
                "text": "grade this " + " ".join(names),
                "grade": "real", "grader": "ryan", "grade_status": "graded",
                "graded_at": "1999-01-01T00:00:00Z",
            }], handle)
        with open(second, "w") as handle:
            handle.write("1. " + " ".join(str(v) for v in reviewers[1].values()) + "\n")
        attached = _run_seat(env, ["--review-findings", "--pick", "latest",
                                   "--reviewer", "seat-alpha", "--file", first])
        assert attached.returncode == 0, attached.stderr
        attached_b = _run_seat(env, ["--review-findings", "--pick", "11.000000",
                                     "--reviewer", "seat-beta", "--file", second])
        assert attached_b.returncode == 0, attached_b.stderr
        saved = json.loads(open(log_path).read())
        assert saved["findings"][0]["grade"] == "", saved["findings"]
        assert "grader" not in saved["findings"][0] and "grade_status" not in saved["findings"][0], saved
        assert saved["findings"][0]["reviewer"] == "seat-alpha", saved
        before = open(log_path, "rb").read()
        shown = _run_seat(env, ["--blind-findings", "--pick", "latest", "--out", out_path])
        assert shown.returncode == 0, shown.stderr
        assert open(log_path, "rb").read() == before
        blind = shown.stdout
        assert open(out_path).read() == blind
        assert "Reviewer A" in blind and "Reviewer B" in blind, blind
        for word in names:
            assert str(word).lower() not in blind.lower(), (word, blind)
        sidecar = os.path.join(d, "review-picks.blindmap.json")
        mapped = json.loads(open(sidecar).read())["11.000000"]
        assert mapped["A"]["model"] == "model-alpha" and mapped["A"]["reviewer"] == "seat-alpha", mapped
        assert mapped["B"]["vendor"] == "vendor-beta" and mapped["B"]["seat"] == "seat-beta", mapped
        assert "model-alpha" not in blind and sidecar not in blind
        early = _run_seat(env, ["--unblind", "--pick", "latest"])
        assert early.returncode != 0, early.stdout
        assert "model-alpha" not in early.stdout and "vendor-beta" not in early.stdout, early.stdout
        assert open(log_path, "rb").read() == before
        one = _run_seat(env, ["--grade-findings", "--pick", "latest",
                              "--grader", "blind-grader", "--grade", "f1=real"])
        assert one.returncode == 0, one.stderr
        still = _run_seat(env, ["--unblind", "--pick", "11.000000"])
        assert still.returncode != 0 and "model-alpha" not in still.stdout, still.stdout
        two = _run_seat(env, ["--grade-findings", "--pick", "latest",
                              "--grader", "blind-grader", "--grade", "f2=false"])
        assert two.returncode == 0, two.stderr
        revealed = _run_seat(env, ["--unblind", "--pick", "latest"])
        assert revealed.returncode == 0, revealed.stderr
        lines = {line.split()[0]: line for line in revealed.stdout.splitlines()}
        assert lines["A"] == (
            "A model=model-alpha vendor=vendor-alpha seat=seat-alpha "
            "family=family-alpha reviewer=seat-alpha"
        ), revealed.stdout
        assert lines["B"] == (
            "B model=model-beta vendor=vendor-beta seat=seat-beta "
            "family=family-beta reviewer=seat-beta"
        ), revealed.stdout
        # Unblind does not rewrite the log's grades.
        final = json.loads(open(log_path).read())
        assert final["findings"][0]["grade"] == "real" and final["findings"][1]["grade"] == "false", final
        assert all(item["grade_status"] == "graded" and item["grader"] == "blind-grader"
                   for item in final["findings"]), final


@test
def mark_self_graded_backs_up_and_leaves_clean_rows():
    with tempfile.TemporaryDirectory() as d:
        log_path = os.path.join(d, "review-picks.jsonl")
        backup_dir = os.path.join(d, "backup")
        untouched = json.dumps({
            "ts": 1, "id": "1.000000", "repo": "rymiwe/example", "pr": "1",
            "findings": [{"id": "f1", "reviewer": "seat-alpha", "text": "plain", "grade": ""}],
            "note": "leave-this",
        })
        graded = {
            "ts": 2, "id": "2.000000", "repo": "rymiwe/example", "pr": "2", "note": "keep-note",
            "findings": [
                {"id": "f1", "reviewer": "seat-alpha", "text": "old chair grade", "grade": "false",
                 "extra": "stay"},
                {"id": "f2", "reviewer": "seat-beta", "text": "already graded", "grade": "real",
                 "grader": "ryan", "graded_at": "2020-01-01T00:00:00Z", "grade_status": "graded"},
                {"id": "f3", "reviewer": "seat-beta", "text": "blank", "grade": ""},
            ],
        }
        original = untouched + "\n" + json.dumps(graded) + "\n"
        with open(log_path, "w") as handle:
            handle.write(original)
        env = _grade_env(d, log_path)
        env["SEAT_USAGE_REVIEW_BACKUP_DIR"] = backup_dir
        result = _run_seat(env, ["--mark-self-graded"])
        assert result.returncode == 0, result.stderr
        assert "marked 1" in result.stdout, result.stdout
        backups = list(Path(backup_dir).glob("review-picks.jsonl.*"))
        assert len(backups) == 1, backups
        assert backups[0].read_bytes() == original.encode()
        original_copy = os.path.join(d, "original.jsonl")
        with open(original_copy, "wb") as handle:
            handle.write(original.encode())
        compared = subprocess.run(["cmp", "-s", str(backups[0]), original_copy])
        assert compared.returncode == 0, compared.returncode
        assert hashlib.sha256(backups[0].read_bytes()).hexdigest() == hashlib.sha256(original.encode()).hexdigest()
        rewritten = open(log_path).read().splitlines()
        assert rewritten[0] == untouched, rewritten[0]
        row = json.loads(rewritten[1])
        first, second, third = row["findings"]
        assert first["chair_grade_suspect"] == "false", first
        assert first["grade"] == "" and first["grade_status"] == "suspect-self-graded", first
        assert first["text"] == "old chair grade" and first["extra"] == "stay" and first["id"] == "f1", first
        assert second["grade"] == "real" and second["grader"] == "ryan" and "chair_grade_suspect" not in second, second
        assert third["grade"] == "" and "chair_grade_suspect" not in third and "grade_status" not in third, third
        assert row["note"] == "keep-note" and row["id"] == "2.000000", row
        assert len(rewritten) == 2
        # A second run migrates nothing and still backs up.
        again = _run_seat(env, ["--mark-self-graded"])
        assert again.returncode == 0 and "marked 0" in again.stdout, again.stdout
        assert open(log_path).read().splitlines()[0] == untouched


@test
def learning_helper_ignores_ungraded_and_suspect():
    rows = [
        {"id": "1", "findings": [
            {"id": "f1", "grade": "real", "grade_status": "graded", "grader": "ryan"},
            {"id": "f2", "grade": "false", "grade_status": "graded", "grader": "blind-grader"},
            {"id": "f3", "grade": "real"},
            {"id": "f4", "grade": "", "grade_status": "suspect-self-graded",
             "chair_grade_suspect": "false"},
            {"id": "f5", "grade": "suspect", "grade_status": "suspect", "grader": "ryan"},
            {"id": "f6", "grade": "real", "grade_status": "graded", "grader": "chair"},
            {"id": "f7", "grade": "real", "grade_status": "graded", "grader": "ryan",
             "chair_grade_suspect": "false"},
        ]},
        {"id": "2", "findings": [
            {"id": "f8", "grade": "false", "grade_status": "suspect-self-graded", "grader": "ryan"},
        ]},
        "not-a-row",
    ]
    got = [item["id"] for item in su.graded_findings_for_learning(rows)]
    assert got == ["f1", "f2", "f7"], got
    assert su.graded_findings_for_learning(None) == []
    source = Path(su.__file__).read_text()
    assert source.count("def graded_findings_for_learning(") == 1
    # Routing writes picks. It does not filter or read them back for a score.
    assert "_read_review_log" not in inspect.getsource(su.cmd_route)
    assert "graded_findings_for_learning" not in inspect.getsource(su.cmd_route)


@test
def help_exits_before_readers_locks_or_cache_writes():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with tempfile.TemporaryDirectory() as home:
            bindir = Path(home) / "bin"
            bindir.mkdir()
            for name in ("agy", "muse"):
                path = bindir / name
                path.write_text(
                    "#!/bin/sh\nprintf '%s\\n' " + repr(name) + " >> \"$HOME/invoked\"\nexit 0\n"
                )
                path.chmod(0o755)
            env = {
                "PATH": f"{bindir}:/usr/bin:/bin",
                "HOME": home,
                "SEAT_USAGE_ANTHROPIC_PROFILE": profile,
                "SEAT_USAGE_ANTHROPIC_USAGE": usage,
                "SEAT_USAGE_PULL_ONLY_FILE": "/nonexistent/pull-only",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            for flag in ("--help", "-h"):
                proc = subprocess.run(
                    [sys.executable, str(BIN), flag, "--route", "lineup"],
                    capture_output=True, text=True, env=env, timeout=20,
                )
                assert proc.returncode == 0, (flag, proc.returncode, proc.stderr, proc.stdout)
                assert proc.stdout.startswith("Print one line per subscription seat"), proc.stdout[:80]
                assert "usage failed" not in proc.stdout and "usage failed" not in proc.stderr
                assert proc.stderr == "", proc.stderr
            assert server.profile_hits == 0 and server.usage_hits == 0, (server.profile_hits, server.usage_hits)
            assert not (Path(home) / "invoked").exists()
            assert not (Path(home) / ".cache").exists()
            assert not (Path(home) / ".config").exists()
            assert not (Path(home) / ".local").exists()


@test
def held_claude_floor_stays_inside_live_freshness():
    # Import-time as_of plus a floor armed after the response leaves a held
    # reading in the 10m bucket while the floor still blocks. The route clock
    # is taken before the later seats, so their time is not added to the age.
    delay = su.LOCK_TIMEOUT
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            su.USAGE_MIN_INTERVAL_S = su.ROUTE_FRESH_S
            start = 1_700_000_000.0
            clock = _FloorClock(start)
            su.NOW = start
            real_time = su.time.time
            real_get = su.get

            def jumping_get(url, headers, timeout=8):
                if url == su.ANTHROPIC_USAGE_URL:
                    clock.t += delay
                return real_get(url, headers, timeout)

            su.time.time = clock
            su.get = jumping_get
            try:
                first = _quiet_cache()
                assert server.usage_hits == 1, server.usage_hits
                arm = clock.t
                assert first["as_of"]["claude"] == arm, (first["as_of"], start, arm)
                stored = json.load(open(su._rate_limit_path()))
                assert stored[su.ANTHROPIC_USAGE_URL] == arm + su.USAGE_MIN_INTERVAL_S, stored
                clock.t = arm + su.USAGE_MIN_INTERVAL_S - 1

                def later(_url=None, _headers=None, timeout=8):
                    clock.t += 8
                    return []

                su.codex = lambda: later()
                su.cursor = lambda: later()
                su.grok = lambda: later()
                summary = su.refresh(["claude", "chatgpt", "cursor", "grok"])
                assert server.usage_hits == 1, server.usage_hits
                route_now = su._ROUTE_NOW
                assert route_now < stored[su.ANTHROPIC_USAGE_URL]
                age = route_now - summary["as_of"]["claude"]
                assert age < su.ROUTE_FRESH_S, age
                assert clock.t - summary["as_of"]["claude"] > su.ROUTE_FRESH_S
                st, why = su.pool_status("fable", summary, route_now)
                assert st != "unknown", (st, why, age)
                # The import-time stamp at this same route clock is 10m old.
                assert int((route_now - start) // 60) == 10, route_now - start
                # cmd_route takes that clock. time.time() after the later seat
                # is already in the 10m bucket, so reverting the selection fails.
                clock.t = arm + su.USAGE_MIN_INTERVAL_S - 1
                argv = sys.argv
                sys.argv = [str(BIN), "--route", "bounded-question"]
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        su.cmd_route()
                finally:
                    sys.argv = argv
                routed = buf.getvalue()
                assert "10m old" not in routed, routed
                assert "fable" in routed, routed
                assert clock.t - summary["as_of"]["claude"] > su.ROUTE_FRESH_S
            finally:
                su.time.time = real_time
                su.get = real_get


class _FloorClock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


@test
def mixed_skips_stale_and_unknown_and_asks_only_when_short():
    stale = snap()
    stale["as_of"] = dict(stale["as_of"])
    stale["as_of"]["claude"] = NOW - 650
    row = su.route_lineup(stale, NOW, kind="mixed")
    assert row["mode"] != "ask" and row["lineup"] is not None, row
    assert "fable" not in _voter_families(row), row
    assert _voter_families(row) == ["astra", "grok", "wonder-full"], row
    fable = next(item for item in row["skipped"] if item["family"] == "fable")
    assert fable["code"] == "stale" and fable["seat"] == "claude", fable
    assert fable["vendor"] == "anthropic", fable
    assert "10m old" in fable["reason"], fable
    text = su.format_lineup(row)
    assert "skipped: fable | stale |" in text, text
    assert "ask |" not in text.splitlines()[0], text
    # Audit and a lineup with no kind still ask on the first unknown.
    # Design skips it, the same way mixed does. This snap has no plan flag.
    for kind in (None, "audit"):
        if kind is None:
            asked = su.route_lineup(stale, NOW)
        else:
            asked = su.route_lineup(stale, NOW, kind=kind)
        assert asked["mode"] == "ask" and "old" in asked["reason"], (kind, asked["reason"])
        assert "skipped" not in asked, (kind, asked)
    design_stale = su.route_lineup(stale, NOW, kind="design")
    assert design_stale["mode"] != "ask" and design_stale["lineup"] is not None, design_stale
    assert _voter_families(design_stale) == ["astra", "grok", "wonder-full"], design_stale
    design_fable = next(item for item in design_stale["skipped"] if item["family"] == "fable")
    assert design_fable["code"] == "stale" and design_fable["seat"] == "claude", design_fable
    # Muse with no reading is skipped, code unknown, and the others still sit.
    mute = without_as_of(snap(), "muse")
    muse_row = su.route_lineup(mute, NOW, kind="mixed")
    assert muse_row["mode"] != "ask", muse_row
    assert "wonder-full" not in _voter_families(muse_row), muse_row
    muse = next(item for item in muse_row["skipped"] if item["family"] == "wonder-full")
    assert muse["code"] == "unknown" and muse["seat"] == "muse", muse
    assert muse["reason"] == "muse: no usage source", muse
    # One eligible voter after skips asks, and the reason lists seats and codes.
    thin = without_as_of(snap(codex=100), "muse")
    thin["as_of"]["claude"] = NOW - 650
    short = su.route_lineup(thin, NOW, kind="mixed")
    assert short["mode"] == "ask" and short["lineup"] is None, short
    codes = {item["seat"]: item["code"] for item in short["skipped"]}
    assert codes["claude"] == "stale" and codes["muse"] == "unknown", short["skipped"]
    assert codes["chatgpt"] == "capped" and "grok" not in codes, short["skipped"]
    assert "claude/stale" in short["reason"] and "muse/unknown" in short["reason"], short["reason"]
    assert "chatgpt/capped" in short["reason"] and "ask Ryan" in short["reason"], short["reason"]
    assert "skipped: fable | stale |" in su.format_lineup(short), su.format_lineup(short)
    # Two eligible voters on one vendor ask. Caps are not what made it short.
    saved_vendor = su.FAMILY_VENDOR.get("astra")
    try:
        su.FAMILY_VENDOR["astra"] = "anthropic"
        same = su.route_lineup(snap(grok_pct=100, muse_week=100, muse_5h=100), NOW, kind="mixed")
    finally:
        su.FAMILY_VENDOR["astra"] = saved_vendor
    assert same["mode"] == "ask", same
    assert sorted(_voter_families(same) if same.get("lineup") else []) == [], same
    assert "need 2 voters from 2 vendors" in same["reason"], same["reason"]
    assert "outside nobody" not in same["reason"], same["reason"]
    assert not same["reason"].startswith("need 2 voters from 2 vendors outside"), same["reason"]
    # The excluded vendor is not counted and is not listed as a skip.
    held = without_as_of(snap(), "chatgpt")
    held["as_of"]["grok"] = NOW - 650
    excluded = su.route_lineup(held, NOW, kind="mixed", author="fable")
    assert excluded["mode"] == "ask", excluded
    assert excluded["excluded"]["vendor"] == "anthropic", excluded
    assert "outside Anthropic" in excluded["reason"], excluded["reason"]
    assert all(item["family"] != "fable" for item in excluded["skipped"]), excluded["skipped"]
    assert "claude/" not in excluded["reason"], excluded["reason"]
    seated = [item["family"] for item in excluded["skipped"]]
    assert "astra" in seated and "grok" in seated, excluded["skipped"]
    # Caps alone still produce the one-voter weaker lineup.
    capped = snap(codex=100, grok_pct=100, muse_week=100, muse_5h=100)
    weaker = su.route_lineup(capped, NOW, kind="mixed")
    assert weaker["mode"] != "ask" and _voter_families(weaker) == ["fable"], weaker
    assert "weaker:" in weaker["reason"], weaker["reason"]
    cap_codes = {item["family"]: item["code"] for item in weaker["skipped"]}
    assert cap_codes == {"astra": "capped", "grok": "capped", "wonder-full": "capped"}, cap_codes
    assert "need 2 voters" not in weaker["reason"], weaker["reason"]
    # A failed read_state is skipped. A recent failure log is not, and
    # --exclude-seat still does not apply to mixed.
    flagged = snap()
    flagged["read_state"] = {"chatgpt": "usage_failed"}
    state_row = su.route_lineup(flagged, NOW, kind="mixed")
    assert "astra" not in _voter_families(state_row), state_row
    assert any(item["code"] == "read_state:usage_failed" for item in state_row["skipped"]), state_row
    with tempfile.TemporaryDirectory() as d:
        fail = os.path.join(d, "failures.jsonl")
        old = os.environ.get("SEAT_USAGE_FAILURES")
        os.environ["SEAT_USAGE_FAILURES"] = fail
        try:
            assert su.record_seat_failure("grok", "max_turns", now=NOW) == "grok"
            logged = su.route_lineup(snap(), NOW, kind="mixed")
        finally:
            if old is None:
                os.environ.pop("SEAT_USAGE_FAILURES", None)
            else:
                os.environ["SEAT_USAGE_FAILURES"] = old
    assert "grok" in _voter_families(logged), logged
    assert not any(str(item.get("code", "")).startswith("failed_recently") for item in logged.get("skipped") or [])
    with tempfile.TemporaryDirectory() as home:
        proc = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--kind", "mixed", "--exclude-seat", "grok"],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": home, "PYTHONDONTWRITEBYTECODE": "1",
                 "SEAT_USAGE_PULL_ONLY_FILE": "/nonexistent/pull-only"},
            timeout=20,
        )
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "exclude-seat" in proc.stderr, proc.stderr


@test
def mixed_json_lists_skipped_and_review_still_skips_its_own_way():
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        body = _shift_snap(now)
        body["as_of"] = dict(body["as_of"])
        body["as_of"]["claude"] = now - 650
        body["as_of"].pop("muse", None)
        with open(os.path.join(d, "latest.json"), "w") as handle:
            json.dump(body, handle)
        home = os.path.join(d, "home")
        os.makedirs(home)
        env = os.environ.copy()
        env["HOME"] = home
        env["SEAT_USAGE_CACHE_DIR"] = d
        env["SEAT_USAGE_ASKS"] = os.path.join(d, "asks.jsonl")
        env["QUARTERMASTER_NO_UPDATE"] = "1"
        proc = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--cached", "--json", "--kind", "mixed"],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert proc.returncode == 0, proc.stderr
        row = json.loads(proc.stdout)[0]
        assert row["mode"] != "ask" and row["kind"] == "mixed", row
        assert "lineup" in row and row["lineup"]["voters"], row
        codes = {item["family"]: item["code"] for item in row["skipped"]}
        assert codes["fable"] == "stale" and codes["wonder-full"] == "unknown", row["skipped"]
        for item in row["skipped"]:
            assert set(item) >= {"family", "seat", "vendor", "reason", "code"}, item
        text = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--cached", "--kind", "mixed"],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert "skipped: fable | stale |" in text.stdout, text.stdout
        assert "skipped: wonder-full | unknown |" in text.stdout, text.stdout
        # Review's own skip list is unchanged by the mixed path. Muse is put
        # back so two vendors remain and the stale Fable skip is the check.
        review_body = json.loads(json.dumps(body))
        review_body["as_of"]["muse"] = now
        with open(os.path.join(d, "latest.json"), "w") as handle:
            json.dump(review_body, handle)
        review = subprocess.run(
            [sys.executable, str(BIN), "--route", "lineup", "--cached", "--json",
             "--kind", "review", "--builder-vendor", "xai"],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert review.returncode == 0, review.stderr
        reviewed = json.loads(review.stdout)[0]
        assert reviewed["mode"] != "ask", reviewed
        assert any(item["family"] == "fable" and item["code"] == "stale" for item in reviewed["skipped"]), reviewed
        assert "fable" not in _voter_families(reviewed)


def _drop_muse_percents(summary):
    for key in ("muse_5h", "muse_5h_reset_ts", "muse_week", "muse_week_reset_ts"):
        summary.pop(key, None)
    return summary


@test
def muse_quota_turn_records_exhausted_and_not_a_used_percent():
    resets_ms = 1792972800000  # 2026-10-26T00:00:00Z, from the sanitized probe
    message = "nope"
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        argv_path = d / "argv.txt"
        binary = d / "muse"
        _muse_stub(binary, '''
import json, sys
open(%r, "w").write("\\n".join(sys.argv))
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}), flush=True)
    elif method == "usage/read":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}), flush=True)
    elif method == "session/start":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"session": {"sessionId": "s1"}}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"status": "accepted"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {
            "terminal": "failed",
            "error": {"kind": "modelError", "message": %s, "retryable": False,
                      "quota": {"limitKind": "tierLimit", "resetsAtMs": %d}},
        }}), flush=True)
        break
''' % (str(argv_path), json.dumps(message), resets_ms))
        dest = d / "muse-usage.json"
        dest.write_text(json.dumps({
            "captured_at": "2026-10-22T17:50:29Z",
            "window": {"used_percent": 99, "window_duration_mins": 300, "resets_at": 1792972800},
            "weekly": {"used_percent": 99, "resets_at": "2026-10-26T00:00:00Z"},
        }))
        assert su.refresh_muse_usage(time.time(), path=str(dest), binary=str(binary), timeout=5) is True
        argv = argv_path.read_text().splitlines()
        assert "--contributor-mode" in argv and argv[argv.index("--contributor-mode") + 1] == "off", argv
        assert "--model" not in argv, argv
        raw = dest.read_text()
        got = json.loads(raw)
        assert got["exhausted"] is True, got
        assert got["exhausted_resets_at"] == 1792972800, got
        assert "used_percent" not in raw and "usedPercent" not in raw, raw
        assert message not in raw, raw
        summary = {"v": 1, "as_of": {}, "muse_5h": 99, "muse_week": 99,
                   "muse_5h_reset_ts": 1, "muse_week_reset_ts": 1}
        su.apply_muse(summary, time.time(), path=str(dest))
        assert summary["muse_exhausted"] is True, summary
        assert summary["muse_exhausted_reset_ts"] == 1792972800, summary
        assert "muse_5h" not in summary and "muse_week" not in summary, summary
        assert abs(summary["as_of"]["muse"] - su._muse_epoch(got["captured_at"])) < 1, summary
        called = d / "called"
        decoy = d / "decoy"
        _muse_stub(decoy, 'open(%r, "w").write("called\\n")\n' % str(called))
        assert su.refresh_muse_usage(time.time(), path=str(dest), binary=str(decoy), timeout=5) is False
        assert not called.exists()
        assert json.loads(dest.read_text())["exhausted"] is True
        with local_tz("UTC"):
            shown = su.format_muse_line(got, time.time())
            assert shown.startswith("muse     | quota exhausted, resets "), shown
            assert "%" not in shown and "99" not in shown, shown
            assert su.muse_line(time.time(), path=str(dest)) == shown
            both = dict(got)
            both["window"] = {"used_percent": 99, "window_duration_mins": 300, "resets_at": 1792972800}
            both["weekly"] = {"used_percent": 99, "resets_at": "2026-10-26T00:00:00Z"}
            both_line = su.format_muse_line(both, time.time())
            assert both_line.startswith("muse     | quota exhausted, resets "), both_line
            assert "Weekly" not in both_line, both_line
        past_doc = {"captured_at": got["captured_at"], "exhausted": True, "exhausted_resets_at": 1}
        assert su.format_muse_line(past_doc, time.time()) is None


@test
def muse_non_quota_failure_records_nothing():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        binary = d / "muse"
        _muse_stub(binary, r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}), flush=True)
    elif method == "usage/read":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}), flush=True)
    elif method == "session/start":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"session": {"sessionId": "s1"}}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"status": "accepted"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "turn/completed", "params": {
            "terminal": "failed",
            "error": {"kind": "modelError", "message": "Subscription quota exhausted. Your usage window resets.",
                      "retryable": False},
        }}), flush=True)
        break
''')
        dest = d / "muse-usage.json"
        before = json.dumps({"captured_at": "2026-10-22T17:50:29Z", "window": {"used_percent": 99,
                             "window_duration_mins": 300, "resets_at": 1},
                             "weekly": {"used_percent": 99, "resets_at": 2}})
        dest.write_text(before)
        assert su.refresh_muse_usage(time.time(), path=str(dest), binary=str(binary), timeout=5) is False
        assert dest.read_text() == before
        absent = d / "missing.json"
        assert su.refresh_muse_usage(time.time(), path=str(absent), binary=str(binary), timeout=5) is False
        assert not absent.exists()


@test
def muse_exhausted_routes_capped_until_the_reset_and_names_a_missing_source():
    reset = NOW + 3 * 86400
    s = _drop_muse_percents(snap())
    s["muse_exhausted"] = True
    s["muse_exhausted_reset_ts"] = reset
    s["as_of"] = dict(s["as_of"], muse=NOW - 5000)
    with local_tz("UTC"):
        for pid in ("muse_5h", "muse_week"):
            st, why = su.pool_status(pid, s, NOW)
            assert st == "capped", (pid, st, why)
            assert why == "Muse quota exhausted, resets " + su._muse_local(reset), why
        mixed = su.route_lineup(s, NOW, kind="mixed")
        review = su.route_review(s, NOW, "xai")
    assert mixed["mode"] != "ask", mixed
    assert "wonder-full" not in [v["family"] for v in mixed["lineup"]["voters"]], mixed
    muse = next(item for item in mixed["skipped"] if item["family"] == "wonder-full")
    assert muse["code"] == "capped" and muse["reason"].startswith("Muse quota exhausted, resets "), muse
    assert "no_credits" not in json.dumps(mixed["skipped"]), mixed["skipped"]
    text = su.format_lineup(mixed)
    assert "skipped: wonder-full | capped | Muse quota exhausted, resets " in text, text
    wonder = next(item for item in review["skipped"] if item["family"] == "wonder")
    assert wonder["code"] == "capped" and wonder["reason"] == muse["reason"], wonder
    assert "wonder" not in [v["family"] for v in review["reviewers"]], review
    shown = su.format_review(review)
    assert "skipped: wonder | capped | Muse quota exhausted, resets " in shown, shown
    rows = {g["id"]: g for g in su.seat_rows(s, NOW)}
    assert rows["mu"]["bars"] == [{"kind": "empty", "name": "mu", "seat": "muse", "reset_ts": reset}], rows["mu"]
    # A reset already past is not capped. An old stamp is stale, as before.
    past = _drop_muse_percents(snap())
    past["muse_exhausted"] = True
    past["muse_exhausted_reset_ts"] = NOW - 1
    past["as_of"] = dict(past["as_of"], muse=NOW - 5000)
    st, why = su.pool_status("muse_week", past, NOW)
    assert st == "unknown" and why.startswith("reading ") and why.endswith(" old"), (st, why)
    past_row = su.route_lineup(past, NOW, kind="mixed")
    past_muse = next(item for item in past_row["skipped"] if item["family"] == "wonder-full")
    assert past_muse["code"] == "stale", past_muse
    fresh_past = _drop_muse_percents(snap())
    fresh_past["muse_exhausted"] = True
    fresh_past["muse_exhausted_reset_ts"] = NOW - 1
    st5, why5 = su.pool_status("muse_5h", fresh_past, NOW)
    assert st5 == "unknown" and why5 == "no used %", (st5, why5)
    # No file, no fields, and a pull-only copy without Muse fields stay unknown.
    mute = without_as_of(snap(), "muse")
    mute_row = su.route_lineup(mute, NOW, kind="mixed")
    mute_muse = next(item for item in mute_row["skipped"] if item["family"] == "wonder-full")
    assert mute_muse["code"] == "unknown" and mute_muse["reason"] == "muse: no usage source", mute_muse
    asked = su.route_lineup(mute, NOW)
    assert asked["mode"] == "ask" and asked["reason"] == "muse: no usage source", asked
    bare = {k: v for k, v in snap().items() if not str(k).startswith("muse")}
    bare["as_of"] = {k: v for k, v in bare["as_of"].items() if k != "muse"}
    bare["pulled_ts"] = NOW
    bare_row = su.route_lineup(bare, NOW, kind="mixed")
    bare_muse = next(item for item in bare_row["skipped"] if item["family"] == "wonder-full")
    assert bare_muse["reason"] == "muse: no usage source" and bare_muse["code"] == "unknown", bare_muse
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "home"
        cache_dir = Path(d) / "cache"
        home.mkdir()
        cache_dir.mkdir()
        body = _shift_snap(now)
        _drop_muse_percents(body)
        body["muse_exhausted"] = True
        body["muse_exhausted_reset_ts"] = now + 3 * 86400
        body["as_of"]["muse"] = now
        (cache_dir / "latest.json").write_text(json.dumps(body))
        proc = _run_bin(home, cache_dir, ["--route", "lineup", "--kind", "mixed", "--cached", "--json"])
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        row = json.loads(proc.stdout)[0]
        item = next(x for x in row["skipped"] if x["family"] == "wonder-full")
        assert item["code"] == "capped" and item["reason"].startswith("Muse quota exhausted, resets "), item
        text = _run_bin(home, cache_dir, ["--route", "lineup", "--kind", "mixed", "--cached"])
        assert "skipped: wonder-full | capped | Muse quota exhausted, resets " in text.stdout, text.stdout
        reviewed = _run_bin(home, cache_dir, ["--route", "review", "--builder-vendor", "xai", "--cached", "--json"])
        assert reviewed.returncode == 0, reviewed.stderr
        rev = json.loads(reviewed.stdout)[0]
        rev_item = next(x for x in rev["skipped"] if x["family"] == "wonder")
        assert rev_item["code"] == "capped" and rev_item["reason"].startswith("Muse quota exhausted, resets "), rev_item


@contextlib.contextmanager
def renewals_file(doc):
    """Point su.RENEWALS_FILE at a temp renewals.json holding doc, or at no file when doc is None."""
    old = su.RENEWALS_FILE
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "renewals.json"
        if doc is not None:
            path.write_text(json.dumps(doc))
        su.RENEWALS_FILE = str(path)
        try:
            yield path
        finally:
            su.RENEWALS_FILE = old


MUSE_ENDS = {"claude": {"at": "2099-10-14", "renews": None}, "muse": {"ends": "2026-11-12"}}


def _muse_routes(ts):
    """Design, review (both forms), mixed, and ask at local time ts, on readings taken at ts."""
    s = _shift_snap(ts)
    return s, {
        "design": su.route_lineup(s, ts, kind="design"),
        "review": su.route_review(s, ts, "xai"),
        "review_lineup": su.route_lineup(s, ts, kind="review", author="grok"),
        "mixed": su.route_lineup(s, ts, kind="mixed", author="grok"),
        "ask": su.route_ask(s, ts),
        "no_kind": su.route_lineup(s, ts),
    }


def _skip(row, family):
    return next((x for x in row.get("skipped") or () if x["family"] == family), None)


@test
def seat_ends_excludes_muse_from_every_route_once_the_day_is_over():
    with local_tz("America/Los_Angeles"), renewals_file(MUSE_ENDS):
        after = datetime(2026, 11, 13, 0, 0, 0).timestamp()
        s, r = _muse_routes(after)
        for pid in ("muse_5h", "muse_week"):
            assert su.pool_status(pid, s, after) == ("capped", "ended 2026-11-12"), su.pool_status(pid, s, after)
        assert "wonder-full" not in [v["family"] for v in r["design"]["lineup"]["voters"]], r["design"]
        assert r["design"]["usage"]["muse_week"]["state"] == "ended", r["design"]["usage"]
        assert r["design"]["usage"]["muse_5h"]["state"] == "ended", r["design"]["usage"]
        assert "wonder" not in [v["family"] for v in r["review"]["reviewers"]], r["review"]
        for key, family in (("review", "wonder"), ("review_lineup", "wonder"), ("mixed", "wonder-full")):
            item = _skip(r[key], family)
            assert item and item["code"] == "ended" and item["reason"] == "ended 2026-11-12", (key, item)
        assert "wonder-full" not in [v["family"] for v in r["mixed"]["lineup"]["voters"]], r["mixed"]
        assert "skipped: wonder-full | ended | ended 2026-11-12" in su.format_lineup(r["mixed"])
        assert "skipped: wonder | ended | ended 2026-11-12" in su.format_review(r["review"])
        assert "wonder-full" not in [c["family"] for c in r["ask"]["candidates"]], r["ask"]
        assert "wonder-full" not in [v["family"] for v in r["no_kind"]["lineup"]["voters"]], r["no_kind"]
        # Status: the Muse line, the status-line row, and no burn nudge for it.
        assert su.muse_line(after, "/nonexistent/muse-usage.json") == "muse     | ended 2026-11-12"
        mu = next(g for g in su.seat_rows(s, after) if g["id"] == "mu")
        assert mu["bars"] == [{"kind": "off", "name": "mu", "seat": "muse"}], mu
        assert all(e["seat"] != "muse" for e in su.burn_entries(s, after))
        # Once it ends there is no Muse reading either. That must not make design ask.
        gone = without_as_of(_drop_muse_percents(_shift_snap(after)), "muse")
        design = su.route_lineup(gone, after, kind="design")
        assert design["mode"] != "ask", design
        assert su.pool_status("muse_week", gone, after) == ("capped", "ended 2026-11-12")


@test
def seat_ends_keeps_muse_eligible_through_its_last_day():
    with local_tz("America/Los_Angeles"), renewals_file(MUSE_ENDS):
        for ts in (datetime(2026, 11, 11, 12, 0, 0).timestamp(), datetime(2026, 11, 12, 23, 59, 59).timestamp()):
            s, r = _muse_routes(ts)
            assert su.pool_status("muse_week", s, ts)[0] not in ("capped", "unknown"), su.pool_status("muse_week", s, ts)
            assert "wonder-full" in [v["family"] for v in r["design"]["lineup"]["voters"]], r["design"]
            assert "wonder-full" in [v["family"] for v in r["mixed"]["lineup"]["voters"]], r["mixed"]
            assert "wonder-full" in [v["family"] for v in r["no_kind"]["lineup"]["voters"]], r["no_kind"]
            assert "wonder-full" in [c["family"] for c in r["ask"]["candidates"]], r["ask"]
            for key, family in (("review", "wonder"), ("review_lineup", "wonder"), ("mixed", "wonder-full")):
                item = _skip(r[key], family)
                assert item is None or item["code"] != "ended", (key, item)
            assert "ended" not in json.dumps(r["design"]["usage"]), r["design"]["usage"]
            assert su.muse_line(ts, "/nonexistent/muse-usage.json") == "muse     | no data yet"
            mu = next(g for g in su.seat_rows(s, ts) if g["id"] == "mu")
            assert [b["kind"] for b in mu["bars"]] == ["seat", "seat"], mu


@test
def seat_ends_missing_changes_nothing():
    with local_tz("America/Los_Angeles"):
        ts = datetime(2026, 11, 13, 12, 0, 0).timestamp()
        outs = []
        for doc in (None, {"claude": {"at": "2099-10-14", "renews": None}}, {"muse": {}},
                    {"claude": {"at": "YYYY-MM-DD", "renews": None}, "muse": {"ends": None}}):
            with renewals_file(doc):
                assert su.seat_ends() == {}, doc
                s, r = _muse_routes(ts)
                outs.append(json.dumps({"routes": r, "rows": su.seat_rows(s, ts), "burn": su.burn_entries(s, ts),
                                        "states": {pid: su.pool_status(pid, s, ts) for pid in su.POOLS}},
                                       sort_keys=True, default=str))
        assert outs[0] == outs[1] == outs[2] == outs[3]
        assert "ended" not in outs[0]


def _renewals_stderr(fn):
    """(fn(), stderr) with a fresh warn-once set, so each case sees its own warnings."""
    su._RENEWALS_WARNED.clear()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn()
    return out, err.getvalue()


@test
def seat_ends_bad_date_warns_once_and_ignores_only_that_seat():
    with local_tz("America/Los_Angeles"):
        ts = datetime(2026, 11, 20, 12, 0, 0).timestamp()
        s = _shift_snap(ts)
        with renewals_file(None):
            clean = {pid: su.pool_status(pid, s, ts) for pid in ("muse_week", "muse_5h")}
        for bad in ("2026-13-01", "2026-11-31", "11/12/2026", "2026-11-12T00:00:00", "YYYY-MM-DD", "", 20261112):
            doc = {"muse": {"ends": bad}, "grok": {"ends": "2026-01-31"}}
            with renewals_file(doc) as path:
                def run():
                    return ({pid: su.pool_status(pid, s, ts) for pid in ("muse_week", "muse_5h")},
                            su.pool_status("grok", s, ts), su.seat_ends(), su.route_lineup(s, ts, kind="design"))
                (muse, grok, ends, design), err = _renewals_stderr(run)
            want = f"seat-usage: warning: {path}: muse.ends must be a YYYY-MM-DD date, got {json.dumps(bad)}; ignoring it\n"
            assert err == want, (bad, err)          # once, though seat_ends ran many times
            assert muse == clean, (bad, muse)        # the bad seat behaves as if it had no ends
            assert grok == ("capped", "ended 2026-01-31"), (bad, grok)   # other seats' ends still apply
            assert ends == {"grok": "2026-01-31"}, ends
            assert "wonder-full" in [v["family"] for v in design["lineup"]["voters"]], design
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "home"
        cache_dir = Path(d) / "cache"
        (home / ".config" / "seat-usage").mkdir(parents=True)
        cache_dir.mkdir()
        (cache_dir / "latest.json").write_text(json.dumps(_shift_snap(time.time())))
        renewals = home / ".config" / "seat-usage" / "renewals.json"
        for args in (["--route", "lineup", "--kind", "design", "--cached", "--json"],
                     ["--route", "review", "--builder-vendor", "xai", "--cached"], ["--cached"]):
            if renewals.exists():
                renewals.unlink()
            base = _run_bin(home, cache_dir, args)
            renewals.write_text(json.dumps({"muse": {"ends": "2026-13-01"}}))
            proc = _run_bin(home, cache_dir, args)
            assert proc.returncode == 0 == base.returncode, (args, proc.returncode, proc.stderr)
            age = lambda t: re.sub(r'("(?:reading|usage)_age_s": )[0-9.e-]+', r"\1X", t)
            assert age(proc.stdout) == age(base.stdout), (args, proc.stdout, base.stdout)
            assert proc.stderr == f'seat-usage: warning: {renewals}: muse.ends must be a YYYY-MM-DD date, got "2026-13-01"; ignoring it\n', proc.stderr


@test
def seat_ends_warns_on_unknown_seats_and_keys():
    cases = (
        ({"msue": {"ends": "2026-11-12"}}, ['unknown seat "msue" ignored']),
        ({"wonder": {"ends": "2026-11-12"}}, ['unknown seat "wonder" ignored']),
        ({"muse": {"end": "2026-11-12"}}, ["unknown key muse.end ignored (keys: at, renews, ends)"]),
        ({"muse": "2026-11-12"}, ['muse must be an object, got "2026-11-12"; ignored']),
    )
    for doc, wants in cases:
        with renewals_file(doc):
            (ends, status), err = _renewals_stderr(lambda: (su.seat_ends(), su.pool_status("muse_week", snap(), NOW)))
        assert ends == {}, (doc, ends)
        assert status[0] != "capped", (doc, status)
        lines = err.splitlines()
        assert len(lines) == len(wants), (doc, err)
        for line, want in zip(lines, wants):
            assert line.startswith("seat-usage: warning: ") and want in line, (doc, line)
    # The shipped example and a real-looking file stay silent.
    for doc in ({"claude": {"at": "YYYY-MM-DD", "renews": None}, "muse": {"ends": None}}, MUSE_ENDS):
        with renewals_file(doc):
            _, err = _renewals_stderr(su.seat_ends)
        assert err == "", (doc, err)


@test
def seat_ends_cli_shows_ended_in_status_and_routes():
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "home"
        cache_dir = Path(d) / "cache"
        (home / ".config" / "seat-usage").mkdir(parents=True)
        cache_dir.mkdir()
        (cache_dir / "latest.json").write_text(json.dumps(_shift_snap(time.time())))
        renewals = home / ".config" / "seat-usage" / "renewals.json"
        renewals.write_text(json.dumps({"muse": {"ends": "2026-01-31"}}))
        status = _run_bin(home, cache_dir, ["--cached"])
        assert status.returncode == 0, status.stderr
        assert "  muse     | ended 2026-01-31" in status.stdout.splitlines(), status.stdout
        rev = _run_bin(home, cache_dir, ["--route", "review", "--builder-vendor", "xai", "--cached", "--json"])
        assert rev.returncode == 0, rev.stderr
        item = _skip(json.loads(rev.stdout)[0], "wonder")
        assert item["code"] == "ended" and item["reason"] == "ended 2026-01-31", item
        design = _run_bin(home, cache_dir, ["--route", "lineup", "--kind", "design", "--cached", "--json"])
        row = json.loads(design.stdout)[0]
        assert "wonder-full" not in [v["family"] for v in row["lineup"]["voters"]], row
        assert row["usage"]["muse_week"]["state"] == "ended", row["usage"]
        renewals.write_text(json.dumps({"muse": {"ends": "2099-01-31"}}))
        design = _run_bin(home, cache_dir, ["--route", "lineup", "--kind", "design", "--cached", "--json"])
        row = json.loads(design.stdout)[0]
        assert "wonder-full" in [v["family"] for v in row["lineup"]["voters"]], row


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
