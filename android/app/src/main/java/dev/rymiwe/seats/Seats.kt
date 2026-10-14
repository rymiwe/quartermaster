package dev.rymiwe.seats

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone
import kotlin.math.max
import kotlin.math.roundToInt

// The payload seat-usage-push writes to the gist. Times are epoch seconds.
data class Seat(
    val id: String,
    val short: String,
    val label: String,
    val pct: Double?,
    val resetTs: Double?,
    val windowS: Double?,
    val pace: String,
    val exhausted: Boolean,
    val asOf: Double?,
)

data class Pool(val id: String, val name: String, val hue: Int, val seats: List<Seat>)

data class Payload(val ts: Double?, val pushedAt: Double?, val pools: List<Pool>) {
    val seats: List<Seat> get() = pools.flatMap { it.seats }
}

// The rail's rules, ported from ops-tab's newtab.js (and the status line for the short form). `now` is epoch seconds everywhere.
object Rules {
    // A time within 24h, a weekday within 7 days, else a date. Empty once past.
    fun resetLabel(ts: Double?, now: Double, tz: TimeZone = TimeZone.getDefault()): String {
        if (ts == null || ts <= now) return ""
        val d = ts - now
        val cal = Calendar.getInstance(tz, Locale.US).apply { timeInMillis = (ts * 1000).toLong() }
        if (d < 86400) {
            val h = cal.get(Calendar.HOUR_OF_DAY)
            val m = cal.get(Calendar.MINUTE)
            val h12 = if (h % 12 == 0) 12 else h % 12
            val ap = if (h < 12) "am" else "pm"
            return if (m == 0) "$h12$ap" else "$h12:${m.toString().padStart(2, '0')}$ap"
        }
        val fmt = if (d < 7 * 86400) "EEE" else "M/d"
        return SimpleDateFormat(fmt, Locale.US).apply { timeZone = tz }.format(cal.time)
    }

    fun paceGlyph(pace: String): String = when (pace) {
        "ahead" -> "▲"
        "under" -> "▼"
        else -> ""
    }

    // Share of the window elapsed as a percent, or null without a window. ops-tab's elapsedPct.
    fun elapsedPct(seat: Seat, now: Double): Double? {
        val r = seat.resetTs ?: return null
        val w = seat.windowS ?: return null
        if (w <= 0) return null
        return (100 - (r - now) * 100 / w).coerceIn(0.0, 100.0)
    }

    // Seconds before the reset that the pool empties at the rate so far, or null when it
    // lasts. Skipped in the first tenth of a window, where one large job projects a cap
    // that is not real. ops-tab's capsLabel and the status line's !Nd.
    fun capsEarlyS(seat: Seat, now: Double): Double? {
        val pct = seat.pct ?: return null
        val w = seat.windowS ?: return null
        val e = elapsedPct(seat, now) ?: return null
        if (e < 10 || pct >= 100 || pct <= e) return null
        val used = w * e / 100
        val left = w - used
        val capIn = (100 - pct) / (pct / used)
        return if (capIn >= left) null else left - capIn
    }

    // The status line's marker: whole days early, only once it is at least a day.
    fun capsDays(seat: Seat, now: Double): Int? =
        capsEarlyS(seat, now)?.let { if (it >= 86400) (it / 86400).toInt() else null }

    // A duration as "2d", "1d 4h", "3h 10m" or "5m", as ops-tab's formatSpan.
    fun span(secs: Double): String {
        val s = max(0.0, secs).toLong()
        val d = s / 86400
        val h = s % 86400 / 3600
        val m = s % 3600 / 60
        return when {
            d >= 2 -> "${d}d"
            d >= 1 -> if (h > 0) "${d}d ${h}h" else "${d}d"
            h >= 1 -> if (m > 0) "${h}h ${m}m" else "${h}h"
            else -> "${m}m"
        }
    }

    // ops-tab's roughSpan: whole hours once it is an hour or more.
    fun roughSpan(secs: Double): String = span(if (secs < 3600) secs else Math.round(secs / 3600) * 3600.0)

    data class Part(val text: String, val caps: Boolean = false)

    // ops-tab's rail text: "62%▲ · caps 2d early · resets Thu", "exhausted · resets Thu",
    // "unknown". `short` is the status line's form for a narrow row: "62%▲ !2d @Thu", "EMPTY @5pm".
    // A seat whose reading lags the payload by 15 minutes says "stale"; the header already
    // carries the age of the payload as a whole.
    fun railParts(seat: Seat, now: Double, short: Boolean = false, payloadTs: Double? = null,
                  tz: TimeZone = TimeZone.getDefault()): List<Part> {
        val head = when {
            seat.exhausted -> if (short) "EMPTY" else "exhausted"
            seat.pct == null -> return listOf(Part(if (short) "?" else "unknown"))
            else -> "${seat.pct.roundToInt()}%${paceGlyph(seat.pace)}"
        }
        val out = mutableListOf(Part(head))
        capsEarlyS(seat, now)?.let {
            if (!short) out += Part("caps ${roughSpan(it)} early", caps = true)
            else capsDays(seat, now)?.let { d -> out += Part("!${d}d", caps = true) }
        }
        val r = resetLabel(seat.resetTs, now, tz)
        if (r.isNotEmpty()) out += Part(if (short) "@$r" else "resets $r")
        if (!short && seat.asOf != null && payloadTs != null && payloadTs - seat.asOf > 15 * 60)
            out += Part("stale ${span(now - seat.asOf)}")
        return out
    }

    fun railText(seat: Seat, now: Double, short: Boolean = false, payloadTs: Double? = null,
                 tz: TimeZone = TimeZone.getDefault()): String =
        railParts(seat, now, short, payloadTs, tz).joinToString(if (short) " " else " · ") { it.text }

    // ops-tab's meter colour: crit at 80, warn at 50.
    fun meterLevel(pct: Double): Int = when {
        pct >= 80 -> 2
        pct >= 50 -> 1
        else -> 0
    }

    fun age(s: Double): String {
        val m = max(0.0, s) / 60
        return when {
            m < 1 -> "now"
            m < 60 -> "${m.toInt()}m"
            m < 48 * 60 -> "${(m / 60).toInt()}h"
            else -> "${(m / 1440).toInt()}d"
        }
    }

    // xterm-256 colour index to 0xRRGGBB, for the status line's hues.
    fun xterm(i: Int): Int {
        val base = intArrayOf(
            0x000000, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080, 0x008080, 0xc0c0c0,
            0x808080, 0xff0000, 0x00ff00, 0xffff00, 0x0000ff, 0xff00ff, 0x00ffff, 0xffffff,
        )
        if (i in 0..15) return base[i]
        if (i in 232..255) {
            val v = 8 + (i - 232) * 10
            return (v shl 16) or (v shl 8) or v
        }
        val c = (i.coerceIn(16, 231)) - 16
        fun ch(n: Int) = if (n == 0) 0 else 55 + n * 40
        return (ch(c / 36) shl 16) or (ch(c / 6 % 6) shl 8) or ch(c % 6)
    }

    // A gist id from a pasted id or gist URL.
    fun gistId(input: String): String? =
        Regex("[0-9a-f]{20,}").findAll(input.trim().lowercase()).lastOrNull()?.value
}
