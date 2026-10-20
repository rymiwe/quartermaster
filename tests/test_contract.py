#!/usr/bin/env python3
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
BIN = Path(os.environ["SEAT_USAGE_BIN"]) if os.environ.get("SEAT_USAGE_BIN") else HERE.parents[0] / "bin" / "seat-usage"
HOOK = Path(os.environ["SEAT_ROUTE_HOOK"]) if os.environ.get("SEAT_ROUTE_HOOK") else HERE.parents[0] / "hooks" / "seat-route-hook"

ROUTE_TEXT = (
    "route: builder | cursor | grok | high | fast | default | "
    "Grok on Cursor: Cursor Models under pace | reading 0m"
)
ROUTE_JSON = (
    '[{"role": "builder", "seat": "cursor", "family": "grok", "effort": "high", '
    '"variant": "fast", "mode": "default", "reason": "Grok on Cursor: Cursor Models under pace", '
    '"reading_age_s": 0}]'
)
EXPECTED_HOOK = json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": ROUTE_TEXT,
    }
}) + "\n"
AGENT = '{"tool_name": "Agent"}\n'
ASK_LINE = (
    "route: bounded-question | claude | fable | - | - | ask | "
    "Other Models reading unknown: fix the failed reading, then ask Ryan | reading 0m\n"
)
ASK_REASON = "Other Models reading unknown: fix the failed reading, then ask Ryan"
CLAUDE_RENEWAL_2099_10_14 = 4095644400.0
CLAUDE_RENEWAL_2099_11_01 = 4097199600.0
_PROFILE_BODY = json.dumps({
    "organization": {"rate_limit_tier": "default_max", "subscription_status": "active"},
}).encode()
_USAGE_BODY = json.dumps({"limits": [
    {"kind": "session", "percent": 11, "resets_at": "2099-01-01T00:00:00Z"},
    {"kind": "weekly_all", "percent": 100, "resets_at": "2099-01-01T00:00:00Z"},
    {"kind": "weekly_scoped", "percent": 100, "resets_at": "2099-01-01T00:00:00Z",
     "scope": {"model": {"display_name": "Fable"}}},
]}).encode()

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def write_seat_usage(home, script):
    path = Path(home) / ".local" / "bin" / "seat-usage"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o755)
    return path


def good_seat_usage(marker=None):
    mark = ""
    if marker is not None:
        mark = f"printf 'called\\n' >> {json.dumps(marker)}\n"
    return f"#!/bin/sh\n{mark}printf '%s\\n' {json.dumps(ROUTE_JSON)}\n"


def run_hook(home, extra=None, timeout=15):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=AGENT,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def assert_hook_route(proc):
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == EXPECTED_HOOK, proc.stdout
    assert proc.stderr == "", proc.stderr


def assert_hook_quiet(proc):
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == "", proc.stdout
    assert proc.stderr == "", proc.stderr


@test
def hook_prints_route_and_stays_quiet_when_seat_usage_is_missing():
    with tempfile.TemporaryDirectory() as home:
        binary = write_seat_usage(home, good_seat_usage())
        assert_hook_route(run_hook(home))
        binary.unlink()
        assert_hook_quiet(run_hook(home))


@test
def hook_prints_route_and_stays_quiet_when_seat_usage_exits_nonzero():
    with tempfile.TemporaryDirectory() as home:
        write_seat_usage(home, good_seat_usage())
        assert_hook_route(run_hook(home))
        write_seat_usage(home, good_seat_usage() + "exit 1\n")
        assert_hook_quiet(run_hook(home))


@test
def hook_prints_route_and_stays_quiet_when_seat_usage_prints_junk():
    with tempfile.TemporaryDirectory() as home:
        write_seat_usage(home, good_seat_usage())
        assert_hook_route(run_hook(home))
        write_seat_usage(home, "#!/bin/sh\nprintf '%s\\n' 'not-json'\n")
        assert_hook_quiet(run_hook(home))


@test
def hook_prints_route_and_stays_quiet_when_seat_usage_times_out():
    with tempfile.TemporaryDirectory() as home:
        write_seat_usage(home, good_seat_usage())
        assert_hook_route(run_hook(home))
        write_seat_usage(home, "#!/bin/sh\nsleep 120\n")
        assert_hook_quiet(run_hook(home, timeout=60))


