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
BLUE, RED = (0x5b, 0x8f, 0xd6), (0xc4, 0x3c, 0x3c)
over = lambda c, under, a: tuple(round(u + (x - u) * a) for x, u in zip(c, under))
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

O_INK, O_PANEL2 = (0xe4, 0xe8, 0xee), (0x20, 0x26, 0x2e)  # orderly's dark palette
TK = 1.7  # seat-meters' K: orderly CSS px to 1x px
PHONE_CX0 = 6 + 14 * TK  # frame inset plus the rail's 14px padding
COL_W = 390 - 2 * PHONE_CX0          # one column's card width, 1x
COL_DX = COL_W + 10 * TK              # one column to the next
two_wide = lambda n=2: round(3 * (390 + (n - 1) * COL_DX))  # the picture's width at 3x, n columns

def phone_text(t, cache, muse=None, name="latest.json", pools=None, flags=()):
    """The layout's words, from --text: host line, card headers, pool titles, chips, stats."""
    cp, mp, out = os.path.join(t, name), os.path.join(t, "muse-usage.json"), os.path.join(t, "p.png")
    json.dump(cache, open(cp, "w"))
    if muse is not None:
        json.dump(muse, open(mp, "w"))
    elif os.path.exists(mp):
        os.remove(mp)
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("SEAT_USAGE_")},
           "SEAT_USAGE_CACHE": cp, "SEAT_USAGE_MUSE_FILE": mp}
    if pools is not None:
        env["SEAT_USAGE_POOLS"] = pools
    r = subprocess.run([sys.executable, BIN, *flags, "--text", out], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert open(out, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    return json.loads(r.stdout)

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
        im = Image.open(out).convert("RGB")
        assert BLUE in {im.getpixel((x, y)) for y in range(im.size[1]) for x in range(im.size[0])}
        doc = phone_text(t, {"ts": NOW}, muse_doc())
    assert _pool(doc, "Muse (not read) \u00b7 weekly")["stats"].startswith("50% \u00b7 5h 35%"), doc

def test_missing_or_stale_muse_file_says_no_reading():
    stale = muse_doc(); stale["window"]["resets_at"] = iso(NOW - 60)
    for muse in (None, stale, {"captured_at": iso(NOW)}):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, {"ts": NOW}, muse)
            assert r.returncode == 0, r.stderr
            im = Image.open(out).convert("RGB")
            assert BLUE not in {im.getpixel((x, y)) for y in range(im.size[1]) for x in range(im.size[0])}
            doc = phone_text(t, {"ts": NOW}, muse)
        assert _pool(doc, "Muse (not read) \u00b7 weekly")["stats"] == "no reading", doc

def test_phone_flag_is_a_no_op_alias():
    """One layout: orderly's cards in two columns of the narrow rail. --phone, which used to
    pick it, is still accepted and draws the same thing."""
    cache = {"ts": NOW, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
             "bot_pct": 24, "bot_reset_ts": NOW + 5 * 86400, "bot_window_s": 7 * 86400}
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, muse_doc())
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == out
        w, h = Image.open(out).size
        r2, out2 = render(t, cache, muse_doc(), "--phone")
        assert r2.returncode == 0, r2.stderr
        assert r2.stdout.strip() == out2
        assert Image.open(out2).size == (w, h), (Image.open(out2).size, (w, h))
        doc = phone_text(t, cache, muse_doc())
        doc2 = phone_text(t, cache, muse_doc(), flags=("--phone",))
    assert w == two_wide(), w           # two columns of orderly's rail at 3x
    assert w / h >= 9 / 19.5, (w, h)    # no narrower than a phone screen
    assert doc == doc2

def test_nearly_finished_5h_window_stays_on_the_page():
    cache = {"ts": NOW, "claude_week": 42, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_5h": 90, "claude_5h_reset_ts": NOW + 60,
             "cursor_api_pct": 20, "cursor_reset_ts": NOW + 29 * 86400, "cursor_window_s": 31 * 86400}
    # left of the cards is only the page, the frame and its border (orderly's palette)
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, muse_doc())
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


def test_google_group_is_its_own_color():
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
    def colors(path):
        im = Image.open(path).convert("RGB")
        w, h = im.size
        return im, {im.getpixel((x, y)) for y in range(h) for x in range(w)}
    with tempfile.TemporaryDirectory() as t:
        base = {"ts": NOW}
        plain, plain_out = render(t, base)
        assert plain.returncode == 0, plain.stderr
        plain_im, plain_px = colors(plain_out)
        full, full_out = render(t, {**base, **google})
        assert full.returncode == 0, full.stderr
        full_im, full_px = colors(full_out)
        assert GOOGLE in full_px
        assert GOOGLE not in plain_px
        assert full_im.size[1] > plain_im.size[1]
        weekly_only = {k: v for k, v in google.items() if not k.endswith("_5h") and "5h_reset" not in k}
        short, short_out = render(t, {**base, **weekly_only})
        assert short.returncode == 0, short.stderr
        assert Image.open(short_out).size[1] < full_im.size[1]


def test_phone_5h_track_has_no_text_beside_it():
    """The 5h figure moved to the stats line under the bars, so no text shares the 5h track's rows."""
    cache = {"ts": NOW, "google_gemini_week": 5, "google_gemini_week_reset_ts": NOW + 86400,
             "google_gemini_5h": 20, "google_gemini_5h_reset_ts": NOW + 3 * 3600}
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, None)
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
        gem = _pool(phone_text(t, cache), "Google (not read) \u00b7 Gemini")
        assert gem["stats"].startswith("5% \u00b7 5h 20%"), gem["stats"]

def test_phone_over_pace_segment_is_red_on_both_tracks():
    """Use past the pace tick draws red on the weekly track and, at full strength, on the 5h one."""
    def red_bands(week, five):
        cache = {"ts": NOW, "claude_week": week, "claude_week_reset_ts": NOW + 4 * 86400,
                 "claude_5h": five, "claude_5h_reset_ts": NOW + 2.5 * 3600}
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, cache, None)
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
        r, out = render(t, cache, None)
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
    assert _pool(doc, "Muse (not read) \u00b7 weekly")["stats"] == "no reading"

def test_phone_hot_5h_is_red_and_no_cached_projection_means_no_caps():
    """Caps wording comes only from seat-usage's <pool>_caps_early_s; far over pace without it says nothing."""
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

