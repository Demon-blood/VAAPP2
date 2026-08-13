package com.fulltimeva.full_time_va

import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.telephony.SmsManager

object VaSms {
    private const val PREFS = "va_sms_delivery"

    fun send(context: Context, target: String, text: String, actionId: Long? = null) {
        require(target.isNotBlank()) { "SMS target is required" }
        require(text.isNotBlank()) { "SMS body is required" }
        @Suppress("DEPRECATION")
        val manager = SmsManager.getDefault()
        val parts = manager.divideMessage(text)

        if (actionId == null) {
            if (parts.size <= 1) {
                manager.sendTextMessage(target, null, text, null, null)
            } else {
                manager.sendMultipartTextMessage(target, null, ArrayList(parts), null, null)
            }
            persistSentForDefaultSmsApp(context, target, text)
            return
        }

        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.edit()
            .putString("target_$actionId", target)
            .putString("body_$actionId", text)
            .putInt("parts_$actionId", parts.size.coerceAtLeast(1))
            .remove("sent_reported_$actionId")
            .remove("delivered_reported_$actionId")
            .apply()

        if (parts.size <= 1) {
            manager.sendTextMessage(
                target,
                null,
                text,
                statusIntent(context, actionId, 0, 1, "sent"),
                statusIntent(context, actionId, 0, 1, "delivered"),
            )
        } else {
            val sent = ArrayList<PendingIntent>(parts.size)
            val delivered = ArrayList<PendingIntent>(parts.size)
            parts.indices.forEach { index ->
                sent.add(statusIntent(context, actionId, index, parts.size, "sent"))
                delivered.add(statusIntent(context, actionId, index, parts.size, "delivered"))
            }
            manager.sendMultipartTextMessage(target, null, ArrayList(parts), sent, delivered)
        }
    }

    fun repostEvidenceIfAvailable(context: Context, actionId: Long): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val partCount = prefs.getInt("parts_$actionId", 0)
        if (partCount <= 0) return false
        val allSent = (0 until partCount).all { prefs.getBoolean("sent_${actionId}_$it", false) }
        if (!allSent) return false

        if (!prefs.getBoolean("sent_reported_$actionId", false)) {
            persistVerifiedSent(context, actionId)
            val posted = VaBackendClient.postActionResult(
                context,
                actionId,
                "sent",
                externalRef = "android-sms:$actionId",
                details = org.json.JSONObject().put("parts", partCount).put("reconciled_from_device", true),
            )
            if (posted) prefs.edit().putBoolean("sent_reported_$actionId", true).apply()
        }
        val allDelivered = (0 until partCount).all { prefs.getBoolean("delivered_${actionId}_$it", false) }
        if (allDelivered && !prefs.getBoolean("delivered_reported_$actionId", false)) {
            val posted = VaBackendClient.postActionResult(
                context,
                actionId,
                "delivered",
                externalRef = "android-sms:$actionId",
                details = org.json.JSONObject().put("parts", partCount).put("reconciled_from_device", true),
            )
            if (posted) prefs.edit().putBoolean("delivered_reported_$actionId", true).apply()
        }
        return true
    }

    fun persistVerifiedSent(context: Context, actionId: Long) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val target = prefs.getString("target_$actionId", "").orEmpty()
        val body = prefs.getString("body_$actionId", "").orEmpty()
        if (target.isNotBlank() && body.isNotBlank()) persistSentForDefaultSmsApp(context, target, body)
    }

    private fun statusIntent(
        context: Context,
        actionId: Long,
        partIndex: Int,
        partCount: Int,
        kind: String,
    ): PendingIntent {
        val intent = Intent(context, SmsStatusReceiver::class.java)
            .setAction("${context.packageName}.SMS_${kind.uppercase()}_${actionId}_$partIndex")
            .putExtra("action_id", actionId)
            .putExtra("part_index", partIndex)
            .putExtra("part_count", partCount)
            .putExtra("kind", kind)
        val requestCode = ("$kind:$actionId:$partIndex".hashCode() and 0x7fffffff)
        return PendingIntent.getBroadcast(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun persistSentForDefaultSmsApp(context: Context, target: String, text: String) {
        if (Telephony.Sms.getDefaultSmsPackage(context) != context.packageName) return
        try {
            context.contentResolver.insert(
                Telephony.Sms.Sent.CONTENT_URI,
                ContentValues().apply {
                    put(Telephony.Sms.ADDRESS, target)
                    put(Telephony.Sms.BODY, text)
                    put(Telephony.Sms.DATE, System.currentTimeMillis())
                    put(Telephony.Sms.READ, 1)
                },
            )
        } catch (_: Exception) {
            // The carrier result has already been reported to the backend. Provider
            // history persistence is secondary and must not trigger a resend.
        }
    }
}
