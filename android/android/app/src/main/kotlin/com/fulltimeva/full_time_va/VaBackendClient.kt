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
    private const val KEY_PENDING_EVENTS = "pending_communication_events_v1"
    private const val KEY_LAST_REQUEST_ERROR = "last_backend_error"
    private const val MAX_PENDING_EVENTS = 200

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

    fun lastRequestError(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_LAST_REQUEST_ERROR, "").orEmpty()

    fun pendingCommunicationEventCount(context: Context): Int = loadPendingCommunicationEvents(context).length()

    fun queueCommunicationEvent(context: Context, event: JSONObject): Boolean {
        val pending = loadPendingCommunicationEvents(context)
        val externalId = event.optString("external_id")
        for (index in 0 until pending.length()) {
            val existing = pending.optJSONObject(index) ?: continue
            if (externalId.isNotBlank() && existing.optString("external_id") == externalId) return true
        }
        pending.put(JSONObject(event.toString()))
        while (pending.length() > MAX_PENDING_EVENTS) pending.remove(0)
        return persistPendingCommunicationEvents(context, pending)
    }

    fun removeQueuedCommunicationEvent(context: Context, externalId: String): Boolean {
        if (externalId.isBlank()) return false
        val pending = loadPendingCommunicationEvents(context)
        val retained = JSONArray()
        var removed = false
        for (index in 0 until pending.length()) {
            val event = pending.optJSONObject(index) ?: continue
            if (event.optString("external_id") == externalId) {
                removed = true
            } else {
                retained.put(event)
            }
        }
        return if (removed) persistPendingCommunicationEvents(context, retained) else true
    }

    fun postBatchChunked(context: Context, events: JSONArray, chunkSize: Int = 25): JSONObject {
        val size = chunkSize.coerceIn(1, 100)
        var processed = 0
        var duplicates = 0
        var failed = 0
        var failureDetail = ""
        var submitted = 0
        var chunks = 0
        var start = 0
        while (start < events.length()) {
            val chunk = JSONArray()
            val end = minOf(start + size, events.length())
            for (index in start until end) chunk.put(events.get(index))
            val response = postBatch(context, chunk)
                ?: return JSONObject()
                    .put("success", false)
                    .put("submitted", submitted)
                    .put("processed", processed)
                    .put("duplicates", duplicates)
                    .put("chunks", chunks)
                    .put("error", lastRequestError(context).ifBlank { "Backend upload failed" })
            submitted += chunk.length()
            processed += response.optInt("processed", 0)
            duplicates += response.optInt("duplicates", 0)
            val chunkFailed = response.optInt("failed", 0)
            failed += chunkFailed
            if (chunkFailed > 0 && failureDetail.isBlank()) {
                val failures = response.optJSONArray("failures")
                val first = failures?.optJSONObject(0)
                failureDetail = first?.optString("error").orEmpty().ifBlank {
                    "$chunkFailed communication record${if (chunkFailed == 1) "" else "s"} failed on the VA backend"
                }
            }
            chunks += 1
            start = end
        }
        return JSONObject()
            .put("success", failed == 0)
            .put("submitted", submitted)
            .put("processed", processed)
            .put("duplicates", duplicates)
            .put("failed", failed)
            .put("chunks", chunks)
            .put("error", if (failed > 0) failureDetail else "")
    }

    fun flushPendingCommunicationEvents(context: Context): JSONObject {
        val pending = loadPendingCommunicationEvents(context)
        if (pending.length() == 0) {
            return JSONObject()
                .put("success", true)
                .put("submitted", 0)
                .put("processed", 0)
                .put("duplicates", 0)
        }
        val result = postBatchChunked(context, pending)
        if (result.optBoolean("success", false)) persistPendingCommunicationEvents(context, JSONArray())
        return result
    }

    private fun loadPendingCommunicationEvents(context: Context): JSONArray {
        val encoded = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_PENDING_EVENTS, null)
            ?: return JSONArray()
        val decoded = decrypt(encoded) ?: return JSONArray()
        return try { JSONArray(decoded) } catch (_: Exception) { JSONArray() }
    }

    private fun persistPendingCommunicationEvents(context: Context, events: JSONArray): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (events.length() == 0) {
            prefs.edit().remove(KEY_PENDING_EVENTS).apply()
            return true
        }
        val encrypted = encrypt(events.toString()) ?: return false
        prefs.edit().putString(KEY_PENDING_EVENTS, encrypted).apply()
        return true
    }

    fun postActionResult(
        context: Context,
        actionId: Long,
        status: String,
        failureReason: String = "",
        externalRef: String = "",
        details: JSONObject = JSONObject(),
    ): Boolean = request(
        context,
        "POST",
        "/api/communications/actions/$actionId/result",
        JSONObject()
            .put("status", status)
            .put("failure_reason", failureReason.take(1900))
            .put("external_ref", externalRef.take(1000))
            .put("details", details),
    ) != null

    fun storeActionEvidence(
        context: Context,
        actionId: Long,
        status: String,
        externalRef: String,
        details: JSONObject = JSONObject(),
    ) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(
                "action_evidence_$actionId",
                JSONObject()
                    .put("status", status)
                    .put("external_ref", externalRef)
                    .put("details", details)
                    .toString(),
            )
            .apply()
    }

    fun repostStoredActionEvidence(context: Context, actionId: Long): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val raw = prefs.getString("action_evidence_$actionId", null) ?: return false
        val evidence = try { JSONObject(raw) } catch (_: Exception) { return false }
        val posted = postActionResult(
            context,
            actionId,
            evidence.optString("status"),
            externalRef = evidence.optString("external_ref"),
            details = evidence.optJSONObject("details") ?: JSONObject(),
        )
        if (posted) prefs.edit().remove("action_evidence_$actionId").apply()
        return true
    }

    fun fetchPendingCommunicationActions(context: Context): JSONArray {
        val response = request(context, "GET", "/api/communications/actions/pending?limit=100", null)
            ?: return JSONArray()
        return response.optJSONArray("actions") ?: JSONArray()
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
        val base = serverUrl(context)?.trimEnd('/')
        val token = deviceToken(context)
        if (base.isNullOrBlank() || token.isNullOrBlank()) {
            saveLastRequestError(context, "Native VA backend link is missing")
            return null
        }
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
            if (code !in 200..299) {
                saveLastRequestError(context, "HTTP $code ${text.take(700)}".trim())
                null
            } else if (text.isBlank()) {
                saveLastRequestError(context, "Backend returned an empty response for $path")
                null
            } else {
                saveLastRequestError(context, "")
                JSONObject(text)
            }
        } catch (exc: Exception) {
            saveLastRequestError(context, "${exc.javaClass.simpleName}: ${exc.message ?: exc.toString()}".take(800))
            null
        } finally {
            connection?.disconnect()
        }
    }

    private fun saveLastRequestError(context: Context, value: String) {
        val editor = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
        if (value.isBlank()) editor.remove(KEY_LAST_REQUEST_ERROR) else editor.putString(KEY_LAST_REQUEST_ERROR, value)
        editor.apply()
    }
}