def test_phone_long_stats_line_wraps_instead_of_being_cut():
    """A Claude line with caps, 5h, reset and stale is wider than the card: every part is drawn, on two lines."""
    now = time.time()
    base = {"ts": now, "as_of": {"claude": now}, "claude_week": 68, "claude_week_reset_ts": now + 4 * 86400,
            "claude_5h": 38, "claude_5h_reset_ts": now + 3600}
    long_ = {**base, "as_of": {"claude": now - 3 * 3600 - 13 * 60 - 5}, "claude_week_caps_early_s": 2 * 86400}
    with tempfile.TemporaryDirectory() as t:
        # not latest.json: no route, so the host lines are the same in both and only the stats differ
        short = phone_text(t, base, name="c.json"); h_short = Image.open(os.path.join(t, "p.png")).size[1]
        doc = phone_text(t, long_, name="c.json"); h_long = Image.open(os.path.join(t, "p.png")).size[1]
    p = _pool(short, "Claude \u00b7 weekly")
    assert p["stats_drawn"] == [p["stats"]], p  # a line that fits stays one line
    p = _pool(doc, "Claude \u00b7 weekly")
    assert p["stats"] == f"68% \u00b7 caps 2d early \u00b7 5h 38% \u00b7 resets {_wd(now + 4 * 86400)} \u00b7 stale 3h 13m", p
    assert len(p["stats_drawn"]) >= 2, p
    assert " \u00b7 ".join(p["stats_drawn"]) == p["stats"], p  # nothing cut, no ellipsis
    assert not any("\u2026" in l for l in p["stats_drawn"]), p
    assert h_long > h_short, (h_short, h_long)  # the card grows for the second line

def test_phone_header_says_an_old_reading_is_stale_with_its_age():
    now = time.time()
    old = now - 3 * 3600 - 13 * 60 - 5
    cache = {"ts": old, "as_of": {"claude": old, "grokbot": old}, "bot_pct": 10, "bot_reset_ts": now + 5 * 86400,
             "bot_window_s": 7 * 86400, "claude_week": 42, "claude_week_reset_ts": now + 4 * 86400,
             "claude_5h": 10, "claude_5h_reset_ts": now + 3600}
    recent = now - 60
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
        fresh = phone_text(t, {**cache, "ts": now, "as_of": {"claude": now, "grokbot": now}})
        # unknown for a reason other than age (no used %): a minute-old reading is not stale
        nopct = phone_text(t, {k: v for k, v in {**cache, "ts": recent, "as_of": {"claude": recent, "grokbot": recent}}.items()
                               if k not in ("bot_pct", "claude_week")})
        # a pulled copy gets 20 minutes before the route calls a reading unknown
        pulled = phone_text(t, {**cache, "ts": now - 25 * 60, "pulled_ts": now,
                                "as_of": {"claude": now - 25 * 60 - 5, "grokbot": now - 25 * 60 - 5}})
    assert "Grok Bot: reading stale, 3h 13m old." in doc["why"], doc["why"]
    assert "Opus: reading stale, 3h 13m old." in doc["why"], doc["why"]
    assert "no reading" not in doc["why"], doc["why"]
    assert "stale" not in fresh["why"] and "no reading" not in fresh["why"], fresh["why"]
    assert "Grok Bot: no reading." in nopct["why"] and "stale" not in nopct["why"], nopct["why"]
    assert "Grok Bot: reading stale, 25m old." in pulled["why"], pulled["why"]

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
    assert _card(doc, "Google (not read) \u00b7 Argon")["header"] is None          # no renewal in the cache
    assert _card(doc, "Muse (not read) \u00b7 weekly")["header"] is None

def test_phone_chips_follow_orderly_whatever_the_claude_plan():
    """orderly seatLabel, except a known non-Max plan drops Fable from Claude weekly
    and does not draw the Fable slice. Unknown and Max keep both. Cursor Other Models
    keeps the Fable chip either way."""
    base = {"ts": NOW, "as_of": FRESH, "claude_week": 10, "claude_week_reset_ts": NOW + 4 * 86400,
            "cursor_api_pct": 20, "cursor_reset_ts": NOW + 20 * 86400, "cursor_window_s": 31 * 86400,
            "grok_pct": 19, "grok_reset_ts": NOW + 3 * 86400, "grok_window_s": 7 * 86400,
            "google_argon_week": 0, "google_argon_week_reset_ts": NOW + 6 * 86400}
    with tempfile.TemporaryDirectory() as t:
        unknown = phone_text(t, base, muse_doc())
        pro = phone_text(t, {**base, "claude_max": False})
        flag_max = phone_text(t, {**base, "claude_max": True, "fable": 12, "fable_reset_ts": NOW + 4 * 86400})
        capped = phone_text(t, {**base, "claude_week": 100, "grok_pct": 100, "grok_exhausted": True})
    fable_slice = lambda doc: any(p["title"] == "Claude \u00b7 Fable slice" for c in doc["cards"] for p in c["pools"])
    for doc in (unknown, flag_max, capped):
        assert _pool(doc, "Claude \u00b7 weekly")["chips"] == [["Fable", "solid"], ["Opus", "solid"]], doc
        assert fable_slice(doc) and _pool(doc, "Claude \u00b7 Fable slice")["chips"] == [["Fable", "solid"]]
    assert _pool(pro, "Claude \u00b7 weekly")["chips"] == [["Opus", "solid"]], pro
    assert not fable_slice(pro), pro
    for doc in (unknown, pro, flag_max, capped):
        assert doc["notes"] == [], doc["notes"]  # no "missing Fable chip" footnote any more
        assert all(p["chips_drawn"] == len(p["chips"]) for c in doc["cards"] for p in c["pools"]), doc
        assert _pool(doc, "SuperGrok \u00b7 weekly")["chips"] == [["Grok", "dotted"]]  # a full pool keeps its chip
        assert _pool(doc, "Cursor \u00b7 Other Models")["chips"] == [["Fable", "dashed"], ["Opus", "dashed"], ["Sol", "dashed"]]
    assert _pool(unknown, "Muse (not read) \u00b7 weekly")["chips"] == [["Wonder full", "solid"], ["Wonder", "solid"]]
    assert _pool(unknown, "Google (not read) \u00b7 Argon")["chips"] == [["Argon", "solid"]]  # config/pools.json
    assert unknown["legend"] == "dashed = metered \u00b7 dotted = builder only \u00b7 \u2605 first usable pool for the family"

def _fable_slice(doc):
    return any(p["title"] == "Claude \u00b7 Fable slice" for c in doc["cards"] for p in c["pools"])


