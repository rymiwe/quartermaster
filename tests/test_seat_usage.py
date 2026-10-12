#!/usr/bin/env python3
"""Route and hook tests for seat-usage. No network."""
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
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
hook = load("seat_route_hook", HOOK)

NOW = 1_800_000_000
WEEK = 7 * 86400
MONTH = 30 * 86400


def snap(**kw):
    as_of = kw.pop("as_of", None)
    s = {
        "v": 1,
        "ts": NOW,
        "as_of": {"claude": NOW, "chatgpt": NOW, "cursor": NOW, "grokbot": NOW, "grok": NOW},
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
    assert under["reason"] == "Fable under pace: bounded shot to Fable (Pace rule)", under
    on = su.route("bounded-question", snap(fable=57), NOW)
    assert on["mode"] == "default" and on["family"] == "fable" and on["effort"] == "-", on
    ahead = su.route("bounded-question", snap(fable=80), NOW)
    assert ahead["mode"] == "suggest" and ahead["family"] == "opus" and ahead["seat"] == "claude", ahead
    assert ahead["reason"] == "Fable ahead of pace: judgment shot to Opus (Pace rule)", ahead
    assert su.pace_code(10, NOW + 3 * 86400, WEEK, NOW) == "under"
    assert su.pace_code(57, NOW + 3 * 86400, WEEK, NOW) == "on"
    assert su.pace_code(80, NOW + 3 * 86400, WEEK, NOW) == "ahead"


@test
def codex_under_on_ahead():
    under = su.route("hard-bug", snap(codex=10), NOW)
    assert under["mode"] == "suggest" and under["family"] == "astra" and under["effort"] == "low", under
    assert under["reason"] == "Codex under pace: hard ticket to Astra (Pace rule)", under
    on = su.route("hard-bug", snap(codex=57), NOW)
    assert on["mode"] == "default" and on["family"] == "astra" and on["effort"] == "low", on
    ahead = su.route("hard-bug", snap(codex=80), NOW)
    assert ahead["mode"] == "suggest" and ahead["family"] == "sol" and ahead["effort"] == "-", ahead
    assert ahead["seat"] == "chatgpt", ahead
    assert ahead["reason"] == "Codex ahead of pace: Sol where it can do the job (Pace rule)", ahead


@test
def builder_spends_the_under_pool_and_keeps_cursor_when_both_are_on():
    # Default grok is under, so an on-pace Cursor still loses to it.
    under = su.route("builder", snap(cursor_auto_pct=15), NOW)
    assert under["mode"] == "default" and under["seat"] == "cursor" and under["family"] == "grok", under
    assert under["effort"] == "high" and under["variant"] == "fast", under
    assert under["reason"] == "Grok on Cursor: Cursor Models under pace", under
    assert under["reading_age_s"] == 0, under
    moved = su.route("builder", snap(cursor_auto_pct=50), NOW)
    assert moved["seat"] == "grok" and moved["variant"] == "-" and moved["effort"] == "high", moved
    assert moved["reason"] == "Grok on SuperGrok: grok under pace", moved
    both_on = su.route("builder", snap(cursor_auto_pct=50, grok_pct=57), NOW)
    assert both_on["mode"] == "default" and both_on["seat"] == "cursor" and both_on["variant"] == "fast", both_on
    assert both_on["reason"] == "Grok on Cursor: Cursor Models on pace", both_on
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
    assert r["reason"] == "Fable slice capped: Opus on Max (Overflow crosses seats)", r


@test
def max_capped_goes_to_cursor_fable():
    week = su.route("bounded-question", snap(fable=100, claude_week=100, claude_5h=10, cursor_api_pct=10), NOW)
    assert week["mode"] == "suggest" and week["seat"] == "cursor" and week["family"] == "fable", week
    assert week["reason"] == "Max capped: Cursor Fable on Other Models", week
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
    assert ok["reason"] == "both Grok pools capped: Opus at low on Max", ok
    blocked = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_week=100, claude_5h=10), NOW)
    assert blocked["mode"] == "ask" and blocked["seat"] == "cursor" and blocked["family"] == "grok", blocked
    assert blocked["effort"] == "high" and blocked["variant"] == "fast", blocked
    assert blocked["reason"] == "both Grok pools capped and Max capped: ask Ryan", blocked
    unknown = snap(cursor_auto_pct=100, grok_pct=100, claude_5h=10)
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
    none = su.route("chair", snap(cursor_auto_pct=100, grok_pct=100, claude_week=100, claude_5h=100), NOW)
    assert none["mode"] == "ask" and none["seat"] == "cursor", none
    assert none["reason"] == "both Grok pools capped and Max capped: ask Ryan", none


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
    assert r["reason"] == "Max capped: Cursor Fable on Other Models", r
    session = snap(claude_5h=100, cursor_api_pct=31, fable_reset_ts=NOW)
    r = su.route("bounded-question", session, NOW)
    assert r["mode"] == "suggest" and r["reason"] == "Max capped: Cursor Fable on Other Models", r
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
    assert capped["reason"] == "both Grok pools capped: Opus on Max at high", capped
    same = su.route_lineup(snap(cursor_auto_pct=100, grok_pct=100, claude_week=10, claude_5h=10), NOW)
    assert same["lineup"]["chair"]["effort"] == capped["effort"] == "high", same
    assert same["lineup"]["builder"]["effort"] == "low", same


