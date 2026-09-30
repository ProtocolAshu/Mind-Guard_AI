package dev.mindguard.domain

import java.time.Instant

/** Minimal, dependency-free JSON encoding for outbound payloads (org.json is unavailable in JVM unit tests). */
object JsonWriter {
    fun event(e: ClientEvent): String = obj(
        linkedMapOf(
            "client_event_id" to e.clientEventId,
            "event_type" to e.eventType,
            "occurred_at" to Instant.ofEpochMilli(e.occurredAtEpochMs).toString(),
            "app_package" to e.appPackage,
            "payload" to e.payload,
        ),
    )

    fun batch(events: List<ClientEvent>): String = "{\"events\":[" + events.joinToString(",") { event(it) } + "]}"

    fun obj(map: Map<String, Any?>): String = map.entries.joinToString(",", "{", "}") { (k, v) -> "${string(k)}:${value(v)}" }

    @Suppress("UNCHECKED_CAST")
    fun value(v: Any?): String = when (v) {
        null -> "null"
        is String -> string(v)
        is Boolean -> v.toString()
        is Int, is Long -> v.toString()
        is Double -> if (v.isFinite()) v.toString() else "null"
        is Float -> if (v.isFinite()) v.toString() else "null"
        is Map<*, *> -> obj(v as Map<String, Any?>)
        is Iterable<*> -> v.joinToString(",", "[", "]") { value(it) }
        else -> string(v.toString())
    }

    fun string(s: String): String {
        val sb = StringBuilder(s.length + 2).append('"')
        for (ch in s) {
            when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> if (ch < ' ') sb.append("\\u%04x".format(ch.code)) else sb.append(ch)
            }
        }
        return sb.append('"').toString()
    }
}
