#!/usr/bin/env python3
"""seat-meters draws a PNG from a cache fixture without touching the network."""
import json, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "seat-meters")
passed = failed = 0

def check(name, cond):
    global passed, failed
    print(("ok " if cond else "FAIL ") + name)
    passed, failed = passed + bool(cond), failed + (not cond)

try:
    import PIL  # noqa: F401
except ImportError:
    print("skip: Pillow not installed"); sys.exit(0)

now = time.time()
with tempfile.TemporaryDirectory() as t:
    cache = os.path.join(t, "latest.json"); out = os.path.join(t, "m.png")
    json.dump({"ts": now, "claude_week": 42, "claude_week_reset_ts": now + 4 * 86400,
               "claude_5h": 10, "claude_5h_reset_ts": now + 3600,
               "cursor_api_pct": 20, "cursor_reset_ts": now + 29 * 86400, "cursor_window_s": 31 * 86400,
               "bot_pct": 24, "bot_reset_ts": now + 5 * 86400, "bot_window_s": 7 * 86400,
               "errors": {"muse": "no data yet"}}, open(cache, "w"))
    r = subprocess.run([sys.executable, BIN, out], env={**os.environ, "SEAT_USAGE_CACHE": cache}, capture_output=True, text=True)
    check("exits_0", r.returncode == 0)
    check("prints_output_path", r.stdout.strip() == out)
    with open(out, "rb") as f:
        check("writes_a_png", f.read(8) == b"\x89PNG\r\n\x1a\n")
    json.dump({"ts": now}, open(cache, "w"))
    r = subprocess.run([sys.executable, BIN, out], env={**os.environ, "SEAT_USAGE_CACHE": cache}, capture_output=True, text=True)
    check("empty_cache_still_renders", r.returncode == 0)
print(f"{passed} passed, {failed} failed"); sys.exit(1 if failed else 0)