@test
def f4_zero_5h_without_reset_is_open():
    # Both Grok pools are capped, so the builder is Opus and the 5h state matters.
    r = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=0, claude_5h_reset_ts=None), NOW)
    assert r["mode"] == "suggest", r
    assert r["seat"] == "claude" and r["family"] == "opus" and r["effort"] == "low", r
    assert r["reason"] == "both Grok pools capped: Opus at low on Max", r
    past = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=0, claude_5h_reset_ts=NOW - 1), NOW)
    assert past["mode"] == "ask" and "Max 5h reading unknown" in past["reason"], past
    used = su.route("builder", snap(cursor_auto_pct=100, grok_pct=100, claude_5h=1, claude_5h_reset_ts=None), NOW)
    assert used["mode"] == "ask" and "Max 5h reading unknown" in used["reason"], used


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
    assert week["reason"] == "Max capped: Cursor Fable on Other Models", week
    session = su.route("bounded-question", snap(fable=100, claude_week=10, claude_5h=100, cursor_api_pct=10), NOW)
    assert session["seat"] == "cursor" and session["family"] == "fable" and session["effort"] == "xhigh", session
    assert session["reason"] == "Max capped: Cursor Fable on Other Models", session


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
            else:
                self._send(200, {"Content-Type": "application/json"}, _PROFILE_OK)
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
            elif mode == "denied":
                self._send(401, {"Content-Type": "application/json"}, _EXPIRED_BODY)
            else:
                self._send(200, {"Content-Type": "application/json"}, _USAGE_OK)
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
    }
    fns = {name: getattr(su, name) for name in ("codex", "cursor", "grok")}
    old_env = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")}
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cache:
            os.makedirs(os.path.join(home, ".claude"))
            with open(os.path.join(home, ".claude", ".credentials.json"), "w") as f:
                json.dump({"claudeAiOauth": {"accessToken": "fixture-token"}}, f)
            su.H = home
            su.HISTORY = os.path.join(home, "history.jsonl")
            su.CACHE_DIR = cache
            su.CACHE = os.path.join(cache, "latest.json")
            su.ANTHROPIC_PROFILE_URL = profile_url
            su.ANTHROPIC_USAGE_URL = usage_url
            su.NOW = time.time()
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
            stored = json.load(open(su._rate_limit_path()))
            assert list(stored) == [usage], stored
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
            success_as_of = su.NOW
            fresh = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert fresh["as_of"]["claude"] == success_as_of, fresh["as_of"]
            assert fresh["fable"] == 33 and fresh["claude_week"] == 22 and fresh["claude_5h"] == 11, fresh
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
            assert renewed["as_of"]["claude"] == su.NOW, renewed["as_of"]
            assert renewed["as_of"]["claude"] != success_as_of
            assert server.bad_auth == 0, server.bad_auth


