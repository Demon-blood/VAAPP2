package com.fulltimeva.full_time_va

import android.app.Service
import android.content.Intent
import android.os.IBinder
import kotlin.concurrent.thread

class RespondViaMessageService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val target = intent?.data?.schemeSpecificPart.orEmpty()
        val text = intent?.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
        if (target.isNotBlank() && text.isNotBlank()) {
            thread(name = "va-respond-via-sms") {
                try {
                    VaSms.send(this, target, text)
                } finally {
                    stopSelf(startId)
                }
            }
        } else {
            stopSelf(startId)
        }
        return START_NOT_STICKY
    }
}
