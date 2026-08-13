package com.fulltimeva.full_time_va

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.work.Worker
import androidx.work.WorkerParameters

class VaCommunicationPendingWorker(
    appContext: Context,
    workerParams: WorkerParameters,
) : Worker(appContext, workerParams) {
    override fun doWork(): Result {
        if (!VaBackendClient.hasCredentials(applicationContext)) return Result.success()
        if (applicationContext.checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            // Permission is user-controlled. Keep the backend action pending so it is
            // visible as a real device capability gap rather than pretending delivery.
            return Result.success()
        }
        val actions = VaBackendClient.fetchPendingCommunicationActions(applicationContext)
        for (index in 0 until actions.length()) {
            val action = actions.optJSONObject(index) ?: continue
            if (action.optString("channel") != "sms" || action.optString("type") != "reply") continue
            val actionId = action.optLong("id", -1L)
            val target = action.optString("target")
            val text = action.optString("text")
            if (actionId <= 0L) continue
            if (VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)) continue
            // Reconcile locally persisted carrier evidence before any resend. This is
            // the device-side equivalent of Gmail's postcondition-first retry rule.
            if (VaSms.repostEvidenceIfAvailable(applicationContext, actionId)) continue
            if (action.optString("channel") != "sms" || !action.optBoolean("can_background_dispatch", false)) continue
            if (target.isBlank() || text.isBlank()) continue
            if (!VaBackendClient.markActionExecuted(applicationContext, actionId)) continue
            try {
                VaSms.send(applicationContext, target, text, actionId)
            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(applicationContext, actionId)
                VaBackendClient.postActionResult(applicationContext, actionId, "failed", exc.toString())
            }
        }
        return Result.success()
    }
}
