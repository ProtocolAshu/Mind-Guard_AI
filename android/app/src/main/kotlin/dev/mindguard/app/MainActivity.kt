package dev.mindguard.app

import android.app.Activity
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import dev.mindguard.analytics.TodayParser
import dev.mindguard.app.ui.Ui
import dev.mindguard.core.ApiException
import dev.mindguard.domain.OverrideKind
import dev.mindguard.monitoring.GuardianService
import dev.mindguard.monitoring.SyncJobService
import dev.mindguard.permissions.GuardianPermission
import dev.mindguard.permissions.PermissionChecker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.io.IOException
import java.util.TimeZone

/**
 * Single-activity host. Coroutines are scoped to the activity (cancelled in onDestroy) and the status collector to the
 * started state (cancelled in onStop), so no work outlives the UI that needs it.
 */
class MainActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var statusJob: Job? = null
    private lateinit var ui: Ui
    private val container get() = (application as MindGuardApplication).container

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ui = Ui(this)
        route()
    }

    override fun onResume() {
        super.onResume()
        if (container.tokens.signedIn && container.settings.onboardingComplete) showToday() // permissions may have changed in Settings
    }

    override fun onStop() {
        statusJob?.cancel()
        super.onStop()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun route(): Unit = when {
        !container.tokens.signedIn -> showSignIn()
        !container.settings.onboardingComplete -> showOnboarding(0)
        else -> showToday()
    }

    private fun show(view: View): Unit = setContentView(view)

    private fun failure(e: Exception): String = when (e) {
        is ApiException -> e.message ?: "The request was rejected."
        is IOException -> "MindGuard could not reach its server. Check your connection."
        else -> "Something went wrong."
    }

    private fun showSignIn(): Unit = with(ui) {
        show(screen {
            title("MindGuard")
            body("Sign in to protect your attention on this phone.")
            val email = input("Email")
            val password = input("Password", password = true)
            val error = label("", 14f, alarm).also { addView(it) }
            fun submit(register: Boolean): Job = scope.launch {
                try {
                    if (register) container.api.register(email.text.toString(), password.text.toString(), "", TimeZone.getDefault().id)
                    else container.api.login(email.text.toString(), password.text.toString())
                    route()
                } catch (e: Exception) {
                    error.text = failure(e)
                }
            }
            button("Sign in") { submit(register = false) }
            button("Create account", primary = false) { submit(register = true) }
        })
    }

    /** The eight onboarding steps of the specification, in order. */
    private fun showOnboarding(step: Int): Unit = with(ui) {
        show(screen {
            note("Step ${step + 1} of 8")
            when (step) {
                0 -> { title("Your attention, protected"); body("MindGuard notices when social media pulls you away from your goals and steps in the way you choose.") }
                1 -> { title("What MindGuard collects"); body("Which of your chosen apps is open and for how long, focus sessions, and how you respond to interventions. Captions or screenshots only when you share them.") }
                2 -> { title("What MindGuard never collects"); body("No private messages, no notification content, no screen recording, no accessibility snooping, nothing about other people. Calls, messages, maps and payments are never restricted.") }
                3 -> {
                    title("Permissions")
                    GuardianPermission.entries.forEach { p ->
                        val granted = container.permissions.granted(p)
                        body("${p.title}${if (granted) " — granted" else if (p.required) " — needed" else " — optional"}")
                        note("${p.why} Without it: ${p.withoutIt}")
                        if (!granted) button("Open settings for ${p.title}", primary = false) {
                            if (p == GuardianPermission.NOTIFICATIONS && PermissionChecker.runtimeNotificationPermission != null) {
                                requestPermissions(arrayOf(PermissionChecker.runtimeNotificationPermission), PermissionChecker.NOTIFICATION_REQUEST_CODE)
                            } else {
                                startActivity(container.permissions.settingsIntent(p))
                            }
                        }
                    }
                }
                4 -> {
                    title("Your goal")
                    val goal = input("I am preparing for placements")
                    button("Save goal") { scope.launch { runCatching { if (goal.text.isNotBlank()) container.api.createGoal(goal.text.toString()) }; showOnboarding(5) } }
                }
                5 -> {
                    title("Your social-media rules")
                    val rules = input("Don't allow short videos during study sessions. After 11 PM, block entertainment.", multiline = true)
                    val result = label("", 14f, soft).also { addView(it) }
                    button("Save rules") {
                        scope.launch {
                            try {
                                val count = container.api.saveConstitution(rules.text.toString())
                                result.text = "$count rules saved."
                                showOnboarding(6)
                            } catch (e: Exception) {
                                result.text = failure(e)
                            }
                        }
                    }
                }
                6 -> {
                    title("How firm should MindGuard be?")
                    listOf("gentle" to "Gentle: heads-ups and check-ins", "balanced" to "Balanced: nudges first, then short pauses", "strict" to "Strict: pauses apps when your rules say so").forEach { (value, text) ->
                        button(text, primary = container.settings.interventionStyle == value) {
                            container.settings.interventionStyle = value
                            scope.launch { runCatching { container.api.updatePreferences(style = value) }; showOnboarding(7) }
                        }
                    }
                }
                else -> {
                    title("Turn on the guardian")
                    body("A persistent notification shows whenever protection is on. You can stop it at any time from that notification.")
                    button("Enable guardian") {
                        scope.launch {
                            runCatching { container.api.setConsent("usage_monitoring", true); container.api.updatePreferences(guardianEnabled = true) }
                            container.settings.guardianEnabled = true
                            container.settings.onboardingComplete = true
                            GuardianService.start(this@MainActivity)
                            SyncJobService.schedule(this@MainActivity)
                            showToday()
                        }
                    }
                }
            }
            if (step in 0..3) button("Continue") { showOnboarding(step + 1) }
        })
    }

    private fun showToday(): Unit = with(ui) {
        val statusLine = TextView(this@MainActivity)
        val summary = LinearLayout(this@MainActivity).apply { orientation = LinearLayout.VERTICAL }
        show(screen {
            title("Today")
            addView(statusLine.apply { setTextColor(soft) })
            addView(summary)
            button("Start a 25-minute focus session") { container.startFocus(25) }
            button(if (container.settings.guardianEnabled) "Pause guardian" else "Turn guardian on", primary = false) {
                scope.launch {
                    val enable = !container.settings.guardianEnabled
                    container.settings.guardianEnabled = enable
                    if (enable) GuardianService.start(this@MainActivity) else GuardianService.stop(this@MainActivity)
                    runCatching { if (enable) container.api.updatePreferences(guardianEnabled = true) else container.api.createOverride(OverrideKind.PAUSE, null) }
                    showToday()
                }
            }
            button("Emergency override", primary = false) { scope.launch { runCatching { container.api.createOverride(OverrideKind.EMERGENCY, null) } } }
            button("Sign out", primary = false) {
                scope.launch {
                    GuardianService.stop(this@MainActivity)
                    container.settings.guardianEnabled = false
                    container.api.logout()
                    route()
                }
            }
        })
        statusJob?.cancel()
        statusJob = scope.launch { container.status.collect { statusLine.text = it.message } }
        scope.launch {
            try {
                val today = TodayParser.parse(container.api.dailySummary())
                summary.removeAllViews()
                summary.addView(label("${today.attentionScore}/100", 56f, ink, bold = true))
                summary.addView(label("Attention score", 14f, soft))
                summary.addView(label("${TodayParser.minutes(today.socialMinutes)} of social media (limit ${TodayParser.minutes(today.limitMinutes)})", 16f, ink))
                summary.addView(label("${today.interventions} interventions · distraction ${today.distractionScore ?: "—"}", 14f, soft))
            } catch (e: Exception) {
                summary.removeAllViews()
                summary.addView(label(failure(e), 14f, alarm))
            }
        }
    }
}
