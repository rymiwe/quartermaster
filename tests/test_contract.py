#!/usr/bin/env python3
"""Contract tests for seat-usage and seat-route-hook. No network, no real credentials."""
import json
import os
import subprocess
import sys
import tempfile
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
