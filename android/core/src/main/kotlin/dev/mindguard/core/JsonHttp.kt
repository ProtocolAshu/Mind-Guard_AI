package dev.mindguard.core

import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.ProtocolException
import java.net.URL

class ApiException(val status: Int, val code: String, message: String) : Exception(message) {
    val retryable: Boolean get() = status == 408 || status == 429 || status >= 500
}

/**
 * JSON-over-HTTPS client on the platform HttpURLConnection (no third-party network stack). Cleartext is refused
 * except for the emulator host during development; the network security config enforces the same rule.
 */
class JsonHttp(baseUrl: String, private val dispatchers: AppDispatchers = AppDispatchers()) {
    private val base = baseUrl.trimEnd('/')

    init {
        require(base.startsWith("https://") || base.startsWith("http://10.0.2.2") || base.startsWith("http://127.0.0.1")) {
            "MindGuard only talks to its API over HTTPS"
        }
    }

    suspend fun send(method: String, path: String, body: ByteArray? = null, bearer: String? = null,
                     contentType: String = "application/json"): String = withContext(dispatchers.io) {
        require(path.startsWith("/api/") || path == "/ready") { "invalid API path" }
        val connection = URL(base + path).openConnection() as HttpURLConnection
        try {
            try {
                connection.requestMethod = method
            } catch (e: ProtocolException) {
                // Some HttpURLConnection implementations reject PATCH; the API accepts PUT for the same partial update.
                if (method != "PATCH") throw e
                connection.requestMethod = "PUT"
            }
            connection.connectTimeout = 10_000
            connection.readTimeout = 20_000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.setRequestProperty("Accept", "application/json")
            bearer?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", contentType)
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status >= 400) throw toApiException(status, text)
            text
        } catch (e: IOException) {
            throw e
        } finally {
            connection.disconnect()
        }
    }

    suspend fun json(method: String, path: String, payload: JSONObject? = null, bearer: String? = null): JSONObject {
        val text = send(method, path, payload?.toString()?.toByteArray(Charsets.UTF_8), bearer)
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }

    private fun toApiException(status: Int, text: String): ApiException = try {
        val error = JSONObject(text).optJSONObject("error")
        ApiException(status, error?.optString("code") ?: "http_$status", error?.optString("message") ?: "HTTP $status")
    } catch (e: JSONException) {
        ApiException(status, "http_$status", "HTTP $status")
    }
}
