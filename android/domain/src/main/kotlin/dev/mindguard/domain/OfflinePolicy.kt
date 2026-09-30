package dev.mindguard.domain

data class CachedTimeWindow(val startMinute: Int, val endMinute: Int, val days: Set<Int>) {
    /** Overnight windows (e.g. 23:00-05:00) belong to the day they start on, like the backend engine. */
    fun contains(weekday: Int, minuteOfDay: Int): Boolean {
        if (startMinute == endMinute) return weekday in days
        return if (startMinute < endMinute) {
            weekday in days && minuteOfDay in startMinute until endMinute
        } else {
            (weekday in days && minuteOfDay >= startMinute) || (((weekday + 6) % 7) in days && minuteOfDay < endMinute)
        }
    }
}

data class CachedRule(
    val ruleId: String,
    val effect: String,
    val appCategories: Set<AppCategory>,
    val appPackages: Set<String>,
    val timeWindow: CachedTimeWindow?,
    val focusSession: Boolean?,
    val hasContentCondition: Boolean,
)

data class OfflineContext(val weekday: Int, val minuteOfDay: Int, val appPackage: String, val focusActive: Boolean, val sessionMinutes: Double)

data class OfflineVerdict(val intervention: InterventionType, val ruleId: String?, val reason: String)

/**
 * Used only when the backend is unreachable. It can at most show a gentle reminder: restrictions always require a
 * server-side guardrail authorization, so an offline device never blocks anything on its own.
 */
object OfflinePolicy {
    private val restrictiveEffects = setOf("WARN", "DELAY", "REQUIRE_CONFIRMATION", "LIMIT", "BLOCK")

    fun evaluate(rules: List<CachedRule>, ctx: OfflineContext): OfflineVerdict {
        if (AppCatalog.isEssential(ctx.appPackage)) return OfflineVerdict(InterventionType.ALLOW, null, "essential app")
        val category = AppCatalog.category(ctx.appPackage)
        val matching = rules.filter { rule ->
            !rule.hasContentCondition && // content cannot be verified offline
                (rule.appPackages.isEmpty() && rule.appCategories.isEmpty() || ctx.appPackage in rule.appPackages || category in rule.appCategories) &&
                (rule.timeWindow?.contains(ctx.weekday, ctx.minuteOfDay) ?: true) &&
                (rule.focusSession == null || rule.focusSession == ctx.focusActive)
        }
        if (matching.any { it.effect == "ALLOW" }) return OfflineVerdict(InterventionType.ALLOW, matching.first { it.effect == "ALLOW" }.ruleId, "exception applies")
        val restrictive = matching.firstOrNull { it.effect in restrictiveEffects } ?: return OfflineVerdict(InterventionType.ALLOW, null, "no rule applies")
        return if (ctx.sessionMinutes >= 5) {
            OfflineVerdict(InterventionType.SOFT_WARNING, restrictive.ruleId, "offline: reminder only, restrictions need the server")
        } else {
            OfflineVerdict(InterventionType.ALLOW, restrictive.ruleId, "offline: too early in the session")
        }
    }
}
