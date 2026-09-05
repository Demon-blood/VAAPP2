from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "2b48b72e720a2e515e346fed253e24c131ae078a"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256: dict[str, str] = {
    "preview/backend/tests/test_v116_device_dispatch_claim.py":
        "dd0e35e2ece35faf6d0037a55a68fa25e2450da5d823cd97178657668198ca79",
    "preview/backend/tests/test_v116_device_dispatch_claim_contract.py":
        "40c10f1b018ca63015f4d1af6cc134333bf8f5893838d91abb61b8965fad4516",
    "preview/docs/V1.0.16_DEVICE_DISPATCH_CLAIM.md":
        "a2b639e50d100b9481da1a6152b8d42d142933bd490d6a63097af7b1094aeb94",
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def replace_once(path: Path, old: str, new: str) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one anchor in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def verify_bundle() -> None:
    if not EXPECTED_PREVIEW_SHA256:
        raise RuntimeError("bundle hashes were not finalized")
    for relative, expected in EXPECTED_PREVIEW_SHA256.items():
        path = BUNDLE_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"missing prepared bundle file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"bundle integrity mismatch for {relative}: {actual}")


def verify_repo(root: Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"{root} is not a git working tree")
    head = run_git(root, "rev-parse", "HEAD")
    if head != EXPECTED_BASELINE:
        raise RuntimeError(
            f"refusing to patch unexpected HEAD {head}; expected v1.0.15 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.15"\nREQUIRED_ANDROID_VERSION = "1.0.15"\n'
    ):
        raise RuntimeError("v1.0.15 backend baseline identity mismatch")
    if "version: 1.0.15+58" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.15 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_models(root: Path) -> None:
    path = root / "backend/app/models/entities.py"
    old = '''
class CommunicationRule(Base):
    __tablename__ = "communication_rules"
'''
    new = '''
class CommunicationDispatchClaim(Base):
    __tablename__ = "communication_dispatch_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    communication_action_id: Mapped[int] = mapped_column(
        ForeignKey("communication_actions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class CommunicationRule(Base):
    __tablename__ = "communication_rules"
'''
    replace_once(path, old, new)


def patch_communications_service(root: Path) -> None:
    path = root / "backend/app/services/communications_service.py"
    replace_once(
        path,
        "from sqlalchemy import select\n",
        "from sqlalchemy import delete, select\nfrom sqlalchemy.exc import IntegrityError\n",
    )
    replace_once(
        path,
        "from app.models.entities import CommunicationAction, CommunicationDeliveryEvidence, CommunicationEvent, CommunicationRule, Task\n",
        (
            "from app.models.entities import (\n"
            "    CommunicationAction,\n"
            "    CommunicationDeliveryEvidence,\n"
            "    CommunicationDispatchClaim,\n"
            "    CommunicationEvent,\n"
            "    CommunicationRule,\n"
            "    Task,\n"
            ")\n"
        ),
    )

    helper = '''async def claim_communication_action(
    db: AsyncSession,
    action_id: int,
    *,
    device_id: int,
) -> tuple[CommunicationAction, bool]:
    """Durably reserve one device action; repeated claims by the same device are idempotent."""
    action = await db.get(CommunicationAction, action_id)
    if action is None:
        raise ValueError("Communication action does not exist")

    existing = (
        await db.execute(
            select(CommunicationDispatchClaim)
            .where(CommunicationDispatchClaim.communication_action_id == action_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return action, bool(existing.device_id == device_id and action.status == "dispatching")
    if action.status != "pending":
        return action, False

    claim = CommunicationDispatchClaim(
        communication_action_id=action.id,
        device_id=device_id,
    )
    try:
        async with db.begin_nested():
            db.add(claim)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(
                select(CommunicationDispatchClaim)
                .where(CommunicationDispatchClaim.communication_action_id == action_id)
                .limit(1)
            )
        ).scalar_one()
        await db.refresh(action)
        return action, bool(existing.device_id == device_id and action.status == "dispatching")

    action.status = "dispatching"
    action.failure_reason = ""
    await write_audit(
        db,
        "communication_action_dispatch_claimed",
        entity_type="communication_action",
        entity_id=str(action.id),
        result="deferred",
        details={
            "device_id": device_id,
            "status": "dispatching",
            "automatic_replay": False,
        },
    )
    await db.commit()
    return action, True


'''

    replace_once(
        path,
        "async def complete_communication_action(\n",
        helper + "async def complete_communication_action(\n",
    )
    replace_once(
        path,
        '''    accepted = {"pending", "dispatched", "sent", "delivered", "completed", "failed", "cancelled", "delivery_failed"}
''',
        '''    accepted = {
        "pending",
        "dispatching",
        "creation_uncertain",
        "dispatched",
        "sent",
        "delivered",
        "completed",
        "failed",
        "cancelled",
        "delivery_failed",
    }
''',
    )
    replace_once(
        path,
        '''    action.status = effective_status
    action.failure_reason = failure_reason[:2000]
''',
        '''    action.status = effective_status
    action.failure_reason = failure_reason[:2000]
    if effective_status == "failed":
        # A definitive device-side failure proves this attempt did not cross the
        # provider boundary, so the claim may be released for the existing safe retry path.
        await db.execute(
            delete(CommunicationDispatchClaim).where(
                CommunicationDispatchClaim.communication_action_id == action.id
            )
        )
''',
    )
    replace_once(
        path,
        '''async def pending_communication_actions(db: AsyncSession, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return real persisted device work that still needs dispatch/reconciliation.

    SMS actions may be initiated by the paired device worker. Notification-app rows
    are also returned so locally persisted RemoteInput handoff evidence can be re-posted
    after a network outage, but they are explicitly marked non-dispatchable because a
    stale RemoteInput action cannot be reconstructed safely.
    """
    rows = list(
        (
            await db.execute(
                select(CommunicationAction)
                .where(CommunicationAction.status == "pending")
                .order_by(CommunicationAction.created_at.asc(), CommunicationAction.id.asc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
    )
    result: list[dict[str, Any]] = []
    for action in rows:
        payload = json.loads(action.payload_json or "{}")
        channel = str(payload.get("channel") or "").lower()
        result.append({
            "id": action.id,
            "type": action.action_type,
            "target": action.target,
            "text": str(payload.get("text") or ""),
            "channel": channel,
            "can_background_dispatch": channel == "sms",
            "created_at": action.created_at,
        })
    return result
''',
        '''async def pending_communication_actions(db: AsyncSession, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return dispatchable work plus reconciliation-only claimed actions.

    A claimed action remains visible so the original device can re-post locally durable
    evidence after a network outage. A ``dispatching`` SMS row may only resume after
    the same backend device re-asserts its durable claim; other devices remain blocked.
    """
    rows = list(
        (
            await db.execute(
                select(CommunicationAction)
                .where(CommunicationAction.status.in_(["pending", "dispatching", "creation_uncertain"]))
                .order_by(CommunicationAction.created_at.asc(), CommunicationAction.id.asc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
    )
    result: list[dict[str, Any]] = []
    for action in rows:
        payload = json.loads(action.payload_json or "{}")
        channel = str(payload.get("channel") or "").lower()
        dispatchable = action.status == "pending" and channel == "sms"
        resumable_claim = action.status == "dispatching" and channel == "sms"
        result.append({
            "id": action.id,
            "type": action.action_type,
            "target": action.target,
            "text": str(payload.get("text") or ""),
            "channel": channel,
            "status": action.status,
            "can_background_dispatch": dispatchable,
            "can_resume_claimed_dispatch": resumable_claim,
            "reconciliation_only": action.status == "creation_uncertain" or (
                action.status == "dispatching" and channel != "sms"
            ),
            "created_at": action.created_at,
        })
    return result
''',
    )


def patch_routes(root: Path) -> None:
    path = root / "backend/app/api/routes.py"
    replace_once(
        path,
        '''from app.services.communications_service import (
    complete_communication_action,
    device_call_policy,
    ingest_communication,
    pending_communication_actions,
)
''',
        '''from app.services.communications_service import (
    claim_communication_action,
    complete_communication_action,
    device_call_policy,
    ingest_communication,
    pending_communication_actions,
)
''',
    )
    claim_route = '''@router.post("/api/communications/actions/{action_id}/claim")
async def communication_claim_action(
    action_id: int,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        action, claimed = await claim_communication_action(db, action_id, device_id=device.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": action.id, "status": action.status, "claimed": claimed}


'''
    replace_once(
        path,
        '@router.get("/api/communications/actions/pending")\n',
        claim_route + '@router.get("/api/communications/actions/pending")\n',
    )


def patch_android_client(root: Path) -> None:
    path = root / (
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt"
    )
    helper = '''    fun claimCommunicationAction(context: Context, actionId: Long): Boolean {
        repeat(3) {
            val response = request(
                context,
                "POST",
                "/api/communications/actions/$actionId/claim",
                null,
            )
            if (response != null) {
                return response.optBoolean("claimed", false) && response.optString("status") == "dispatching"
            }
        }
        return false
    }

'''
    replace_once(
        path,
        "    fun fetchPendingCommunicationActions(context: Context): JSONArray {\n",
        helper + "    fun fetchPendingCommunicationActions(context: Context): JSONArray {\n",
    )
    replace_once(
        path,
        '''    fun markActionExecuted(context: Context, actionId: Long): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val key = "action_done_$actionId"
        if (prefs.getBoolean(key, false)) return false
        prefs.edit().putBoolean(key, true).apply()
        return true
    }
''',
        '''    fun markActionExecuted(context: Context, actionId: Long): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val key = "action_done_$actionId"
        if (prefs.getBoolean(key, false)) return false
        return prefs.edit().putBoolean(key, true).commit()
    }
''',
    )

    old_store = '''    fun storeActionEvidence(
        context: Context,
        actionId: Long,
        status: String,
        externalRef: String,
        details: JSONObject = JSONObject(),
    ) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(
                "action_evidence_$actionId",
                JSONObject()
                    .put("status", status)
                    .put("external_ref", externalRef)
                    .put("details", details)
                    .toString(),
            )
            .apply()
    }

'''
    new_store = '''    fun storeActionEvidence(
        context: Context,
        actionId: Long,
        status: String,
        externalRef: String,
        details: JSONObject = JSONObject(),
        failureReason: String = "",
    ) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(
                "action_evidence_$actionId",
                JSONObject()
                    .put("status", status)
                    .put("failure_reason", failureReason.take(1900))
                    .put("external_ref", externalRef)
                    .put("details", details)
                    .toString(),
            )
            .apply()
    }

    fun postOrStoreActionResult(
        context: Context,
        actionId: Long,
        status: String,
        failureReason: String = "",
        externalRef: String = "",
        details: JSONObject = JSONObject(),
    ): Boolean {
        val posted = postActionResult(
            context,
            actionId,
            status,
            failureReason,
            externalRef,
            details,
        )
        if (!posted) {
            storeActionEvidence(
                context,
                actionId,
                status,
                externalRef,
                details,
                failureReason,
            )
        }
        return posted
    }

'''
    replace_once(path, old_store, new_store)
    replace_once(
        path,
        '''        val posted = postActionResult(
            context,
            actionId,
            evidence.optString("status"),
            externalRef = evidence.optString("external_ref"),
            details = evidence.optJSONObject("details") ?: JSONObject(),
        )
''',
        '''        val posted = postActionResult(
            context,
            actionId,
            evidence.optString("status"),
            failureReason = evidence.optString("failure_reason"),
            externalRef = evidence.optString("external_ref"),
            details = evidence.optJSONObject("details") ?: JSONObject(),
        )
''',
    )


def patch_android_worker(root: Path) -> None:
    path = root / (
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaCommunicationPendingWorker.kt"
    )
    replace_once(
        path,
        '''            val action = actions.optJSONObject(index) ?: continue
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
''',
        '''            val action = actions.optJSONObject(index) ?: continue
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
''',
    )
    replace_once(
        path,
        '''            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(applicationContext, actionId)
                VaBackendClient.postActionResult(applicationContext, actionId, "failed", exc.toString())
            }
''',
        '''            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(applicationContext, actionId)
                VaBackendClient.postOrStoreActionResult(
                    applicationContext,
                    actionId,
                    "failed",
                    exc.toString(),
                    "android-sms:$actionId",
                )
            }
''',
    )


def patch_notification_listener(root: Path) -> None:
    path = root / (
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/"
        "VaNotificationListenerService.kt"
    )
    replace_once(
        path,
        '''            val actionId = deviceAction.optLong("id", -1L)
            val replyText = deviceAction.optString("text")
            if (actionId <= 0 || replyText.isBlank() || !VaBackendClient.markActionExecuted(this, actionId)) return@thread
            try {
''',
        '''            val actionId = deviceAction.optLong("id", -1L)
            val replyText = deviceAction.optString("text")
            if (actionId <= 0 || replyText.isBlank()) return@thread
            if (!VaBackendClient.claimCommunicationAction(this, actionId)) return@thread
            if (!VaBackendClient.markActionExecuted(this, actionId)) return@thread
            try {
''',
    )
    replace_once(
        path,
        '''            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(this, actionId)
                VaBackendClient.postActionResult(this, actionId, "failed", exc.toString())
            }
''',
        '''            } catch (exc: Exception) {
                VaBackendClient.clearActionExecuted(this, actionId)
                VaBackendClient.postOrStoreActionResult(
                    this,
                    actionId,
                    "failed",
                    exc.toString(),
                    "remote-input:${sbn.key}",
                    JSONObject().put("package_name", sbn.packageName).put("notification_key", sbn.key),
                )
            }
''',
    )


def patch_sms_status_receiver(root: Path) -> None:
    path = root / (
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsStatusReceiver.kt"
    )
    replace_once(
        path,
        '''                    VaBackendClient.clearActionExecuted(context, actionId)
                    VaBackendClient.postActionResult(
                        context,
                        actionId,
                        "failed",
                        "Android SmsManager reported send failure resultCode=$resultCode for part ${partIndex + 1}/$partCount",
                        "android-sms:$actionId",
                        JSONObject().put("part_index", partIndex).put("part_count", partCount).put("result_code", resultCode),
                    )
''',
        '''                    val multipartUncertain = partCount > 1
                    if (!multipartUncertain) VaBackendClient.clearActionExecuted(context, actionId)
                    VaBackendClient.postOrStoreActionResult(
                        context,
                        actionId,
                        if (multipartUncertain) "creation_uncertain" else "failed",
                        "Android SmsManager reported send failure resultCode=$resultCode for part ${partIndex + 1}/$partCount",
                        "android-sms:$actionId",
                        JSONObject()
                            .put("part_index", partIndex)
                            .put("part_count", partCount)
                            .put("result_code", resultCode)
                            .put("multipart_uncertain", multipartUncertain),
                    )
''',
    )
    replace_once(
        path,
        '''                    VaBackendClient.postActionResult(
                        context,
                        actionId,
                        "delivery_failed",
                        "Carrier delivery receipt failed for part ${partIndex + 1}/$partCount",
                        "android-sms:$actionId",
                        JSONObject().put("part_index", partIndex).put("part_count", partCount).put("result_code", resultCode),
                    )
''',
        '''                    VaBackendClient.postOrStoreActionResult(
                        context,
                        actionId,
                        "delivery_failed",
                        "Carrier delivery receipt failed for part ${partIndex + 1}/$partCount",
                        "android-sms:$actionId",
                        JSONObject().put("part_index", partIndex).put("part_count", partCount).put("result_code", resultCode),
                    )
''',
    )


def patch_autonomous_core(root: Path) -> None:
    path = root / "backend/app/services/autonomous_core.py"
    helper = '''LEGACY_DEVICE_UNCERTAINTY_ERROR = (
    "Android device did not report a definitive dispatch outcome; automatic resend is unsafe"
)


def _device_action_verify_delay(step: VAObjectiveStep, now: datetime) -> timedelta:
    age = max(timedelta(0), now - (step.created_at or now))
    if age < timedelta(minutes=30):
        return timedelta(seconds=15)
    if age < timedelta(hours=24):
        return timedelta(minutes=2)
    if age < timedelta(days=7):
        return timedelta(minutes=15)
    return timedelta(hours=1)


async def _recover_legacy_device_communication_uncertainty(db: AsyncSession, now: datetime) -> int:
    """Reopen only steps terminalized by the historical elapsed-time cutoff."""
    rows = list(
        (
            await db.execute(
                select(VAObjectiveStep)
                .where(
                    VAObjectiveStep.status == "failed",
                    VAObjectiveStep.verification_type == "device_action_verified",
                    VAObjectiveStep.last_error == LEGACY_DEVICE_UNCERTAINTY_ERROR,
                )
                .order_by(VAObjectiveStep.id.asc())
                .limit(100)
            )
        ).scalars()
    )
    recovered = 0
    for step in rows:
        params = _loads(step.parameters_json, {})
        params = params if isinstance(params, dict) else {}
        action_id = int(params.get("communication_action_id") or 0)
        action = await db.get(CommunicationAction, action_id) if action_id > 0 else None
        if action is None or action.status in {"failed", "cancelled", "delivery_failed"}:
            continue
        objective = await db.get(VAObjective, step.objective_id)
        if (
            objective is None
            or objective.status in TERMINAL_OBJECTIVE_STATES
            or objective.status == "needs_user"
        ):
            continue
        step.status = "verifying"
        step.finished_at = None
        step.last_error = ""
        step.run_after = now
        objective.last_error = ""
        await _transition_objective(db, objective, "verifying")
        await write_audit(
            db,
            "device_communication_legacy_uncertainty_reopened",
            entity_type="communication_action",
            entity_id=str(action.id),
            result="deferred",
            details={
                "objective_id": objective.id,
                "step_id": step.id,
                "action_status": action.status,
                "automatic_replay": False,
            },
        )
        recovered += 1
    if recovered:
        await db.commit()
    return recovered


'''
    replace_once(
        path,
        "async def _resume_browser_reconciliation(\n",
        helper + "async def _resume_browser_reconciliation(\n",
    )

    replace_once(
        path,
        '''async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:
    now = utcnow()
    await _recover_legacy_browser_uncertainty(db, now)
    await _recover_legacy_gmail_uncertainty(db, now)
''',
        '''async def verify_ready_steps(db: AsyncSession, *, limit: int = 50) -> int:
    now = utcnow()
    await _recover_legacy_browser_uncertainty(db, now)
    await _recover_legacy_gmail_uncertainty(db, now)
    await _recover_legacy_device_communication_uncertainty(db, now)
''',
    )

    old = '''            if not verified_type:
                if action.status == "pending" and step.created_at <= now - timedelta(minutes=30):
                    step.status = "failed"
                    step.finished_at = now
                    step.last_error = "Android device did not report a definitive dispatch outcome; automatic resend is unsafe"
                    await _transition_objective(
                        db,
                        objective,
                        "blocked_system",
                        reason="Device dispatch outcome is unknown; VAAPP will not risk sending a duplicate message",
                    )
                    continue
                step.run_after = now + timedelta(seconds=15)
                await _transition_objective(db, objective, "verifying")
                continue
'''
    new = '''            if action.status == "delivery_failed":
                step.status = "failed"
                step.finished_at = now
                step.last_error = action.failure_reason or "Device reported definitive delivery failure"
                await _transition_objective(db, objective, "blocked_system", reason=step.last_error)
                continue
            if not verified_type:
                step.status = "verifying"
                step.finished_at = None
                if action.status == "pending":
                    step.last_error = "Waiting for the paired Android device to claim and report this action"
                elif action.status == "creation_uncertain":
                    step.last_error = (
                        "Android reported an ambiguous multipart SMS send outcome; "
                        "automatic resend remains disabled while evidence is reconciled"
                    )
                else:
                    step.last_error = (
                        "Device dispatch was claimed; waiting for durable carrier or RemoteInput evidence"
                    )
                step.run_after = now + _device_action_verify_delay(step, now)
                await _transition_objective(db, objective, "verifying")
                continue
'''
    replace_once(path, old, new)


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/tests/test_v116_device_dispatch_claim.py",
        "backend/tests/test_v116_device_dispatch_claim.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v116_device_dispatch_claim_contract.py",
        "backend/tests/test_v116_device_dispatch_claim_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.16_DEVICE_DISPATCH_CLAIM.md",
        "docs/V1.0.16_DEVICE_DISPATCH_CLAIM.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status_path.write_text(
        '''# VAAPP v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity

Updated: 2026-09-05

## Source of truth

- Repository: `Demon-blood/VAAPP2`
- Branch: `main`
- Verified v1.0.15 source baseline: `2b48b72e720a2e515e346fed253e24c131ae078a`
- Verified v1.0.15 GitHub Actions run: `33967944880` — success
- Verified v1.0.15 prerelease tag: `va-android-115-3-1`
- v1.0.15 release identity: backend `1.0.15`, Android `1.0.15+58`
- v1.0.15 APK SHA-256: `19165c0c6a531a9bf8545ea9ccf6672f35e269d32b1f896a4d5dcb7f5856360d`
- Historical v1.0.14 evidence: source `8557dd449db554528ab7e111d0029faf784c996f`, GitHub Actions run `33961135886`, tag `va-android-114-3-1`.
- Historical v1.0.13 evidence: source `ecaa113d4461a550cb49c6046a42ecf880729346`, GitHub Actions run `33434347111`, tag `va-android-113-4-1`.
- Historical v1.0.12 evidence: source `22a392f1341ef19caf8a761cd7bfa44000fdc08c`, GitHub Actions run `33333446575`, tag `va-android-112-2-1`.
- Historical v1.0.11 evidence: source `221205e82444f9c0bff2589cf3ffc015408e664a`, GitHub Actions run `33331650005`, tag `va-android-111-2-1`.

The operator subsequently reported production deployment and phone smoke testing complete for v1.0.15.

## v1.0.16 maintenance scope

- Background device communication is atomically claimed on the backend before carrier dispatch, with durable claim ownership bound to the paired device in an additive claim table.
- The first `pending -> dispatching` claim creates ownership; the same device can idempotently re-assert it after a lost response, while another device is denied.
- Claimed SMS actions remain visible for evidence reconciliation and same-device claim recovery; the synchronously durable local marker prevents a prior send from being replayed.
- Existing Android stored-evidence reconciliation remains before any new provider send.
- Local `action_done_<id>` protection is made synchronously durable before `SmsManager` is called.
- Missing evidence no longer becomes terminal solely because 30 minutes elapsed.
- Verification cadence backs off while the same action remains VA-owned.
- Historical steps failed by the old elapsed-time cutoff reopen without creating a replacement action.
- Late device evidence completes the original objective.
- Definitive replay-safe device failure can release the claim for safe retry; multipart partial-send ambiguity remains reconciliation-only. Provider/system uncertainty never becomes fake Needs You work.

## Release identity

- Backend: `1.0.16`
- Required Android: `1.0.16`
- Android: `1.0.16+59`

Source publication remains gated by backend tests, Ruff, Flutter analysis/tests, Android signing, and the signed APK build.
''',
        encoding="utf-8",
    )

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.15":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-09-05",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.15",
            "verified_baseline_android_version": "1.0.15+58",
            "verified_maintenance_actions_run_id": 33967944880,
            "verified_baseline_release_tag": "va-android-115-3-1",
            "current_phase_name": "v1.0.16 Device Communication Dispatch Claim & Late-Evidence Continuity",
            "current_version": "1.0.16",
            "current_android_version": "1.0.16+59",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "v116_features": [
                "device communication actions are atomically claimed before provider dispatch with durable paired-device ownership",
                "multipart SMS partial failure remains creation_uncertain and never authorizes whole-message replay",
                "same-device claim retries recover lost claim responses while cross-device claims remain denied",
                "Android stored evidence and a synchronously durable local action marker remain first-class safeguards",
                "elapsed time alone no longer terminalizes unresolved device delivery evidence",
                "device verification cadence backs off while the same action remains VA-owned",
                "historical elapsed-time failures reopen against the original communication action",
                "late sent delivered or RemoteInput evidence completes the original objective",
                "only definitive replay-safe device failure releases a dispatch claim for retry",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    invariant = (
        "missing device dispatch evidence never authorizes cross-device replay; same-device claim recovery still requires the durable local no-send marker"
    )
    if invariant not in invariants:
        invariants.append(invariant)
    state["invariants"] = invariants
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff_path = root / "VAAPP_PROJECT_HANDOFF.md"
    handoff = read_text(handoff_path)
    current = (
        "Current candidate: **v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity**."
    )
    if current not in handoff:
        raise RuntimeError("unexpected VAAPP_PROJECT_HANDOFF.md baseline")
    marker = "## Product objective\n"
    if marker not in handoff:
        raise RuntimeError("handoff product-objective marker is missing")
    suffix = marker + handoff.split(marker, 1)[1]
    prefix = '''# VAAPP project handoff

Updated: 2026-09-05
Repository: `Demon-blood/VAAPP2`
Branch: `main`

## Verified source of truth

The verified maintenance baseline for this release is commit `2b48b72e720a2e515e346fed253e24c131ae078a` (`v1.0.15 — Generic Browser Late-Evidence Recovery & Objective Continuity`). GitHub Actions run `33967944880` completed successfully end-to-end with 392 backend tests, Ruff gates, Flutter analysis/tests, Android signing, signed APK build, source verification, and prerelease publication under tag `va-android-115-3-1`.

Verified v1.0.15 release identity: backend `1.0.15` / Android `1.0.15+58`. APK SHA-256: `19165c0c6a531a9bf8545ea9ccf6672f35e269d32b1f896a4d5dcb7f5856360d`. The operator subsequently reported production deployment and phone smoke testing complete.

Historical v1.0.14 source remains `8557dd449db554528ab7e111d0029faf784c996f` with successful Actions run `33961135886` and tag `va-android-114-3-1`. Historical v1.0.13 source remains `ecaa113d4461a550cb49c6046a42ecf880729346` with successful Actions run `33434347111` and tag `va-android-113-4-1`. Historical v1.0.12 source remains `22a392f1341ef19caf8a761cd7bfa44000fdc08c` with successful Actions run `33333446575` and tag `va-android-112-2-1`. Historical v1.0.11 source remains `221205e82444f9c0bff2589cf3ffc015408e664a` with successful Actions run `33331650005` and tag `va-android-111-2-1`.

Original production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.

## Current maintenance candidate

Backend `1.0.16` / Android `1.0.16+59`.

Current candidate: **v1.0.16 — Device Communication Dispatch Claim & Late-Evidence Continuity**.

v1.0.16 makes a durable paired-device dispatch claim the cross-device at-most-once boundary for device communication actions. Android must claim the original action before SMS carrier dispatch; the same device may idempotently re-assert a lost claim response, another device is denied, local evidence reconciliation still runs before any send, and the local execution marker is synchronously persisted before the provider boundary. Missing callbacks remain VA-owned with backoff instead of terminalizing after 30 minutes, and historical elapsed-time failures can reopen against the same action when late device evidence arrives. Definitive replay-safe device failures may release the claim for safe retry, while multipart partial-send ambiguity stays VA-owned and non-replayable.

The guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass.

Next work after the v1.0.16 gate is green: **v1.x maintenance and real-world hardening**.

'''
    handoff_path.write_text(prefix + suffix, encoding="utf-8")


def patch_legacy_v070_android_contract(root: Path) -> None:
    """Advance the historical Android bridge contract to durable v1.0.16 result reporting."""
    path = root / "backend/tests/test_v070_android_communications_contract.py"
    replace_once(
        path,
        '    assert "postActionResult" in notification\n',
        (
            '    assert "storeActionEvidence" in notification\n'
            '    assert "postOrStoreActionResult" in notification\n'
        ),
    )


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.15"\nREQUIRED_ANDROID_VERSION = "1.0.15"\n',
        'APP_VERSION = "1.0.16"\nREQUIRED_ANDROID_VERSION = "1.0.16"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.15"', 'version = "1.0.16"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.15+58", "version: 1.0.16+59")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.15';\nconst String minimumBackendVersion = '1.0.15';\n",
        "const String appRelease = '1.0.16';\nconst String minimumBackendVersion = '1.0.16';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.15"', 'APP_VERSION = "1.0.16"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.15"', 'REQUIRED_ANDROID_VERSION = "1.0.16"'),
        ('version = "1.0.15"', 'version = "1.0.16"'),
        ('version: 1.0.15+58', 'version: 1.0.16+59'),
        ("appRelease = '1.0.15'", "appRelease = '1.0.16'"),
        ("minimumBackendVersion = '1.0.15'", "minimumBackendVersion = '1.0.16'"),
        ('APP_VERSION == "1.0.15"', 'APP_VERSION == "1.0.16"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v116_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.16")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    tracked = [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
    untracked = [
        line
        for line in run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
        if line
    ]
    changed = sorted(set(tracked + untracked))
    if not changed:
        raise RuntimeError("patch produced no changes")
    forbidden = [name for name in changed if name.startswith(".github/workflows/")]
    if forbidden:
        raise RuntimeError(f"workflow files changed unexpectedly: {forbidden}")
    required = {
        "backend/app/models/entities.py",
        "backend/app/services/communications_service.py",
        "backend/app/services/autonomous_core.py",
        "backend/app/api/routes.py",
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt",
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaCommunicationPendingWorker.kt",
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaNotificationListenerService.kt",
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsStatusReceiver.kt",
        "backend/tests/test_v070_android_communications_contract.py",
        "backend/tests/test_v116_device_dispatch_claim.py",
        "backend/tests/test_v116_device_dispatch_claim_contract.py",
        "docs/V1.0.16_DEVICE_DISPATCH_CLAIM.md",
        "backend/app/core/version.py",
        "backend/pyproject.toml",
        "android/pubspec.yaml",
        "android/lib/release_contract.dart",
        "STATUS.md",
        "VAAPP_PROJECT_STATE.json",
        "VAAPP_PROJECT_HANDOFF.md",
    }
    missing = sorted(required.difference(changed))
    if missing:
        raise RuntimeError(f"required v1.0.16 changes missing from diff: {missing}")

    legacy_android_contract = read_text(
        root / "backend/tests/test_v070_android_communications_contract.py"
    )
    for marker in ("storeActionEvidence", "postOrStoreActionResult"):
        if marker not in legacy_android_contract:
            raise RuntimeError(f"v0.7 Android communications compatibility marker missing: {marker}")

    models = read_text(root / "backend/app/models/entities.py")
    service = read_text(root / "backend/app/services/communications_service.py")
    core = read_text(root / "backend/app/services/autonomous_core.py")
    routes = read_text(root / "backend/app/api/routes.py")
    client = read_text(
        root / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaBackendClient.kt"
    )
    worker = read_text(
        root
        / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaCommunicationPendingWorker.kt"
    )
    listener = read_text(
        root
        / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaNotificationListenerService.kt"
    )
    sms_status = read_text(
        root / "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/SmsStatusReceiver.kt"
    )
    for marker in (
        "class CommunicationDispatchClaim",
        '__tablename__ = "communication_dispatch_claims"',
        'ForeignKey("communication_actions.id", ondelete="CASCADE")',
    ):
        if marker not in models:
            raise RuntimeError(f"v1.0.16 dispatch-claim model marker missing: {marker}")
    for marker in (
        "async def claim_communication_action",
        "CommunicationDispatchClaim",
        "db.begin_nested()",
        "except IntegrityError",
        'action.status != "pending"',
        'action.status = "dispatching"',
        "communication_action_dispatch_claimed",
        'CommunicationAction.status.in_(["pending", "dispatching", "creation_uncertain"])',
        'dispatchable = action.status == "pending" and channel == "sms"',
        'resumable_claim = action.status == "dispatching" and channel == "sms"',
        '"can_resume_claimed_dispatch": resumable_claim',
        'delete(CommunicationDispatchClaim)',
        '"creation_uncertain"',
    ):
        if marker not in service:
            raise RuntimeError(f"v1.0.16 communication claim marker missing: {marker}")
    for marker in (
        "def _device_action_verify_delay",
        "async def _recover_legacy_device_communication_uncertainty",
        "device_communication_legacy_uncertainty_reopened",
        "await _recover_legacy_device_communication_uncertainty(db, now)",
        "Waiting for the paired Android device to claim and report this action",
        "Device dispatch was claimed; waiting for durable carrier or RemoteInput evidence",
        "Android reported an ambiguous multipart SMS send outcome",
        'if action.status == "delivery_failed":',
    ):
        if marker not in core:
            raise RuntimeError(f"v1.0.16 Autonomous Core marker missing: {marker}")
    if "step.created_at <= now - timedelta(minutes=30)" in core:
        raise RuntimeError("elapsed time must not terminalize device dispatch uncertainty")
    if '@router.post("/api/communications/actions/{action_id}/claim")' not in routes:
        raise RuntimeError("v1.0.16 claim route missing")
    if "fun claimCommunicationAction(context: Context, actionId: Long): Boolean" not in client:
        raise RuntimeError("v1.0.16 Android claim client missing")
    if "repeat(3)" not in client:
        raise RuntimeError("same-device claim response loss must be retryable")
    if 'return prefs.edit().putBoolean(key, true).commit()' not in client:
        raise RuntimeError("local action marker must be synchronously durable before provider dispatch")
    claim = worker.index("VaBackendClient.claimCommunicationAction(applicationContext, actionId)")
    local = worker.index("VaBackendClient.markActionExecuted(applicationContext, actionId)")
    send = worker.index("VaSms.send(applicationContext, target, text, actionId)")
    if not claim < local < send:
        raise RuntimeError("Android dispatch ordering must remain claim -> local marker -> provider send")
    carrier = worker.index("VaSms.repostEvidenceIfAvailable(applicationContext, actionId)")
    stored = worker.index(
        "VaBackendClient.repostStoredActionEvidence(applicationContext, actionId)",
        carrier,
    )
    if not carrier < stored < claim:
        raise RuntimeError("SMS carrier evidence must reconcile before stored failures and provider claim")
    if 'val channel = action.optString("channel")' not in worker:
        raise RuntimeError("v1.0.16 reconciliation-only channel gate missing")
    for marker in (
        "fun postOrStoreActionResult",
        'failureReason = evidence.optString("failure_reason")',
    ):
        if marker not in client:
            raise RuntimeError(f"durable Android failure-evidence marker missing: {marker}")
    listener_claim = listener.index("VaBackendClient.claimCommunicationAction(this, actionId)")
    listener_local = listener.index("VaBackendClient.markActionExecuted(this, actionId)")
    listener_send = listener.index("replyAction.actionIntent.send(this, 0, fillInIntent)")
    if not listener_claim < listener_local < listener_send:
        raise RuntimeError("RemoteInput dispatch must remain claim -> local marker -> provider side effect")
    if "VaBackendClient.postOrStoreActionResult" not in listener:
        raise RuntimeError("RemoteInput definitive failure must be durably reportable")
    if sms_status.count("VaBackendClient.postOrStoreActionResult") < 2:
        raise RuntimeError("SMS negative carrier evidence must survive backend outages")
    if "val multipartUncertain = partCount > 1" not in sms_status:
        raise RuntimeError("multipart SMS partial failure must remain non-replayable uncertainty")
    if 'if (multipartUncertain) "creation_uncertain" else "failed"' not in sms_status:
        raise RuntimeError("multipart SMS failure classification marker missing")
    if 'APP_VERSION = "1.0.16"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.16 backend version missing")
    if "version: 1.0.16+59" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.16 Android version missing")
    status = read_text(root / "STATUS.md")
    state = read_text(root / "VAAPP_PROJECT_STATE.json")
    handoff = read_text(root / "VAAPP_PROJECT_HANDOFF.md")
    for historical in ("v1.0.15", "v1.0.14", "v1.0.13", "v1.0.12", "v1.0.11"):
        if historical not in status:
            raise RuntimeError(f"historical status evidence missing: {historical}")
    if '"verified_baseline_actions_run": 41' not in state:
        raise RuntimeError("original production v1 baseline run was not preserved")
    if '"verified_baseline_actions_conclusion": "success"' not in state:
        raise RuntimeError("original production v1 baseline conclusion was not preserved")
    if "GitHub Actions run #41" not in handoff:
        raise RuntimeError("original production v1 handoff evidence was not preserved")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    verify_bundle()
    verify_repo(root)
    patch_models(root)
    patch_communications_service(root)
    patch_routes(root)
    patch_android_client(root)
    patch_android_worker(root)
    patch_notification_listener(root)
    patch_sms_status_receiver(root)
    patch_autonomous_core(root)
    write_new_files(root)
    patch_legacy_v070_android_contract(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.16 source patch prepared. Changed files:")
    tracked = run_git(root, "diff", "--name-only").splitlines()
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    for name in sorted(set(tracked + untracked)):
        if name:
            print(f"  {name}")


if __name__ == "__main__":
    main()