@test
def profile_401_expired_token_is_not_a_rate_limit():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.profile_mode = "expired"
            server.usage_mode = "ok"
            first = _quiet_cache()
            second = _quiet_cache()
            assert server.profile_hits == 2, server.profile_hits
            assert server.usage_hits == 2, server.usage_hits
            assert server.retry_sent == [], server.retry_sent
            assert not os.path.exists(su._rate_limit_path()), "401 wrote a rate-limit deadline"
            assert first["as_of"]["claude"] == su.NOW and second["as_of"]["claude"] == su.NOW, (first, second)
            assert any("profile failed: HTTPError" in ln for ln in second["lines"]), second["lines"]
            assert any("weekly Fable 33%" in ln for ln in second["lines"]), second["lines"]
            assert server.bad_auth == 0, server.bad_auth


@test
def usage_429_without_retry_after_does_not_invent_a_window():
    with anthropic_server() as server:
        profile, usage = server_urls(server)
        with seat_http_sandbox(profile, usage):
            server.usage_mode = "ok"
            fresh = _quiet_cache()
            assert fresh["as_of"]["claude"] == su.NOW, fresh["as_of"]
            assert server.usage_hits == 1, server.usage_hits
            server.usage_mode = "bare429"
            failed = _quiet_cache()
            assert server.usage_hits == 2, server.usage_hits
            assert server.retry_sent == [], server.retry_sent
            assert "claude" not in failed["as_of"], failed["as_of"]
            assert any("usage failed: HTTPError" in ln for ln in failed["lines"]), failed["lines"]
            assert not os.path.exists(su._rate_limit_path()) or usage not in json.load(open(su._rate_limit_path()))
            _quiet_cache()
            assert server.usage_hits == 3, server.usage_hits


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
    assert [v["effort"] for v in r["lineup"]["voters"]] == ["high", "high"], r["lineup"]["voters"]
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "astra"], r


@test
def lineup_cursor_ahead_moves_to_supergrok_while_that_pool_is_under():
    r = su.route_lineup(snap(cursor_auto_pct=80), NOW)
    assert r["mode"] == "default", r
    assert r["lineup"]["chair"]["seat"] == "grok" and r["lineup"]["chair"]["effort"] == "high", r
    assert r["lineup"]["builder"]["seat"] == "grok" and r["lineup"]["builder"]["effort"] == "high", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "astra"], r


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
    assert [v["family"] for v in r["lineup"]["voters"]] == ["astra", "grok"], r
    grok = r["lineup"]["voters"][1]
    assert grok["effort"] == "high" and grok["seat"] == "grok" and grok["vendor"] == "xai", grok
    assert r["alternatives"] == [], r


@test
def lineup_cursor_capped_stays_on_supergrok_with_fable_and_astra():
    r = su.route_lineup(snap(cursor_auto_pct=100), NOW)
    assert r["mode"] == "default", r
    assert r["lineup"]["chair"]["family"] == "grok" and r["lineup"]["chair"]["seat"] == "grok", r
    assert r["lineup"]["builder"]["family"] == "grok" and r["lineup"]["builder"]["seat"] == "grok", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable", "astra"], r
    assert all(v["effort"] == "high" for v in r["lineup"]["voters"]), r


@test
def lineup_both_grok_pools_capped_degrades_to_opus_and_one_voter():
    r = su.route_lineup(snap(cursor_auto_pct=100, grok_exhausted=True), NOW)
    assert r["mode"] == "suggest" and r["lineup"] is not None, r
    assert r["lineup"]["chair"]["family"] == "opus" and r["lineup"]["builder"]["family"] == "opus", r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["astra"], r
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
def lineup_codex_capped_degrades_to_one_voter():
    r = su.route_lineup(snap(codex=100), NOW)
    assert r["mode"] == "suggest" and r["lineup"] is not None, r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["fable"], r
    assert "weaker:" in r["reason"], r


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
            assert row["mode"] == "suggest" and row["lineup"] is not None, row
            assert [v["family"] for v in row["lineup"]["voters"]] == ["fable"], row
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
    assert blocked["mode"] == "ask" and blocked["lineup"] is None, blocked
    assert "resets in 4.0h" in blocked["reason"], blocked
    assert "claude 5h full" in blocked["reason"], blocked


