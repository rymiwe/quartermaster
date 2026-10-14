package dev.rymiwe.seats

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import kotlin.math.max
import kotlin.math.min

// Draws the widget as one bitmap: ops-tab's quota rail. One row per seat in the status
// line's order, each pool a faintly tinted band edged in its hue, and every meter's pace
// tick on one shared "now" line. Rows carry a label and text where they fit; a short
// widget keeps the meters alone, which still shows who is past pace and who is empty.
class Render(private val density: Float) {
    private val c = Palette

    private fun dp(v: Float) = v * density

    private val mono = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.MONOSPACE }
    private val sans = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL) }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)

    fun draw(wDp: Int, hDp: Int, payload: Payload?, error: String?, configured: Boolean, now: Double): Bitmap {
        val w = max(1, dp(wDp.toFloat()).toInt())
        val h = max(1, dp(hDp.toFloat()).toInt())
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val cv = Canvas(bmp)
        val r = dp(14f)
        fill.color = c.panel
        cv.drawRoundRect(RectF(0f, 0f, w.toFloat(), h.toFloat()), r, r, fill)

        val pad = dp(10f)
        if (!configured || payload == null) {
            val msg = if (!configured) "Long-press → reconfigure, then paste the gist id" else (error ?: "no data yet · tap to refresh")
            mono.textSize = dp(12f); mono.color = c.muted
            cv.drawText(fit(msg, mono, w - 2 * pad), pad, h / 2f + mono.textSize / 3, mono)
            return bmp
        }
        drawRail(cv, pad, pad, w - 2 * pad, h - 2 * pad, payload, error, now)
        return bmp
    }

    // The reading's age, amber after 30 minutes or a failed fetch, red after 3 hours.
    private fun age(p: Payload, error: String?, now: Double): Pair<String, Int> {
        val ageS = p.ts?.let { now - it }
        val text = when {
            error != null && ageS != null -> "${Rules.age(ageS)} · offline"
            error != null -> "offline"
            ageS != null -> Rules.age(ageS)
            else -> ""
        }
        val color = when {
            ageS == null || ageS > 3 * 3600 -> c.crit
            error != null || ageS > 30 * 60 -> c.warn
            else -> c.muted
        }
        return text to color
    }

    private fun drawRail(cv: Canvas, x0: Float, y0: Float, w: Float, h: Float, p: Payload, error: String?, now: Double) {
        val pools = p.pools.filter { it.seats.isNotEmpty() }
        val n = pools.sumOf { it.seats.size }
        if (n == 0) return
        val (age, ageColor) = age(p, error, now)
        fun rowH(avail: Float, gap: Float) = min(dp(24f), (avail - gap * (pools.size - 1)) / n)

        // Full rows under a one-line header that carries the age; when they don't fit,
        // meters alone with the age at the right.
        val headerH = dp(15f)
        var poolGap = dp(5f)
        var rowH = rowH(h - headerH, poolGap)
        if (rowH < dp(14f)) { poolGap = dp(3f); rowH = rowH(h - headerH, poolGap) }
        val full = rowH >= dp(11f)
        var top = y0 + headerH
        if (!full) { poolGap = dp(2f); top = y0; rowH = rowH(h, poolGap) }
        if (rowH < dp(3f)) return

        val edge = dp(3f)
        val padL = dp(8f)
        val padR = dp(8f)
        val colGap = dp(10f)
        mono.textSize = if (full) min(dp(11f), rowH * 0.58f) else min(dp(11f), max(dp(9f), h * 0.22f))

        var bandW = w
        var labelW = 0f
        var textW = 0f
        var meterW: Float
        var short = false
        val wide = w >= dp(420f)
        if (full) {
            mono.textSize = dp(10f); mono.color = ageColor
            if (age.isNotEmpty()) cv.drawText(age, x0 + w - mono.measureText(age), y0 + headerH * 0.62f, mono)
            sans.textSize = min(dp(12f), rowH * 0.62f); sans.color = c.ink
            mono.textSize = min(dp(11f), rowH * 0.58f)
            // The band's hue names the vendor, so a narrow row can say "Claude wk".
            labelW = min(w * 0.32f, pools.flatMap { pl -> pl.seats.map { sans.measureText(label(pl, it, wide)) } }.max())
            // Size the text column to its longest row; fall back to the status line's short
            // form when that would squeeze the meter.
            meterW = 0f
            for (variant in listOf(false, true)) {
                short = variant
                textW = min(w * 0.42f, pools.flatMap { it.seats }.maxOf { mono.measureText(Rules.railText(it, now, short, p.ts)) })
                meterW = w - edge - padL - labelW - textW - 2 * colGap - padR
                if (meterW >= dp(70f)) break
            }
        } else {
            val ageW = if (age.isEmpty()) 0f else mono.measureText(age) + colGap
            bandW = w - ageW
            meterW = bandW - edge - padL - padR
            if (age.isNotEmpty()) {
                mono.color = ageColor
                cv.drawText(age, x0 + w - mono.measureText(age), y0 + h / 2 + mono.textSize * 0.36f, mono)
            }
        }
        if (meterW < dp(40f)) return

        val meterH = if (full) dp(6f) else min(dp(6f), max(dp(2f), rowH * 0.6f))
        var y = top
        for (pool in pools) {
            val bandH = rowH * pool.seats.size
            val hue = xc(pool.hue)
            // A faint tint of the pool's hue, edged in the hue, as ops-tab's .seat rows.
            fill.color = (hue and 0x00ffffff) or 0x17000000
            cv.drawRoundRect(RectF(x0, y, x0 + bandW, y + bandH), dp(4f), dp(4f), fill)
            fill.color = hue
            cv.drawRect(x0, y, x0 + edge, y + bandH, fill)
            pool.seats.forEachIndexed { i, s ->
                val cy = y + rowH * i + rowH / 2
                val lx = x0 + edge + padL
                if (full) {
                    cv.drawText(fit(label(pool, s, wide), sans, labelW), lx, cy + sans.textSize * 0.36f, sans)
                    val mx = lx + labelW + colGap
                    drawMeter(cv, mx, cy, meterW, meterH, dp(3f), s, now)
                    drawParts(cv, mx + meterW + colGap, cy + mono.textSize * 0.36f, textW, Rules.railParts(s, now, short, p.ts),
                        if (short) " " else " · ")
                } else {
                    // Ticks reach the row edges, so the "now" line runs unbroken down a pool.
                    drawMeter(cv, lx, cy, meterW, meterH, (rowH - meterH) / 2, s, now)
                }
            }
            y += bandH + poolGap
        }
    }

    private fun label(pool: Pool, s: Seat, wide: Boolean) = if (wide) s.label else "${pool.name} ${s.short}"

    private fun drawParts(cv: Canvas, x: Float, base: Float, w: Float, parts: List<Rules.Part>, sep: String) {
        var cx = x
        val end = x + w
        parts.forEachIndexed { i, part ->
            val text = (if (i > 0) sep else "") + part.text
            val room = end - cx
            if (room <= mono.measureText("…")) return
            val shown = fit(text, mono, room)
            mono.color = c.muted
            if (part.caps && i > 0) {           // the separator stays muted, the warning goes red
                cv.drawText(sep, cx, base, mono)
                cx += mono.measureText(sep)
                mono.color = c.crit
                val t = fit(part.text, mono, end - cx)
                cv.drawText(t, cx, base, mono)
                cx += mono.measureText(t)
            } else {
                if (part.caps) mono.color = c.crit
                cv.drawText(shown, cx, base, mono)
                cx += mono.measureText(shown)
            }
            if (shown != text) return
        }
    }

    // ops-tab's .meter: a track half the meter wide, slid so its pace tick sits on the
    // meter's centre line. Left of the line is the window so far, right of it the window
    // still to come, and the track's right end is the cap. Fill past the line is red; a
    // full pool is red throughout; a seat with no known window centres and uses the
    // 50/80 colours.
    private fun drawMeter(cv: Canvas, x: Float, cy: Float, w: Float, h: Float, reach: Float, s: Seat, now: Double) {
        val y = cy - h / 2
        val r = h / 2
        val half = w / 2
        val e = Rules.elapsedPct(s, now)
        val tx = x + half - ((e ?: 50.0) / 100 * half).toFloat()
        fill.color = c.track
        cv.drawRoundRect(RectF(tx, y, tx + half, y + h), r, r, fill)
        val pct = (if (s.exhausted) 100.0 else s.pct)?.coerceIn(0.0, 100.0)
        if (pct != null && pct > 0) {
            val fw = max((half * pct / 100).toFloat(), h)
            val rect = RectF(tx, y, tx + fw, y + h)
            val over = e != null && pct < 100 && pct > e
            fill.color = when {
                pct >= 100 -> c.meterCrit
                e == null -> when (Rules.meterLevel(pct)) { 2 -> c.meterCrit; 1 -> c.meterWarn; else -> c.accent }
                else -> c.accent
            }
            cv.drawRoundRect(rect, r, r, fill)
            if (over) {
                cv.save()
                cv.clipRect(x + half, y, tx + fw, y + h)
                fill.color = c.meterCrit
                cv.drawRoundRect(rect, r, r, fill)
                cv.restore()
            }
        }
        if (e != null) {
            fill.color = c.ink
            cv.drawRoundRect(RectF(x + half - dp(1f), y - reach, x + half + dp(1f), y + h + reach), dp(1f), dp(1f), fill)
        }
    }

    private fun fit(s: String, p: Paint, w: Float): String {
        if (p.measureText(s) <= w) return s
        var n = s.length
        while (n > 1 && p.measureText(s.substring(0, n) + "…") > w) n--
        return s.substring(0, n) + "…"
    }

    private fun xc(i: Int) = 0xff000000.toInt() or Rules.xterm(i)
}

// ops-tab's dark theme. Pool hues come from the payload as xterm-256 indexes.
object Palette {
    val panel = 0xff1b2027.toInt()
    val ink = 0xffe4e8ee.toInt()
    val muted = 0xff6f7987.toInt()
    val track = 0x24e4e8ee                              // ink at 14%, as ops-tab's .track
    val accent = 0xff5b8fd6.toInt()
    val meterWarn = 0xffc99019.toInt()
    val meterCrit = 0xffc43c3c.toInt()
    val warn = 0xfffab219.toInt()
    val crit = 0xffe04b4b.toInt()
}
