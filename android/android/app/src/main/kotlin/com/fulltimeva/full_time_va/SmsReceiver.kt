package com.fulltimeva.full_time_va

import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import org.json.JSONObject
import kotlin.concurrent.thread

class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_DELIVER_ACTION &&
            intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION
        ) return
        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (messages.isEmpty()) return
        val sender = messages.firstOrNull()?.originatingAddress.orEmpty()
        val body = messages.joinToString("") { it.messageBody.orEmpty() }
        val timestamp = messages.minOfOrNull { it.timestampMillis } ?: System.currentTimeMillis()
        val externalId = "sms:$timestamp:${sender.hashCode()}:${body.hashCode()}"

        // The default SMS handler is responsible for persisting SMS_DELIVER messages.
        var persistedUri: android.net.Uri? = null
        if (intent.action == Telephony.Sms.Intents.SMS_DELIVER_ACTION) {
            try {
                persistedUri = context.contentResolver.insert(
                    Telephony.Sms.Inbox.CONTENT_URI,
                    ContentValues().apply {
                        put(Telephony.Sms.ADDRESS, sender)
                        put(Telephony.Sms.BODY, body)
                        put(Telephony.Sms.DATE, timestamp)
                        put(Telephony.Sms.READ, 0)
                    },
                )
            } catch (_: Exception) {
                // Ingestion still proceeds; role/permission status is shown in the app.
            }
        }

        val event = JSONObject()
            .put("external_id", externalId)
            .put("channel", "sms")
            .put("provider", "android_sms")
            .put("package_name", context.packageName)
            .put("thread_key", VaBackendClient.normalizeNumber(sender))
            .put("sender", sender)
            .put("recipient", "me")
            .put("body", body)
            .put("direction", "incoming")
            .put("event_type", "message")
            .put("occurred_at", VaBackendClient.isoTime(timestamp))
            .put("supports_direct_reply", true)
            .put("allow_action", true)

        // Persist first. BroadcastReceiver lifetime is short and Android may stop the
        // process after onReceive returns, so a real inbound SMS must be durable before
        // any network request is attempted.
        VaBackendClient.queueCommunicationEvent(context, event)

        val pendingResult = goAsync()
        thread(name = "va-sms-ingest") {
            try {
                val response = VaBackendClient.postEvent(context, event)
                if (response == null) {
                    VaCommunicationPendingWorker.scheduleImmediate(context)
                    return@thread
                }
                VaBackendClient.removeQueuedCommunicationEvent(context, externalId)
                val decision = response.optJSONObject("decision")
                if (decision?.optBoolean("delete_from_device", false) == true && persistedUri != null) {
                    try { context.contentResolver.delete(persistedUri!!, null, null) } catch (_: Exception) {}
                }
                if (decision?.optBoolean("interrupt", false) == true) {
                    VaBackendClient.notifyAttention(
                        context,
                        "Message needs you now",
                        if (sender.isBlank()) body else "$sender · $body",
                        externalId,
                    )
                }
                val action = response.optJSONObject("device_action") ?: return@thread
                if (action.optString("type") != "reply") return@thread
                val actionId = action.optLong("id", -1L)
                val text = action.optString("text")
                if (actionId <= 0 || text.isBlank() || !VaBackendClient.markActionExecuted(context, actionId)) return@thread
                try {
                    VaSms.send(context, sender, text, actionId)
                    // SmsStatusReceiver reports real carrier send/delivery callbacks.
                    // Do not mark this action complete merely because SmsManager accepted the request.
                } catch (exc: Exception) {
                    VaBackendClient.clearActionExecuted(context, actionId)
                    VaBackendClient.postActionResult(context, actionId, "failed", exc.toString())
                }
            } finally {
                pendingResult.finish()
            }
        }
    }

}
