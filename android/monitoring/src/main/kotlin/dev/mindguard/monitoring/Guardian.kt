package dev.mindguard.monitoring

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import dev.mindguard.domain.ForegroundEvent
import dev.mindguard.domain.ForegroundKind
import dev.mindguard.intervention.Channels
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Reads foreground changes from UsageStatsManager (requires the user-granted Usage Access special permission). */
class UsageEventSource(context: Context) {
    private val usage = context.applicationContext.getSystemService(UsageStatsManager::class.java)

    fun read(fromMs: Long, toMs: Long): List<ForegroundEvent> {
        val events = usage?.queryEvents(fromMs, toMs) ?: return emptyList()
        val out = mutableListOf<ForegroundEvent>()
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            val kind = when (event.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> ForegroundKind.RESUMED
                UsageEvents.Event.ACTIVITY_PAUSED -> ForegroundKind.PAUSED
                else -> null
            } ?: continue
            out += ForegroundEvent(event.packageName, event.timeStamp, kind)
        }
        return out
    }
}

/** The work the guardian performs on each tick; implemented in the app module where all dependencies are wired. */
interface GuardianEngine {
    suspend fun tick(nowMs: Long)
    suspend fun sync()
    fun statusText(): String
}

interface GuardianEngineProvider {
    val guardianEngine: GuardianEngine
}

/**
 * Foreground service with a persistent, user-visible notification — the platform-supported way to keep monitoring
 * the apps the user chose. Stopping protection is always one tap away in the notification.
 */
class GuardianService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loop: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        Channels.ensure(this)
        val notification = statusNotification("Protecting the apps you chose")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        val engine = (application as? GuardianEngineProvider)?.guardianEngine ?: return START_NOT_STICKY
        if (loop?.isActive != true) {
            loop = scope.launch {
                while (isActive) {
                    runCatching { engine.tick(System.currentTimeMillis()) }
                    delay(POLL_INTERVAL_MS)
                }
            }
        }
        return START_STICKY
    }

    private fun statusNotification(text: String): Notification {
        val stop = PendingIntent.getService(this, 0, Intent(this, GuardianService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, Channels.GUARDIAN)
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setContentTitle("MindGuard is on")
            .setContentText(text)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Stop protection", stop).build())
            .build()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "dev.mindguard.monitoring.STOP"
        private const val NOTIFICATION_ID = 1_001
        private const val POLL_INTERVAL_MS = 20_000L

        fun start(context: Context) = context.startForegroundService(Intent(context, GuardianService::class.java))

        fun stop(context: Context) = context.startService(Intent(context, GuardianService::class.java).setAction(ACTION_STOP))
    }
}

/** Periodic upload of queued events and policy refresh, scheduled with the platform JobScheduler. */
class SyncJobService : JobService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onStartJob(params: JobParameters): Boolean {
        val engine = (application as? GuardianEngineProvider)?.guardianEngine ?: return false
        scope.launch {
            runCatching { engine.sync() }
            jobFinished(params, false)
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean {
        scope.coroutineContext[Job]?.cancel()
        return true
    }

    companion object {
        private const val JOB_ID = 2_001

        fun schedule(context: Context) {
            val scheduler = context.getSystemService(JobScheduler::class.java) ?: return
            scheduler.schedule(
                JobInfo.Builder(JOB_ID, ComponentName(context, SyncJobService::class.java))
                    .setPeriodic(15 * 60 * 1000L)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setPersisted(true)
                    .build(),
            )
        }
    }
}

/** Restarts protection after reboot only if the user had turned the guardian on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val prefs = context.getSharedPreferences("mindguard_settings", Context.MODE_PRIVATE)
        if (prefs.getBoolean("guardian_enabled", false)) {
            runCatching { GuardianService.start(context) }
            SyncJobService.schedule(context)
        }
    }
}
