package dev.mindguard.app

import android.app.Application
import dev.mindguard.ai.SharedContentAnalyzer
import dev.mindguard.core.ApiException
import dev.mindguard.core.Clock
import dev.mindguard.core.JsonHttp
import dev.mindguard.core.SafeLog
import dev.mindguard.core.SecureStore
import dev.mindguard.data.EventQueue
import dev.mindguard.data.EventUploader
import dev.mindguard.data.MindGuardApi
import dev.mindguard.data.PolicyCache
import dev.mindguard.data.TokenStore
import dev.mindguard.domain.ClientEvent
import dev.mindguard.domain.CommandAction
import dev.mindguard.domain.CommandCheck
import dev.mindguard.domain.CommandValidator
import dev.mindguard.domain.InterventionType
import dev.mindguard.domain.OfflineContext
import dev.mindguard.domain.OfflinePolicy
import dev.mindguard.domain.OutcomeType
import dev.mindguard.domain.OverrideKind
import dev.mindguard.domain.SessionTracker
import dev.mindguard.intervention.CompositeController
import dev.mindguard.intervention.ExecutionResult
import dev.mindguard.intervention.InterventionActions
import dev.mindguard.intervention.InterventionActionsProvider
import dev.mindguard.intervention.NotificationController
import dev.mindguard.intervention.OverlayController
import dev.mindguard.monitoring.GuardianEngine
import dev.mindguard.monitoring.GuardianEngineProvider
import dev.mindguard.monitoring.UsageEventSource
import dev.mindguard.permissions.PermissionChecker
import dev.mindguard.settings.LocalSettings
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.IOException
import java.time.ZoneId
import java.time.ZonedDateTime

data class GuardianStatus(val running: Boolean, val message: String, val lastDecision: InterventionType? = null, val queued: Long = 0)

/** Manual dependency container (no reflection-based DI framework needed at this size). */
class AppContainer(private val app: Application, private val clock: Clock = Clock.SYSTEM) : InterventionActions, GuardianEngine {
    val settings = LocalSettings(app)
    private val secure = SecureStore(app)
    val tokens = TokenStore(secure)
    val api = MindGuardApi(JsonHttp(settings.apiBaseUrl), tokens)
    val permissions = PermissionChecker(app)
    private val queue = EventQueue(app)
    private val uploader = EventUploader(queue, api, clock)
    private val policyCache = PolicyCache(app)
    private val notifications = NotificationController(app)
    private val controller = CompositeController(permissions, OverlayController(app, this), notifications)
    val content = SharedContentAnalyzer(api)
    private val source = UsageEventSource(app)
    private var tracker = SessionTracker(settings.monitoredPackages)
    private var lastPollMs = clock.nowMs() - 60_000
    private var lastEvaluationMs = 0L
    private var lastUploadMs = 0L
    private val _status = MutableStateFlow(GuardianStatus(running = false, message = "Protection is off"))
    val status: StateFlow<GuardianStatus> = _status.asStateFlow()

    override fun statusText(): String = _status.value.message

    override suspend fun tick(nowMs: Long) {
        if (!settings.guardianEnabled) {
            _status.value = GuardianStatus(false, "Protection is off")
            return
        }
        if (!permissions.usageAccess()) {
            _status.value = GuardianStatus(false, "Usage access is needed to notice sessions")
            return
        }
        val events = mutableListOf<ClientEvent>()
        source.read(lastPollMs - 2_000, nowMs).forEach { events += tracker.onEvent(it) }
        events += tracker.flush(nowMs)
        lastPollMs = nowMs
        endFocusIfDue(nowMs, events)
        queue.enqueue(events, nowMs)
        val session = tracker.active(nowMs)
        if (session != null && session.minutes >= 1.0 && nowMs - lastEvaluationMs >= EVALUATION_INTERVAL_MS) {
            lastEvaluationMs = nowMs
            evaluate(session.packageName, session.minutes, nowMs)
        } else if (nowMs - lastUploadMs >= UPLOAD_INTERVAL_MS) {
            lastUploadMs = nowMs
            runCatching { uploader.uploadDue() }
        }
        _status.value = _status.value.copy(running = true, queued = queue.size(),
            message = session?.let { "Watching ${it.packageName.substringAfterLast('.')} for ${it.minutes.toInt()} min" } ?: "Protecting the apps you chose")
    }

