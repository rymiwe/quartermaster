#!/usr/bin/env python3
"""Contract tests for seat-usage and seat-route-hook. No network, no real credentials."""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
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
CLAUDE_RENEWAL_2026_10_14 = 1791961200.0
CLAUDE_RENEWAL_2026_11_01 = 1793516400.0
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
        write_renewals(Path(home) / ".config" / "seat-usage" / "renewals.json", "2026-10-14", None)
        proc = run_bounded(claude_env(home, profile, usage))
        assert proc.returncode == 0, (proc.returncode, proc.stderr, proc.stdout)
        assert proc.stdout == ASK_LINE, proc.stdout
        assert proc.stderr == "", proc.stderr
        cache = json.loads((Path(home) / ".cache" / "seat-usage" / "latest.json").read_text())
        assert cache["claude_week"] == 100 and cache["fable"] == 100 and cache["claude_5h"] == 11, cache
        assert cache["claude_renews"] is None, cache.get("claude_renews")
        assert cache["claude_renews_ts"] == CLAUDE_RENEWAL_2026_10_14, cache.get("claude_renews_ts")
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
        write_renewals(Path(home) / ".config" / "seat-usage" / "renewals.json", "2026-10-14", None)
        cache_dir = Path(scratch) / "cache"
        asks_path = Path(scratch) / "asks.jsonl"
        renewals_path = Path(scratch) / "renewals.json"
        write_renewals(renewals_path, "2026-11-01", False)
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
        assert cache["claude_renews_ts"] == CLAUDE_RENEWAL_2026_11_01, cache.get("claude_renews_ts")
        assert cache["claude_week"] == 100, cache.get("claude_week")
        ask = json.loads(asks_path.read_text().splitlines()[0])
        assert ask["row"]["mode"] == "ask" and ask["row"]["reason"] == ASK_REASON, ask["row"]
        assert history_rows(home)[1]["pool"] == "claude weekly all"
        assert history_rows(home)[1]["used_pct"] == 100


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
