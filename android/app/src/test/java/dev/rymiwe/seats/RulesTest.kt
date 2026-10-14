package dev.rymiwe.seats

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.TimeZone

class RulesTest {
    private val utc = TimeZone.getTimeZone("UTC")
    private val now = 1_800_000_000.0   // 2027-01-15 08:00 UTC, a Friday
    private val week = 7 * 86400.0

    private fun seat(
        pct: Double?, reset: Double? = null, window: Double? = null, id: String = "s",
        exhausted: Boolean = false, asOf: Double? = null, pace: String = "",
    ) = Seat(id, id, id, pct, reset, window, pace, exhausted, asOf)

    @Test fun elapsedShare() {
        assertEquals(50.0, Rules.elapsedPct(seat(10.0, now + week / 2, week), now)!!, 0.001)
        assertEquals(100.0, Rules.elapsedPct(seat(10.0, now - 10, week), now)!!, 0.001)
        assertEquals(0.0, Rules.elapsedPct(seat(10.0, now + 2 * week, week), now)!!, 0.001)
        assertNull(Rules.elapsedPct(seat(10.0, now + 10, null), now))
    }

    @Test fun resetLabels() {
        assertEquals("9am", Rules.resetLabel(now + 3600, now, utc))
        assertEquals("9:30am", Rules.resetLabel(now + 5400, now, utc))
        assertEquals("12pm", Rules.resetLabel(now + 4 * 3600, now, utc))
        assertEquals("Mon", Rules.resetLabel(now + 3 * 86400, now, utc))
        assertEquals("1/25", Rules.resetLabel(now + 10 * 86400, now, utc))
        assertEquals("", Rules.resetLabel(now - 1, now, utc))
    }

    @Test fun railText() {
        // Half the window gone, 40% used: on pace, no caps.
        assertEquals("40%▲ · resets Mon", Rules.railText(seat(40.0, now + 3 * 86400, 6 * 86400.0, pace = "ahead"), now, tz = utc))
        assertEquals("exhausted · resets Mon", Rules.railText(seat(100.0, now + 3 * 86400, exhausted = true), now, tz = utc))
        assertEquals("unknown", Rules.railText(seat(null), now, tz = utc))
        assertEquals("10%", Rules.railText(seat(10.0), now, tz = utc))
    }

    @Test fun railTextCapsAndStale() {
        // Half of a 6-day window gone and 75% used: empties in 1 day, 2 days early.
        val s = seat(75.0, now + 3 * 86400, 6 * 86400.0, pace = "ahead", asOf = now - 3600)
        assertEquals("75%▲ · caps 2d early · resets Mon", Rules.railText(s, now, tz = utc))
        assertEquals("75%▲ · caps 2d early · resets Mon · stale 1h", Rules.railText(s, now, payloadTs = now, tz = utc))
        assertEquals("75%▲ !2d @Mon", Rules.railText(s, now, short = true, tz = utc))
        val caps = Rules.railParts(s, now, tz = utc).filter { it.caps }.map { it.text }
        assertEquals(listOf("caps 2d early"), caps)
    }

    @Test fun railTextShort() {
        assertEquals("62%▲ @Mon", Rules.railText(seat(62.4, now + 3 * 86400, week, pace = "ahead"), now, short = true, tz = utc))
        assertEquals("EMPTY @9am", Rules.railText(seat(100.0, now + 3600, exhausted = true), now, short = true, tz = utc))
        assertEquals("?", Rules.railText(seat(null), now, short = true, tz = utc))
    }

    @Test fun capsProjection() {
        val win = 6 * 86400.0
        // On or under pace never caps.
        assertNull(Rules.capsEarlyS(seat(50.0, now + 3 * 86400, win), now))
        // Inside the first tenth of the window, a spike is not projected.
        assertNull(Rules.capsEarlyS(seat(30.0, now + win * 0.95, win), now))
        assertNull(Rules.capsEarlyS(seat(100.0, now + 3 * 86400, win), now))
        assertNull(Rules.capsEarlyS(seat(75.0), now))
        assertEquals(2 * 86400.0, Rules.capsEarlyS(seat(75.0, now + 3 * 86400, win), now)!!, 1.0)
        assertEquals(2, Rules.capsDays(seat(75.0, now + 3 * 86400, win), now))
        // Under a day early: the rail says so, the status line stays quiet.
        val slight = seat(55.0, now + 3 * 86400, win)
        assertTrue(Rules.capsEarlyS(slight, now)!! < 86400)
        assertNull(Rules.capsDays(slight, now))
    }

    @Test fun spans() {
        assertEquals("2d", Rules.span(2.5 * 86400))
        assertEquals("1d 4h", Rules.span(86400 + 4 * 3600.0))
        assertEquals("3h 10m", Rules.span(3 * 3600 + 600.0))
        assertEquals("5m", Rules.span(300.0))
        assertEquals("3h", Rules.roughSpan(3 * 3600 + 600.0))
    }

    @Test fun xtermColours() {
        assertEquals(0xff8700, Rules.xterm(208))
        assertEquals(0x00d7af, Rules.xterm(43))
        assertEquals(0x6c6c6c, Rules.xterm(242))
        assertEquals(0xff0000, Rules.xterm(196))
    }

    @Test fun gistIdFromInput() {
        val id = "0123456789abcdef0123456789abcdef"
        assertEquals(id, Rules.gistId(id))
        assertEquals(id, Rules.gistId("https://gist.github.com/rymiwe/$id"))
        assertEquals(id, Rules.gistId("  https://gist.githubusercontent.com/rymiwe/$id/raw/seats.json "))
        assertNull(Rules.gistId("not a gist"))
    }
}