def test_phone_fable_slice_row_follows_orderly():
    """quota_claude_fable, except a known non-Max plan collapses the row instead of
    drawing it absent. A Max or unknown plan still draws it: live with a percent,
    otherwise unknown. A plan name that contains max wins over a false flag."""
    now = time.time()
    base = {"ts": now, "as_of": {"claude": now - 120}, "claude_week": 10, "claude_week_reset_ts": now + 4 * 86400}
    fab = lambda doc: _pool(doc, "Claude \u00b7 Fable slice")
    with tempfile.TemporaryDirectory() as t:
        pro = phone_text(t, {**base, "claude_max": False})            # the pulled copy: only the bit
        named = phone_text(t, {**base, "claude_plan": "claude_ai"})    # the plan name
        odd = phone_text(t, {**base, "claude_plan": "enterprise"})
        old = phone_text(t, {**base, "claude_max": False, "as_of": {"claude": now - 3600 - 5}})
        no_read = phone_text(t, {**base, "claude_max": False, "as_of": {}})
        unknown = phone_text(t, base)
        mx = phone_text(t, {**base, "claude_max": True})
        live = phone_text(t, {**base, "claude_max": True, "fable": 12, "fable_reset_ts": now + 3 * 86400})
        bare = phone_text(t, {**base, "claude_max": True, "fable": 60})  # a percent with no reset
        # a plan name wins over the bit: Max named, so the row stays
        named_max = phone_text(t, {**base, "claude_plan": "default_claude_max_5x", "claude_max": False})
        # a kept percent with no as_of.claude: orderly's red "read failed" after the percent
        kept = phone_text(t, {**base, "as_of": {}, "fable": 12, "fable_reset_ts": now + 3 * 86400,
                              "fable_caps_early_s": 86400, "errors": {"claude": "HTTPError 401: bad"}})
        kept_bare = phone_text(t, {**base, "as_of": {}, "fable": 60})
    for doc in (pro, named, odd, old, no_read):
        assert not _fable_slice(doc), doc
        assert "Fable" not in [c[0] for c in _pool(doc, "Claude \u00b7 weekly")["chips"]]
    for doc in (unknown, mx):
        assert fab(doc)["state"] == "unknown"
        assert fab(doc)["stats"] == "unknown \u00b7 seat-usage has no value \u00b7 verified 2m", fab(doc)
    assert fab(live)["state"] == "live" and fab(live)["stats"].startswith("12%"), fab(live)
    assert (fab(bare)["state"], fab(bare)["stats"]) == ("live", "60%"), fab(bare)
    assert fab(named_max)["state"] == "unknown", fab(named_max)
    assert fab(kept)["stats"].startswith("12% \u00b7 caps 1d early \u00b7 read failed: HTTPError 401 \u00b7 resets"), fab(kept)
    assert fab(kept)["red"] == ["caps 1d early", "read failed: HTTPError 401"], fab(kept)
    assert fab(kept_bare)["stats"] == "60% \u00b7 read failed", fab(kept_bare)
    assert not fab(unknown)["muted"] and not fab(live)["muted"]

