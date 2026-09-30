package dev.mindguard.analytics

import org.json.JSONObject

data class TodaySummary(
    val attentionScore: Int,
    val socialMinutes: Double,
    val screenMinutes: Double,
    val productiveMinutes: Double,
    val focusMinutes: Double,
    val interventions: Int,
    val successRate: Double?,
    val distractionScore: Int?,
    val doomscrollScore: Int?,
    val limitMinutes: Double,
    val topApps: List<Pair<String, Double>>,
)

object TodayParser {
    fun parse(json: JSONObject): TodaySummary {
        val apps = json.optJSONArray("top_apps")
        return TodaySummary(
            attentionScore = json.optInt("attention_score"),
            socialMinutes = json.optDouble("social_minutes", 0.0),
            screenMinutes = json.optDouble("screen_minutes", 0.0),
            productiveMinutes = json.optDouble("productive_minutes", 0.0),
            focusMinutes = json.optDouble("focus_minutes", 0.0),
            interventions = json.optInt("interventions"),
            successRate = if (json.isNull("intervention_success_rate")) null else json.optDouble("intervention_success_rate"),
            distractionScore = if (json.isNull("distraction_score")) null else json.optInt("distraction_score"),
            doomscrollScore = if (json.isNull("doomscroll_score")) null else json.optInt("doomscroll_score"),
            limitMinutes = json.optDouble("daily_limit_minutes", 120.0),
            topApps = (0 until (apps?.length() ?: 0)).map { apps!!.getJSONObject(it).let { a -> a.optString("name") to a.optDouble("minutes") } },
        )
    }

    fun minutes(value: Double): String {
        val total = Math.round(value).toInt()
        return if (total < 60) "$total min" else "${total / 60} h ${total % 60} min"
    }
}
