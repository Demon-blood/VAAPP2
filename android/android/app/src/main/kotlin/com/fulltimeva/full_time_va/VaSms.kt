package com.fulltimeva.full_time_va

import android.content.ContentValues
import android.content.Context
import android.provider.Telephony
import android.telephony.SmsManager

object VaSms {
    fun send(context: Context, target: String, text: String) {
        require(target.isNotBlank()) { "SMS target is required" }
        require(text.isNotBlank()) { "SMS body is required" }
        @Suppress("DEPRECATION")
        val manager = SmsManager.getDefault()
        val parts = manager.divideMessage(text)
        if (parts.size <= 1) {
            manager.sendTextMessage(target, null, text, null, null)
        } else {
            manager.sendMultipartTextMessage(target, null, ArrayList(parts), null, null)
        }
        // The default SMS application owns provider persistence for outgoing messages.
        if (Telephony.Sms.getDefaultSmsPackage(context) == context.packageName) {
            context.contentResolver.insert(
                Telephony.Sms.Sent.CONTENT_URI,
                ContentValues().apply {
                    put(Telephony.Sms.ADDRESS, target)
                    put(Telephony.Sms.BODY, text)
                    put(Telephony.Sms.DATE, System.currentTimeMillis())
                    put(Telephony.Sms.READ, 1)
                },
            )
        }
    }
}
