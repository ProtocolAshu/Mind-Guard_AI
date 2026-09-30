package dev.mindguard.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import dev.mindguard.app.ui.Ui
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Receives captions, links or screenshots the user explicitly shares to MindGuard (the only content input on Android). */
class ShareActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val ui = Ui(this)
        val container = (application as MindGuardApplication).container
        with(ui) {
            val status = label("Checking what you shared…", 16f, ink)
            setContentView(screen {
                title("Shared content")
                addView(status)
                note("MindGuard classifies it against your goals. Nothing you share is stored.")
                button("Done", primary = false) { finish() }
            })
            scope.launch {
                status.text = try {
                    val verdict = when {
                        intent?.action != Intent.ACTION_SEND -> null
                        intent.type?.startsWith("text/") == true -> intent.getStringExtra(Intent.EXTRA_TEXT)?.let { container.content.analyzeText(it) }
                        intent.type?.startsWith("image/") == true -> {
                            @Suppress("DEPRECATION")
                            val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
                            val bytes = uri?.let { withContext(Dispatchers.IO) { contentResolver.openInputStream(it)?.use { s -> s.readBytes() } } }
                            bytes?.let { container.content.analyzeImage(it, intent.type ?: "image/jpeg") }
                        }
                        else -> null
                    }
                    when {
                        verdict == null -> "Nothing MindGuard can analyse was shared."
                        verdict.injectionDetected -> "This contains instructions aimed at AI systems. They were ignored and the content is treated with caution."
                        else -> "Looks like ${verdict.category.replace('_', ' ')} (${(verdict.confidence * 100).toInt()}% confident), ${verdict.goalRelevance.replace('_', ' ')} for your goals."
                    }
                } catch (e: Exception) {
                    "Could not analyse this: ${e.message ?: "check that screenshot or content analysis is enabled in Privacy"}"
                }
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
