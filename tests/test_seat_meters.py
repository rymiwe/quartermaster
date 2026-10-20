#!/usr/bin/env python3
"""seat-meters draws a PNG from a cache fixture without touching the network.
Runs under pytest or as a script: python3 tests/test_seat_meters.py"""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "seat-meters")
try:
    from PIL import Image
except ImportError:
    Image = None
    if __name__ != "__main__":
        import pytest
        pytest.skip("Pillow not installed", allow_module_level=True)
NOW = time.time()
BLUE, TRACK = (0x5b, 0x8f, 0xd6), (0x2a, 0x33, 0x40)
iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def muse_doc(now=NOW):
    """Same shape seat-usage writes to ~/.cache/quartermaster/muse-usage.json."""
    return {"captured_at": iso(now - 300),
            "window": {"used_percent": 35, "window_duration_mins": 300, "resets_at": iso(now + 2 * 3600)},
            "weekly": {"used_percent": 50, "resets_at": now + 3 * 86400}}

def render(t, cache, muse=None, *flags):
    cp, mp, out = os.path.join(t, "latest.json"), os.path.join(t, "muse-usage.json"), os.path.join(t, "m.png")
    json.dump(cache, open(cp, "w"))
    if muse is not None:
        json.dump(muse, open(mp, "w"))
    env = {**os.environ, "SEAT_USAGE_CACHE": cp, "SEAT_USAGE_MUSE_FILE": mp}
    r = subprocess.run([sys.executable, BIN, *flags, out], env=env, capture_output=True, text=True)
    return r, out

def muse_band(out):
    """Pixels of the Muse row, the only row when the cache is empty (y 44.. in 1x units)."""
    im = Image.open(out).convert("RGB"); w, h = im.size
    return im, [im.getpixel((x, y)) for y in range(88, h - 28) for x in range(0, w, 2)]

def test_renders_full_cache():
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, {"ts": NOW, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
                            "claude_5h": 10, "claude_5h_reset_ts": NOW + 3600,
                            "cursor_api_pct": 20, "cursor_reset_ts": NOW + 29 * 86400, "cursor_window_s": 31 * 86400,
                            "bot_pct": 24, "bot_reset_ts": NOW + 5 * 86400, "bot_window_s": 7 * 86400,
                            "errors": {"muse": "no data yet"}}, muse_doc())
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == out
        assert open(out, "rb").read(8) == b"\x89PNG\r\n\x1a\n"

def test_empty_cache_still_renders():
    with tempfile.TemporaryDirectory() as t:
        r, _ = render(t, {"ts": NOW})
        assert r.returncode == 0, r.stderr

def test_muse_file_draws_a_muse_track():
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, {"ts": NOW}, muse_doc())
        assert r.returncode == 0, r.stderr
        im, band = muse_band(out)
        assert im.size[1] == 2 * (14 * 2 + 26 + 40)   # one Muse row with its 5h track
        assert BLUE in band and TRACK in band

def test_missing_or_stale_muse_file_says_no_reading():
    stale = muse_doc(); stale["window"]["resets_at"] = iso(NOW - 60)
    for muse in (None, stale, {"captured_at": iso(NOW)}):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, {"ts": NOW}, muse)
            assert r.returncode == 0, r.stderr
            im, band = muse_band(out)
            assert im.size[1] == 2 * (14 * 2 + 26 + 26)   # a Muse row, text only
            assert BLUE not in band and TRACK not in band

def test_phone_layout_is_tall_and_narrow():
    cache = {"ts": NOW, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
             "bot_pct": 24, "bot_reset_ts": NOW + 5 * 86400, "bot_window_s": 7 * 86400}
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, muse_doc(), "--phone")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == out
        w, h = Image.open(out).size
        r2, wide = render(t, cache, muse_doc())
        ww, wh = Image.open(wide).size
        assert w == 3 * 390, w              # phone width at 3x
        assert w < ww and h > wh, (w, h, ww, wh)   # narrower and taller than the wide layout

def test_nearly_finished_5h_window_stays_on_the_page():
    BG = (0x14, 0x1a, 0x22)
    cache = {"ts": NOW, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_5h": 90, "claude_5h_reset_ts": NOW + 60,
             "cursor_api_pct": 20, "cursor_reset_ts": NOW + 29 * 86400, "cursor_window_s": 31 * 86400}
    for flags, edge in (((), 14 * 2), (("--phone",), 10 * 3)):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, cache, muse_doc(), *flags)
            assert r.returncode == 0, r.stderr
            im = Image.open(out).convert("RGB")
            spill = [(x, y) for x in range(edge) for y in range(im.size[1]) if im.getpixel((x, y)) != BG]
            assert not spill, (flags, spill[:3])

def test_expired_5h_window_draws_no_bar():
    cache = {"ts": NOW - 9 * 3600, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_5h": 19, "claude_5h_reset_ts": NOW - 3600}
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, muse_doc())
        assert r.returncode == 0, r.stderr
        expired = Image.open(out).size[1]
        render(t, {**cache, "claude_5h_reset_ts": NOW + 3600}, muse_doc())
        assert expired < Image.open(out).size[1]   # the 5h line is gone

def _epoch(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def test_google_group_is_its_own_color_in_both_layouts():
    """Numbers match tests/fixtures/agy-usage.tsv: remaining 100/100/99 -> used 0/0/1."""
    week = _epoch("2026-10-27T01:39:26Z")
    five = _epoch("2026-10-20T06:39:26Z")
    argon = _epoch("2026-10-27T01:38:40Z")
    google = {
        "google_argon_week": 1, "google_argon_week_reset_ts": argon,
        "google_gemini_week": 0, "google_gemini_week_reset_ts": week,
        "google_gemini_5h": 0, "google_gemini_5h_reset_ts": five,
        "google_claudegpt_week": 0, "google_claudegpt_week_reset_ts": week,
        "google_claudegpt_5h": 0, "google_claudegpt_5h_reset_ts": five,
    }
    GOOGLE = (0x42, 0x85, 0xf4)
    with tempfile.TemporaryDirectory() as t:
        base = {"ts": NOW}
        plain, plain_out = render(t, base)
        assert plain.returncode == 0, plain.stderr
        def colors(path):
            im = Image.open(path).convert("RGB")
            w, h = im.size
            return im, {im.getpixel((x, y)) for y in range(h) for x in range(w)}
        plain_im, plain_px = colors(plain_out)
        plain_h = plain_im.size[1]
        wide, wide_out = render(t, {**base, **google})
        assert wide.returncode == 0, wide.stderr
        wide_im, wide_px = colors(wide_out)
        wide_h = wide_im.size[1]
        phone, phone_out = render(t, {**base, **google}, None, "--phone")
        assert phone.returncode == 0, phone.stderr
        phone_im, phone_px = colors(phone_out)
        assert GOOGLE in wide_px
        assert GOOGLE in phone_px
        assert GOOGLE not in plain_px
        assert wide_h > plain_h
        assert phone_im.size[0] == 3 * 390
        weekly_only = {k: v for k, v in google.items() if not k.endswith("_5h") and "5h_reset" not in k}
        short, short_out = render(t, {**base, **weekly_only})
        assert short.returncode == 0, short.stderr
        assert Image.open(short_out).size[1] < wide_h


if __name__ == "__main__":
    if Image is None:
        print("skip: Pillow not installed"); sys.exit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try: fn(); print("ok " + fn.__name__)
        except AssertionError as e: failed += 1; print(f"FAIL {fn.__name__} {e}")
    print(f"{len(tests) - failed} passed, {failed} failed"); sys.exit(1 if failed else 0)
