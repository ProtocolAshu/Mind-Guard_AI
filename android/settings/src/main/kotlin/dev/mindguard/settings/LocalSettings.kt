package dev.mindguard.settings

import android.content.Context
import dev.mindguard.domain.AppCatalog

/** Non-secret device preferences. Tokens live in SecureStore, never here. */
class LocalSettings(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("mindguard_settings", Context.MODE_PRIVATE)

    var apiBaseUrl: String
        get() = prefs.getString("api_base_url", DEFAULT_API) ?: DEFAULT_API
        set(value) = prefs.edit().putString("api_base_url", value.trim()).apply()

    var guardianEnabled: Boolean
        get() = prefs.getBoolean("guardian_enabled", false)
        set(value) = prefs.edit().putBoolean("guardian_enabled", value).apply()

    var onboardingComplete: Boolean
        get() = prefs.getBoolean("onboarding_complete", false)
        set(value) = prefs.edit().putBoolean("onboarding_complete", value).apply()

    var interventionStyle: String
        get() = prefs.getString("intervention_style", "balanced") ?: "balanced"
        set(value) = prefs.edit().putString("intervention_style", value).apply()

    var monitoredPackages: Set<String>
        get() = prefs.getStringSet("monitored_packages", null)?.toSet() ?: AppCatalog.defaultMonitored
        set(value) = prefs.edit().putStringSet("monitored_packages", value.filter { AppCatalog.isValidPackage(it) && !AppCatalog.isEssential(it) }.toSet()).apply()

    var focusEndsAtMs: Long
        get() = prefs.getLong("focus_ends_at", 0L)
        set(value) = prefs.edit().putLong("focus_ends_at", value).apply()

    var lastPolicySyncMs: Long
        get() = prefs.getLong("last_policy_sync", 0L)
        set(value) = prefs.edit().putLong("last_policy_sync", value).apply()

    companion object {
        const val DEFAULT_API = "https://api.mindguard.example"
    }
}
