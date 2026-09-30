package dev.mindguard.intervention

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import dev.mindguard.domain.CommandAction
import dev.mindguard.domain.CommandName
import dev.mindguard.domain.DeviceCommand
import dev.mindguard.permissions.PermissionChecker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

sealed interface ExecutionResult {
    data object Shown : ExecutionResult
    data class Downgraded(val reason: String) : ExecutionResult
    data class Failed(val reason: String) : ExecutionResult
}

/** Executes guardrail-authorized commands with platform-supported mechanisms only (no force-stop, no hidden UI). */
interface InterventionController {
    suspend fun execute(command: DeviceCommand): ExecutionResult
    fun dismiss(interventionId: String)
}

/** Implemented by the Application so broadcast receivers and overlays can reach the action handler. */
interface InterventionActions {
    suspend fun onAction(interventionId: String?, action: CommandAction, appPackage: String?)
}

interface InterventionActionsProvider {
    val interventionActions: InterventionActions
}

object Channels {
    const val GUARDIAN = "guardian_status"
    const val NUDGES = "nudges"

    fun ensure(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(NotificationChannel(GUARDIAN, "Protection status", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Always visible while MindGuard monitors the apps you chose."
        })
        manager.createNotificationChannel(NotificationChannel(NUDGES, "Interventions", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Heads-ups, check-ins and pauses you configured."
        })
    }
}

private val restrictive = setOf(CommandName.DELAY_GATE, CommandName.LIMIT_ACCESS, CommandName.BLOCK_APP)

private fun label(action: CommandAction): String = when (action) {
    CommandAction.ACCEPT -> "Okay"
    CommandAction.CONTINUE -> "Continue"
    CommandAction.STOP -> "Stop for now"
    CommandAction.ALLOW_ONCE -> "Allow once"
    CommandAction.EMERGENCY -> "Emergency"
    CommandAction.START_FOCUS -> "Start focus"
}

class NotificationController(private val context: Context) : InterventionController {
    private val manager = context.getSystemService(NotificationManager::class.java)

    override suspend fun execute(command: DeviceCommand): ExecutionResult {
        val id = command.interventionId ?: return ExecutionResult.Failed("missing intervention id")
        Channels.ensure(context)
        val builder = Notification.Builder(context, Channels.NUDGES)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(command.title)
            .setContentText(command.body)
            .setStyle(Notification.BigTextStyle().bigText(command.body))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
        command.actions.take(3).forEachIndexed { index, action ->
            val intent = Intent(context, InterventionActionReceiver::class.java)
                .setAction(ACTION)
                .putExtra(EXTRA_ID, id)
                .putExtra(EXTRA_ACTION, action.name)
                .putExtra(EXTRA_APP, command.appPackage)
            val pending = PendingIntent.getBroadcast(context, id.hashCode() * 31 + index, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            builder.addAction(Notification.Action.Builder(null, label(action), pending).build())
        }
        return try {
            manager?.notify(id.hashCode(), builder.build())
            ExecutionResult.Shown
        } catch (e: SecurityException) {
            ExecutionResult.Failed("notifications not permitted")
        }
    }

    /** Offline reminder: shown locally, not tied to a server intervention, and never restrictive. */
    fun localReminder(title: String, body: String) {
        Channels.ensure(context)
        runCatching {
            manager?.notify(REMINDER_ID, Notification.Builder(context, Channels.NUDGES).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title).setContentText(body).setAutoCancel(true).build())
        }
    }

    override fun dismiss(interventionId: String) {
        manager?.cancel(interventionId.hashCode())
    }

    companion object {
        const val ACTION = "dev.mindguard.intervention.ACTION"
        const val EXTRA_ID = "intervention_id"
        const val EXTRA_ACTION = "action"
        const val EXTRA_APP = "app_package"
        private const val REMINDER_ID = 7_001
    }
}

/**
 * Full-screen gate drawn with TYPE_APPLICATION_OVERLAY (requires "Display over other apps").
 * It never traps the user: every restriction shows Allow once and Emergency, and going home is always possible.
 */