@test
def both_grok_pools_over_pace_seats_opus_and_no_grok_voter():
    s = snap(cursor_auto_pct=80, grok_pct=80)
    chair = su.route("chair", s, NOW)
    builder = su.route("builder", s, NOW)
    assert chair["family"] == "opus" and chair["effort"] == "high", chair
    assert builder["family"] == "opus" and builder["effort"] == "low", builder
    r = su.route_lineup(s, NOW)
    assert r["lineup"]["chair"]["effort"] == chair["effort"], r
    assert r["lineup"]["builder"]["effort"] == builder["effort"], r
    assert [v["family"] for v in r["lineup"]["voters"]] == ["astra"], r
    assert "weaker:" in r["reason"], r


@test
def supergrok_burn_is_spent_before_cursor():
    soon = NOW + 12 * 3600
    r = su.route("builder", snap(grok_pct=10, grok_reset_ts=soon), NOW)
    assert r["seat"] == "grok" and r["effort"] == "xhigh" and r["mode"] == "burn", r
    assert r["variant"] == "-", r
    chair = su.route("chair", snap(grok_pct=10, grok_reset_ts=soon), NOW)
    assert chair["seat"] == "grok" and chair["effort"] == "xhigh", chair


@test
def host_both_ahead_suggests_the_bot_and_names_over_pace():
    r = su.route_host(snap(bot_pct=80, claude_week=80), NOW)
    assert r["mode"] == "suggest" and r["suggestion"] == "grokbot", r
    assert "over pace" in r["reason"], r
    assert r["selects"] is False and "never" not in r, r
    assert [h["id"] for h in r["hosts"]] == ["grokbot", "opus"], r


UNDER_HARD_BUG = (
    "route: hard-bug | chatgpt | astra | low | - | suggest | "
    "Codex under pace: hard ticket to Astra (Pace rule) | reading 0m\n"
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
def muse_is_not_a_routing_input():
    for name in (
        "POOLS", "SEAT_KEYS", "ROLES", "ROUTE_RULES", "FAMILY_VENDOR", "_FAMILY_NAME",
        "VOTER_ROSTER", "LINE_SEATS", "LINEUP_SEATS", "HOST_SEATS", "BURN_POOLS", "RENEWAL_KEYS",
    ):
        blob = repr(getattr(su, name)).lower()
        assert "muse" not in blob, name
    for name in ("route", "_route_worker", "_apply_rule", "route_lineup", "route_host", "cmd_route", "pool_status"):
        consts = " ".join(_string_consts(getattr(su, name))).lower()
        assert "muse" not in consts, name
    extra = snap(muse=40, model="muse-spark-1.3")
    for role in ("bounded-question", "hard-bug", "builder", "chair"):
        assert su.route(role, snap(), NOW) == su.route(role, extra, NOW), role
    assert su.route_lineup(snap(), NOW) == su.route_lineup(extra, NOW)
    assert su.route_host(snap(), NOW) == su.route_host(extra, NOW)
    before = json.dumps(su.SUMMARY, sort_keys=True, default=str)
    with local_tz("UTC"):
        assert su.muse_line(now=NOW, path=str(MUSE_FIXTURES / "fresh.json")) == FRESH_MUSE_UTC
    assert json.dumps(su.SUMMARY, sort_keys=True, default=str) == before


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
def full_run_prints_muse_and_leaves_it_out_of_the_cache():
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
            assert all(not ln.startswith("muse") for ln in loud["lines"]), loud["lines"]
            assert not any("muse" in key for key in loud), loud
            written = json.load(open(cache))
            assert all(not str(ln).startswith("muse") for ln in written["lines"]), written["lines"]
            assert not any("muse" in key for key in written), written
        finally:
            su.CACHE, su.CACHE_DIR, su.SUMMARY, su.muse_usage_path = old
            for name, fn in saved.items():
                setattr(su, name, fn)


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
