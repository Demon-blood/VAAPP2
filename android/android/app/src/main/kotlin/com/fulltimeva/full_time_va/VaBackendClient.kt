package com.fulltimeva.full_time_va

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object VaBackendClient {
    private const val PREFS = "va_native"
    private const val KEY_ALIAS = "full_time_va_native_credentials_v1"
    private const val CHANNEL_ID = "va_communications_attention"

    fun saveCredentials(context: Context, serverUrl: String?, deviceToken: String?) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val editor = prefs.edit().remove("server_url").remove("device_token")
        if (serverUrl.isNullOrBlank() || deviceToken.isNullOrBlank()) {
            editor.remove("secure_server_url").remove("secure_device_token").apply()
            return
        }
        // The communications services are only useful on modern Android. Refuse to
        // persist a bearer token unencrypted on pre-Marshmallow devices.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            editor.remove("secure_server_url").remove("secure_device_token").apply()
            return
        }
        val encryptedUrl = encrypt(serverUrl.trimEnd('/')) ?: return
        val encryptedToken = encrypt(deviceToken) ?: return
        editor.putString("secure_server_url", encryptedUrl)
            .putString("secure_device_token", encryptedToken)
            .apply()
    }

    fun hasCredentials(context: Context): Boolean =
        !serverUrl(context).isNullOrBlank() && !deviceToken(context).isNullOrBlank()

    fun isoTime(epochMillis: Long): String {
        val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        format.timeZone = TimeZone.getTimeZone("UTC")
        return format.format(Date(epochMillis))
    }

    fun fingerprint(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8))
        return digest.joinToString("") { "%02x".format(it) }
    }

    fun postEvent(context: Context, event: JSONObject): JSONObject? =
        request(context, "POST", "/api/communications/ingest", event)

    fun postBatch(context: Context, events: JSONArray): JSONObject? =
        request(context, "POST", "/api/communications/batch", JSONObject().put("events", events))

    fun postActionResult(context: Context, actionId: Long, status: String, failureReason: String = "") {
        request(
            context,
            "POST",
            "/api/communications/actions/$actionId/result",
            JSONObject().put("status", status).put("failure_reason", failureReason.take(1900)),
        )
    }

    fun fetchCallPolicy(context: Context): JSONObject? =
        request(context, "GET", "/api/communications/device-policy", null)

    fun storeCallPolicy(context: Context, policy: JSONObject) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.edit()
            .putString("blocked_numbers", policy.optJSONArray("blocked_numbers")?.toString() ?: "[]")
            .putString("silenced_numbers", policy.optJSONArray("silenced_numbers")?.toString() ?: "[]")
            .putString("vip_numbers", policy.optJSONArray("vip_numbers")?.toString() ?: "[]")
            .putBoolean("silence_unknown", policy.optBoolean("silence_unknown", false))
            .apply()
    }

    fun numberSet(context: Context, key: String): Set<String> {
        val raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(key, "[]") ?: "[]"
        return try {
            val array = JSONArray(raw)
            buildSet {
                for (i in 0 until array.length()) add(normalizeNumber(array.optString(i)))
            }
        } catch (_: Exception) {
            emptySet()
        }
    }

    fun silenceUnknown(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("silence_unknown", false)

    fun markActionExecuted(context: Context, actionId: Long): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val key = "action_done_$actionId"
        if (prefs.getBoolean(key, false)) return false
        prefs.edit().putBoolean(key, true).apply()
        return true
    }

    fun clearActionExecuted(context: Context, actionId: Long) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove("action_done_$actionId").apply()
    }

    fun normalizeNumber(value: String?): String =
        (value ?: "").filter { it.isDigit() || it == '+' }.trim()

    fun notifyAttention(context: Context, title: String, text: String, notificationKey: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "VA communications needing attention",
                    NotificationManager.IMPORTANCE_HIGH,
                ),
            )
        }
        val launch = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pending = PendingIntent.getActivity(
            context,
            fingerprint(notificationKey).take(7).toInt(16),
            launch,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            android.app.Notification.Builder(context, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            android.app.Notification.Builder(context)
        }
        @Suppress("DEPRECATION")
        val notification = builder
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle(title.take(120))
            .setContentText(text.take(240))
            .setStyle(android.app.Notification.BigTextStyle().bigText(text.take(1000)))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()
        manager.notify(fingerprint(notificationKey).take(7).toInt(16), notification)
    }

    private fun serverUrl(context: Context): String? = decryptPreference(context, "secure_server_url")
    private fun deviceToken(context: Context): String? = decryptPreference(context, "secure_device_token")

    private fun decryptPreference(context: Context, key: String): String? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return null
        val encoded = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(key, null) ?: return null
        return decrypt(encoded)
    }

    private fun getOrCreateKey(): SecretKey? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return null
        return try {
            val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            (keyStore.getKey(KEY_ALIAS, null) as? SecretKey) ?: run {
                val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
                generator.init(
                    KeyGenParameterSpec.Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .build(),
                )
                generator.generateKey()
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun encrypt(value: String): String? {
        val key = getOrCreateKey() ?: return null
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key)
            val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
            val ciphertext = Base64.encodeToString(
                cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8)),
                Base64.NO_WRAP,
            )
            "$iv:$ciphertext"
        } catch (_: Exception) {
            null
        }
    }

    private fun decrypt(value: String): String? {
        val key = getOrCreateKey() ?: return null
        val parts = value.split(':', limit = 2)
        if (parts.size != 2) return null
        return try {
            val iv = Base64.decode(parts[0], Base64.NO_WRAP)
            val ciphertext = Base64.decode(parts[1], Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
            String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8)
        } catch (_: Exception) {
            null
        }
    }

    private fun request(
        context: Context,
        method: String,
        path: String,
        body: JSONObject?,
    ): JSONObject? {
        val base = serverUrl(context)?.trimEnd('/') ?: return null
        val token = deviceToken(context) ?: return null
        if (base.isBlank() || token.isBlank()) return null
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(base + path).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = 12_000
                readTimeout = 25_000
                setRequestProperty("Authorization", "Bearer $token")
                setRequestProperty("Accept", "application/json")
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    outputStream.use { it.write(body.toString().toByteArray(StandardCharsets.UTF_8)) }
                }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = if (stream == null) "" else BufferedReader(InputStreamReader(stream, StandardCharsets.UTF_8)).use { it.readText() }
            if (code !in 200..299 || text.isBlank()) null else JSONObject(text)
        } catch (_: Exception) {
            null
        } finally {
            connection?.disconnect()
        }
    }
}
