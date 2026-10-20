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
BLUE, RED, INK, BG = (0x5b, 0x8f, 0xd6), (0xc4, 0x3c, 0x3c), (0xe6, 0xeb, 0xf2), (0x14, 0x1a, 0x22)
over = lambda c, under, a: tuple(round(u + (x - u) * a) for x, u in zip(c, under))
TRACK = over(INK, BG, 0.14)  # the track is ink at 14% over the page, like orderly's
near = lambda p, q, tol=2: all(abs(a - b) <= tol for a, b in zip(p, q))

def runs(px, ok, run=12):
    """Indexes in runs of `run` or more pixels that satisfy `ok`: a bar, not antialiased text."""
    out, n = set(), 0
    for i, p in enumerate(px + [None]):
        if p is not None and ok(p): n += 1; continue
        if n >= run: out.update(range(i - n, i))
        n = 0
    return out
has_run = lambda px, ok, run=12: bool(runs(px, ok, run))
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
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("SEAT_USAGE_")},
           "SEAT_USAGE_CACHE": cp, "SEAT_USAGE_MUSE_FILE": mp}
    r = subprocess.run([sys.executable, BIN, *flags, out], env=env, capture_output=True, text=True)
    return r, out

O_INK, O_PANEL2 = (0xe4, 0xe8, 0xee), (0x20, 0x26, 0x2e)  # orderly's dark palette, used by --phone
PHONE_CX0 = 6 + 14 * 1.3  # phone: frame inset plus the rail's 14px padding, at K = 1.3

def phone_text(t, cache, muse=None, name="latest.json"):
    """The phone layout's words, from --text: host line, card headers, pool titles, chips, stats."""
    cp, mp, out = os.path.join(t, name), os.path.join(t, "muse-usage.json"), os.path.join(t, "p.png")
    json.dump(cache, open(cp, "w"))
    if muse is not None:
        json.dump(muse, open(mp, "w"))
    elif os.path.exists(mp):
        os.remove(mp)
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("SEAT_USAGE_")},
           "SEAT_USAGE_CACHE": cp, "SEAT_USAGE_MUSE_FILE": mp}
    r = subprocess.run([sys.executable, BIN, "--phone", "--text", out], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert open(out, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    return json.loads(r.stdout)

def muse_band(out):
    """Pixels of the Muse row, the only row when the cache is empty (y 44.. in 1x units)."""
    im = Image.open(out).convert("RGB"); w, h = im.size
    return im, [[im.getpixel((x, y)) for x in range(w)] for y in range(88, h - 28)]

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
        assert any(BLUE in row for row in band) and any(has_run(row, lambda p: near(p, TRACK)) for row in band)

def test_missing_or_stale_muse_file_says_no_reading():
    stale = muse_doc(); stale["window"]["resets_at"] = iso(NOW - 60)
    for muse in (None, stale, {"captured_at": iso(NOW)}):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, {"ts": NOW}, muse)
            assert r.returncode == 0, r.stderr
            im, band = muse_band(out)
            assert im.size[1] == 2 * (14 * 2 + 26 + 26)   # a Muse row, text only
            assert not any(BLUE in row for row in band)
            assert not any(has_run(row, lambda p: near(p, TRACK)) for row in band)

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
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, muse_doc())
        assert r.returncode == 0, r.stderr
        im = Image.open(out).convert("RGB")
        spill = [(x, y) for x in range(14 * 2) for y in range(im.size[1]) if im.getpixel((x, y)) != BG]
        assert not spill, spill[:3]
    # phone: left of the cards is only the page, the frame and its border (orderly's palette)
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, muse_doc(), "--phone")
        assert r.returncode == 0, r.stderr
        im = Image.open(out).convert("RGB")
        page = [(0x14, 0x17, 0x1c), (0x20, 0x26, 0x2e), (0x2b, 0x32, 0x3c)]
        edge = int(PHONE_CX0 * 3) - 1
        spill = [(x, y) for x in range(edge) for y in range(im.size[1])
                 if not any(near(im.getpixel((x, y)), c, 12) for c in page)]
        assert not spill, spill[:3]

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
    # The fixture's resets, moved to now: absolute stamps expired at 2026-10-20T06:39:26Z and dropped the 5h rows.
    week, five, argon = NOW + 7 * 86400 - 60, NOW + 5 * 3600 - 60, NOW + 7 * 86400 - 106
    google = {
        "google_argon_week": 1, "google_argon_week_reset_ts": argon,
        "google_gemini_week": 0, "google_gemini_week_reset_ts": week,
        "google_gemini_5h": 0, "google_gemini_5h_reset_ts": five,
        "google_claudegpt_week": 0, "google_claudegpt_week_reset_ts": week,
        "google_claudegpt_5h": 0, "google_claudegpt_5h_reset_ts": five,
    }
    GOOGLE = (0x54, 0x68, 0xf0)
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


