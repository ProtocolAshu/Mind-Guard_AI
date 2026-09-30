package dev.mindguard.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import dev.mindguard.core.ApiException
import dev.mindguard.core.Clock
import dev.mindguard.core.SafeLog
import dev.mindguard.domain.Backoff
import dev.mindguard.domain.CachedRule
import dev.mindguard.domain.ClientEvent
import dev.mindguard.domain.JsonWriter
import java.io.IOException
import kotlin.random.Random

/** Durable outbox for client events (survives process death and offline periods). Stores no content, only usage events. */
class EventQueue(context: Context) : SQLiteOpenHelper(context.applicationContext, "mindguard_outbox.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE outbox (id TEXT PRIMARY KEY, body TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_ms INTEGER NOT NULL DEFAULT 0, created_ms INTEGER NOT NULL)")
        db.execSQL("CREATE INDEX ix_outbox_due ON outbox(next_attempt_ms)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun enqueue(events: List<ClientEvent>, nowMs: Long) {
        if (events.isEmpty()) return
        writableDatabase.transaction {
            events.forEach { e ->
                insertWithOnConflict("outbox", null, ContentValues().apply {
                    put("id", e.clientEventId); put("body", JsonWriter.event(e)); put("created_ms", nowMs)
                }, SQLiteDatabase.CONFLICT_IGNORE)
            }
        }
    }

    fun due(nowMs: Long, limit: Int = 200): List<Pair<String, String>> =
        readableDatabase.rawQuery("SELECT id, body FROM outbox WHERE next_attempt_ms <= ? ORDER BY created_ms LIMIT ?", arrayOf(nowMs.toString(), limit.toString())).use { c ->
            buildList { while (c.moveToNext()) add(c.getString(0) to c.getString(1)) }
        }

    fun remove(ids: List<String>) = writableDatabase.transaction { ids.forEach { delete("outbox", "id = ?", arrayOf(it)) } }

    fun reschedule(ids: List<String>, nowMs: Long, random: Random = Random.Default) = writableDatabase.transaction {
        ids.forEach { id ->
            val attempts = rawQuery("SELECT attempts FROM outbox WHERE id = ?", arrayOf(id)).use { if (it.moveToFirst()) it.getInt(0) else 0 }
            val delay = Backoff.delayMs(attempts) { bound -> random.nextLong(bound + 1) }
            execSQL("UPDATE outbox SET attempts = attempts + 1, next_attempt_ms = ? WHERE id = ?", arrayOf<Any>(nowMs + delay, id))
        }
    }

    fun size(): Long = readableDatabase.rawQuery("SELECT COUNT(*) FROM outbox", null).use { if (it.moveToFirst()) it.getLong(0) else 0 }

    /** Retention: events older than the backend's accepted age are dropped rather than retried forever. */
    fun purgeOlderThan(cutoffMs: Long) = writableDatabase.delete("outbox", "created_ms < ?", arrayOf(cutoffMs.toString()))

    private inline fun SQLiteDatabase.transaction(block: SQLiteDatabase.() -> Unit) {
        beginTransaction()
        try {
            block()
            setTransactionSuccessful()
        } finally {
            endTransaction()
        }
    }
}

class EventUploader(private val queue: EventQueue, private val api: MindGuardApi, private val clock: Clock) {
    /** Uploads due events in batches; returns how many left the outbox. */
    suspend fun uploadDue(): Int {
        val now = clock.nowMs()
        queue.purgeOlderThan(now - 7L * 24 * 3600 * 1000)
        var sent = 0
        while (true) {
            val batch = queue.due(now)
            if (batch.isEmpty()) return sent
            val ids = batch.map { it.first }
            try {
                api.postEventsJson("{\"events\":[" + batch.joinToString(",") { it.second } + "]}")
                queue.remove(ids) // accepted, duplicate or permanently rejected by schema: none benefit from a retry
                sent += ids.size
            } catch (e: ApiException) {
                if (e.retryable) {
                    queue.reschedule(ids, now)
                    return sent
                }
                SafeLog.w(TAG, "dropping batch rejected with ${e.status} ${e.code}")
                queue.remove(ids)
            } catch (e: IOException) {
                queue.reschedule(ids, now)
                return sent
            }
            if (batch.size < 200) return sent
        }
    }

    private companion object {
        const val TAG = "EventUploader"
    }
}

/** Cached active rules for offline reminders (see domain OfflinePolicy). */
class PolicyCache(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("mindguard_policy_cache", Context.MODE_PRIVATE)
    @Volatile private var rules: List<CachedRule> = emptyList()

    fun replace(newRules: List<CachedRule>) {
        rules = newRules
        prefs.edit().putInt("rule_count", newRules.size).apply()
    }

    fun rules(): List<CachedRule> = rules
}