@test
def hook_prints_route_and_stays_silent_for_council_voter():
    with tempfile.TemporaryDirectory() as home:
        marker = str(Path(home) / "called.txt")
        write_seat_usage(home, good_seat_usage(marker))
        assert_hook_route(run_hook(home))
        assert Path(marker).read_text() == "called\n", Path(marker).read_text()
        Path(marker).unlink()
        assert_hook_quiet(run_hook(home, {"COUNCIL_VOTER": "1"}))
        assert not Path(marker).exists(), "COUNCIL_VOTER=1 still ran seat-usage"


class _AnthropicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path.endswith("/api/oauth/profile"):
            body = _PROFILE_BODY
        elif self.path.endswith("/api/oauth/usage"):
            body = _USAGE_BODY
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextlib.contextmanager
def loopback_anthropic():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AnthropicHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        yield base + "/api/oauth/profile", base + "/api/oauth/usage"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def claude_env(home, profile, usage, extra=None):
    if not profile.startswith("http://127.0.0.1:") or not usage.startswith("http://127.0.0.1:"):
        raise AssertionError("refusing a non-local anthropic url")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SEAT_USAGE_ANTHROPIC_PROFILE": profile,
        "SEAT_USAGE_ANTHROPIC_USAGE": usage,
        # Runs back to back against one cache; the call floor would serve the first reading.
        "SEAT_USAGE_MIN_INTERVAL_S": "0",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }
    if extra:
        env.update(extra)
    return env


def write_credentials(home):
    path = Path(home) / ".claude"
    path.mkdir(parents=True, exist_ok=True)
    (path / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "fixture-token"},
    }))


def write_renewals(path, at, renews):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claude": {"at": at, "renews": renews}}))


