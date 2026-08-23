package com.fulltimeva.full_time_va

import android.Manifest
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.CallLog
import android.provider.Settings
import android.provider.Telephony
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class MainActivity : FlutterActivity() {
    private val channelName = "full_time_va/device"
    private val permissionRequestCode = 7010

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        scheduleCommunicationCatchUp()
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName).setMethodCallHandler { call, result ->
            handleMethod(call, result)
        }
    }

    private fun scheduleCommunicationCatchUp() {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = PeriodicWorkRequestBuilder<VaCommunicationPendingWorker>(15, TimeUnit.MINUTES)
            .setConstraints(constraints)
            .build()
        val manager = WorkManager.getInstance(this)
        manager.enqueueUniquePeriodicWork(
            "va-communication-pending-actions",
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
        manager.enqueueUniqueWork(
            "va-communication-pending-actions-now",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<VaCommunicationPendingWorker>()
                .setConstraints(constraints)
                .build(),
        )
    }

    private fun handleMethod(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "syncCredentials" -> {
                VaBackendClient.saveCredentials(
                    this,
                    call.argument<String>("serverUrl"),
                    call.argument<String>("deviceToken"),
                )
                scheduleCommunicationCatchUp()
                result.success(true)
            }
            "clearCredentials" -> {
                VaBackendClient.saveCredentials(this, null, null)
                result.success(true)
            }
            "getCommunicationStatus" -> result.success(communicationStatus())
            "requestRuntimePermissions" -> {
                requestCommunicationPermissions()
                result.success(true)
            }
            "requestSmsRole" -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    requestRole(RoleManager.ROLE_SMS)
                } else {
                    requestLegacySmsRole()
                }
                result.success(true)
            }
            "requestCallScreeningRole" -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    requestRole(RoleManager.ROLE_CALL_SCREENING)
                }
                result.success(true)
            }
            "openNotificationAccess" -> {
                val action = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP_MR1) {
                    Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS
                } else {
                    Settings.ACTION_SETTINGS
                }
                startActivity(Intent(action))
                result.success(true)
            }
            "readPhoneContacts" -> readPhoneContacts(result)
            "sendSms" -> sendSms(call, result)
            "syncRecentCommunications" -> syncRecentCommunications(result)
            "syncCallPolicy" -> syncCallPolicy(result)
            else -> result.notImplemented()
        }
    }

    private fun readPhoneContacts(result: MethodChannel.Result) {
        thread(name = "va-phone-contacts") {
            try {
                val payload = VaContacts.read(this)
                runOnUiThread { result.success(payload) }
            } catch (exc: Exception) {
                runOnUiThread { result.error("contacts_read_failed", exc.toString(), null) }
            }
        }
    }

    private fun requestRole(role: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val roleManager = getSystemService(RoleManager::class.java)
        if (roleManager.isRoleAvailable(role) && !roleManager.isRoleHeld(role)) {
            startActivityForResult(roleManager.createRequestRoleIntent(role), 7100 + role.hashCode().and(0xFF))
        }
    }

    private fun requestLegacySmsRole() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.KITKAT) return
        if (Telephony.Sms.getDefaultSmsPackage(this) == packageName) return
        startActivity(
            Intent(Telephony.Sms.Intents.ACTION_CHANGE_DEFAULT).putExtra(
                Telephony.Sms.Intents.EXTRA_PACKAGE_NAME,
                packageName,
            ),
        )
    }

    private fun requestCommunicationPermissions() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val wanted = mutableListOf(
            Manifest.permission.READ_SMS,
            Manifest.permission.RECEIVE_SMS,
            Manifest.permission.RECEIVE_MMS,
            Manifest.permission.RECEIVE_WAP_PUSH,
            Manifest.permission.SEND_SMS,
            Manifest.permission.READ_CALL_LOG,
            Manifest.permission.READ_PHONE_STATE,
            Manifest.permission.READ_CONTACTS,
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) wanted.add(Manifest.permission.READ_PHONE_NUMBERS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) wanted.add(Manifest.permission.POST_NOTIFICATIONS)
        val missing = wanted.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), permissionRequestCode)
    }

    private fun communicationStatus(): Map<String, Any> {
        val smsRole = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val manager = getSystemService(RoleManager::class.java)
            manager.isRoleAvailable(RoleManager.ROLE_SMS) && manager.isRoleHeld(RoleManager.ROLE_SMS)
        } else {
            Telephony.Sms.getDefaultSmsPackage(this) == packageName
        }
        val callScreeningRole = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val manager = getSystemService(RoleManager::class.java)
            manager.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING) && manager.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
        } else false
        val listenerSetting = Settings.Secure.getString(contentResolver, "enabled_notification_listeners").orEmpty()
        val notificationAccess = listenerSetting.contains(packageName)
        return mapOf(
            "sms_role" to smsRole,
            "call_screening_role" to callScreeningRole,
            "notification_access" to notificationAccess,
            "read_sms" to hasPermission(Manifest.permission.READ_SMS),
            "receive_sms" to hasPermission(Manifest.permission.RECEIVE_SMS),
            "receive_mms" to hasPermission(Manifest.permission.RECEIVE_MMS),
            "send_sms" to hasPermission(Manifest.permission.SEND_SMS),
            "read_call_log" to hasPermission(Manifest.permission.READ_CALL_LOG),
            "read_contacts" to hasPermission(Manifest.permission.READ_CONTACTS),
            "backend_linked" to VaBackendClient.hasCredentials(this),
            "pending_communication_events" to VaBackendClient.pendingCommunicationEventCount(this),
            "last_backend_error" to VaBackendClient.lastRequestError(this),
        )
    }

    private fun hasPermission(permission: String): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    private fun sendSms(call: MethodCall, result: MethodChannel.Result) {
        val target = call.argument<String>("target").orEmpty().trim()
        val text = call.argument<String>("text").orEmpty().trim()
        if (target.isBlank() || text.isBlank()) {
            result.error("invalid_sms", "Phone number and message are required", null)
            return
        }
        if (!hasPermission(Manifest.permission.SEND_SMS)) {
            result.error("sms_permission", "SMS send permission is not granted", null)
            return
        }
        thread(name = "va-manual-sms") {
            try {
                VaSms.send(this, target, text)
                runOnUiThread { result.success(true) }
            } catch (exc: Exception) {
                runOnUiThread { result.error("sms_send_failed", exc.toString(), null) }
            }
        }
    }

    private fun syncCallPolicy(result: MethodChannel.Result) {
        if (!VaBackendClient.hasCredentials(this)) {
            result.success(false)
            return
        }
        thread(name = "va-policy-sync") {
            val policy = VaBackendClient.fetchCallPolicy(this)
            if (policy != null) VaBackendClient.storeCallPolicy(this, policy)
            runOnUiThread { result.success(policy != null) }
        }
    }

    private fun syncRecentCommunications(result: MethodChannel.Result) {
        if (!VaBackendClient.hasCredentials(this)) {
            result.success(
                mapOf(
                    "success" to false,
                    "processed" to 0,
                    "reason" to "backend_not_linked",
                    "error" to "The native phone bridge is not linked to the VA backend.",
                ),
            )
            return
        }
        thread(name = "va-history-sync") {
            val queued = VaBackendClient.flushPendingCommunicationEvents(this)
            val events = JSONArray()
            val smsScanned = collectSms(events, 180)
            val mmsScanned = collectMms(events, 120)
            val callsScanned = collectCalls(events, 120)
            val history = if (events.length() > 0) {
                // Keep each request bounded. Historical ingestion commits real records
                // and can otherwise exceed the native HTTP timeout on a large inbox.
                VaBackendClient.postBatchChunked(this, events, 25)
            } else {
                JSONObject()
                    .put("success", true)
                    .put("submitted", 0)
                    .put("processed", 0)
                    .put("duplicates", 0)
            }
            val success = queued.optBoolean("success", true) && history.optBoolean("success", true)
            val error = when {
                !queued.optBoolean("success", true) -> queued.optString("error")
                !history.optBoolean("success", true) -> history.optString("error")
                else -> ""
            }
            val payload = mapOf(
                "success" to success,
                "sms_scanned" to smsScanned,
                "mms_scanned" to mmsScanned,
                "calls_scanned" to callsScanned,
                "submitted" to history.optInt("submitted", 0),
                "processed" to history.optInt("processed", 0),
                "duplicates" to history.optInt("duplicates", 0),
                "failed" to history.optInt("failed", 0),
                "queued_submitted" to queued.optInt("submitted", 0),
                "queued_failed" to queued.optInt("failed", 0),
                "queued_processed" to queued.optInt("processed", 0),
                "pending_after" to VaBackendClient.pendingCommunicationEventCount(this),
                "error" to error,
            )
            runOnUiThread { result.success(payload) }
        }
    }

    private fun collectSms(events: JSONArray, limit: Int): Int {
        if (!hasPermission(Manifest.permission.READ_SMS)) return 0
        val before = events.length()
        try {
            contentResolver.query(
                Telephony.Sms.CONTENT_URI,
                arrayOf(Telephony.Sms._ID, Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.TYPE),
                null,
                null,
                "${Telephony.Sms.DATE} DESC",
            )?.use { cursor ->
                val idIndex = cursor.getColumnIndexOrThrow(Telephony.Sms._ID)
                val addressIndex = cursor.getColumnIndexOrThrow(Telephony.Sms.ADDRESS)
                val bodyIndex = cursor.getColumnIndexOrThrow(Telephony.Sms.BODY)
                val dateIndex = cursor.getColumnIndexOrThrow(Telephony.Sms.DATE)
                val typeIndex = cursor.getColumnIndexOrThrow(Telephony.Sms.TYPE)
                var seen = 0
                while (cursor.moveToNext() && seen < limit) {
                    seen += 1
                    val type = cursor.getInt(typeIndex)
                    val outgoing = type == Telephony.Sms.MESSAGE_TYPE_SENT || type == Telephony.Sms.MESSAGE_TYPE_OUTBOX
                    val address = cursor.getString(addressIndex).orEmpty()
                    val timestamp = cursor.getLong(dateIndex)
                    events.put(
                        JSONObject()
                            .put("external_id", "smsdb:${cursor.getLong(idIndex)}")
                            .put("channel", "sms")
                            .put("provider", "android_sms_history")
                            .put("package_name", packageName)
                            .put("thread_key", VaBackendClient.normalizeNumber(address))
                            .put("sender", if (outgoing) "me" else address)
                            .put("recipient", if (outgoing) address else "me")
                            .put("body", cursor.getString(bodyIndex).orEmpty())
                            .put("direction", if (outgoing) "outgoing" else "incoming")
                            .put("event_type", "message")
                            .put("occurred_at", VaBackendClient.isoTime(timestamp))
                            .put("supports_direct_reply", !outgoing)
                            .put("allow_action", false),
                    )
                }
            }
        } catch (_: Exception) {
            // Permission/role diagnostics remain visible in the Flutter UI.
        }
        return events.length() - before
    }

    private fun collectMms(events: JSONArray, limit: Int): Int {
        if (!hasPermission(Manifest.permission.READ_SMS)) return 0
        val before = events.length()
        try {
            contentResolver.query(
                Telephony.Mms.CONTENT_URI,
                arrayOf(Telephony.Mms._ID, Telephony.Mms.DATE, Telephony.Mms.MESSAGE_BOX),
                null,
                null,
                "${Telephony.Mms.DATE} DESC",
            )?.use { cursor ->
                val idIndex = cursor.getColumnIndexOrThrow(Telephony.Mms._ID)
                val dateIndex = cursor.getColumnIndexOrThrow(Telephony.Mms.DATE)
                val boxIndex = cursor.getColumnIndexOrThrow(Telephony.Mms.MESSAGE_BOX)
                var seen = 0
                while (cursor.moveToNext() && seen < limit) {
                    seen += 1
                    val id = cursor.getLong(idIndex)
                    val box = cursor.getInt(boxIndex)
                    val outgoing = box == Telephony.Mms.MESSAGE_BOX_SENT || box == Telephony.Mms.MESSAGE_BOX_OUTBOX
                    val address = mmsAddress(id, outgoing)
                    val timestamp = cursor.getLong(dateIndex) * 1000L
                    val body = mmsBody(id)
                    events.put(
                        JSONObject()
                            .put("external_id", "mmsdb:$id")
                            .put("channel", "sms")
                            .put("provider", "android_mms_history")
                            .put("package_name", packageName)
                            .put("thread_key", VaBackendClient.normalizeNumber(address))
                            .put("sender", if (outgoing) "me" else address)
                            .put("recipient", if (outgoing) address else "me")
                            .put("body", body)
                            .put("direction", if (outgoing) "outgoing" else "incoming")
                            .put("event_type", "mms")
                            .put("occurred_at", VaBackendClient.isoTime(timestamp))
                            .put("supports_direct_reply", !outgoing)
                            .put("allow_action", false),
                    )
                }
            }
        } catch (_: Exception) {
            // MMS history is best-effort; SMS/call diagnostics stay visible in Flutter.
        }
        return events.length() - before
    }

    private fun mmsAddress(messageId: Long, outgoing: Boolean): String {
        val uri = android.net.Uri.parse("content://mms/$messageId/addr")
        val wantedType = if (outgoing) 151 else 137 // MMS TO / FROM
        try {
            contentResolver.query(uri, arrayOf("address", "type"), null, null, null)?.use { cursor ->
                val addressIndex = cursor.getColumnIndexOrThrow("address")
                val typeIndex = cursor.getColumnIndexOrThrow("type")
                while (cursor.moveToNext()) {
                    val address = cursor.getString(addressIndex).orEmpty()
                    if (cursor.getInt(typeIndex) == wantedType && address.isNotBlank() && address != "insert-address-token") {
                        return address
                    }
                }
            }
        } catch (_: Exception) {}
        return ""
    }

    private fun mmsBody(messageId: Long): String {
        val uri = android.net.Uri.parse("content://mms/$messageId/part")
        val parts = mutableListOf<String>()
        try {
            contentResolver.query(uri, arrayOf("_id", "ct", "text", "_data"), null, null, null)?.use { cursor ->
                val idIndex = cursor.getColumnIndexOrThrow("_id")
                val contentTypeIndex = cursor.getColumnIndexOrThrow("ct")
                val textIndex = cursor.getColumnIndexOrThrow("text")
                val dataIndex = cursor.getColumnIndexOrThrow("_data")
                while (cursor.moveToNext()) {
                    val contentType = cursor.getString(contentTypeIndex).orEmpty()
                    if (contentType == "text/plain") {
                        val inline = cursor.getString(textIndex).orEmpty().trim()
                        if (inline.isNotEmpty()) {
                            parts.add(inline)
                        } else if (!cursor.getString(dataIndex).isNullOrBlank()) {
                            val partId = cursor.getLong(idIndex)
                            try {
                                contentResolver.openInputStream(android.net.Uri.parse("content://mms/part/$partId"))
                                    ?.bufferedReader()?.use { reader ->
                                        val value = reader.readText().trim()
                                        if (value.isNotEmpty()) parts.add(value)
                                    }
                            } catch (_: Exception) {}
                        }
                    }
                }
            }
        } catch (_: Exception) {}
        return if (parts.isEmpty()) "MMS attachment" else parts.joinToString("\n")
    }

    private fun collectCalls(events: JSONArray, limit: Int): Int {
        if (!hasPermission(Manifest.permission.READ_CALL_LOG)) return 0
        val before = events.length()
        try {
            contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(CallLog.Calls._ID, CallLog.Calls.NUMBER, CallLog.Calls.DATE, CallLog.Calls.TYPE, CallLog.Calls.DURATION),
                null,
                null,
                "${CallLog.Calls.DATE} DESC",
            )?.use { cursor ->
                val idIndex = cursor.getColumnIndexOrThrow(CallLog.Calls._ID)
                val numberIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.NUMBER)
                val dateIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.DATE)
                val typeIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.TYPE)
                val durationIndex = cursor.getColumnIndexOrThrow(CallLog.Calls.DURATION)
                var seen = 0
                while (cursor.moveToNext() && seen < limit) {
                    seen += 1
                    val type = cursor.getInt(typeIndex)
                    val outgoing = type == CallLog.Calls.OUTGOING_TYPE
                    val eventType = when (type) {
                        CallLog.Calls.MISSED_TYPE -> "missed_call"
                        CallLog.Calls.REJECTED_TYPE -> "rejected_call"
                        CallLog.Calls.BLOCKED_TYPE -> "blocked_call"
                        CallLog.Calls.OUTGOING_TYPE -> "outgoing_call"
                        else -> "incoming_call"
                    }
                    val number = cursor.getString(numberIndex).orEmpty()
                    val timestamp = cursor.getLong(dateIndex)
                    val duration = cursor.getLong(durationIndex)
                    events.put(
                        JSONObject()
                            .put("external_id", "calllog:${cursor.getLong(idIndex)}")
                            .put("channel", "call")
                            .put("provider", "android_call_log")
                            .put("package_name", packageName)
                            .put("thread_key", VaBackendClient.normalizeNumber(number))
                            .put("sender", if (outgoing) "me" else number)
                            .put("recipient", if (outgoing) number else "me")
                            .put("body", "Call duration: $duration seconds")
                            .put("direction", if (outgoing) "outgoing" else "incoming")
                            .put("event_type", eventType)
                            .put("occurred_at", VaBackendClient.isoTime(timestamp))
                            .put("supports_direct_reply", false)
                            .put("allow_action", false),
                    )
                }
            }
        } catch (_: Exception) {
            // Permission/role diagnostics remain visible in the Flutter UI.
        }
        return events.length() - before
    }
}
