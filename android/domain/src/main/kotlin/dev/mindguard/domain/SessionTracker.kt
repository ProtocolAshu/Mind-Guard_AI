package dev.mindguard.domain

enum class ForegroundKind { RESUMED, PAUSED }

/** Platform-neutral foreground change (UsageEvents ACTIVITY_RESUMED / ACTIVITY_PAUSED on Android). */
data class ForegroundEvent(val packageName: String, val timestampMs: Long, val kind: ForegroundKind)

data class ActiveSession(val packageName: String, val startedAtMs: Long, val foregroundMs: Long) {
    val minutes: Double get() = foregroundMs / 60_000.0
}

/**
 * Turns an ordered stream of foreground changes into client events for monitored apps:
 * APP_OPENED when a session starts, SESSION_EXTENDED at each checkpoint of continuous use and APP_CLOSED once
 * the app has stayed in the background longer than [gapMs] (brief switches do not split a session).
 * Event ids are deterministic, so re-reading an overlapping UsageStats window never double-counts.
 */
class SessionTracker(
    private val monitored: Set<String>,
    private val gapMs: Long = 30_000,
    private val checkpointMs: Long = 5 * 60_000,
) {
    private data class State(val pkg: String, val startedAt: Long, var foregroundMs: Long, var resumedAt: Long?, var pausedAt: Long?, var nextCheckpoint: Long)

    private var state: State? = null
    private var lastTimestamp = Long.MIN_VALUE

    fun onEvent(event: ForegroundEvent): List<ClientEvent> {
        if (event.timestampMs < lastTimestamp) return emptyList() // out of order or already processed
        lastTimestamp = event.timestampMs
        val out = mutableListOf<ClientEvent>()
        closeIfExpired(event.timestampMs, out)
        val current = state
        when (event.kind) {
            ForegroundKind.RESUMED -> {
                if (current != null && current.pkg != event.packageName) {
                    pause(current, event.timestampMs)
                    out += close(current, event.timestampMs)
                    state = null
                }
                if (event.packageName !in monitored) return out
                val s = state
                if (s == null) {
                    state = State(event.packageName, event.timestampMs, 0, event.timestampMs, null, checkpointMs)
                    out += ClientEvent(eventId("open", event.packageName, event.timestampMs), "APP_OPENED", event.timestampMs, event.packageName)
                } else if (s.resumedAt == null) {
                    s.resumedAt = event.timestampMs
                    s.pausedAt = null
                }
            }
            ForegroundKind.PAUSED -> {
                val s = state ?: return out
                if (s.pkg == event.packageName) pause(s, event.timestampMs)
            }
        }
        return out
    }

    /** Emits checkpoint and close events up to [nowMs]; call on every poll. */
    fun flush(nowMs: Long): List<ClientEvent> {
        val out = mutableListOf<ClientEvent>()
        closeIfExpired(nowMs, out)
        val s = state ?: return out
        val foreground = s.foregroundMs + (s.resumedAt?.let { (nowMs - it).coerceAtLeast(0) } ?: 0)
        while (foreground >= s.nextCheckpoint) {
            out += ClientEvent(eventId("ext", s.pkg, s.startedAt + s.nextCheckpoint), "SESSION_EXTENDED", nowMs, s.pkg,
                mapOf("duration_seconds" to s.nextCheckpoint / 1000))
            s.nextCheckpoint += checkpointMs
        }
        return out
    }

    fun active(nowMs: Long): ActiveSession? {
        val s = state ?: return null
        if (s.resumedAt == null) return null
        return ActiveSession(s.pkg, s.startedAt, s.foregroundMs + (nowMs - s.resumedAt!!).coerceAtLeast(0))
    }

    private fun pause(s: State, at: Long) {
        s.resumedAt?.let { s.foregroundMs += (at - it).coerceAtLeast(0) }
        s.resumedAt = null
        s.pausedAt = at
    }

    private fun closeIfExpired(now: Long, out: MutableList<ClientEvent>) {
        val s = state ?: return
        val pausedAt = s.pausedAt ?: return
        if (now - pausedAt > gapMs) {
            out += close(s, pausedAt)
            state = null
        }
    }

    private fun close(s: State, at: Long): ClientEvent =
        ClientEvent(eventId("close", s.pkg, s.startedAt), "APP_CLOSED", at, s.pkg, mapOf("duration_seconds" to s.foregroundMs / 1000))

    companion object {
        /** Deterministic id matching ^[A-Za-z0-9_.:-]{8,64}$ (backend ClientEvent.client_event_id). */
        fun eventId(kind: String, pkg: String, timestampMs: Long): String {
            val hash = (pkg.hashCode().toLong() and 0xffffffffL).toString(36)
            return "and:$kind:$hash:${timestampMs.toString(36)}"
        }
    }
}
