package dev.mindguard.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflinePolicyTest {
    private val night = CachedTimeWindow(23 * 60, 5 * 60, setOf(0, 1, 2, 3, 4, 5, 6))
    private val block = CachedRule("r_block_social", "BLOCK", setOf(AppCategory.SOCIAL_MEDIA), emptySet(), night, null, false)

    @Test
    fun overnightWindowsBelongToTheStartDay() {
        val monOnly = CachedTimeWindow(23 * 60, 5 * 60, setOf(0))
        assertTrue(monOnly.contains(0, 23 * 60 + 30))
        assertTrue(monOnly.contains(1, 2 * 60))
        assertFalse(monOnly.contains(1, 23 * 60 + 30))
    }

    @Test
    fun offlineNeverRestrictsOnlyReminds() {
        val v = OfflinePolicy.evaluate(listOf(block), OfflineContext(2, 23 * 60 + 40, "com.instagram.android", false, 12.0))
        assertEquals(InterventionType.SOFT_WARNING, v.intervention)
        val early = OfflinePolicy.evaluate(listOf(block), OfflineContext(2, 23 * 60 + 40, "com.instagram.android", false, 1.0))
        assertEquals(InterventionType.ALLOW, early.intervention)
    }

    @Test
    fun exceptionsEssentialAppsAndContentRulesAreRespected() {
        val allow = CachedRule("r_allow_youtube", "ALLOW", emptySet(), setOf("com.instagram.android"), null, null, false)
        assertEquals(InterventionType.ALLOW, OfflinePolicy.evaluate(listOf(block, allow), OfflineContext(2, 23 * 60 + 40, "com.instagram.android", false, 20.0)).intervention)
        assertEquals(InterventionType.ALLOW, OfflinePolicy.evaluate(listOf(block), OfflineContext(2, 23 * 60 + 40, "com.google.android.dialer", false, 20.0)).intervention)
        val contentRule = block.copy(ruleId = "r_block_shorts", hasContentCondition = true)
        assertEquals(InterventionType.ALLOW, OfflinePolicy.evaluate(listOf(contentRule), OfflineContext(2, 23 * 60 + 40, "com.instagram.android", false, 20.0)).intervention)
    }
}
