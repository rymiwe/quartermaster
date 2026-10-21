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
PHONE_CX0 = 6 + 14 * 1.3  # frame inset plus the rail's 14px padding, at K = 1.3

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
    assert _pool(doc, "Muse \u00b7 weekly")["stats"].startswith("50% \u00b7 5h 35%"), doc

def test_missing_or_stale_muse_file_says_no_reading():
    stale = muse_doc(); stale["window"]["resets_at"] = iso(NOW - 60)
    for muse in (None, stale, {"captured_at": iso(NOW)}):
        with tempfile.TemporaryDirectory() as t:
            r, out = render(t, {"ts": NOW}, muse)
            assert r.returncode == 0, r.stderr
            im = Image.open(out).convert("RGB")
            assert BLUE not in {im.getpixel((x, y)) for y in range(im.size[1]) for x in range(im.size[0])}
            doc = phone_text(t, {"ts": NOW}, muse)
        assert _pool(doc, "Muse \u00b7 weekly")["stats"] == "no reading", doc

def test_phone_flag_is_a_no_op_alias():
    """One layout: orderly's meters panel at the narrow rail width. --phone, which used to
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
    assert w == 3 * 390, w              # orderly's narrow rail at 3x
    assert h > w, (w, h)                # tall
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
        gem = _pool(phone_text(t, cache), "Google AI Pro \u00b7 Gemini")
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
    assert _pool(doc, "Muse \u00b7 weekly")["stats"] == "no reading"

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
    assert len(p["stats_drawn"]) == 2, p
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
    assert _card(doc, "Google AI Pro \u00b7 Argon")["header"] is None          # no renewal in the cache
    assert _card(doc, "Muse \u00b7 weekly")["header"] is None

def test_phone_chips_follow_orderly_whatever_the_claude_plan():
    """orderly seatLabel: every family whose sources include the seat. The plan hides no chip;
    only the Fable slice row itself still needs a Max cache."""
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
    for doc in (unknown, pro, flag_max, capped):
        assert _pool(doc, "Claude \u00b7 weekly")["chips"] == [["Fable", "solid"], ["Opus", "solid"]], doc
        assert doc["notes"] == [], doc["notes"]  # no "missing Fable chip" footnote any more
        assert all(p["chips_drawn"] == len(p["chips"]) for c in doc["cards"] for p in c["pools"]), doc
        assert _pool(doc, "SuperGrok \u00b7 weekly")["chips"] == [["Grok", "dotted"]]  # a full pool keeps its chip
    for doc in (unknown, pro, flag_max, capped):  # orderly always draws the Fable slice row, with its chip
        assert fable_slice(doc) and _pool(doc, "Claude \u00b7 Fable slice")["chips"] == [["Fable", "solid"]]
    assert _pool(unknown, "Cursor \u00b7 Other Models")["chips"] == [["Fable", "dashed"], ["Opus", "dashed"], ["Sol", "dashed"]]
    assert _pool(unknown, "Muse \u00b7 weekly")["chips"] == [["Wonder full", "solid"], ["Wonder", "solid"]]
    assert _pool(unknown, "Google AI Pro \u00b7 Argon")["chips"] == [["Argon", "solid"]]  # config/pools.json
    assert unknown["legend"] == "dashed = metered \u00b7 dotted = builder only \u00b7 \u2605 first usable pool for the family"

def test_phone_fable_slice_row_follows_orderly():
    """quota_claude_fable (orderly server.rb:2031-2047) and renderRail (newtab.js:1205-1243):
    live with a percent; absent ("not on <plan>") when the plan is known not to be Max and the
    Claude read finished; otherwise unknown. Always drawn, always with its Fable chip."""
    now = time.time()
    base = {"ts": now, "as_of": {"claude": now - 120}, "claude_week": 10, "claude_week_reset_ts": now + 4 * 86400}
    fab = lambda doc: _pool(doc, "Claude \u00b7 Fable slice")
    with tempfile.TemporaryDirectory() as t:
        pro = phone_text(t, {**base, "claude_max": False})            # the pulled copy: only the bit
        pro_png = os.path.join(t, "p.png"); pro_im = Image.open(pro_png).convert("RGB"); pro_im.load()
        named = phone_text(t, {**base, "claude_plan": "claude_ai"})    # orderly's own input: the plan name
        odd = phone_text(t, {**base, "claude_plan": "enterprise"})
        old = phone_text(t, {**base, "claude_max": False, "as_of": {"claude": now - 3600 - 5}})
        no_read = phone_text(t, {**base, "claude_max": False, "as_of": {}})   # no finished read: not absent
        unknown = phone_text(t, base)
        mx = phone_text(t, {**base, "claude_max": True})
        live = phone_text(t, {**base, "claude_max": True, "fable": 12, "fable_reset_ts": now + 3 * 86400})
        bare = phone_text(t, {**base, "claude_max": True, "fable": 60})  # a percent with no reset
        # a plan name wins over the bit, as orderly reads only the name: Max named, not absent
        named_max = phone_text(t, {**base, "claude_plan": "default_claude_max_5x", "claude_max": False})
        # a kept percent with no as_of.claude: orderly's red "read failed" after the percent
        kept = phone_text(t, {**base, "as_of": {}, "fable": 12, "fable_reset_ts": now + 3 * 86400,
                              "fable_caps_early_s": 86400, "errors": {"claude": "HTTPError 401: bad"}})
        kept_bare = phone_text(t, {**base, "as_of": {}, "fable": 60})
    assert (fab(pro)["state"], fab(pro)["stats"]) == ("absent", "not on this plan"), fab(pro)
    assert fab(named)["stats"] == "not on Pro" and fab(odd)["stats"] == "not on enterprise"
    assert fab(old)["stats"] == "not on this plan \u00b7 stale 1h", fab(old)
    for doc in (pro, named, odd, old):
        assert fab(doc)["chips"] == [["Fable", "solid"]] and fab(doc)["state"] == "absent"
    for doc in (unknown, mx):
        assert fab(doc)["state"] == "unknown"
        assert fab(doc)["stats"] == "unknown \u00b7 seat-usage has no value \u00b7 verified 2m", fab(doc)
    assert fab(no_read)["stats"] == "read failed \u00b7 unknown \u00b7 seat-usage has no value", fab(no_read)
    assert fab(no_read)["red"] == ["read failed"]
    assert fab(live)["state"] == "live" and fab(live)["stats"].startswith("12%"), fab(live)
    assert (fab(bare)["state"], fab(bare)["stats"]) == ("live", "60%"), fab(bare)
    assert fab(named_max)["state"] == "unknown", fab(named_max)
    assert fab(kept)["stats"].startswith("12% \u00b7 caps 1d early \u00b7 read failed: HTTPError 401 \u00b7 resets"), fab(kept)
    assert fab(kept)["red"] == ["caps 1d early", "read failed: HTTPError 401"], fab(kept)
    assert fab(kept_bare)["stats"] == "60% \u00b7 read failed", fab(kept_bare)
    # absent styling: the title in orderly's --muted, and a track the card's full content width
    # at ink 8% over the Claude card (.track.absent); no other row is muted
    assert fab(pro)["muted"] and not fab(unknown)["muted"] and not fab(live)["muted"]
    assert not any(p["muted"] for c in pro["cards"] for p in c["pools"] if p["title"] != "Claude \u00b7 Fable slice")
    track = over(O_INK, over((0xff, 0x87, 0x00), O_PANEL2, 0.09), 0.08)
    w, h = pro_im.size
    tx0, tx1 = (PHONE_CX0 + 11 * 1.3) * 3, (390 - 6 - 14 * 1.3 - 10 * 1.3) * 3  # card content edges
    lo, hi = int(tx0) + 6, int(tx1) - 6
    full = [y for y in range(h) if all(near(pro_im.getpixel((x, y)), track, 3) for x in range(lo, hi))]
    assert full, "no full-width absent track"

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
    assert _pool(doc, "Google AI Pro \u00b7 Argon")["chips"] == [["Argon", "solid"]]
    assert _pool(doc, "Google AI Pro \u00b7 Gemini")["chips"] == [["Argon", "solid"], ["Gemini Flash", "solid"]]
    assert _pool(doc, "Google AI Pro \u00b7 Claude/GPT")["chips"] == [["Opus", "solid"], ["Sonnet", "solid"], ["GPT-OSS", "solid"]]
    assert all(p["chips_drawn"] == len(p["chips"]) for c in doc["cards"] for p in c["pools"]), doc
    # read, not hard-coded; orderly styles chips by seat only, so pools.json's "style" is not used
    assert _pool(edited, "Google AI Pro \u00b7 Argon")["chips"] == [["Test fam", "solid"]]
    assert _pool(edited, "Google AI Pro \u00b7 Claude/GPT")["chips"] == []
    assert all(p["chips"] == [] for p in _card(none, "Google AI Pro \u00b7 Argon")["pools"])
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
    assert _pool(no_reset, "Google AI Pro \u00b7 Gemini")["chips"] == [["Zeta", "solid"], ["Argon", "solid"]]
    assert all(p["chips"] == [] for p in _card(no_stamp, "Google AI Pro \u00b7 Argon")["pools"]), no_stamp
    assert _pool(doc, "Google AI Pro \u00b7 Argon")["chips"] == [["Zeta", "solid"], ["Argon", "solid"]]
    assert _pool(doc, "Google AI Pro \u00b7 Gemini")["chips"] == [["Zeta", "solid"], ["Argon", "solid"]]
    assert _pool(doc, "Google AI Pro \u00b7 Claude/GPT")["chips"] == [["Opus", "solid"], ["GPT-OSS", "solid"]]
    assert _pool(no_week, "Google AI Pro \u00b7 Gemini")["chips"] == []

def test_phone_chips_that_do_not_fit_wrap_and_the_card_grows():
    with tempfile.TemporaryDirectory() as t:
        conf = json.load(open(os.path.join(ROOT, "config", "pools.json")))
        alt = os.path.join(t, "pools.json")
        heights = []
        for fams in (["A"], ["Family %d" % i for i in range(12)]):
            conf["google_ai_pro"]["pools"]["argon"] = {"families": fams}
            json.dump(conf, open(alt, "w"))
            doc = phone_text(t, GOOGLE_CACHE, pools=alt)
            p = _pool(doc, "Google AI Pro \u00b7 Argon")
            assert p["chips_drawn"] == len(fams), p
            heights.append(Image.open(os.path.join(t, "p.png")).size[1])
        conf["google_ai_pro"]["pools"] = ["not", "a", "dict"]  # a bad block: no chips, no crash
        json.dump(conf, open(alt, "w"))
        assert _pool(phone_text(t, GOOGLE_CACHE, pools=alt), "Google AI Pro \u00b7 Argon")["chips"] == []
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
    assert _pool(doc, "Google AI Pro \u00b7 Gemini")["red"] == ["caps 3h early"]
    for title in ("Grok Bot \u00b7 weekly", "Google AI Pro \u00b7 Argon", "Google AI Pro \u00b7 Claude/GPT", "Codex \u00b7 weekly", "Cursor \u00b7 Other Models"):
        assert "caps" not in _pool(doc, title)["stats"], _pool(doc, title)

if __name__ == "__main__":
    if Image is None:
        print("skip: Pillow not installed"); sys.exit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try: fn(); print("ok " + fn.__name__)
        except AssertionError as e: failed += 1; print(f"FAIL {fn.__name__} {e}")
    print(f"{len(tests) - failed} passed, {failed} failed"); sys.exit(1 if failed else 0)
