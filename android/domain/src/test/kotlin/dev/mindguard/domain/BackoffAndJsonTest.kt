package dev.mindguard.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackoffAndJsonTest {
    @Test
    fun backoffGrowsExponentiallyAndIsCapped() {
        val upper = { bound: Long -> bound } // deterministic "jitter" at the upper bound
        assertEquals(2_000L, Backoff.delayMs(0, random = upper))
        assertEquals(16_000L, Backoff.delayMs(3, random = upper))
        assertEquals(900_000L, Backoff.delayMs(40, random = upper))
        assertTrue(Backoff.isRetryable(503) && Backoff.isRetryable(429) && Backoff.isRetryable(0))
        assertFalse(Backoff.isRetryable(422))
    }

    @Test
    fun encodesEventsInTheApiSchemaWithEscaping() {
        val e = ClientEvent("and:open:x:1", "APP_OPENED", 0, "com.instagram.android", mapOf("note" to "quote\" and \\ newline\n", "n" to 3))
        assertEquals(
            "{\"events\":[{\"client_event_id\":\"and:open:x:1\",\"event_type\":\"APP_OPENED\",\"occurred_at\":\"1970-01-01T00:00:00Z\"," +
                "\"app_package\":\"com.instagram.android\",\"payload\":{\"note\":\"quote\\\" and \\\\ newline\\n\",\"n\":3}}]}",
            JsonWriter.batch(listOf(e)),
        )
    }
}
