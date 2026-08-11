package com.fulltimeva.full_time_va

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import kotlin.concurrent.thread

class MmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val timestamp = System.currentTimeMillis()
        thread(name = "va-mms-ingest") {
            VaBackendClient.postEvent(
                context,
                JSONObject()
                    .put("external_id", "mms:$timestamp:${intent.dataString.hashCode()}")
                    .put("channel", "sms")
                    .put("provider", "android_mms")
                    .put("package_name", context.packageName)
                    .put("thread_key", "mms")
                    .put("sender", "MMS")
                    .put("recipient", "me")
                    .put("body", "MMS received; open the message thread for attachment content.")
                    .put("direction", "incoming")
                    .put("event_type", "mms")
                    .put("occurred_at", VaBackendClient.isoTime(timestamp))
                    .put("supports_direct_reply", false)
                    .put("allow_action", false),
            )
        }
    }
}