def test_phone_5h_track_has_no_text_beside_it():
    """The 5h figure moved to the stats line under the bars, so no text shares the 5h track's rows."""
    cache = {"ts": NOW, "google_gemini_week": 5, "google_gemini_week_reset_ts": NOW + 86400,
             "google_gemini_5h": 20, "google_gemini_5h_reset_ts": NOW + 3 * 3600}
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, None, "--phone")
        assert r.returncode == 0, r.stderr
        im = Image.open(out).convert("RGB"); w, h = im.size
        # the track is orderly's ink at 14% over the Google card's 9% tint over the rail
        track = over(O_INK, over((0x54, 0x68, 0xf0), O_PANEL2, 0.09), 0.14)
        is_bar = lambda p: near(p, track) or p in (BLUE, RED)
        rows = [y for y in range(h) if has_run([im.getpixel((x, y)) for x in range(w)], is_bar)]
        bands = [[rows[0]]] if rows else []
        for y in rows[1:]:
            (bands[-1].append(y) if y - bands[-1][-1] <= 1 else bands.append([y]))
        assert len(bands) == 2, bands
        five = bands[1]
        lo, hi = int(PHONE_CX0 * 3) + 12, w - int(PHONE_CX0 * 3)
        bar = {x for y in five for x in runs([im.getpixel((x, y)) for x in range(w)], is_bar) if lo <= x < hi}
        assert bar, "no 5h track drawn"
        def dim_text(p):  # muted label text, antialiased against the card: grey and clearly lit
            return not is_bar(p) and not near(p, O_INK, 8) and max(p) - min(p) < 48 and sum(p) / 3 > 90
        tick = {x for x in range(lo, hi) if all(near(im.getpixel((x, y)), O_INK, 8) for y in five)}
        ink = {x for y in five for x in range(lo, hi)
               if dim_text(im.getpixel((x, y))) and not any(abs(x - t) <= 4 for t in tick)}
        assert not ink, sorted(ink)[:5]
        pools = phone_text(t, cache)["cards"][0]["pools"]
        assert pools[0]["stats"].startswith("5% \u00b7 5h 20%"), pools[0]["stats"]

def test_phone_over_pace_segment_is_red_on_both_tracks():
    """Use past the pace tick draws red on the weekly track and, at full strength, on the 5h one."""
    def red_bands(week, five):
        cache = {"ts": NOW, "claude_week": week, "claude_week_reset_ts": NOW + 4 * 86400,
                 "claude_5h": five, "claude_5h_reset_ts": NOW + 2.5 * 3600}
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, cache, None, "--phone")
            assert r.returncode == 0, r.stderr
            im = Image.open(out).convert("RGB"); w, h = im.size
            rows = [y for y in range(h) if any(im.getpixel((x, y)) == RED for x in range(0, w, 2))]
            bands = [[rows[0]]] if rows else []
            for y in rows[1:]:
                (bands[-1].append(y) if y - bands[-1][-1] <= 1 else bands.append([y]))
            return bands
    assert len(red_bands(60, 90)) == 2   # weekly 60% at 43% elapsed, 5h 90% at 50% elapsed
    assert red_bands(10, 10) == []       # both under pace: no red

