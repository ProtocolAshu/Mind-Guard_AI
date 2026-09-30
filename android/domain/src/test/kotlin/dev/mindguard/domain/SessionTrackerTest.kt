package dev.mindguard.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionTrackerTest {
    private val ig = "com.instagram.android"
    private val min = 60_000L

    @Test
    fun opensExtendsAndClosesAMonitoredSession() {
        val t = SessionTracker(setOf(ig))
        val opened = t.onEvent(ForegroundEvent(ig, 0, ForegroundKind.RESUMED))
        assertEquals(listOf("APP_OPENED"), opened.map { it.eventType })
        val ext = t.flush(11 * min)
        assertEquals(listOf("SESSION_EXTENDED", "SESSION_EXTENDED"), ext.map { it.eventType })
        assertEquals(600L, ext.last().payload["duration_seconds"])
        t.onEvent(ForegroundEvent(ig, 12 * min, ForegroundKind.PAUSED))
        assertTrue(t.flush(12 * min + 10_000).isEmpty())
        val closed = t.flush(12 * min + 31_000)
        assertEquals("APP_CLOSED", closed.single().eventType)
        assertEquals(720L, closed.single().payload["duration_seconds"])
    }

    @Test
    fun briefSwitchDoesNotSplitTheSession() {
        val t = SessionTracker(setOf(ig))
        t.onEvent(ForegroundEvent(ig, 0, ForegroundKind.RESUMED))
        t.onEvent(ForegroundEvent(ig, 3 * min, ForegroundKind.PAUSED))
        val back = t.onEvent(ForegroundEvent(ig, 3 * min + 10_000, ForegroundKind.RESUMED))
        assertTrue(back.isEmpty())
        assertEquals(6.0, t.active(6 * min + 10_000)!!.minutes, 0.01)
    }

    @Test
    fun switchingToAnotherAppClosesAndIgnoresUnmonitoredApps() {
        val t = SessionTracker(setOf(ig))
        t.onEvent(ForegroundEvent(ig, 0, ForegroundKind.RESUMED))
        val out = t.onEvent(ForegroundEvent("com.example.notes", 2 * min, ForegroundKind.RESUMED))
        assertEquals(listOf("APP_CLOSED"), out.map { it.eventType })
        assertNull(t.active(3 * min))
    }

    @Test
    fun eventIdsAreDeterministicAndMatchTheBackendPattern() {
        val a = SessionTracker.eventId("open", ig, 1_726_300_000_000)
        assertEquals(a, SessionTracker.eventId("open", ig, 1_726_300_000_000))
        assertTrue(Regex("^[A-Za-z0-9_.:-]{8,64}$").matches(a))
    }

    @Test
    fun replayedOlderEventsAreIgnored() {
        val t = SessionTracker(setOf(ig))
        t.onEvent(ForegroundEvent(ig, 5 * min, ForegroundKind.RESUMED))
        assertTrue(t.onEvent(ForegroundEvent(ig, 1 * min, ForegroundKind.RESUMED)).isEmpty())
    }
}
