package dev.mindguard.data

import dev.mindguard.core.ApiException
import dev.mindguard.core.JsonHttp
import dev.mindguard.core.SecureStore
import dev.mindguard.domain.AppCategory
import dev.mindguard.domain.CachedRule
import dev.mindguard.domain.CachedTimeWindow
import dev.mindguard.domain.CommandAction
import dev.mindguard.domain.CommandName
import dev.mindguard.domain.DeviceCapabilities
import dev.mindguard.domain.DeviceCommand
import dev.mindguard.domain.InterventionType
import dev.mindguard.domain.OutcomeType
import dev.mindguard.domain.OverrideKind
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

class TokenStore(private val secure: SecureStore) {
    val access: String? get() = secure.get("access_token")
    val refresh: String? get() = secure.get("refresh_token")
    val signedIn: Boolean get() = refresh != null

    fun save(response: JSONObject) {
        secure.put("access_token", response.getString("access_token"))
        secure.put("refresh_token", response.getString("refresh_token"))
    }

    fun clear() {
        secure.put("access_token", null)
        secure.put("refresh_token", null)
    }
}

data class Evaluation(
    val runId: String,
    val decision: InterventionType,
    val interventionId: String?,
    val command: DeviceCommand,
    val explanation: String,
    val points: List<String>,
    val riskBand: String?,
    val degraded: Boolean,
)

data class UploadResult(val accepted: Int, val duplicates: Int, val rejected: Int)

data class RemoteSettings(val guardianEnabled: Boolean, val style: String, val consents: Map<String, Boolean>)

/** Typed client for the MindGuard API. Every call is scoped to the signed-in user by the backend. */
class MindGuardApi(private val http: JsonHttp, private val tokens: TokenStore) {
    private val refreshLock = Mutex()

    suspend fun login(email: String, password: String) =
        tokens.save(http.json("POST", "/api/auth/login", JSONObject().put("email", email).put("password", password)))

    suspend fun register(email: String, password: String, name: String, timezone: String) = tokens.save(
        http.json("POST", "/api/auth/register", JSONObject().put("email", email).put("password", password).put("display_name", name).put("timezone", timezone)),
    )

    suspend fun logout() {
        tokens.refresh?.let { runCatching { http.json("POST", "/api/auth/logout", JSONObject().put("refresh_token", it)) } }
        tokens.clear()
    }

    /** Single-flight refresh: concurrent 401s trigger one rotation; a rejected refresh signs the user out. */
    private suspend fun refresh(staleAccess: String?): Boolean = refreshLock.withLock {
        if (tokens.access != staleAccess) return@withLock tokens.access != null
        val refresh = tokens.refresh ?: return@withLock false
        try {
            tokens.save(http.json("POST", "/api/auth/refresh", JSONObject().put("refresh_token", refresh)))
            true
        } catch (e: ApiException) {
            if (e.status == 401) tokens.clear()
            false
        }
    }

    private suspend fun <T> authed(block: suspend (String?) -> T): T {
        val access = tokens.access
        return try {
            block(access)
        } catch (e: ApiException) {
            if (e.status == 401 && refresh(access)) block(tokens.access) else throw e
        }
    }

    suspend fun evaluate(appPackage: String, sessionMinutes: Double, capabilities: DeviceCapabilities): Evaluation = authed { bearer ->
        val body = JSONObject().put("app_package", appPackage).put("session_minutes", sessionMinutes)
            .put("device_capabilities", capabilitiesJson(capabilities))
        parseEvaluation(http.json("POST", "/api/interventions/evaluate", body, bearer))
    }

    suspend fun feedback(interventionId: String, outcome: OutcomeType) = authed { bearer ->
        http.json("POST", "/api/interventions/feedback", JSONObject().put("intervention_id", interventionId).put("outcome", outcome.wire), bearer)
    }

    suspend fun postEventsJson(batchJson: String): UploadResult = authed { bearer ->
        val res = JSONObject(http.send("POST", "/api/events", batchJson.toByteArray(Charsets.UTF_8), bearer))
        UploadResult(res.optInt("accepted"), res.optInt("duplicates"), res.optJSONArray("rejected")?.length() ?: 0)
    }

    suspend fun reportCapabilities(capabilities: DeviceCapabilities) = authed { bearer ->
        http.json("PUT", "/api/settings/device", JSONObject().put("capabilities", capabilitiesJson(capabilities)), bearer)
    }

    suspend fun settings(): RemoteSettings = authed { bearer ->
        val res = http.json("GET", "/api/settings", null, bearer)
        val prefs = res.getJSONObject("preferences")
        val consents = res.getJSONArray("consents")
        RemoteSettings(prefs.getBoolean("guardian_enabled"), prefs.getString("intervention_style"),
            (0 until consents.length()).associate { consents.getJSONObject(it).let { c -> c.getString("scope") to c.getBoolean("granted") } })
    }

    suspend fun updatePreferences(guardianEnabled: Boolean? = null, style: String? = null) = authed { bearer ->
        val body = JSONObject()
        guardianEnabled?.let { body.put("guardian_enabled", it) }
        style?.let { body.put("intervention_style", it) }
        http.json("PATCH", "/api/settings", body, bearer)
    }