def test_tiny_nonzero_fill_still_renders():
    """A fill thinner than a supersampled pixel draws nothing rather than crashing Pillow."""
    for flags in ((), ("--phone",)):
        with tempfile.TemporaryDirectory() as t:
            r, _ = render(t, {"ts": NOW, "claude_week": 0.02, "claude_week_reset_ts": NOW + 4 * 86400,
                              "claude_5h": 0.02, "claude_5h_reset_ts": NOW + 3600}, None, *flags)
            assert r.returncode == 0, r.stderr

def test_phone_over_pace_5h_blue_stays_dim():
    """Over pace, the 5h fill's blue head keeps the 5h track's 55% strength; only the red tail is full."""
    cache = {"ts": NOW, "claude_week": 10, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_5h": 90, "claude_5h_reset_ts": NOW + 2.5 * 3600}
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, None, "--phone")
        assert r.returncode == 0, r.stderr
        im = Image.open(out).convert("RGB"); w, h = im.size
        red_rows = [y for y in range(h) if any(im.getpixel((x, y)) == RED for x in range(w))]
        assert red_rows, "no over-pace red on the 5h track"
        five = [[im.getpixel((x, y)) for x in range(w)] for y in red_rows]
        assert not any(BLUE in row for row in five), "5h blue drawn at full strength"
        dim_blue = lambda p: p[2] - p[0] > 40 and p[2] < BLUE[2] - 30  # blue over the tinted panel's track
        assert any(has_run(row, dim_blue, 6) for row in five), "no dim blue head on the 5h track"

def _md(ts):
    dt = datetime.fromtimestamp(ts); return f"{dt.month}/{dt.day}"
def _wd(ts):
    return datetime.fromtimestamp(ts).strftime("%a")
def _pool(doc, title):
    return next(p for c in doc["cards"] for p in c["pools"] if p["title"] == title)
def _card(doc, title):
    return next(c for c in doc["cards"] if any(p["title"] == title for p in c["pools"]))
FRESH = {"claude": NOW, "chatgpt": NOW, "cursor": NOW, "grokbot": NOW, "grok": NOW, "google": NOW}

def test_phone_stats_line_reads_like_orderly():
    cache = {"ts": NOW, "as_of": FRESH, "claude_week": 55.4, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_5h": 1, "claude_5h_reset_ts": NOW + 2.5 * 3600, "grok_pct": 19, "grok_reset_ts": NOW + 3 * 86400,
             "grok_window_s": 7 * 86400, "cursor_api_pct": 20.5, "cursor_reset_ts": NOW + 20 * 86400,
             "cursor_window_s": 31 * 86400}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
    assert _pool(doc, "Claude \u00b7 weekly")["stats"] == f"55% \u00b7 5h 1% \u00b7 resets {_wd(NOW + 4 * 86400)}"
    assert _pool(doc, "SuperGrok \u00b7 weekly")["stats"] == f"19% \u00b7 resets {_wd(NOW + 3 * 86400)}"
    assert _pool(doc, "Cursor \u00b7 Other Models")["stats"] == "21%"  # half up, like orderly; reset is the header's
    assert _pool(doc, "Muse \u00b7 weekly")["stats"] == "no reading"

def test_phone_hot_5h_is_red_and_there_is_no_caps_projection():
    """quartermaster computes no time-to-cap, so no 'caps ... early' even far over pace."""
    cache = {"ts": NOW, "as_of": FRESH, "claude_week": 90, "claude_week_reset_ts": NOW + 6 * 86400,
             "claude_5h": 90, "claude_5h_reset_ts": NOW + 4 * 3600}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
    p = _pool(doc, "Claude \u00b7 weekly")
    assert p["red"] == ["5h 90%"], p
    assert not any("caps" in q["stats"] or "early" in q["stats"] for c in doc["cards"] for q in c["pools"])

