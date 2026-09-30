package dev.mindguard.core

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import java.security.GeneralSecurityException
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Injectable dispatchers so coroutine code stays testable. */
data class AppDispatchers(
    val io: CoroutineDispatcher = Dispatchers.IO,
    val default: CoroutineDispatcher = Dispatchers.Default,
    val main: CoroutineDispatcher = Dispatchers.Main,
)

fun interface Clock {
    fun nowMs(): Long

    companion object {
        val SYSTEM = Clock { System.currentTimeMillis() }
    }
}

/** Logging that never prints tokens, passwords or content. */
object SafeLog {
    private val secrets = Regex("(Bearer\\s+[A-Za-z0-9._~+/=-]+|\"(access_token|refresh_token|password)\"\\s*:\\s*\"[^\"]*\")")

    fun redact(message: String): String = secrets.replace(message) { m -> if (m.value.startsWith("Bearer")) "Bearer ***" else "\"${m.groupValues[2]}\":\"***\"" }

    fun i(tag: String, message: String) = Log.i(tag, redact(message))
    fun w(tag: String, message: String, error: Throwable? = null) = Log.w(tag, redact(message) + (error?.let { " (${it.javaClass.simpleName})" } ?: ""))
}

/**
 * Small encrypted key-value store: AES-256-GCM with a non-exportable key in the Android Keystore; only ciphertext
 * reaches SharedPreferences. If the key is lost (e.g. device restored from backup), values are discarded, never
 * decrypted with a different key.
 */
class SecureStore(context: Context, fileName: String = "mindguard_secure") {
    private val prefs = context.applicationContext.getSharedPreferences(fileName, Context.MODE_PRIVATE)

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keyStore.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    @Synchronized
    fun put(name: String, value: String?) {
        if (value == null) {
            prefs.edit().remove(name).apply()
            return
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val payload = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        prefs.edit().putString(name, Base64.encodeToString(payload, Base64.NO_WRAP)).apply()
    }

    @Synchronized
    fun get(name: String): String? {
        val stored = prefs.getString(name, null) ?: return null
        return try {
            val bytes = Base64.decode(stored, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, IV_BYTES))
            String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES), Charsets.UTF_8)
        } catch (e: GeneralSecurityException) {
            SafeLog.w("SecureStore", "discarding undecryptable value", e)
            prefs.edit().remove(name).apply()
            null
        } catch (e: IllegalArgumentException) {
            prefs.edit().remove(name).apply()
            null
        }
    }

    fun clear() = prefs.edit().clear().apply()

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "mindguard_tokens_v1"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}