    suspend fun setConsent(scope: String, granted: Boolean) = authed { bearer ->
        http.json("PUT", "/api/settings/consents/$scope", JSONObject().put("granted", granted), bearer)
    }

    suspend fun createGoal(title: String) = authed { bearer -> http.json("POST", "/api/goals", JSONObject().put("title", title), bearer) }

    /** Compiles and saves the constitution; returns the number of enforceable rules. */
    suspend fun saveConstitution(text: String): Int = authed { bearer ->
        http.json("POST", "/api/policies", JSONObject().put("text", text), bearer).getJSONObject("policy").getJSONArray("rules").length()
    }

    suspend fun createOverride(kind: OverrideKind, appPackage: String?) = authed { bearer ->
        http.json("POST", "/api/overrides", JSONObject().put("kind", kind.wire).put("app_package", appPackage ?: JSONObject.NULL), bearer)
    }

    suspend fun dailySummary(): JSONObject = authed { bearer -> http.json("GET", "/api/analytics/daily", null, bearer) }

    suspend fun activeRules(): List<CachedRule> = authed { bearer ->
        val policies = JSONArray(http.send("GET", "/api/policies", null, bearer))
        (0 until policies.length()).map { policies.getJSONObject(it) }.filter { it.optString("status") == "active" }
            .flatMap { p -> p.getJSONArray("rules").let { rules -> (0 until rules.length()).map { parseRule(rules.getJSONObject(it)) } } }
    }

    suspend fun analyzeText(text: String): JSONObject = authed { bearer ->
        http.json("POST", "/api/content/analyze", JSONObject().put("text", text.take(40_000)), bearer)
    }

    suspend fun analyzeScreenshot(image: ByteArray, mimeType: String): JSONObject = authed { bearer ->
        val boundary = "mindguard-${System.nanoTime()}"
        val head = "--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"shared\"\r\nContent-Type: $mimeType\r\n\r\n"
        val body = head.toByteArray() + image + "\r\n--$boundary--\r\n".toByteArray()
        JSONObject(http.send("POST", "/api/content/screenshot", body, bearer, "multipart/form-data; boundary=$boundary"))
    }

    companion object {
        fun capabilitiesJson(c: DeviceCapabilities): JSONObject =
            JSONObject().put("usage_access", c.usageAccess).put("notifications", c.notifications).put("overlay", c.overlay).put("dnd_access", c.dndAccess)

        fun parseCommand(json: JSONObject): DeviceCommand {
            val actions = json.optJSONArray("actions") ?: JSONArray()
            return DeviceCommand(
                command = runCatching { CommandName.valueOf(json.getString("command")) }.getOrDefault(CommandName.NONE),
                interventionId = json.optString("intervention_id").takeIf { it.isNotBlank() && it != "null" },
                appPackage = json.optString("app_package").takeIf { it.isNotBlank() && it != "null" },
                durationSeconds = json.optInt("duration_seconds"),
                title = json.optString("title"),
                body = json.optString("body"),
                actions = (0 until actions.length()).mapNotNull { runCatching { CommandAction.valueOf(actions.getString(it)) }.getOrNull() },
                expiresAtEpochMs = json.optString("expires_at").takeIf { it.isNotBlank() && it != "null" }?.let { Instant.parse(it).toEpochMilli() },
                reversible = json.optBoolean("reversible", true),
            )
        }

        fun parseEvaluation(json: JSONObject): Evaluation {
            val points = json.optJSONArray("explanation_points") ?: JSONArray()
            return Evaluation(
                runId = json.getString("run_id"),
                decision = runCatching { InterventionType.valueOf(json.getString("decision")) }.getOrDefault(InterventionType.ALLOW),
                interventionId = json.optString("intervention_id").takeIf { it.isNotBlank() && it != "null" },
                command = parseCommand(json.getJSONObject("command")),
                explanation = json.optString("explanation"),
                points = (0 until points.length()).map { points.getString(it) },
                riskBand = json.optJSONObject("risk")?.optString("band"),
                degraded = json.optBoolean("degraded"),
            )
        }

        fun parseRule(rule: JSONObject): CachedRule {
            val condition = rule.optJSONObject("condition") ?: JSONObject()
            val window = condition.optJSONObject("time_window")
            fun minutes(hhmm: String) = hhmm.split(":").let { it[0].toInt() * 60 + it[1].toInt() }
            fun strings(key: String) = condition.optJSONArray(key)?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
            return CachedRule(
                ruleId = rule.getString("rule_id"),
                effect = rule.getString("effect"),
                appCategories = strings("app_categories").map { AppCategory.fromWire(it) }.toSet(),
                appPackages = strings("app_packages").toSet(),
                timeWindow = window?.let { w ->
                    val days = w.optJSONArray("days")?.let { a -> (0 until a.length()).map { a.getInt(it) }.toSet() } ?: (0..6).toSet()
                    CachedTimeWindow(minutes(w.getString("start")), minutes(w.getString("end")), days)
                },
                focusSession = if (condition.isNull("focus_session") || !condition.has("focus_session")) null else condition.getBoolean("focus_session"),
                hasContentCondition = strings("content_categories").isNotEmpty(),
            )
        }
    }
}
