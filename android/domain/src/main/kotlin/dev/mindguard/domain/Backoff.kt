package dev.mindguard.domain

/** Exponential backoff with full jitter for event uploads. */
object Backoff {
    fun delayMs(attempt: Int, baseMs: Long = 2_000, maxMs: Long = 15 * 60_000, random: (Long) -> Long): Long {
        require(attempt >= 0)
        val exp = if (attempt >= 30) maxMs else (baseMs shl attempt).coerceAtMost(maxMs)
        return random(exp.coerceAtLeast(1)).coerceIn(0, maxMs)
    }

    /** 4xx responses other than 408/429 are permanent: retrying the same payload cannot succeed. */
    fun isRetryable(status: Int): Boolean = status == 408 || status == 429 || status >= 500 || status == 0
}
