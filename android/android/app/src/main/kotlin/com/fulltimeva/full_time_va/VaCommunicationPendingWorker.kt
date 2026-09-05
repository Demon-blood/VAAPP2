package com.fulltimeva.full_time_va

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters

class VaCommunicationPendingWorker(
    appContext: Context,
    workerParams: WorkerParameters,
) : Worker(appContext, workerParams) {
    companion object {
        fun scheduleImmediate(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                "va-communication-inbound-flush",
                ExistingWorkPolicy.REPLACE,
                OneTimeWorkRequestBuilder<VaCommunicationPendingWorker>()
                    .setConstraints(constraints)
                    .build(),
            )
        }
    }

    override fun doWork(): Result {
        if (!VaBackendClient.hasCredentials(applicationContext)) return Result.success()

        // Inbound events are evidence too. Flush the encrypted device outbox before
        // fetching reply actions so a temporary network/Render outage cannot make a
        // real SMS or supported notification disappear from VAAPP.
        val flushed = VaBackendClient.flushPendingCommunicationEvents(applicationContext)
        if (!flushed.optBoolean("success", true)) return Result.retry()

        if (applicationContext.checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            // Permission is user-controlled. Keep the backend action pending so it is
            // visible as a real device capability gap rather than pretending delivery.
            return Result.success()
        }
        val actions = VaBackendClient.fetchPendingCommunicationActions(applicationContext)
        for (index in 0 until actions.length()) {
            val action = actions.optJSONObject(index) ?: continue
            if (action.optString("type") != "reply") continue
            val actionId = action.optLong("id", -1L)
            if (actionId <= 0L) continue
            val channel = action.optString("channel")
            if (channel != "sms") {
                // RemoteInput/failure evidence is reconciliation-only. Notification-app
                // actions can never be reconstructed from this background feed.
                VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)
                continue
            }
            val target = action.optString("target")
            val text = action.optString("text")
            // Reconcile carrier evidence before generic stored failures. If carrier
            // handoff succeeded but a later delivery callback failed while offline,
            // post the stronger sent evidence first so backend monotonicity is preserved.
            if (VaSms.repostEvidenceIfAvailable(applicationContext, actionId)) {
                VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)
                continue
            }
            if (VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)) continue
            val mayDispatch = action.optBoolean("can_background_dispatch", false) ||
                action.optBoolean("can_resume_claimed_dispatch", false)
            if (!mayDispatch) continue
            if (target.isBlank() || text.isBlank()) continue
            // The backend claim is the durable cross-device at-most-once boundary.
            // If the claim response is lost, fail closed and do not touch the carrier.
            if (!VaBackendClient.claimCommunicationAction(applicationContext, actionId)) continue
            // The local marker protects the narrower crash/callback-loss window on this install.
            if (!VaBackendClient.markActionExecuted(applicationContext, actionId)) continue
            try {
                VaSms.send(applicationContext, target, text, actionId)
            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(applicationContext, actionId)
                VaBackendClient.postOrStoreActionResult(
                    applicationContext,
                    actionId,
                    "failed",
                    exc.toString(),
                    "android-sms:$actionId",
                )
            }
        }
        return Result.success()
    }
}