    private suspend fun evaluate(appPackage: String, minutes: Double, nowMs: Long) {
        try {
            uploader.uploadDue() // the server's context is built from uploaded events
            val result = api.evaluate(appPackage, minutes, permissions.capabilities())
            _status.value = _status.value.copy(lastDecision = result.decision)
            when (val check = CommandValidator.check(result.command, nowMs)) {
                is CommandCheck.Execute -> when (val shown = controller.execute(check.command)) {
                    is ExecutionResult.Failed -> SafeLog.w(TAG, "intervention not shown: ${shown.reason}")
                    else -> Unit
                }
                is CommandCheck.Reject -> if (result.decision != InterventionType.ALLOW) SafeLog.w(TAG, "command rejected on device: ${check.reason}")
            }
        } catch (e: IOException) {
            offlineReminder(appPackage, minutes, nowMs)
        } catch (e: ApiException) {
            if (e.retryable) offlineReminder(appPackage, minutes, nowMs) else SafeLog.w(TAG, "evaluation refused: ${e.code}")
        }
    }

    private fun offlineReminder(appPackage: String, minutes: Double, nowMs: Long) {
        val local = ZonedDateTime.ofInstant(java.time.Instant.ofEpochMilli(nowMs), ZoneId.systemDefault())
        val verdict = OfflinePolicy.evaluate(policyCache.rules(), OfflineContext(local.dayOfWeek.value - 1, local.hour * 60 + local.minute,
            appPackage, settings.focusEndsAtMs > nowMs, minutes))
        if (verdict.intervention == InterventionType.SOFT_WARNING) {
            notifications.localReminder("A reminder from your rules", "You're offline, so MindGuard can only remind you: this matches one of your rules.")
        }
    }

    private fun endFocusIfDue(nowMs: Long, events: MutableList<ClientEvent>) {
        val ends = settings.focusEndsAtMs
        if (ends in 1..nowMs) {
            settings.focusEndsAtMs = 0
            events += ClientEvent("and:focus-end:${ends.toString(36)}", "FOCUS_ENDED", ends, null, mapOf("completed" to true))
        }
    }

    fun startFocus(minutes: Int) {
        val now = clock.nowMs()
        settings.focusEndsAtMs = now + minutes * 60_000L
        queue.enqueue(listOf(ClientEvent("and:focus-start:${now.toString(36)}", "FOCUS_STARTED", now, null, mapOf("planned_minutes" to minutes))), now)
    }

    override suspend fun sync() {
        uploader.uploadDue()
        runCatching { policyCache.replace(api.activeRules()) }
        runCatching { api.reportCapabilities(permissions.capabilities()) }
        settings.lastPolicySyncMs = clock.nowMs()
    }

    fun restartTracking() {
        tracker = SessionTracker(settings.monitoredPackages)
    }

    override suspend fun onAction(interventionId: String?, action: CommandAction, appPackage: String?) {
        val outcome = when (action) {
            CommandAction.ACCEPT, CommandAction.START_FOCUS -> OutcomeType.ACCEPTED
            CommandAction.STOP -> OutcomeType.STOPPED_SESSION
            CommandAction.CONTINUE -> OutcomeType.CONTINUED_SESSION
            CommandAction.ALLOW_ONCE, CommandAction.EMERGENCY -> OutcomeType.OVERRIDDEN
        }
        try {
            when (action) {
                CommandAction.ALLOW_ONCE -> api.createOverride(OverrideKind.ALLOW_ONCE, appPackage)
                CommandAction.EMERGENCY -> api.createOverride(OverrideKind.EMERGENCY, null)
                CommandAction.START_FOCUS -> startFocus(25)
                else -> Unit
            }
            interventionId?.let { api.feedback(it, outcome) }
        } catch (e: IOException) {
            SafeLog.w(TAG, "action recorded locally only; will not retry feedback", e)
        } catch (e: ApiException) {
            SafeLog.w(TAG, "action rejected by server: ${e.code}")
        }
    }

    private companion object {
        const val TAG = "Guardian"
        const val EVALUATION_INTERVAL_MS = 2 * 60_000L
        const val UPLOAD_INTERVAL_MS = 60_000L
    }
}

class MindGuardApplication : Application(), InterventionActionsProvider, GuardianEngineProvider {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }

    override val interventionActions: InterventionActions get() = container
    override val guardianEngine: GuardianEngine get() = container
}
