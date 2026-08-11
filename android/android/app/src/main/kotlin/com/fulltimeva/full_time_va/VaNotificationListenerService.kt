package com.fulltimeva.full_time_va

import android.app.Notification
import android.app.RemoteInput
import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import kotlin.concurrent.thread

class VaNotificationListenerService : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn ?: return
        if (sbn.packageName == packageName) return
        val channel = channelFor(sbn.packageName) ?: return
        val notification = sbn.notification ?: return
        if (notification.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = notification.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (
            extras.getCharSequence(Notification.EXTRA_TEXT)
                ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
                ?: extras.getCharSequence(Notification.EXTRA_SUB_TEXT)
        )?.toString().orEmpty()
        if (title.isBlank() && text.isBlank()) return
        val replyAction = notification.actions?.firstOrNull { action ->
            !action.remoteInputs.isNullOrEmpty()
        }
        val contentFingerprint = VaBackendClient.fingerprint("${sbn.packageName}|${sbn.key}|$title|$text").take(32)
        val externalId = "notification:${sbn.packageName}:$contentFingerprint"
        thread(name = "va-notification-ingest") {
            val response = VaBackendClient.postEvent(
                this,
                JSONObject()
                    .put("external_id", externalId.take(250))
                    .put("channel", channel)
                    .put("provider", channel)
                    .put("package_name", sbn.packageName)
                    .put("thread_key", title.take(250))
                    .put("sender", title)
                    .put("recipient", "me")
                    .put("body", text)
                    .put("direction", "incoming")
                    .put("event_type", "message")
                    .put("occurred_at", VaBackendClient.isoTime(sbn.postTime))
                    .put("supports_direct_reply", replyAction != null)
                    .put("allow_action", replyAction != null),
            ) ?: return@thread
            val deviceAction = response.optJSONObject("device_action") ?: return@thread
            if (deviceAction.optString("type") != "reply" || replyAction == null) return@thread
            val actionId = deviceAction.optLong("id", -1L)
            val replyText = deviceAction.optString("text")
            if (actionId <= 0 || replyText.isBlank() || !VaBackendClient.markActionExecuted(this, actionId)) return@thread
            try {
                val remoteInputs = replyAction.remoteInputs ?: return@thread
                val fillInIntent = Intent()
                val results = android.os.Bundle()
                for (remoteInput in remoteInputs) {
                    results.putCharSequence(remoteInput.resultKey, replyText)
                }
                RemoteInput.addResultsToIntent(remoteInputs, fillInIntent, results)
                replyAction.actionIntent.send(this, 0, fillInIntent)
                VaBackendClient.postActionResult(this, actionId, "completed")
            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(this, actionId)
                VaBackendClient.postActionResult(this, actionId, "failed", exc.toString())
            }
        }
    }

    private fun channelFor(packageName: String): String? = when {
        packageName == "com.whatsapp" || packageName.startsWith("com.whatsapp.") -> "whatsapp"
        packageName == "org.thoughtcrime.securesms" -> "signal"
        packageName.startsWith("org.telegram.") -> "telegram"
        packageName == "com.facebook.orca" -> "messenger"
        else -> null
    }
}