def run_bounded(env):
    return subprocess.run(
        [sys.executable, str(BIN), "--route", "bounded-question"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def history_rows(home):
    path = Path(home) / ".local" / "state" / "seat-usage" / "history.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@test
def default_home_receives_cache_asks_history_and_renewals():
    with tempfile.TemporaryDirectory() as home, loopback_anthropic() as (profile, usage):
        write_credentials(home)
        write_renewals(Path(home) / ".config" / "seat-usage" / "renewals.json", "2099-10-14", None)
        proc = run_bounded(claude_env(home, profile, usage))
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        assert proc.stdout == ASK_LINE, proc.stdout
        assert proc.stderr == "", proc.stderr
        cache = json.loads((Path(home) / ".cache" / "seat-usage" / "latest.json").read_text())
        assert cache["claude_week"] == 100 and cache["fable"] == 100 and cache["claude_5h"] == 11, cache
        assert cache["claude_renews"] is None, cache.get("claude_renews")
        assert cache["claude_renews_ts"] == CLAUDE_RENEWAL_2099_10_14, cache.get("claude_renews_ts")
        assert cache["lines"][0].startswith("claude   | max active"), cache["lines"]
        asks = (Path(home) / ".local" / "state" / "seat-usage" / "asks.jsonl").read_text().splitlines()
        assert len(asks) == 1, asks
        ask = json.loads(asks[0])
        assert ask["row"]["mode"] == "ask" and ask["row"]["reason"] == ASK_REASON, ask["row"]
        assert [(row["seat"], row["pool"], row["used_pct"], row["source"]) for row in history_rows(home)] == [
            ("claude", "claude 5h", 11, "live"),
            ("claude", "claude weekly all", 100, "live"),
            ("claude", "claude weekly Fable", 100, "live"),
        ]


@test
def env_overrides_redirect_cache_asks_and_renewals():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as scratch, loopback_anthropic() as (profile, usage):
        write_credentials(home)
        write_renewals(Path(home) / ".config" / "seat-usage" / "renewals.json", "2099-10-14", None)
        cache_dir = Path(scratch) / "cache"
        asks_path = Path(scratch) / "asks.jsonl"
        renewals_path = Path(scratch) / "renewals.json"
        write_renewals(renewals_path, "2099-11-01", False)
        proc = run_bounded(claude_env(home, profile, usage, {
            "SEAT_USAGE_CACHE_DIR": str(cache_dir),
            "SEAT_USAGE_ASKS": str(asks_path),
            "SEAT_USAGE_RENEWALS": str(renewals_path),
        }))
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        assert proc.stdout == ASK_LINE, proc.stdout
        assert proc.stderr == "", proc.stderr
        assert not (Path(home) / ".cache" / "seat-usage" / "latest.json").exists()
        assert not (Path(home) / ".local" / "state" / "seat-usage" / "asks.jsonl").exists()
        cache = json.loads((cache_dir / "latest.json").read_text())
        assert cache["claude_renews"] is False, cache.get("claude_renews")
        assert cache["claude_renews_ts"] == CLAUDE_RENEWAL_2099_11_01, cache.get("claude_renews_ts")
        assert cache["claude_week"] == 100, cache.get("claude_week")
        ask = json.loads(asks_path.read_text().splitlines()[0])
        assert ask["row"]["mode"] == "ask" and ask["row"]["reason"] == ASK_REASON, ask["row"]
        assert history_rows(home)[1]["pool"] == "claude weekly all"
        assert history_rows(home)[1]["used_pct"] == 100


BARE_ROUTE = (
    "route: bounded-question | claude | fable | - | - | suggest | "
    "Fable under pace: score 0.87 (fit 0.90, headroom 1.00, cost 3, lose-it 0.00) | reading 0m\n"
    "route: hard-bug | chatgpt | astra | low | - | suggest | "
    "Codex under pace: score 0.85 (fit 0.88, headroom 1.00, cost 3, lose-it 0.00) | reading 0m\n"
    "route: builder | cursor | grok | high | fast | default | "
    "Cursor Models under pace: score 0.78 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00) | reading 0m\n"
    "route: chair | cursor | grok | high | fast | default | "
    "Cursor Models under pace: score 0.78 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00) | reading 0m\n"
)
CACHED_LINE = (
    "[seat-usage] cached 0m ago; run ~/.local/bin/seat-usage for live numbers\n"
    "  claude   | max active\n"
    "  muse     | no data yet\n"
)
HOOK_ROW_KEYS = {"role", "seat", "family", "effort", "variant", "mode", "reason", "reading_age_s"}
LAUNCH_ROWS = (
    {"role": "bounded-question", "seat": "claude", "family": "fable", "effort": "-",
     "variant": "-", "mode": "suggest", "reason": "Fable under pace: score 0.87 (fit 0.90, headroom 1.00, cost 3, lose-it 0.00)"},
    {"role": "hard-bug", "seat": "chatgpt", "family": "astra", "effort": "low",
     "variant": "-", "mode": "suggest", "reason": "Codex under pace: score 0.85 (fit 0.88, headroom 1.00, cost 3, lose-it 0.00)"},
    {"role": "builder", "seat": "cursor", "family": "grok", "effort": "high",
     "variant": "fast", "mode": "default", "reason": "Cursor Models under pace: score 0.78 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00)"},
    {"role": "chair", "seat": "cursor", "family": "grok", "effort": "high",
     "variant": "fast", "mode": "default", "reason": "Cursor Models under pace: score 0.78 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00)"},
)


def fresh_cache(now):
    return {
        "v": 1,
        "ts": now,
        "as_of": {seat: now for seat in ("claude", "chatgpt", "cursor", "grokbot", "grok", "muse")},
        "claude_5h": 10,
        "claude_5h_reset_ts": now + 4 * 3600,
        "claude_week": 10,
        "claude_week_reset_ts": now + 3 * 86400,
        "fable": 10,
        "fable_reset_ts": now + 3 * 86400,
        "codex": 10,
        "codex_reset_ts": now + 3 * 86400,
        "codex_window_s": 7 * 86400,
        "cursor_auto_pct": 15,
        "cursor_api_pct": 10,
        "cursor_pct": 15,
        "cursor_reset_ts": now + 15 * 86400,
        "cursor_window_s": 30 * 86400,
        "grok_pct": 10,
        "grok_reset_ts": now + 3 * 86400,
        "grok_window_s": 7 * 86400,
        "grok_exhausted": False,
        "bot_pct": 10,
        "bot_reset_ts": now + 3 * 86400,
        "bot_window_s": 7 * 86400,
        "muse_5h": 10,
        "muse_5h_reset_ts": now + 4 * 3600,
        "muse_week": 10,
        "muse_week_reset_ts": now + 3 * 86400,
        "lines": ["claude   | max active"],
        "burn": [],
    }


def run_bin(args, env):
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def cached_env(cache_dir, extra=None):
    home = Path(cache_dir) / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SEAT_USAGE_CACHE_DIR": str(cache_dir),
        "SEAT_USAGE_ASKS": str(Path(cache_dir) / "asks.jsonl"),
        "QUARTERMASTER_NO_UPDATE": "1",
    }
    if extra:
        env.update(extra)
    return env


def seed_and_run(args, extra=None):
    with tempfile.TemporaryDirectory() as cache_dir:
        now = time.time()
        (Path(cache_dir) / "latest.json").write_text(json.dumps(fresh_cache(now)))
        return run_bin(args, cached_env(cache_dir, extra))


def assert_hook_row(row, expected):
    assert set(row) == HOOK_ROW_KEYS, set(row)
    for key, value in expected.items():
        assert row[key] == value, (key, row)
    assert isinstance(row["reading_age_s"], (int, float)) and not isinstance(row["reading_age_s"], bool), row
    assert int(row["reading_age_s"] // 60) == 0, row["reading_age_s"]


@test
def cached_lineup_json_names_the_xai_pair():
    proc = seed_and_run(["--route", "lineup", "--cached", "--json"])
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stderr == "", proc.stderr
    row = json.loads(proc.stdout)[0]
    assert row["role"] == "lineup" and row["mode"] == "default", row
    assert row["reason"] == "xAI chairs and builds: Fable, Astra, and Wonder full vote", row["reason"]
    assert row["rule"] == row["reason"], row["rule"]
    chair = row["lineup"]["chair"]
    builder = row["lineup"]["builder"]
    assert chair["seat"] == "cursor" and chair["family"] == "grok" and chair["effort"] == "high", chair
    assert chair["variant"] == "fast" and chair["mode"] == "default", chair
    assert chair["reason"] == "Cursor Models under pace: score 0.78 (fit 0.80, headroom 1.00, cost 2, lose-it 0.00)", chair["reason"]
    assert builder["role"] == "builder" and builder["seat"] == "cursor" and builder["effort"] == "high", builder
    assert builder["reason"] == chair["reason"], builder
    voters = [(v["role"], v["seat"], v["family"], v["effort"], v["mode"]) for v in row["lineup"]["voters"]]
    assert voters == [
        ("voter", "claude", "fable", "high", "default"),
        ("voter", "chatgpt", "astra", "high", "default"),
        ("voter", "muse", "wonder-full", "high", "default"),
    ], voters


@test
def cached_host_json_suggests_grokbot_and_does_not_select():
    proc = seed_and_run(["--route", "host", "--cached", "--json"])
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stderr == "", proc.stderr
    row = json.loads(proc.stdout)[0]
    assert row["role"] == "host" and row["suggestion"] == "grokbot" and row["selects"] is False, row
    assert row["seat"] == "grokbot" and row["family"] == "grokbot" and row["effort"] == "-", row
    assert row["variant"] == "-" and row["mode"] == "suggest", row
    assert row["reason"] == "", row["reason"]
    assert row["lean"] == ["cursor_models", "grok"], row["lean"]
    assert row["hosts"][1]["binding"] == "claude_week", row["hosts"][1]
    assert "(" not in row["reason"] and ")" not in row["reason"]
    hosts = [(h["id"], h["seat"], h["family"], h["effort"], h["state"], h["available"], h["used_pct"]) for h in row["hosts"]]
    assert hosts == [
        ("grokbot", "grokbot", "grokbot", "-", "under", True, 10),
        ("opus", "claude", "opus", "high", "under", True, 10),
    ], hosts


@test
def bare_cached_route_prints_the_four_launch_lines():
    proc = seed_and_run(["--route", "--cached"])
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == BARE_ROUTE, proc.stdout
    assert proc.stderr == "", proc.stderr


@test
def cached_route_json_rows_have_the_hook_keys():
    proc = seed_and_run(["--route", "--cached", "--json"])
    assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stderr == "", proc.stderr
    rows = json.loads(proc.stdout)
    assert len(rows) == len(LAUNCH_ROWS), rows
    for row, expected in zip(rows, LAUNCH_ROWS):
        assert_hook_row(row, expected)


@test
def cached_is_silent_for_council_voter_and_prints_the_seed():
    shown = seed_and_run(["--cached"])
    assert shown.returncode == 0, (shown.returncode, shown.stderr, shown.stdout)
    assert shown.stdout == CACHED_LINE, shown.stdout
    assert shown.stderr == "", shown.stderr
    quiet = seed_and_run(["--cached"], {"COUNCIL_VOTER": "1"})
    assert quiet.returncode == 0, (quiet.returncode, quiet.stderr, quiet.stdout)
    assert quiet.stdout == "" and quiet.stderr == "", (quiet.stdout, quiet.stderr)


@test
def unknown_role_exits_2_with_the_role_list():
    with tempfile.TemporaryDirectory() as home:
        proc = run_bin(["--route", "nope"], {
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
    assert proc.returncode == 2, proc.returncode
    assert proc.stdout == "", proc.stdout
    assert proc.stderr == "unknown role nope; roles: bounded-question, hard-bug, builder, chair, lineup, host, ask\n", proc.stderr


@test
def voter_route_exits_2():
    with tempfile.TemporaryDirectory() as home:
        proc = run_bin(["--route", "voter"], {
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
    assert proc.returncode == 2, proc.returncode
    assert proc.stdout == "", proc.stdout
    assert proc.stderr == "voters do not route\n", proc.stderr


@test
def example_renewals_schema_sets_the_term_and_a_placeholder_date_does_not():
    example_path = HERE.parents[0] / "renewals.example.json"
    example = json.loads(example_path.read_text())
    assert set(example) == {"claude"}, example
    assert example["claude"]["at"] == "YYYY-MM-DD", example["claude"]
    assert example["claude"]["renews"] is None, example["claude"]
    with tempfile.TemporaryDirectory() as home, loopback_anthropic() as (profile, usage):
        write_credentials(home)
        cache_path = Path(home) / ".cache" / "seat-usage" / "latest.json"
        placeholder = run_bounded(claude_env(home, profile, usage, {
            "SEAT_USAGE_RENEWALS": str(example_path),
        }))
        assert placeholder.returncode == 0, (placeholder.returncode, placeholder.stderr, placeholder.stdout)
        assert placeholder.stdout == ASK_LINE, placeholder.stdout
        placeholder_cache = json.loads(cache_path.read_text())
        assert placeholder_cache["claude_week"] == 100, placeholder_cache.get("claude_week")
        assert "claude_renews_ts" not in placeholder_cache, placeholder_cache.get("claude_renews_ts")
        assert "claude_renews" not in placeholder_cache, placeholder_cache.get("claude_renews")
        dated = json.loads(json.dumps(example))
        dated["claude"]["at"] = "2099-10-14"
        dated_path = Path(home) / "renewals.json"
        dated_path.write_text(json.dumps(dated))
        applied = run_bounded(claude_env(home, profile, usage, {
            "SEAT_USAGE_RENEWALS": str(dated_path),
        }))
        assert applied.returncode == 0, (applied.returncode, applied.stderr, applied.stdout)
        assert applied.stdout == ASK_LINE, applied.stdout
        applied_cache = json.loads(cache_path.read_text())
        assert applied_cache["claude_renews"] is None, applied_cache.get("claude_renews")
        assert applied_cache["claude_renews_ts"] == CLAUDE_RENEWAL_2099_10_14, applied_cache.get("claude_renews_ts")
        assert applied_cache["claude_week"] == 100, applied_cache.get("claude_week")


@test
def missing_renewals_file_still_prints_the_route():
    with tempfile.TemporaryDirectory() as home, loopback_anthropic() as (profile, usage):
        write_credentials(home)
        renewals = Path(home) / ".config" / "seat-usage" / "renewals.json"
        assert not renewals.exists()
        proc = run_bounded(claude_env(home, profile, usage))
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        assert proc.stdout == ASK_LINE, proc.stdout
        assert proc.stderr == "", proc.stderr
        cache = json.loads((Path(home) / ".cache" / "seat-usage" / "latest.json").read_text())
        assert cache["claude_week"] == 100 and cache["fable"] == 100 and cache["claude_5h"] == 11, cache
        assert "claude_renews_ts" not in cache and "claude_renews" not in cache, cache


def main():
    wanted = sys.argv[1:]
    fns = TESTS
    if wanted:
        names = {fn.__name__ for fn in TESTS}
        missing = [name for name in wanted if name not in names]
        if missing:
            print("unknown test " + ", ".join(missing))
            return 2
        fns = [fn for fn in TESTS if fn.__name__ in set(wanted)]
    passed = failed = 0
    for fn in fns:
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