def test_phone_stale_reading_says_so():
    now = time.time()  # NOW is fixed at import; the stamp must be 40 minutes before this render
    cache = {"ts": now, "as_of": {"claude": now - 40 * 60 - 5}, "claude_week": 10, "claude_week_reset_ts": now + 4 * 86400}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
    assert _pool(doc, "Claude \u00b7 weekly")["stats"].endswith(" \u00b7 stale 40m")

def test_phone_card_headers_use_cached_renewals_only():
    claude_t, codex_t, cursor_t, grok_t = NOW + 25 * 86400, NOW + 3 * 86400, NOW + 27 * 86400, NOW + 45 * 86400
    cache = {"ts": NOW, "as_of": FRESH,
             "claude_week": 10, "claude_week_reset_ts": NOW + 4 * 86400, "claude_renews_ts": claude_t, "claude_renews": True,
             "codex": 2, "codex_reset_ts": NOW + 5 * 86400, "codex_window_s": 7 * 86400,
             "codex_renews_ts": codex_t, "codex_renews": False,
             "cursor_auto_pct": 2, "cursor_api_pct": 20, "cursor_reset_ts": cursor_t, "cursor_window_s": 31 * 86400,
             "cursor_renews": True, "bot_pct": 30, "bot_reset_ts": NOW + 2 * 86400, "bot_window_s": 7 * 86400,
             "grok_pct": 19, "grok_reset_ts": NOW + 3 * 86400, "grok_window_s": 7 * 86400, "grok_renews_ts": grok_t,
             "google_argon_week": 0, "google_argon_week_reset_ts": NOW + 6 * 86400}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
    assert _card(doc, "Claude \u00b7 weekly")["header"] == f"Claude \u00b7 renews {_md(claude_t)}"
    codex = _card(doc, "Codex \u00b7 weekly")
    assert codex["header"] == f"Codex \u00b7 sub ends {_wd(codex_t)}" and codex["header_warn"]  # last week of a plan that ends
    cur = _card(doc, "Cursor \u00b7 Cursor Models")
    assert cur["header"] == f"Cursor \u00b7 renews {_md(cursor_t)}"
    assert cur["sub"] == "Cursor Models and Other Models reset then"
    assert _pool(doc, "Cursor \u00b7 Cursor Models")["stats"] == "2%"           # its reset is on the header
    assert "resets" in _pool(doc, "Grok Bot \u00b7 weekly")["stats"]          # Grok Bot's is its own
    assert _card(doc, "SuperGrok \u00b7 weekly")["header"] == f"SuperGrok \u00b7 term ends {_md(grok_t)}"  # renews unknown
    assert _card(doc, "Google AI Pro \u00b7 Argon")["header"] is None          # no renewal in the cache
    assert _card(doc, "Muse \u00b7 weekly")["header"] is None

