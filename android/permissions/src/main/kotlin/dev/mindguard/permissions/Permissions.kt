package dev.mindguard.permissions

import android.Manifest
import android.app.AppOpsManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Process
import android.provider.Settings
import dev.mindguard.domain.DeviceCapabilities

enum class GuardianPermission(val title: String, val why: String, val withoutIt: String, val required: Boolean) {
    USAGE_ACCESS("Usage access", "See which app is in the foreground and for how long. Nothing inside apps is read.",
        "MindGuard cannot notice sessions, so it never intervenes.", true),
    NOTIFICATIONS("Notifications", "Show heads-ups, check-ins and the persistent protection notice.",
        "Interventions cannot be shown; decisions are only recorded.", true),
    OVERLAY("Display over other apps", "Show pause screens and delay gates on top of an app you chose to limit.",
        "Pauses and limits are softened to notifications.", false),
    DND("Do Not Disturb access", "Silence notifications during focus sessions you start.",
        "Focus sessions still run, but notifications keep arriving.", false),
}

class PermissionChecker(context: Context) {
    private val app = context.applicationContext

    fun usageAccess(): Boolean {
        val ops = app.getSystemService(AppOpsManager::class.java) ?: return false
        return ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), app.packageName) == AppOpsManager.MODE_ALLOWED
    }

    fun notifications(): Boolean {
        val manager = app.getSystemService(NotificationManager::class.java) ?: return false
        val runtime = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            app.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        return runtime && manager.areNotificationsEnabled()
    }

    fun overlay(): Boolean = Settings.canDrawOverlays(app)

    fun dnd(): Boolean = app.getSystemService(NotificationManager::class.java)?.isNotificationPolicyAccessGranted ?: false

    fun granted(permission: GuardianPermission): Boolean = when (permission) {
        GuardianPermission.USAGE_ACCESS -> usageAccess()
        GuardianPermission.NOTIFICATIONS -> notifications()
        GuardianPermission.OVERLAY -> overlay()
        GuardianPermission.DND -> dnd()
    }

    fun capabilities() = DeviceCapabilities(usageAccess(), notifications(), overlay(), dnd())

    /** Special-access permissions are granted in system settings; only POST_NOTIFICATIONS is a runtime dialog. */
    fun settingsIntent(permission: GuardianPermission): Intent = when (permission) {
        GuardianPermission.USAGE_ACCESS -> Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
        GuardianPermission.NOTIFICATIONS -> Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, app.packageName)
        GuardianPermission.OVERLAY -> Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${app.packageName}"))
        GuardianPermission.DND -> Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
    }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    companion object {
        const val NOTIFICATION_REQUEST_CODE = 42
        val runtimeNotificationPermission: String? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) Manifest.permission.POST_NOTIFICATIONS else null
    }
}
