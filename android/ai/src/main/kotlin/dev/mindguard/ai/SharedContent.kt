package dev.mindguard.ai

import dev.mindguard.data.MindGuardApi
import org.json.JSONObject

data class ContentVerdict(val category: String, val confidence: Double, val goalRelevance: String, val injectionDetected: Boolean)

/**
 * Multimodal input on Android is user-initiated only: captions, links or screenshots the user explicitly shares to
 * MindGuard through the system share sheet. Classification runs on the backend (local model first, cloud AI only if
 * consented); screenshots are re-encoded and stripped of metadata server-side and never stored.
 */
class SharedContentAnalyzer(private val api: MindGuardApi) {
    suspend fun analyzeText(text: String): ContentVerdict = parse(api.analyzeText(text))

    suspend fun analyzeImage(bytes: ByteArray, mimeType: String): ContentVerdict {
        require(mimeType in setOf("image/png", "image/jpeg", "image/webp")) { "unsupported image type" }
        require(bytes.size <= 5_000_000) { "image too large" }
        return parse(api.analyzeScreenshot(bytes, mimeType))
    }

    private fun parse(json: JSONObject) = ContentVerdict(
        category = json.optString("category", "unknown"),
        confidence = json.optDouble("confidence", 0.0),
        goalRelevance = json.optString("goal_relevance", "neutral"),
        injectionDetected = json.optBoolean("injection_detected"),
    )
}
