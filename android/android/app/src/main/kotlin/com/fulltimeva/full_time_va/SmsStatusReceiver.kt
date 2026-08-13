package com.fulltimeva.full_time_va

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import kotlin.concurrent.thread

class SmsStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val actionId = intent.getLongExtra("action_id", -1L)
        val partIndex = intent.getIntExtra("part_index", -1)
        val partCount = intent.getIntExtra("part_count", 1).coerceAtLeast(1)
        val kind = intent.getStringExtra("kind").orEmpty()
        if (actionId <= 0L || partIndex < 0 || kind !in setOf("sent", "delivered")) return

        val prefs = context.getSharedPreferences("va_sms_delivery", Context.MODE_PRIVATE)
        val ok = resultCode == Activity.RESULT_OK
        prefs.edit().putBoolean("${kind}_${actionId}_$partIndex", ok).apply()

        thread(name = "va-sms-$kind-result") {
            if (!ok) {
                if (kind == "sent") {
                    VaBackendClient.clearActionExecuted(context, actionId)
                    VaBackendClient.postActionResult(
                        context,
                        actionId,
                        "failed",
                        "Android SmsManager reported send failure resultCode=$resultCode for part ${partIndex + 1}/$partCount",
                        "android-sms:$actionId",
                        JSONObject().put("part_index", partIndex).put("part_count", partCount).put("result_code", resultCode),
                    )
                } else {
                    VaBackendClient.postActionResult(
                        context,
                        actionId,
                        "delivery_failed",
                        "Carrier delivery receipt failed for part ${partIndex + 1}/$partCount",
                        "android-sms:$actionId",
                        JSONObject().put("part_index", partIndex).put("part_count", partCount).put("result_code", resultCode),
                    )
                }
                return@thread
            }

            val allOk = (0 until partCount).all { prefs.getBoolean("${kind}_${actionId}_$it", false) }
            if (!allOk) return@thread
            // The carrier result is persisted locally first. If the backend is offline,
            // the periodic worker will re-post this evidence before considering any resend.
            VaSms.repostEvidenceIfAvailable(context, actionId)
        }
    }
}