class OverlayController(private val context: Context, private val actions: InterventionActions) : InterventionController {
    private val windowManager = context.getSystemService(WindowManager::class.java)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var shown: Pair<String, View>? = null
    private var countdown: Job? = null

    override suspend fun execute(command: DeviceCommand): ExecutionResult = withContext(Dispatchers.Main) {
        val id = command.interventionId ?: return@withContext ExecutionResult.Failed("missing intervention id")
        shown?.let { removeView() }
        val root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(64, 64, 64, 64)
            setBackgroundColor(0xF20E1620.toInt())
        }
        val title = TextView(context).apply { text = command.title; textSize = 24f; setTextColor(0xFFE3E9F0.toInt()) }
        val body = TextView(context).apply { text = command.body; textSize = 16f; setTextColor(0xFFA3B0BE.toInt()); setPadding(0, 24, 0, 32) }
        root.addView(title)
        root.addView(body)
        val buttons = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        command.actions.forEach { action ->
            val button = Button(context).apply {
                text = label(action)
                isEnabled = !(command.command == CommandName.DELAY_GATE && action == CommandAction.CONTINUE)
                setOnClickListener { handle(id, action, command) }
            }
            buttons.addView(button)
            if (command.command == CommandName.DELAY_GATE && action == CommandAction.CONTINUE) {
                countdown = scope.launch {
                    for (remaining in command.durationSeconds downTo 1) {
                        button.text = "Continue in ${remaining}s"
                        delay(1_000)
                    }
                    button.text = label(action)
                    button.isEnabled = true
                }
            }
        }
        if (command.command in restrictive) {
            buttons.addView(Button(context).apply { text = "Go to home screen"; setOnClickListener { handle(id, CommandAction.ACCEPT, command) } })
        }
        root.addView(buttons)
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN, PixelFormat.TRANSLUCENT,
        )
        try {
            windowManager.addView(root, params)
            shown = id to root
            ExecutionResult.Shown
        } catch (e: RuntimeException) {
            ExecutionResult.Failed("overlay not permitted")
        }
    }

    private fun handle(id: String, action: CommandAction, command: DeviceCommand) {
        removeView()
        if (action == CommandAction.ACCEPT && command.command in restrictive) {
            context.startActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
        scope.launch(Dispatchers.Default) { actions.onAction(id, action, command.appPackage) }
    }

    private fun removeView() {
        countdown?.cancel()
        shown?.let { (_, view) -> runCatching { windowManager.removeView(view) } }
        shown = null
    }

    override fun dismiss(interventionId: String) {
        if (shown?.first == interventionId) scope.launch { removeView() }
    }
}

/** Mirrors the backend capability guardrail: restrictions need the overlay; otherwise fall back to a notification. */
class CompositeController(
    private val permissions: PermissionChecker,
    private val overlay: OverlayController,
    private val notifications: NotificationController,
) : InterventionController {
    override suspend fun execute(command: DeviceCommand): ExecutionResult {
        if (command.command in restrictive && permissions.overlay()) return overlay.execute(command)
        if (!permissions.notifications()) return ExecutionResult.Failed("no permission to show interventions")
        val result = notifications.execute(command)
        return if (command.command in restrictive && result == ExecutionResult.Shown) ExecutionResult.Downgraded("overlay not granted") else result
    }

    override fun dismiss(interventionId: String) {
        overlay.dismiss(interventionId)
        notifications.dismiss(interventionId)
    }
}

class InterventionActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != NotificationController.ACTION) return
        val id = intent.getStringExtra(NotificationController.EXTRA_ID)
        val action = runCatching { CommandAction.valueOf(intent.getStringExtra(NotificationController.EXTRA_ACTION) ?: "") }.getOrNull() ?: return
        val provider = context.applicationContext as? InterventionActionsProvider ?: return
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.Default).launch {
            try {
                provider.interventionActions.onAction(id, action, intent.getStringExtra(NotificationController.EXTRA_APP))
                id?.let { context.getSystemService(NotificationManager::class.java)?.cancel(it.hashCode()) }
            } finally {
                pending.finish()
            }
        }
    }
}