def test_phone_chips_follow_orderly_and_the_claude_plan():
    base = {"ts": NOW, "as_of": FRESH, "claude_week": 10, "claude_week_reset_ts": NOW + 4 * 86400,
            "cursor_api_pct": 20, "cursor_reset_ts": NOW + 20 * 86400, "cursor_window_s": 31 * 86400,
            "grok_pct": 19, "grok_reset_ts": NOW + 3 * 86400, "grok_window_s": 7 * 86400,
            "google_argon_week": 0, "google_argon_week_reset_ts": NOW + 6 * 86400}
    with tempfile.TemporaryDirectory() as t:
        unknown = phone_text(t, base, muse_doc())
        pro = phone_text(t, {**base, "claude_plan": "pro"})
        mx = phone_text(t, {**base, "claude_plan": "max_5x", "fable": 12, "fable_reset_ts": NOW + 4 * 86400})
    assert _pool(unknown, "Claude \u00b7 weekly")["chips"] == [["Opus", "solid"]]
    assert any("Claude plan" in n for n in unknown["notes"])
    assert _pool(pro, "Claude \u00b7 weekly")["chips"] == [["Opus", "solid"]] and pro["notes"] == []
    assert not any(p["title"] == "Claude \u00b7 Fable slice" for c in pro["cards"] for p in c["pools"])
    assert _pool(mx, "Claude \u00b7 weekly")["chips"] == [["Fable", "solid"], ["Opus", "solid"]]
    assert _pool(mx, "Claude \u00b7 Fable slice")["chips"] == [["Fable", "solid"]]
    for doc in (unknown, pro, mx):  # every chip the text lists is drawn in the PNG too
        assert all(p["chips_drawn"] == len(p["chips"]) for c in doc["cards"] for p in c["pools"]), doc
    assert _pool(unknown, "Cursor \u00b7 Other Models")["chips"] == [["Fable", "dashed"], ["Opus", "dashed"], ["Sol", "dashed"]]
    assert _pool(unknown, "SuperGrok \u00b7 weekly")["chips"] == [["Grok", "dotted"]]
    assert _pool(unknown, "Muse \u00b7 weekly")["chips"] == [["Wonder full", "solid"], ["Wonder", "solid"]]
    assert _pool(unknown, "Google AI Pro \u00b7 Argon")["chips"] == []          # no family map for Google
    assert unknown["legend"] == "dashed = metered \u00b7 dotted = builder only \u00b7 \u2605 first usable pool for the family"

def test_phone_errors_wrap_inside_the_frame():
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, {"ts": NOW, "errors": {"claude": "HTTPError 401 " + "x " * 60}})
        r, out = render(t, {"ts": NOW, "errors": {"claude": "HTTPError 401 " + "x " * 60}}, None, "--phone")
        assert r.returncode == 0, r.stderr
    assert doc["errors"]["claude"].startswith("HTTPError 401")

def test_phone_header_is_seat_usages_host_pick():
    cache = {"ts": NOW, "as_of": {"claude": NOW, "grokbot": NOW}, "bot_pct": 10, "bot_reset_ts": NOW + 5 * 86400,
             "bot_window_s": 7 * 86400, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_5h": 10, "claude_5h_reset_ts": NOW + 3600}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
        env = {k: v for k, v in os.environ.items() if not k.startswith("SEAT_USAGE_")}
        route = json.loads(subprocess.run([sys.executable, os.path.join(ROOT, "bin", "seat-usage"), "--route", "host",
                                           "--cached", "--json"], env={**env, "SEAT_USAGE_CACHE_DIR": t},
                                          capture_output=True, text=True).stdout)[0]
        ahead = phone_text(t, {**cache, "bot_pct": 90})
        none = phone_text(t, cache, name="elsewhere.json")  # seat-usage cannot read that file: no route
    assert route["suggestion"] == "grokbot"
    assert doc["host"] == "Next host: Grok Bot" + (f" ({route['reason']})" if route["reason"] else "")
    assert ahead["host"].startswith("Next host: Opus high")  # no "on Max": the cache has no plan
    assert "Grok Bot: its weekly allowance is at 90%, ahead of the time elapsed" in ahead["why"]
    assert none["host"] == "Next host: no cached route" and none["why"] == ""

def test_text_flag_prints_json_not_the_path():
    with tempfile.TemporaryDirectory() as t:
        r, _ = render(t, {"ts": NOW}, None, "--text")
        assert r.returncode != 0 and "--text needs --phone" in r.stderr
        r, out = render(t, {"ts": NOW}, None, "--phone")
        assert r.stdout.strip() == out
        doc = phone_text(t, {"ts": NOW})
        assert doc["host"].startswith("Next host:")


if __name__ == "__main__":
    if Image is None:
        print("skip: Pillow not installed"); sys.exit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try: fn(); print("ok " + fn.__name__)
        except AssertionError as e: failed += 1; print(f"FAIL {fn.__name__} {e}")
    print(f"{len(tests) - failed} passed, {failed} failed"); sys.exit(1 if failed else 0)