def test_phone_fable_track_shares_the_weekly_scale():
    """A Fable window that ends before Claude weekly's still gets a full weekly-width track,
    not one shrunk to fit a scale computed without it."""
    now = time.time()
    cache = {"ts": now, "as_of": {"claude": now}, "claude_max": True,
             "claude_week": 10, "claude_week_reset_ts": now + 6 * 86400,      # 1 day in
             "fable": 10, "fable_reset_ts": now + 1 * 86400}                  # 6 days in
    with tempfile.TemporaryDirectory() as t:
        r, out = render(t, cache, None)
        assert r.returncode == 0, r.stderr
        im = Image.open(out).convert("RGB"); w, h = im.size
    track = over(O_INK, over((0xff, 0x87, 0x00), O_PANEL2, 0.09), 0.14)
    is_bar = lambda p: near(p, track, 3) or near(p, BLUE, 3)
    rows = {}
    for y in range(h):
        xs = [x for x in range(w) if is_bar(im.getpixel((x, y)))]
        if len(xs) > 30: rows[y] = max(xs) - min(xs)
    bands = []
    for y in sorted(rows):
        (bands[-1].append(y) if bands and y - bands[-1][-1] <= 1 else bands.append([y]))
    assert len(bands) == 2, bands  # Claude weekly and the Fable slice
    mid = [rows[b[len(b) // 2]] for b in bands]
    assert abs(mid[0] - mid[1]) <= 4, mid  # same weekly window, same width

def test_phone_ended_subscription_follows_orderly():
    """orderly subEnded (newtab.js:773-775): renews false and the paid-through stamp past. The
    row gets a muted title, its chips, "sub ended" in place of usage and reset (newtab.js:1190-1201),
    the header says "sub ended" without a warning, and ended plans sink below active ones,
    a subscription's rows together (newtab.js:1159-1163)."""
    now = time.time()
    base = {"ts": now, "as_of": {"claude": now, "chatgpt": now, "cursor": now, "grokbot": now, "grok": now, "google": now},
            "claude_max": False, "claude_week": 10, "claude_week_reset_ts": now + 4 * 86400,
            "codex": 40, "codex_reset_ts": now + 5 * 86400, "codex_window_s": 7 * 86400, "codex_caps_early_s": 86400,
            "cursor_auto_pct": 2, "cursor_api_pct": 20, "cursor_reset_ts": now + 20 * 86400, "cursor_window_s": 31 * 86400,
            "cursor_renews": True, "bot_pct": 30, "bot_reset_ts": now + 2 * 86400, "bot_window_s": 7 * 86400,
            "grok_pct": 19, "grok_reset_ts": now + 3 * 86400, "grok_window_s": 7 * 86400,
            "grok_renews_ts": now + 40 * 86400, "grok_renews": True,
            "google_argon_week": 0, "google_argon_week_reset_ts": now + 6 * 86400}
    codex_end = {"codex_renews": False, "codex_renews_ts": now - 86400}
    titles = lambda doc: [p["title"] for c in doc["cards"] for p in c["pools"]]
    with tempfile.TemporaryDirectory() as t:
        active = phone_text(t, {**base, "codex_renews": True, "codex_renews_ts": now + 9 * 86400}, muse_doc())
        ended = phone_text(t, {**base, **codex_end}, muse_doc())
        ending = phone_text(t, {**base, "codex_renews": False, "codex_renews_ts": now + 3 * 86400})  # not yet
        due = phone_text(t, {**base, "codex_renews_ts": now - 86400})                     # renews unknown
        lapsed_but_renews = phone_text(t, {**base, "codex_renews": True, "codex_renews_ts": now - 86400})
        both = phone_text(t, {**base, **codex_end, "cursor_renews": False, "cursor_reset_ts": now - 3600})
        claude = phone_text(t, {**base, "claude_renews": False, "claude_renews_ts": now - 60})
    cx = lambda doc: _pool(doc, "Codex \u00b7 weekly")
    # ended: muted, "sub ended" only (no percent, caps, reset or stale), no red, chips kept
    assert (cx(ended)["state"], cx(ended)["muted"], cx(ended)["ended"]) == ("ended", True, True), cx(ended)
    assert cx(ended)["stats"] == "sub ended" and cx(ended)["red"] == [], cx(ended)
    assert cx(ended)["chips"] == [["Astra", "solid"], ["Sol", "solid"]] == cx(active)["chips"]
    hdr = _card(ended, "Codex \u00b7 weekly")
    assert hdr["header"] == "Codex \u00b7 sub ended" and not hdr["header_warn"], hdr
    # it sinks below every active row, Muse and Google included; the rest keep their order
    assert titles(ended)[-1] == "Codex \u00b7 weekly", titles(ended)
    assert titles(ended)[:-1] == [x for x in titles(active) if x != "Codex \u00b7 weekly"]
    assert titles(active).index("Codex \u00b7 weekly") == 1, titles(active)  # no Fable slice off Max
    # not ended: a term still ahead, an unknown renewal, or one set to renew
    for doc in (active, ending, due, lapsed_but_renews):
        assert not cx(doc)["ended"] and not cx(doc)["muted"] and cx(doc)["stats"].startswith("40%"), cx(doc)
        assert titles(doc).index("Codex \u00b7 weekly") == 1, titles(doc)
    assert _card(ending, "Codex \u00b7 weekly")["header_warn"]
    assert _card(due, "Codex \u00b7 weekly")["header"] == "Codex \u00b7 sub renewal due"
    # Grok Bot is on the Cursor bill: the Cursor subscription's three rows end and sink together
    cur = ["Cursor \u00b7 Cursor Models", "Cursor \u00b7 Other Models", "Grok Bot \u00b7 weekly"]
    assert titles(both)[-4:] == ["Codex \u00b7 weekly"] + cur, titles(both)
    assert all(_pool(both, x)["stats"] == "sub ended" and _pool(both, x)["muted"] for x in cur)
    assert _card(both, cur[0])["header"] == "Cursor \u00b7 sub ended"
    assert len([c for c in both["cards"] if any(p["title"] in cur for p in c["pools"])]) == 1
    # Off Max the Fable slice is collapsed, so only the weekly row ends with the sub.
    assert _pool(claude, "Claude \u00b7 weekly")["state"] == "ended"
    assert _pool(claude, "Claude \u00b7 weekly")["stats"] == "sub ended"
    assert "Claude \u00b7 Fable slice" not in titles(claude), titles(claude)
    # On Max the slice ends with the weekly row and the two sink together.
    with tempfile.TemporaryDirectory() as t:
        mx_ended = phone_text(t, {**base, "claude_max": True, "fable": 10, "fable_reset_ts": now + 86400,
                                  "claude_renews": False, "claude_renews_ts": now - 60})
    for x in ("Claude \u00b7 weekly", "Claude \u00b7 Fable slice"):
        assert _pool(mx_ended, x)["state"] == "ended" and _pool(mx_ended, x)["stats"] == "sub ended", _pool(mx_ended, x)
    assert titles(mx_ended)[-2:] == ["Claude \u00b7 weekly", "Claude \u00b7 Fable slice"], titles(mx_ended)
    assert not any(p["ended"] for c in active["cards"] for p in c["pools"])
    # an ended plan whose usage numbers are gone still gets its rows (orderly lists every seat)
    bare = {"ts": now, "as_of": {}, "codex_renews": False, "codex_renews_ts": now - 86400,
            "cursor_renews": False, "cursor_reset_ts": now - 3600, "grok_renews": True, "grok_renews_ts": now - 60}
    with tempfile.TemporaryDirectory() as t:
        gone = phone_text(t, bare)
    assert titles(gone)[-4:] == ["Codex \u00b7 weekly"] + cur, titles(gone)
    assert all(_pool(gone, x)["stats"] == "sub ended" for x in ["Codex \u00b7 weekly"] + cur)
    assert _card(gone, "Codex \u00b7 weekly")["header"] == "Codex \u00b7 sub ended"
    assert "SuperGrok \u00b7 weekly" not in titles(gone)  # set to renew: no reading, no row, as before

def test_phone_ended_row_height_counts_wrapped_chips_once():
    """An ended row with chips on a second line is as tall as its content: the striped track, then
    "sub ended", then the card's padding, with no extra space below."""
    now = time.time()
    cache = {"ts": now, "as_of": {"cursor": now}, "cursor_api_pct": 100, "cursor_reset_ts": now - 3600,
             "cursor_window_s": 31 * 86400, "cursor_renews": False, "cursor_ondemand": 5, "cursor_auto_pct": 3,
             "bot_pct": 30, "bot_reset_ts": now + 2 * 86400, "bot_window_s": 7 * 86400}
    with tempfile.TemporaryDirectory() as t:
        # not latest.json: no host line, which follows seat-usage's route and would differ
        doc = phone_text(t, cache, name="c.json")
        im = Image.open(os.path.join(t, "p.png")).convert("RGB"); w, h = im.size
        live = phone_text(t, {**cache, "cursor_renews": True, "cursor_reset_ts": now + 86400}, name="c.json")
        im2 = Image.open(os.path.join(t, "p.png")).convert("RGB")
    other = _pool(doc, "Cursor \u00b7 Other Models")
    assert other["ended"] and other["chips"][-1] == ["$", "solid"] and other["spend"] == "draw", other
    assert other["chips_drawn"] == len(other["chips"])
    # live rows of the same shape (chips wrapped, one 6px bar, one stats line each) are as tall
    rows_ = ("Cursor \u00b7 Cursor Models", "Cursor \u00b7 Other Models", "Grok Bot \u00b7 weekly")
    assert [len(_pool(live, x)["stats_drawn"]) for x in rows_] == [len(_pool(doc, x)["stats_drawn"]) for x in rows_], live
    assert [_pool(live, x)["chips_drawn"] for x in rows_] == [_pool(doc, x)["chips_drawn"] for x in rows_]
    assert _card(live, "Grok Bot \u00b7 weekly")["sub"] == _card(doc, "Grok Bot \u00b7 weekly")["sub"], (live, doc)
    bl, bd = _card(live, rows_[0])["box"], _card(doc, rows_[0])["box"]
    assert bl[3] - bl[1] == bd[3] - bd[1], (bl, bd)  # the Cursor card, wherever its column puts it

def test_phone_ended_track_is_full_width_striped_grey():
    """.meter .track.ended (newtab.html:154): repeating-linear-gradient(135deg, ink 22% 0 4px,
    ink 10% 4px 8px) across the whole meter, with no fill and no now tick."""
    now = time.time()
    cache = {"ts": now, "as_of": {"chatgpt": now}, "codex": 90, "codex_reset_ts": now + 2 * 86400,
             "codex_window_s": 7 * 86400, "codex_renews": False, "codex_renews_ts": now - 86400}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
        im = Image.open(os.path.join(t, "p.png")).convert("RGB"); w, h = im.size
    box = _card(doc, "Codex \u00b7 weekly")["box"]  # whichever column the card landed in
    tint = over((0xaf, 0x87, 0xff), O_PANEL2, 0.09)
    hi_c, lo_c = over(O_INK, tint, 0.22), over(O_INK, tint, 0.10)
    tx0, tx1 = box[0] + 11 * TK * 3, box[2] - 10 * TK * 3  # card content edges
    lo, hi = int(tx0) + 6, int(tx1) - 6
    striped = []
    for y in range(box[1], box[3]):
        px = [im.getpixel((x, y)) for x in range(lo, hi)]
        a, b = runs(px, lambda p: near(p, hi_c, 3), 8), runs(px, lambda p: near(p, lo_c, 3), 8)
        # both stripe tones, alternating across the full width, and nothing else in the band
        if len(a) > 0.3 * len(px) and len(b) > 0.3 * len(px) and min(a | b) < 50 and max(a | b) > len(px) - 50:
            striped.append(y)
    assert len(striped) >= 6, striped
    band = [im.getpixel((x, y)) for y in striped for x in range(lo, hi)]
    assert not any(near(p, BLUE, 20) or near(p, RED, 20) or near(p, O_INK, 20) for p in band)  # no fill, no tick
    # the stripes run up to the right: one row down, the pattern sits one pixel left
    y = striped[len(striped) // 2]
    row = lambda yy: [near(im.getpixel((x, yy)), hi_c, 3) for x in range(lo, hi)]
    assert sum(p == q for p, q in zip(row(y)[1:], row(y + 1))) > 0.9 * (hi - lo - 1)

def test_phone_ended_row_stays_off_the_shared_scale():
    """renderRail leaves an ended row out of the time scale (newtab.js:1169): a long window on an
    ended plan must not squeeze the active tracks."""
    now = time.time()
    base = {"ts": now, "as_of": {"claude": now}, "claude_week": 10, "claude_week_reset_ts": now + 6 * 86400}
    long_codex = {"codex": 5, "codex_reset_ts": now + 29 * 86400, "codex_window_s": 30 * 86400,
                  "codex_renews_ts": now - 86400}
    def claude_width(cache):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, cache, None)
            assert r.returncode == 0, r.stderr
            im = Image.open(out).convert("RGB"); w, h = im.size
        track = over(O_INK, over((0xff, 0x87, 0x00), O_PANEL2, 0.09), 0.14)
        widths = []
        for y in range(h):
            xs = [x for x in range(w) if near(im.getpixel((x, y)), track, 3) or near(im.getpixel((x, y)), BLUE, 3)]
            if len(xs) > 30: widths.append(max(xs) - min(xs))
        return max(widths)
    alone = claude_width(base)
    assert abs(claude_width({**base, **long_codex, "codex_renews": False}) - alone) <= 4
    assert claude_width({**base, **long_codex, "codex_renews": True}) < alone * 0.6  # live: it counts

def _every_seat(now, stale=0):
    """A cache with every subscription and pool, every reading `stale` seconds old."""
    at = now - stale
    return {"ts": now, "as_of": {k: at for k in ("claude", "chatgpt", "cursor", "grokbot", "grok", "google")},
            "claude_max": False, "claude_week": 79, "claude_week_reset_ts": now + 0.2 * 86400,
            "claude_5h": 73, "claude_5h_reset_ts": now + 3600,
            "codex": 24, "codex_reset_ts": now + 5 * 86400, "codex_window_s": 7 * 86400, "codex_caps_early_s": 3 * 86400,
            "codex_5h": 8, "codex_5h_reset_ts": now + 2 * 3600, "codex_renews": False, "codex_renews_ts": now + 19 * 86400,
            "cursor_auto_pct": 0, "cursor_api_pct": 3, "cursor_reset_ts": now + 31 * 86400, "cursor_window_s": 31 * 86400,
            "cursor_renews": True, "bot_pct": 6, "bot_reset_ts": now + 6 * 86400, "bot_window_s": 7 * 86400,
            "grok_pct": 36, "grok_reset_ts": now + 2 * 86400, "grok_window_s": 7 * 86400,
            "grok_renews": False, "grok_renews_ts": now + 46 * 86400,
            "google_argon_week": 3, "google_argon_week_reset_ts": now + 5 * 86400,
            "google_gemini_week": 0, "google_gemini_week_reset_ts": now + 6 * 86400,
            "google_gemini_5h": 0, "google_gemini_5h_reset_ts": now + 3 * 3600,
            "google_claudegpt_week": 1, "google_claudegpt_week_reset_ts": now + 6 * 86400,
            "google_claudegpt_5h": 0, "google_claudegpt_5h_reset_ts": now + 3 * 3600}

def test_layout_is_no_narrower_than_a_phone_screen():
    """The picture is at least as wide as a 9:19.5 portrait phone screen, so the phone fits it to
    its width: a full cache, the same with every reading stale (a longer stats line per row), a
    single card, and footers of errors that need more columns, even more than there are cards."""
    now = time.time()
    errs = {f"seat{i}": "HTTPError 503: the vendor did not answer this read in time" for i in range(30)}
    with tempfile.TemporaryDirectory() as t:
        docs = {"full": phone_text(t, _every_seat(now), muse_doc(now)),
                "stale": phone_text(t, _every_seat(now, 17 * 60), muse_doc(now)),
                "one": phone_text(t, {"ts": now, "as_of": {"claude": now}, "claude_week": 5,
                                      "claude_week_reset_ts": now + 86400}),
                "few_cards": phone_text(t, {"ts": now, "errors": {f"e{i}": "the vendor did not answer " * 8 for i in range(20)}}),
                "errors": phone_text(t, {**_every_seat(now), "errors": errs}, muse_doc(now))}
        im_w, im_h = Image.open(os.path.join(t, "p.png")).size
    for name, doc in docs.items():
        w, h = doc["size"]
        assert w / h >= 9 / 19.5, (name, w, h)
        assert w == two_wide(len(doc["columns"])), (name, w)
    assert (im_w, im_h) == tuple(docs["errors"]["size"])  # --text says the drawn size
    assert [len(docs[n]["columns"]) for n in ("full", "stale", "one")] == [2, 2, 2]
    assert len(docs["errors"]["columns"]) > 2, docs["errors"]["size"]
    # two cards and a long footer: more columns than cards, the extra ones empty
    few = docs["few_cards"]
    assert len(few["cards"]) == 2 and len(few["columns"]) > 2, few["columns"]
    assert sum(few["columns"], []) == [0, 1], few["columns"]
    w, h = docs["full"]["size"]
    assert w / h >= 0.64, (w, h)  # the chat's own image area in Ryan's screenshot, 1080 x ~1680

def test_layout_draws_every_card_once_in_reading_order():
    """Columns are runs of the cards in orderly's order: every card exactly once, top to bottom
    and then the next column, boxes inside the picture and apart, each with its vendor stripe."""
    now = time.time()
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, _every_seat(now), muse_doc(now))
        im = Image.open(os.path.join(t, "p.png")).convert("RGB")
    cols = doc["columns"]
    assert sum(cols, []) == list(range(len(doc["cards"]))), cols  # each once, in order
    assert all(cols), cols                                         # no empty column
    assert len(doc["cards"]) == 6, [c["pools"][0]["title"] for c in doc["cards"]]
    w, h = doc["size"]
    boxes = [c["box"] for c in doc["cards"]]
    for i, (c, b) in enumerate(zip(doc["cards"], boxes)):
        assert c["column"] == next(k for k, col in enumerate(cols) if i in col)
        assert 0 <= b[0] < b[2] <= w and 0 <= b[1] < b[3] <= h, b
        for j, o in enumerate(boxes[:i]):
            assert b[2] <= o[0] or o[2] <= b[0] or b[3] <= o[1] or o[3] <= b[1], (i, j, b, o)  # apart
    hues = {"Claude": (0xff, 0x87, 0x00), "Codex": (0xaf, 0x87, 0xff), "Cursor": (0x00, 0xd7, 0xaf),
            "SuperGrok": (0x5f, 0xaf, 0xff), "Google": (0x54, 0x68, 0xf0), "Muse": (0xff, 0x87, 0xd7)}
    for c, b in zip(doc["cards"], boxes):
        hue = hues[c["pools"][0]["title"].split(" ")[0]]
        assert near(im.getpixel((b[0] + 2, (b[1] + b[3]) // 2)), hue, 3), c["pools"][0]["title"]
    # the columns' heights are balanced: the shorter is at least half the taller
    col_h = [sum(boxes[i][3] - boxes[i][1] for i in col) for col in cols]
    assert min(col_h) * 2 >= max(col_h), col_h

def test_layout_keeps_ended_cards_last():
    """Ended plans sink below active ones (newtab.js:1159-1163): the last cards in reading order,
    at the bottom of the last column, whatever the column split."""
    now = time.time()
    ended = {"codex_renews": False, "codex_renews_ts": now - 86400, "grok_renews": False, "grok_renews_ts": now - 60}
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, {**_every_seat(now), **ended}, muse_doc(now))
    flags = [all(p["ended"] for p in c["pools"]) for c in doc["cards"]]
    assert flags == [False] * 4 + [True] * 2, flags
    assert [c["pools"][0]["title"] for c in doc["cards"][-2:]] == ["Codex \u00b7 weekly", "SuperGrok \u00b7 weekly"]
    last = doc["columns"][-1]
    assert last[-2:] == [4, 5], doc["columns"]
    boxes = [c["box"] for c in doc["cards"]]
    assert boxes[5][1] > boxes[4][1] and boxes[5][1] == max(boxes[i][1] for i in last)

def test_phone_spend_chip_follows_orderly():
    """orderly spendChip: "$" after the family chips on the Cursor rows when spend is above zero,
    on any of them (Grok Bot included) when the pool is capped; amber when capped, red past the limit."""
    base = {"ts": NOW, "as_of": FRESH, "cursor_api_pct": 20, "cursor_auto_pct": 5, "cursor_reset_ts": NOW + 20 * 86400,
            "cursor_window_s": 31 * 86400, "bot_pct": 30, "bot_reset_ts": NOW + 5 * 86400, "bot_window_s": 7 * 86400}
    sp = lambda doc, title: (_pool(doc, title)["spend"], _pool(doc, title)["chips"][-1])
    OM, CM, GB = "Cursor \u00b7 Other Models", "Cursor \u00b7 Cursor Models", "Grok Bot \u00b7 weekly"
    with tempfile.TemporaryDirectory() as t:
        none = phone_text(t, base)                                       # no spend number: no chip
        zero = phone_text(t, {**base, "cursor_ondemand": 0})            # zero, nothing capped: no chip
        spent = phone_text(t, {**base, "cursor_ondemand": 4.5})
        over = phone_text(t, {**base, "cursor_ondemand": 30, "cursor_ondemand_limit": 20})
        capped = phone_text(t, {**base, "cursor_ondemand": 0, "cursor_api_pct": 100, "bot_exhausted": True})
    for doc in (none, zero):
        assert all(p["spend"] is None and ["$", "solid"] not in p["chips"] for c in doc["cards"] for p in c["pools"]), doc
    assert sp(spent, OM) == ("plain", ["$", "solid"]) and sp(spent, CM) == ("plain", ["$", "solid"])
    assert _pool(spent, OM)["chips"][:3] == [["Fable", "dashed"], ["Opus", "dashed"], ["Sol", "dashed"]]
    assert _pool(spent, GB)["spend"] is None  # the Bot shows spend only when its allowance is used up
    assert sp(over, OM)[0] == "over" and sp(over, CM)[0] == "over"
    assert sp(capped, OM)[0] == "draw" and sp(capped, GB)[0] == "draw"
    assert _pool(capped, CM)["spend"] is None  # not capped, and zero spend
    assert all(p["chips_drawn"] == len(p["chips"]) for c in capped["cards"] for p in c["pools"]), capped

def test_phone_spend_chip_colors():
    """The "$" chip is orderly --warn when its pool is capped and --crit past the limit."""
    WARN, CRIT = (0xfa, 0xb2, 0x19), (0xe0, 0x4b, 0x4b)
    # as_of.claude so the Fable row reads "unknown", not a red "read failed"
    base = {"ts": NOW, "as_of": {"claude": NOW}, "cursor_api_pct": 20, "cursor_reset_ts": NOW + 20 * 86400,
            "cursor_window_s": 31 * 86400}
    def has(cache, color):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, cache, None)
            assert r.returncode == 0, r.stderr
            im = Image.open(out).convert("RGB")
            return color in {im.getpixel((x, y)) for y in range(im.size[1]) for x in range(im.size[0])}
    assert not has(base, WARN) and not has(base, CRIT)
    assert has({**base, "cursor_api_pct": 100, "cursor_ondemand": 0}, WARN)
    assert has({**base, "cursor_ondemand": 30, "cursor_ondemand_limit": 20}, CRIT)
    # Grok Bot alone and exhausted, under the limit: amber, not Grok blue
    bot = {"ts": NOW, "as_of": {"claude": NOW}, "bot_pct": 100, "bot_exhausted": True, "bot_reset_ts": NOW + 5 * 86400,
           "bot_window_s": 7 * 86400, "cursor_ondemand": 30, "cursor_ondemand_limit": 50}
    assert has(bot, WARN)

def test_phone_errors_wrap_inside_the_frame():
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, {"ts": NOW, "errors": {"claude": "HTTPError 401 " + "x " * 60}})
        r, out = render(t, {"ts": NOW, "errors": {"claude": "HTTPError 401 " + "x " * 60}}, None)
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
        r, out = render(t, {"ts": NOW}, None)
        assert r.stdout.strip() == out
        doc = phone_text(t, {"ts": NOW})
        assert doc["host"].startswith("Next host:")


GOOGLE_CACHE = {"ts": NOW, "as_of": FRESH,
                "google_argon_week": 5, "google_argon_week_reset_ts": NOW + 6 * 86400,
                "google_gemini_week": 5, "google_gemini_week_reset_ts": NOW + 6 * 86400,
                "google_claudegpt_week": 5, "google_claudegpt_week_reset_ts": NOW + 6 * 86400}

def test_phone_google_chips_are_data_in_pools_json():
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, GOOGLE_CACHE)
        conf = json.load(open(os.path.join(ROOT, "config", "pools.json")))
        conf["google_ai_pro"]["pools"]["argon"] = {"families": ["Test fam"], "style": "dashed"}
        del conf["google_ai_pro"]["pools"]["claudegpt"]
        alt = os.path.join(t, "pools.json"); json.dump(conf, open(alt, "w"))
        edited = phone_text(t, GOOGLE_CACHE, pools=alt)
        json.dump({k: v for k, v in conf.items() if k != "google_ai_pro"}, open(alt, "w"))
        none = phone_text(t, GOOGLE_CACHE, pools=alt)
    assert _pool(doc, "Google (not read) \u00b7 Argon")["chips"] == [["Argon", "solid"]]
    assert _pool(doc, "Google (not read) \u00b7 Gemini")["chips"] == [["Argon", "solid"], ["Gemini Flash", "solid"]]
    assert _pool(doc, "Google (not read) \u00b7 Claude/GPT")["chips"] == [["Opus", "solid"], ["Sonnet", "solid"], ["GPT-OSS", "solid"]]
    assert all(p["chips_drawn"] == len(p["chips"]) for c in doc["cards"] for p in c["pools"]), doc
    # read, not hard-coded; orderly styles chips by seat only, so pools.json's "style" is not used
    assert _pool(edited, "Google (not read) \u00b7 Argon")["chips"] == [["Test fam", "solid"]]
    assert _pool(edited, "Google (not read) \u00b7 Claude/GPT")["chips"] == []
    assert all(p["chips"] == [] for p in _card(none, "Google (not read) \u00b7 Argon")["pools"])
    assert doc["notes"] == []

def test_phone_google_chips_take_orderlys_family_order():
    """orderly families(): known families keep FAMILIES' order, so a Google row naming Opus last
    still draws it first; new families follow in the order rows name them. A Google pool with no
    weekly reading names none (seat_rows carries families only on a weekly bar)."""
    with tempfile.TemporaryDirectory() as t:
        conf = json.load(open(os.path.join(ROOT, "config", "pools.json")))
        conf["google_ai_pro"]["pools"]["argon"] = {"families": ["Zeta", "Argon"]}
        conf["google_ai_pro"]["pools"]["gemini"] = {"families": ["Argon", "Zeta"]}
        conf["google_ai_pro"]["pools"]["claudegpt"] = {"families": ["GPT-OSS", "Opus"]}
        alt = os.path.join(t, "pools.json"); json.dump(conf, open(alt, "w"))
        doc = phone_text(t, GOOGLE_CACHE, pools=alt)
        five_only = {k: v for k, v in GOOGLE_CACHE.items() if not k.startswith("google_gemini_week")}
        five_only.update({"google_gemini_5h": 5, "google_gemini_5h_reset_ts": NOW + 3600})
        no_week = phone_text(t, five_only, pools=alt)
        # a weekly percent with no reset still names its families (seat_rows needs no reset)
        no_reset = phone_text(t, {**five_only, "google_gemini_week": 5}, pools=alt)
        # no as_of.google: seat_rows draws a failed bar, which names no families
        no_stamp = phone_text(t, {**GOOGLE_CACHE, "as_of": {k: v for k, v in FRESH.items() if k != "google"}}, pools=alt)
    assert _pool(no_reset, "Google (not read) \u00b7 Gemini")["chips"] == [["Zeta", "solid"], ["Argon", "solid"]]
    assert all(p["chips"] == [] for p in _card(no_stamp, "Google (not read) \u00b7 Argon")["pools"]), no_stamp
    assert _pool(doc, "Google (not read) \u00b7 Argon")["chips"] == [["Zeta", "solid"], ["Argon", "solid"]]
    assert _pool(doc, "Google (not read) \u00b7 Gemini")["chips"] == [["Zeta", "solid"], ["Argon", "solid"]]
    assert _pool(doc, "Google (not read) \u00b7 Claude/GPT")["chips"] == [["Opus", "solid"], ["GPT-OSS", "solid"]]
    assert _pool(no_week, "Google (not read) \u00b7 Gemini")["chips"] == []

def test_phone_chips_that_do_not_fit_wrap_and_the_card_grows():
    with tempfile.TemporaryDirectory() as t:
        conf = json.load(open(os.path.join(ROOT, "config", "pools.json")))
        alt = os.path.join(t, "pools.json")
        heights = []
        for fams in (["A"], ["Family %d" % i for i in range(12)]):
            conf["google_ai_pro"]["pools"]["argon"] = {"families": fams}
            json.dump(conf, open(alt, "w"))
            doc = phone_text(t, GOOGLE_CACHE, pools=alt)
            p = _pool(doc, "Google (not read) \u00b7 Argon")
            assert p["chips_drawn"] == len(fams), p
            heights.append(Image.open(os.path.join(t, "p.png")).size[1])
        conf["google_ai_pro"]["pools"] = ["not", "a", "dict"]  # a bad block: no chips, no crash
        json.dump(conf, open(alt, "w"))
        assert _pool(phone_text(t, GOOGLE_CACHE, pools=alt), "Google (not read) \u00b7 Argon")["chips"] == []
    assert heights[1] > heights[0], heights

def test_phone_caps_wording_comes_from_the_cached_projection():
    cache = {"ts": NOW, "as_of": FRESH, "claude_week": 60, "claude_week_reset_ts": NOW + 4 * 86400,
             "claude_week_caps_early_s": 2 * 86400 + 600,                     # orderly roughSpan: "2d"
             "grok_pct": 19, "grok_reset_ts": NOW + 3 * 86400, "grok_window_s": 7 * 86400,
             "grok_caps_early_s": 40 * 60,                                    # under an hour: minutes
             "bot_pct": 30, "bot_reset_ts": NOW + 2 * 86400, "bot_window_s": 7 * 86400,
             "bot_caps_early_s": 3600,                                        # not this row's key
             "google_argon_week": 50, "google_argon_week_reset_ts": NOW + 5 * 86400,
             "google_argon_week_caps_early_s": 0,                             # zero: nothing
             "codex": 50, "codex_reset_ts": NOW + 5 * 86400, "codex_window_s": 7 * 86400,
             "codex_caps_early_s": True,                                      # a bool is not a number
             "cursor_api_pct": 20, "cursor_reset_ts": NOW - 60, "cursor_window_s": 31 * 86400,
             "cursor_other_caps_early_s": 86400,                              # pool already reset
             "claude_5h": 10, "claude_5h_reset_ts": NOW + 3600, "claude_5h_caps_early_s": 1800,
             "google_gemini_week": 50, "google_gemini_week_reset_ts": NOW + 5 * 86400,
             "google_gemini_week_caps_early_s": 2.5 * 3600,                   # half up, as JS: "3h"
             "google_claudegpt_week": 50, "google_claudegpt_week_reset_ts": NOW + 5 * 86400,
             "google_claudegpt_week_caps_early_s": float("inf")}              # not finite: nothing
    with tempfile.TemporaryDirectory() as t:
        doc = phone_text(t, cache)
    cl = _pool(doc, "Claude \u00b7 weekly")
    assert cl["stats"].startswith("60% \u00b7 caps 2d early \u00b7 5h 10%"), cl  # after the pct, weekly only
    assert cl["red"] == ["caps 2d early"], cl
    assert _pool(doc, "SuperGrok \u00b7 weekly")["red"] == ["caps 40m early"]
    assert _pool(doc, "Google (not read) \u00b7 Gemini")["red"] == ["caps 3h early"]
    for title in ("Grok Bot \u00b7 weekly", "Google (not read) \u00b7 Argon", "Google (not read) \u00b7 Claude/GPT", "Codex \u00b7 weekly", "Cursor \u00b7 Other Models"):
        assert "caps" not in _pool(doc, title)["stats"], _pool(doc, title)

def _plan_fixture(now, **over):
    """Both Claude windows full enough that the host line names the Claude tier.

    Google's weekly pool is a known cap, so the Opus host falls through to
    Claude. A stamped Google seat with no Opus percent would be an unknown
    reading, and that ask would hide the tier word this fixture is checking.
    """
    cache = {
        "ts": now,
        "as_of": {k: now for k in ("claude", "chatgpt", "cursor", "grokbot", "grok", "google")},
        "claude_5h": 100, "claude_5h_reset_ts": now + 3600,
        "claude_week": 34, "claude_week_reset_ts": now + 4 * 86400,
        "google_claudegpt_week": 100, "google_claudegpt_week_reset_ts": now + 4 * 86400,
        "codex": 10, "codex_plan": "plus", "codex_reset_ts": now + 5 * 86400, "codex_window_s": 7 * 86400,
        "cursor_auto_pct": 5, "cursor_api_pct": 20, "cursor_plan": "pro+",
        "cursor_reset_ts": now + 20 * 86400, "cursor_window_s": 31 * 86400,
        "bot_pct": 100, "bot_reset_ts": now + 2 * 86400, "bot_window_s": 7 * 86400,
        "grok_pct": 19, "grok_tier": "SuperGrok", "grok_reset_ts": now + 3 * 86400, "grok_window_s": 7 * 86400,
        "google_argon_week": 1, "google_argon_week_reset_ts": now + 6 * 86400,
    }
    cache.update(over)
    return cache

def _fable_chip_rows(doc):
    return [p["title"] for c in doc["cards"] for p in c["pools"] if any(chip[0] == "Fable" for chip in p["chips"])]

def test_pro_versus_max_fixture():
    """Pro hides the Fable slice and says no Max. Max keeps the slice and the tier's word.
    Google AI Pro and Muse stay their own cards; neither gets a plan code from the cache,
    so both titles keep the not-read label."""
    now = time.time()
    with tempfile.TemporaryDirectory() as t:
        pro = phone_text(t, _plan_fixture(now, claude_max=False, claude_tier="claude_ai"), muse_doc(now))
        mx = phone_text(t, _plan_fixture(now, claude_max=True, claude_tier="claude_max_5x",
                                         fable=12, fable_reset_ts=now + 4 * 86400), muse_doc(now))
    assert _fable_chip_rows(pro) == ["Cursor \u00b7 Other Models"], _fable_chip_rows(pro)
    assert not _fable_slice(pro), pro
    assert "Max" not in json.dumps(pro), pro["host"]
    assert "Pro 5h full" in pro["host"], pro["host"]
    assert _fable_chip_rows(mx) == ["Claude \u00b7 weekly", "Claude \u00b7 Fable slice", "Cursor \u00b7 Other Models"], _fable_chip_rows(mx)
    assert "Max" in json.dumps(mx) and "Max 5h full" in mx["host"], mx["host"]
    for doc in (pro, mx):
        titles = [p["title"] for c in doc["cards"] for p in c["pools"]]
        assert "Google (not read) \u00b7 Argon" in titles, titles
        assert "Muse (not read) \u00b7 weekly" in titles, titles

if __name__ == "__main__":
    if Image is None:
        print("skip: Pillow not installed"); sys.exit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try: fn(); print("ok " + fn.__name__)
        except AssertionError as e: failed += 1; print(f"FAIL {fn.__name__} {e}")
    print(f"{len(tests) - failed} passed, {failed} failed"); sys.exit(1 if failed else 0)
