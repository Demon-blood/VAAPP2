package com.fulltimeva.full_time_va

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract
import android.telecom.Call
import android.telecom.CallScreeningService
import org.json.JSONObject
import kotlin.concurrent.thread

class VaCallScreeningService : CallScreeningService() {
    override fun onScreenCall(callDetails: Call.Details) {
        val rawNumber = callDetails.handle?.schemeSpecificPart.orEmpty()
        val number = VaBackendClient.normalizeNumber(rawNumber)
        val blocked = VaBackendClient.numberSet(this, "blocked_numbers").contains(number)
        val silenced = VaBackendClient.numberSet(this, "silenced_numbers").contains(number)
        val vip = VaBackendClient.numberSet(this, "vip_numbers").contains(number)
        val knownContact = vip || isKnownContact(number)
        val silenceUnknown = VaBackendClient.silenceUnknown(this)
        val shouldSilence = !blocked && (silenced || (silenceUnknown && !knownContact))

        val builder = CallResponse.Builder()
        if (blocked) {
            builder.setDisallowCall(true).setRejectCall(true).setSkipNotification(true).setSkipCallLog(false)
        } else if (shouldSilence && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            builder.setSilenceCall(true)
        }
        respondToCall(callDetails, builder.build())

        val timestamp = System.currentTimeMillis()
        thread(name = "va-call-log") {
            VaBackendClient.postEvent(
                this,
                JSONObject()
                    .put("external_id", "call:$timestamp:${number.hashCode()}")
                    .put("channel", "call")
                    .put("provider", "android_call_screening")
                    .put("package_name", packageName)
                    .put("thread_key", number)
                    .put("sender", rawNumber)
                    .put("recipient", "me")
                    .put("body", if (blocked) "Incoming call blocked by VA rule" else if (shouldSilence) "Incoming call silenced by VA rule" else "Incoming call allowed")
                    .put("direction", "incoming")
                    .put("event_type", if (blocked) "blocked_call" else if (shouldSilence) "silenced_call" else "incoming_call")
                    .put("occurred_at", VaBackendClient.isoTime(timestamp))
                    .put("supports_direct_reply", false)
                    .put("allow_action", false),
            )
        }
    }

    private fun isKnownContact(number: String): Boolean {
        if (number.isBlank() || checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return false
        return try {
            val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
            contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup._ID), null, null, null)?.use { it.moveToFirst() } == true
        } catch (_: Exception) {
            false
        }
    }
}
