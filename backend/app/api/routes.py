from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.crypto import hash_token, new_token
from app.core.database import get_db
from app.core.settings import get_settings
from app.core.version import APP_VERSION, REQUIRED_ANDROID_VERSION
from app.integrations.ai_client import AIConfigurationError, ai_usage_status, ensure_ai_configured
from app.integrations.enable_banking import EnableBankingConfigurationError, ensure_enable_banking_configured
from app.integrations.cloudflare_api import (
    CloudflareConfigurationError,
    resource_summary as cloudflare_resource_summary,
    verify_cloudflare_connection,
)
from app.integrations.discord_api import (
    DiscordConfigurationError,
    send_discord_message,
    verify_discord_connection,
)
from app.integrations.github_api import (
    GitHubConfigurationError,
    create_issue as github_create_issue,
    dispatch_workflow as github_dispatch_workflow,
    list_workflow_runs as github_list_workflow_runs,
    list_notifications as github_list_notifications,
    list_repositories as github_list_repositories,
    verify_github_connection,
)
from app.integrations.google_api import (
    GoogleConfigurationError,
    complete_google_authorization,
    create_calendar_event,
    create_google_authorization,
    ensure_google_configured,
    send_gmail_message,
)
from app.models.entities import (
    AuditLog,
    AutomationRule,
    BankAccount,
    Bill,
    Creditor,
    Device,
    DocumentRecord,
    ContactRecord,
    EmailMessage,
    OAuthConnection,
    OrderRecord,
    Payment,
    SubscriptionRecord,
    SupportCase,
    Task,
    ServiceConnector,
)
from app.schemas.api import (
    AccountPolicyRequest,
    AccountResponse,
    AutomationDecision,
    AutomationRuleRequest,
    BillResponse,
    ConnectionStartResponse,
    CreatePaymentRequest,
    CreditorUpsertRequest,
    DashboardResponse,
    DeviceFcmRequest,
    DiscordMessageRequest,
    EmailResponse,
    GitHubIssueRequest,
    PairDeviceRequest,
    PairDeviceResponse,
    PaymentResponse,
    StartBankAuthRequest,
    TaskResponse,
)
from app.services.audit import write_audit
from app.services.certificate_service import generate_enable_banking_keypair
from app.services.android_signing import install_repository_signing, repository_signing_status
from app.services.banking_service import (
    auto_pay_eligible_bills,
    complete_bank_connection,
    complete_payment_authorization,
    create_payment_for_bill,
    refresh_all_payments,
    refresh_payment,
    start_bank_connection,
    sync_all_banks,
)
from app.services.action_reconciler import reconcile_action_queue
from app.services.email_processor import sync_gmail
from app.services.document_policy import document_retention_decision
from app.services.operations_service import cleanup_low_value_documents, sync_google_contacts
from app.services.runtime_config import CONFIG_SECTIONS, get_runtime_value, section_status, set_runtime_values
from app.services.automation_engine import run_connector_automation_rules
from app.services.connector_service import (
    CONNECTOR_PRESETS,
    CONNECTOR_TEMPLATES,
    connector_public,
    execute_connector,
    generic_oauth_callback,
    generic_oauth_start,
    list_connectors,
    test_connector,
    upsert_connector,
)

settings = get_settings()
router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@router.get("/api/system/info")
async def system_info() -> dict:
    return {
        "service": settings.app_name,
        "version": APP_VERSION,
        "required_android_version": REQUIRED_ANDROID_VERSION,
        "environment": settings.environment,
        "capabilities": [
            "dashboard",
            "gmail",
            "calendar",
            "tasks",
            "documents",
            "orders",
            "subscriptions",
            "support_cases",
            "payments",
            "banking",
            "service_catalog",
            "universal_connectors",
            "automation_rules",
            "ai_free_tier_budgeting",
            "ai_sender_learning",
            "ai_message_fingerprints",
            "action_center",
            "manual_safe_action_run",
            "task_action_execution",
            "branded_ui",
            "smart_document_retention",
            "document_cleanup",
            "money_live_refresh",
            "phone_deployment",
        ],
    }


@router.post("/api/pair", response_model=PairDeviceResponse)
async def pair_device(payload: PairDeviceRequest, db: AsyncSession = Depends(get_db)) -> PairDeviceResponse:
    if payload.pairing_secret != settings.pairing_secret:
        raise HTTPException(status_code=403, detail="Invalid pairing secret")
    token = new_token(36)
    device = Device(
        name=payload.device_name,
        token_hash=hash_token(token),
        fcm_token=payload.fcm_token,
    )
    db.add(device)
    await db.flush()
    await write_audit(db, "device_paired", entity_type="device", entity_id=str(device.id))
    await db.commit()
    return PairDeviceResponse(device_token=token)


@router.put("/api/device/fcm")
async def update_fcm(
    payload: DeviceFcmRequest,
    device: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    device.fcm_token = payload.fcm_token
    await db.commit()
    return {"updated": True}


@router.get("/api/configuration")
async def configuration_status(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    sections = await section_status(db)
    by_slug = {item["slug"]: item for item in sections}
    google_connected = bool(
        (await db.execute(select(func.count()).select_from(OAuthConnection).where(OAuthConnection.provider == "google"))).scalar()
    )
    bank_count = (await db.execute(select(func.count()).select_from(BankAccount))).scalar() or 0
    return {
        "google_oauth_configured": by_slug["google"]["configured"],
        "google_connected": google_connected,
        "ai_configured": by_slug["ai"]["configured"],
        "enable_banking_configured": by_slug["enable_banking"]["configured"],
        "bank_accounts_connected": bank_count,
        "github_configured": by_slug["github"]["configured"],
        "cloudflare_configured": by_slug["cloudflare"]["configured"],
        "discord_configured": by_slug["discord"]["configured"],
        "google_drive_enabled": google_connected,
        "google_contacts_enabled": google_connected,
        "automation_enabled": settings.automation_enabled,
        "mobile_setup_enabled": True,
    }


@router.get("/api/ai/status")
async def ai_status(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    return await ai_usage_status(db)


@router.get("/api/dashboard", response_model=DashboardResponse)
async def dashboard(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> DashboardResponse:
    open_tasks = (
        await db.execute(select(func.count()).select_from(Task).where(Task.status.in_(["open", "waiting"])))
    ).scalar() or 0
    action_emails = (
        await db.execute(select(func.count()).select_from(EmailMessage).where(EmailMessage.action_required.is_(True)))
    ).scalar() or 0
    unpaid_bills = (
        await db.execute(select(func.count()).select_from(Bill).where(Bill.status.not_in(["paid", "cancelled"])))
    ).scalar() or 0
    payment_actions = (
        await db.execute(
            select(func.count()).select_from(Payment).where(Payment.requires_user_action.is_(True))
        )
    ).scalar() or 0
    google_connected = bool(
        (await db.execute(select(func.count()).select_from(OAuthConnection).where(OAuthConnection.provider == "google"))).scalar()
    )
    bank_connected = bool((await db.execute(select(func.count()).select_from(BankAccount))).scalar())
    ai_configured = bool(await get_runtime_value(db, "ai_api_key") and await get_runtime_value(db, "ai_model"))
    github_configured = bool(await get_runtime_value(db, "github_token"))
    cloudflare_configured = bool(await get_runtime_value(db, "cloudflare_api_token") and await get_runtime_value(db, "cloudflare_account_id"))
    discord_configured = bool(await get_runtime_value(db, "discord_bot_token") and await get_runtime_value(db, "discord_default_channel_id"))
    return DashboardResponse(
        open_tasks=open_tasks,
        action_emails=action_emails,
        unpaid_bills=unpaid_bills,
        payments_requiring_action=payment_actions,
        connected_services={
            "google": google_connected,
            "drive": google_connected,
            "contacts": google_connected,
            "ai": ai_configured,
            "banking": bank_connected,
            "github": github_configured,
            "cloudflare": cloudflare_configured,
            "discord": discord_configured,
        },
    )


@router.get("/api/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[Task]:
    query = select(Task).order_by(Task.due_at.asc().nullslast(), Task.id.desc())
    if status:
        query = query.where(Task.status == status)
    return list((await db.execute(query)).scalars())


@router.patch("/api/tasks/{task_id}/status")
async def set_task_status(
    task_id: int,
    status_value: str = Query(alias="status"),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if status_value not in {"open", "waiting", "completed", "cancelled"}:
        raise HTTPException(status_code=422, detail="Invalid status")
    task.status = status_value
    await write_audit(db, "task_status_changed", entity_type="task", entity_id=str(task.id), details={"status": status_value})
    await db.commit()
    await reconcile_action_queue(db)
    return {"updated": True}


@router.post("/api/tasks/{task_id}/execute")
async def execute_task_action(
    task_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in {"open", "waiting"}:
        return {"executed": False, "status": task.status, "message": "Task is no longer open"}
    if not task.source_id:
        raise HTTPException(status_code=409, detail="This task has no executable source")

    email = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == task.source_id).limit(1)
        )
    ).scalar_one_or_none()
    if email is None:
        raise HTTPException(status_code=409, detail="The source email is no longer available")
    try:
        decision = AutomationDecision.model_validate_json(email.analysis_json or "{}")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="The saved email action is invalid") from exc

    if task.source_type == "email_reply":
        if not decision.reply:
            raise HTTPException(status_code=409, detail="No reply action is stored for this email")
        already_sent = (
            await db.execute(
                select(AuditLog.id).where(
                    AuditLog.event_type == "email_reply_sent",
                    AuditLog.entity_type == "email",
                    AuditLog.entity_id == email.provider_message_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if already_sent is not None:
            task.status = "completed"
            await db.commit()
            await reconcile_action_queue(db)
            return {"executed": False, "action": "email_reply", "message": "Reply was already sent"}
        sent_id = await send_gmail_message(
            db,
            to=str(decision.reply.get("to") or email.sender),
            subject=str(decision.reply.get("subject") or f"Re: {email.subject}"),
            body=str(decision.reply.get("body") or ""),
        )
        task.status = "completed"
        await write_audit(
            db,
            "email_reply_sent",
            entity_type="email",
            entity_id=email.provider_message_id,
            details={"gmail_message_id": sent_id, "task_id": task.id, "manual_approval": True},
        )
        await db.commit()
        await reconcile_action_queue(db)
        return {"executed": True, "action": "email_reply", "message": "Reply sent"}

    if task.source_type == "calendar_review":
        if not decision.calendar_event:
            raise HTTPException(status_code=409, detail="No calendar action is stored for this email")
        already_created = (
            await db.execute(
                select(AuditLog.id).where(
                    AuditLog.event_type == "calendar_event_created",
                    AuditLog.entity_type == "email",
                    AuditLog.entity_id == email.provider_message_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if already_created is not None:
            task.status = "completed"
            await db.commit()
            await reconcile_action_queue(db)
            return {"executed": False, "action": "calendar_event", "message": "Calendar event already exists"}
        event_id = await create_calendar_event(db, decision.calendar_event)
        task.status = "completed"
        await write_audit(
            db,
            "calendar_event_created",
            entity_type="email",
            entity_id=email.provider_message_id,
            details={"calendar_event_id": event_id, "task_id": task.id, "manual_approval": True},
        )
        await db.commit()
        await reconcile_action_queue(db)
        return {"executed": True, "action": "calendar_event", "message": "Calendar event created"}

    if task.source_type == "bill_review":
        raise HTTPException(status_code=409, detail="Review and approve this creditor in Money > Bills")

    raise HTTPException(
        status_code=409,
        detail="This task represents a follow-up that cannot be executed safely by a generic button. Complete it manually or use its linked workflow.",
    )


@router.get("/api/emails", response_model=list[EmailResponse])
async def list_emails(
    action_only: bool = False,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[EmailMessage]:
    query = select(EmailMessage).order_by(EmailMessage.received_at.desc().nullslast(), EmailMessage.id.desc()).limit(250)
    if action_only:
        query = query.where(EmailMessage.action_required.is_(True))
    return list((await db.execute(query)).scalars())


@router.get("/api/bills", response_model=list[BillResponse])
async def list_bills(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[Bill]:
    return list((await db.execute(select(Bill).order_by(Bill.due_at.asc().nullslast(), Bill.id.desc()))).scalars())


@router.get("/api/accounts", response_model=list[AccountResponse])
async def list_accounts(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[BankAccount]:
    return list((await db.execute(select(BankAccount).order_by(BankAccount.name))).scalars())


@router.put("/api/accounts/{account_id}/policy", response_model=AccountResponse)
async def update_account_policy(
    account_id: int,
    payload: AccountPolicyRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> BankAccount:
    account = await db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    account.account_scope = payload.account_scope
    account.safety_reserve = payload.safety_reserve
    account.enabled_for_payments = payload.enabled_for_payments
    await write_audit(db, "account_policy_updated", entity_type="bank_account", entity_id=str(account.id))
    await db.commit()
    await db.refresh(account)
    return account


@router.get("/api/payments", response_model=list[PaymentResponse])
async def list_payments(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[Payment]:
    return list((await db.execute(select(Payment).order_by(Payment.id.desc()))).scalars())


@router.post("/api/creditors")
async def upsert_creditor(
    payload: CreditorUpsertRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    iban = payload.iban.replace(" ", "").upper()
    creditor = (await db.execute(select(Creditor).where(Creditor.iban == iban))).scalar_one_or_none()
    if creditor is None:
        creditor = Creditor(name=payload.name, iban=iban)
        db.add(creditor)
    creditor.name = payload.name
    creditor.account_scope = payload.account_scope
    creditor.auto_pay_enabled = payload.auto_pay_enabled
    creditor.max_auto_amount = payload.max_auto_amount
    creditor.normal_min_amount = payload.normal_min_amount
    creditor.normal_max_amount = payload.normal_max_amount
    creditor.notes = payload.notes
    await db.flush()
    matching_bills = (
        await db.execute(
            select(Bill).where(
                Bill.iban == iban,
                Bill.status.in_(["detected", "requires_review", "validated"]),
            )
        )
    ).scalars().all()
    for bill in matching_bills:
        bill.creditor_id = creditor.id
        bill.creditor_name = creditor.name
        bill.account_scope = creditor.account_scope
        bill.status = "validated" if creditor.auto_pay_enabled else "requires_review"
        bill.risk_reason = "" if creditor.auto_pay_enabled else "Creditor is not enabled for automatic payment"
    await write_audit(db, "creditor_policy_updated", entity_type="creditor", entity_id=str(creditor.id), details={"matching_bills": len(matching_bills)})
    await db.commit()
    await reconcile_action_queue(db)
    return {"id": creditor.id, "updated": True}


def _rule_public(rule: AutomationRule) -> dict:
    return {
        "id": rule.id,
        "rule_type": rule.rule_type,
        "name": rule.name,
        "conditions": json.loads(rule.conditions_json or "{}"),
        "actions": json.loads(rule.actions_json or "{}"),
        "enabled": rule.enabled,
        "last_run_at": rule.last_run_at,
        "last_result": rule.last_result,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


@router.get("/api/rules")
async def list_rules(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (await db.execute(select(AutomationRule).order_by(AutomationRule.name))).scalars().all()
    return [_rule_public(rule) for rule in rows]


@router.post("/api/rules")
async def create_rule(
    payload: AutomationRuleRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.rule_type not in {"auto_reply", "connector_schedule"}:
        raise HTTPException(status_code=422, detail="Unsupported rule type")
    if payload.rule_type == "connector_schedule":
        connector_slug = str(payload.actions.get("connector_slug") or "")
        operation = str(payload.actions.get("operation") or "")
        connector = (
            await db.execute(select(ServiceConnector).where(ServiceConnector.slug == connector_slug))
        ).scalar_one_or_none()
        if connector is None:
            raise HTTPException(status_code=422, detail="Selected connector does not exist")
        operations = {item["key"] for item in CONNECTOR_TEMPLATES[connector.connector_type].get("operations", [])}
        if operation not in operations:
            raise HTTPException(status_code=422, detail="Selected connector operation is invalid")
        interval = int(payload.conditions.get("interval_minutes") or 0)
        if interval < 1 or interval > 43_200:
            raise HTTPException(status_code=422, detail="Interval must be between 1 and 43,200 minutes")
    rule = AutomationRule(
        rule_type=payload.rule_type,
        name=payload.name,
        conditions_json=json.dumps(payload.conditions, ensure_ascii=False),
        actions_json=json.dumps(payload.actions, ensure_ascii=False),
        enabled=payload.enabled,
    )
    db.add(rule)
    await db.flush()
    await write_audit(db, "automation_rule_created", entity_type="automation_rule", entity_id=str(rule.id))
    await db.commit()
    await db.refresh(rule)
    return _rule_public(rule)


@router.patch("/api/rules/{rule_id}/enabled")
async def set_rule_enabled(
    rule_id: int,
    enabled: bool = Query(...),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rule = await db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.enabled = enabled
    await write_audit(db, "automation_rule_toggled", entity_type="automation_rule", entity_id=str(rule.id), details={"enabled": enabled})
    await db.commit()
    return _rule_public(rule)


@router.delete("/api/rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rule = await db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await write_audit(db, "automation_rule_deleted", entity_type="automation_rule", entity_id=str(rule_id))
    await db.commit()
    return {"deleted": True}


@router.get("/api/google/start", response_model=ConnectionStartResponse)
async def google_start(
    request: Request,
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> ConnectionStartResponse:
    try:
        redirect_uri = str(request.url_for("google_callback"))
        return ConnectionStartResponse(authorization_url=await create_google_authorization(db, redirect_uri))
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/google/callback", response_class=HTMLResponse)
async def google_callback(code: str, state: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    try:
        connection = await complete_google_authorization(db, code, state)
        return HTMLResponse(
            f"<html><body><h2>Google connected</h2><p>{html.escape(connection.account_key)}</p>"
            "<p>You may return to Full-Time VA.</p></body></html>"
        )
    except Exception as exc:
        return HTMLResponse(f"<html><body><h2>Connection failed</h2><p>{html.escape(str(exc))}</p></body></html>", status_code=400)


@router.post("/api/actions/run")
async def run_all_safe_actions(
    request: Request,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run the same safe automation stages used by the background scheduler.

    Provider-mandated authorization is never bypassed; resulting payment authorization
    URLs remain visible through the payment approval queue.
    """
    result: dict[str, object] = {}
    errors: dict[str, str] = {}
    google_connected = bool(
        (
            await db.execute(
                select(func.count()).select_from(OAuthConnection).where(OAuthConnection.provider == "google")
            )
        ).scalar()
    )
    bank_connected = bool((await db.execute(select(func.count()).select_from(BankAccount))).scalar())

    if google_connected:
        try:
            result["gmail_processed"] = await sync_gmail(db, max_messages=250)
        except Exception as exc:
            await db.rollback()
            errors["gmail"] = str(exc)
    else:
        result["gmail_skipped"] = "Google is not connected"

    if bank_connected:
        try:
            result["accounts_synced"] = await sync_all_banks(db)
            result["auto_pay"] = await auto_pay_eligible_bills(
                db, redirect_url=str(request.url_for("payment_authorization_callback"))
            )
            result["payments_refreshed"] = await refresh_all_payments(db)
        except Exception as exc:
            await db.rollback()
            errors["banking"] = str(exc)
    else:
        result["banking_skipped"] = "No bank account is connected"

    if google_connected:
        try:
            result["contacts_synced"] = await sync_google_contacts(db)
        except Exception as exc:
            await db.rollback()
            errors["contacts"] = str(exc)
        try:
            result["document_cleanup"] = await cleanup_low_value_documents(db)
        except Exception as exc:
            await db.rollback()
            errors["documents"] = str(exc)
    else:
        result["contacts_skipped"] = "Google is not connected"
        result["document_cleanup_skipped"] = "Google is not connected"

    try:
        result["connector_rules"] = await run_connector_automation_rules(db)
    except Exception as exc:
        await db.rollback()
        errors["connectors"] = str(exc)

    try:
        result["action_queue"] = await reconcile_action_queue(db)
    except Exception as exc:
        await db.rollback()
        errors["action_queue"] = str(exc)

    result["errors"] = errors
    return result


@router.post("/api/sync/gmail")
async def manual_gmail_sync(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        count = await sync_gmail(db, max_messages=250)
        return {"processed": count}
    except (GoogleConfigurationError, AIConfigurationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/banking/start", response_model=ConnectionStartResponse)
async def banking_start(
    payload: StartBankAuthRequest,
    request: Request,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> ConnectionStartResponse:
    try:
        url = await start_bank_connection(
            db,
            institution_country=payload.institution_country,
            institution_name=payload.institution_name,
            psu_type=payload.psu_type,
            redirect_url=str(request.url_for("banking_callback")),
        )
        return ConnectionStartResponse(authorization_url=url)
    except EnableBankingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/banking/callback", response_class=HTMLResponse)
async def banking_callback(code: str, state: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    try:
        connection = await complete_bank_connection(db, code=code, state=state)
        return HTMLResponse(
            f"<html><body><h2>Bank connected</h2><p>{html.escape(connection.institution_name)}</p>"
            "<p>You may return to Full-Time VA.</p></body></html>"
        )
    except Exception as exc:
        return HTMLResponse(f"<html><body><h2>Connection failed</h2><p>{html.escape(str(exc))}</p></body></html>", status_code=400)


@router.post("/api/sync/banks")
async def manual_bank_sync(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        return {"accounts_synced": await sync_all_banks(db)}
    except EnableBankingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/payments/auto-run")
async def run_automatic_payments_now(
    request: Request,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    bank_count = (await db.execute(select(func.count()).select_from(BankAccount))).scalar() or 0
    if bank_count == 0:
        return {
            "accounts_synced": 0,
            "auto_pay": {"initiated": 0, "skipped": 0, "failed": 0},
            "payments_refreshed": 0,
            "message": "No bank account is connected",
        }
    try:
        synced = await sync_all_banks(db)
        auto_pay = await auto_pay_eligible_bills(
            db, redirect_url=str(request.url_for("payment_authorization_callback"))
        )
        refreshed = await refresh_all_payments(db)
        await reconcile_action_queue(db)
        return {
            "accounts_synced": synced,
            "auto_pay": auto_pay,
            "payments_refreshed": refreshed,
        }
    except EnableBankingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/payments", response_model=PaymentResponse)
async def create_payment(
    payload: CreatePaymentRequest,
    request: Request,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> Payment:
    try:
        return await create_payment_for_bill(db, bill_id=payload.bill_id, bank_account_id=payload.bank_account_id, redirect_url=str(request.url_for("payment_authorization_callback")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EnableBankingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/banking/payment-callback", response_class=HTMLResponse, name="payment_authorization_callback")
async def payment_authorization_callback(
    state: str,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    try:
        payment = await complete_payment_authorization(
            db,
            state=state,
            error=error,
            error_description=error_description,
        )
        if error:
            return HTMLResponse(
                f"<html><body><h2>Payment was not authorized</h2><p>{html.escape(error_description or error)}</p>"
                "<p>You may return to Full-Time VA.</p></body></html>",
                status_code=400,
            )
        status = payment.status if payment is not None else "authorization returned"
        return HTMLResponse(
            f"<html><body><h2>Payment authorization completed</h2><p>Status: {html.escape(str(status))}</p>"
            "<p>You may return to Full-Time VA.</p></body></html>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><h2>Payment status check failed</h2><p>{html.escape(str(exc))}</p></body></html>",
            status_code=400,
        )


@router.post("/api/payments/{payment_id}/refresh", response_model=PaymentResponse)
async def refresh_payment_status(
    payment_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> Payment:
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return await refresh_payment(db, payment)


@router.get("/api/audit")
async def audit_log(
    limit: int = Query(default=200, ge=1, le=1000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list((await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))).scalars())
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "result": row.result,
            "details": json.loads(row.details_json),
            "created_at": row.created_at,
        }
        for row in rows
    ]

@router.post("/api/google/watch")
async def enable_google_watch(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    from app.integrations.google_api import start_gmail_watch

    if not settings.google_pubsub_topic:
        raise HTTPException(status_code=503, detail="GOOGLE_PUBSUB_TOPIC is not configured")
    try:
        return await start_gmail_watch(db, settings.google_pubsub_topic)
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/google/pubsub")
async def google_pubsub(
    request: Request,
    token: str = Query(default=""),
) -> dict:
    if not settings.google_pubsub_verification_token or token != settings.google_pubsub_verification_token:
        raise HTTPException(status_code=403, detail="Invalid Pub/Sub verification token")
    # Acknowledge promptly; the regular scheduler provides a fallback when Pub/Sub delivery is delayed.
    from app.core.database import SessionLocal

    async with SessionLocal() as db:
        await sync_gmail(db, max_messages=250)
    return {"acknowledged": True}

@router.get("/api/documents")
async def list_documents(
    limit: int = Query(default=250, ge=1, le=1000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    # Never expose known boilerplate in the app even if a previous cleanup attempt could
    # not remove the Drive file yet. Cleanup is retried at startup and through Run VA now.
    rows = list(
        (await db.execute(select(DocumentRecord).order_by(DocumentRecord.id.desc()).limit(limit * 2))).scalars()
    )
    visible = [
        row for row in rows
        if document_retention_decision(row.name, row.mime_type, row.size_bytes)[0]
    ][:limit]
    return [
        {
            "id": row.id,
            "name": row.name,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "category": row.category,
            "account_scope": row.account_scope,
            "drive_file_id": row.drive_file_id,
            "drive_web_url": row.drive_web_url,
            "created_at": row.created_at,
        }
        for row in visible
    ]


@router.post("/api/documents/cleanup")
async def cleanup_documents_now(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await cleanup_low_value_documents(db)
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/contacts")
async def list_contacts(
    limit: int = Query(default=500, ge=1, le=2000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list(
        (await db.execute(select(ContactRecord).order_by(ContactRecord.display_name).limit(limit))).scalars()
    )
    return [
        {
            "id": row.id,
            "resource_name": row.resource_name,
            "display_name": row.display_name,
            "emails": json.loads(row.emails_json),
            "phones": json.loads(row.phones_json),
            "organization": row.organization,
            "last_synced_at": row.last_synced_at,
        }
        for row in rows
    ]


@router.post("/api/sync/contacts")
async def manual_contact_sync(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        return {"contacts_synced": await sync_google_contacts(db)}
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/orders")
async def list_orders(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = list(
        (await db.execute(select(OrderRecord).order_by(OrderRecord.id.desc()).limit(500))).scalars()
    )
    return [
        {
            "id": row.id,
            "merchant": row.merchant,
            "order_number": row.order_number,
            "status": row.status,
            "total_amount": row.total_amount,
            "currency": row.currency,
            "expected_delivery_at": row.expected_delivery_at,
            "tracking_url": row.tracking_url,
            "account_scope": row.account_scope,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.get("/api/subscriptions")
async def list_subscriptions(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = list(
        (
            await db.execute(
                select(SubscriptionRecord).order_by(
                    SubscriptionRecord.next_charge_at.asc().nullslast(), SubscriptionRecord.id.desc()
                )
            )
        ).scalars()
    )
    return [
        {
            "id": row.id,
            "provider_name": row.provider_name,
            "description": row.description,
            "amount": row.amount,
            "currency": row.currency,
            "billing_cycle": row.billing_cycle,
            "next_charge_at": row.next_charge_at,
            "status": row.status,
            "account_scope": row.account_scope,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.get("/api/support-cases")
async def list_support_cases(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = list(
        (
            await db.execute(
                select(SupportCase).order_by(
                    SupportCase.next_follow_up_at.asc().nullslast(), SupportCase.id.desc()
                )
            )
        ).scalars()
    )
    return [
        {
            "id": row.id,
            "requester": row.requester,
            "subject": row.subject,
            "category": row.category,
            "priority": row.priority,
            "status": row.status,
            "last_action": row.last_action,
            "next_follow_up_at": row.next_follow_up_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.patch("/api/support-cases/{case_id}/status")
async def update_support_case_status(
    case_id: int,
    status_value: str = Query(alias="status"),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if status_value not in {"open", "waiting", "resolved", "closed"}:
        raise HTTPException(status_code=422, detail="Invalid support case status")
    case = await db.get(SupportCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Support case not found")
    case.status = status_value
    await write_audit(
        db,
        "support_case_status_changed",
        entity_type="support_case",
        entity_id=str(case.id),
        details={"status": status_value},
    )
    await db.commit()
    await reconcile_action_queue(db)
    return {"updated": True}


@router.get("/api/services/status")
async def live_service_status(_: Device = Depends(require_device), db: AsyncSession = Depends(get_db)) -> dict:
    result: dict[str, dict] = {}
    checks = [
        ("github", bool(await get_runtime_value(db, "github_token")), verify_github_connection),
        ("cloudflare", bool(await get_runtime_value(db, "cloudflare_api_token") and await get_runtime_value(db, "cloudflare_account_id")), verify_cloudflare_connection),
        ("discord", bool(await get_runtime_value(db, "discord_bot_token") and await get_runtime_value(db, "discord_default_channel_id")), verify_discord_connection),
    ]
    for name, configured, check in checks:
        if not configured:
            result[name] = {"configured": False, "live": False, "detail": "Configuration required"}
            continue
        try:
            identity = await check(db)
            result[name] = {"configured": True, "live": True, "identity": identity}
        except Exception as exc:
            result[name] = {"configured": True, "live": False, "detail": str(exc)}
    result["custom_connectors"] = await list_connectors(db)
    return result




@router.get("/api/github/android/signing/status")
async def android_signing_status(
    repository: str = Query(default=""),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await repository_signing_status(db, repository)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/github/android/signing/setup")
async def android_signing_setup(
    payload: dict,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await install_repository_signing(db, str(payload.get("repository") or ""))
        await write_audit(
            db,
            "android_release_signing_configured",
            entity_type="github_repository",
            entity_id=result["repository"],
            details={"fingerprint_sha256": result["fingerprint_sha256"]},
        )
        await db.commit()
        return result
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/setup/android-signing", response_class=HTMLResponse)
async def android_signing_bootstrap_page(db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    repository = html.escape(await get_runtime_value(db, "github_default_repository"))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full-Time VA Android signing</title>
<style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:32px auto;padding:0 18px;background:#111;color:#eee}}input,button{{width:100%;box-sizing:border-box;padding:14px;margin:8px 0;border-radius:10px;border:1px solid #666;background:#1d1d22;color:#fff}}button{{background:#b8b5ff;color:#17151f;font-weight:700}}code{{word-break:break-all}}.note{{color:#c9c6d8;line-height:1.5}}</style></head>
<body><h1>Android update signing</h1>
<p class="note">This creates one persistent Android release key, keeps a copy encrypted in the VA database, and installs the signing values as GitHub Actions repository secrets. Do not rotate the key after the first stable-signed APK is installed.</p>
<form method="post" action="/setup/android-signing">
<label>Pairing secret</label><input name="pairing_secret" type="password" required autocomplete="off">
<label>GitHub repository</label><input name="repository" value="{repository}" placeholder="owner/repository" required>
<button type="submit">Initialize persistent signing</button></form>
<p class="note">Your configured GitHub token needs repository <strong>Secrets: Read and write</strong> permission. The pairing secret is checked by this server and is not stored by this page.</p></body></html>"""
    return HTMLResponse(page)


@router.post("/setup/android-signing", response_class=HTMLResponse)
async def android_signing_bootstrap_submit(
    pairing_secret: str = Form(...),
    repository: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    import secrets as secrets_module

    if not secrets_module.compare_digest(pairing_secret, settings.pairing_secret):
        return HTMLResponse("<h1>Invalid pairing secret</h1><p>Return and enter the current Render PAIRING_SECRET value.</p>", status_code=403)
    try:
        result = await install_repository_signing(db, repository)
        await write_audit(
            db,
            "android_release_signing_configured",
            entity_type="github_repository",
            entity_id=result["repository"],
            details={"fingerprint_sha256": result["fingerprint_sha256"]},
        )
        await db.commit()
        fingerprint = html.escape(result["fingerprint_sha256"])
        repo = html.escape(result["repository"])
        return HTMLResponse(f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:32px auto;padding:0 18px;background:#111;color:#eee}}code{{word-break:break-all}}</style></head><body><h1>Signing configured</h1><p>Repository: <strong>{repo}</strong></p><p>Certificate SHA-256:</p><code>{fingerprint}</code><p>The next APK build will use this same persistent release key. Keep this key for all future updates.</p></body></html>""")
    except Exception as exc:
        return HTMLResponse(f"<h1>Signing setup failed</h1><pre>{html.escape(str(exc))}</pre><p>If GitHub says the token lacks permission, recreate the fine-grained token with repository Secrets: Read and write.</p>", status_code=503)


@router.get("/api/github/repositories")
async def github_repositories(_: Device = Depends(require_device), db: AsyncSession = Depends(get_db)) -> list[dict]:
    try:
        return await github_list_repositories(db)
    except GitHubConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/github/notifications")
async def github_notifications(_: Device = Depends(require_device), db: AsyncSession = Depends(get_db)) -> list[dict]:
    # GitHub personal notifications are optional and are not part of VA server
    # health. A failure here must never surface as a global server outage banner.
    try:
        return await github_list_notifications(db)
    except Exception:
        return []


@router.post("/api/github/issues")
async def create_github_issue(
    payload: GitHubIssueRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await github_create_issue(db, payload.repository, payload.title, payload.body, payload.labels)
    except GitHubConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await write_audit(
        db,
        "github_issue_created",
        entity_type="github_issue",
        entity_id=str(result.get("number") or ""),
        details={"repository": payload.repository, "url": result.get("html_url")},
    )
    await db.commit()
    return result


@router.post("/api/github/workflows/android/build")
async def github_android_build(
    payload: dict,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    repository = str(payload.get("repository") or await get_runtime_value(db, "github_default_repository")).strip()
    if not repository:
        raise HTTPException(status_code=422, detail="Configure a default GitHub repository or provide repository")
    try:
        result = await github_dispatch_workflow(
            db,
            repository,
            str(payload.get("workflow_id") or "android-release.yml"),
            str(payload.get("ref") or "main"),
        )
    except (GitHubConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit(db, "android_build_dispatched", entity_type="github_workflow", entity_id=repository, details=result)
    await db.commit()
    return result


@router.get("/api/github/workflows/android/runs")
async def github_android_runs(
    repository: str | None = None,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    target = (repository or await get_runtime_value(db, "github_default_repository")).strip()
    if not target:
        raise HTTPException(status_code=422, detail="Configure a default GitHub repository")
    try:
        return await github_list_workflow_runs(db, target)
    except (GitHubConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/cloudflare/resources")
async def cloudflare_resources(_: Device = Depends(require_device), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await cloudflare_resource_summary(db)
    except CloudflareConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/discord/messages")
async def discord_message(
    payload: DiscordMessageRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await send_discord_message(db, payload.content, payload.channel_id)
    except DiscordConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await write_audit(
        db,
        "discord_message_sent",
        entity_type="discord_message",
        entity_id=str(result.get("id") or ""),
        details={"channel_id": result.get("channel_id")},
    )
    await db.commit()
    return result


@router.post("/api/sync/external")
async def sync_external_services(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    output: dict[str, object] = {}
    try:
        output["contacts_synced"] = await sync_google_contacts(db)
    except Exception as exc:
        output["contacts_error"] = str(exc)
    if await get_runtime_value(db, "github_token"):
        try:
            output["github_notifications"] = len(await github_list_notifications(db))
        except Exception as exc:
            output["github_error"] = str(exc)
    if await get_runtime_value(db, "cloudflare_api_token") and await get_runtime_value(db, "cloudflare_account_id"):
        try:
            resources = await cloudflare_resource_summary(db)
            output["cloudflare"] = {key: len(value) for key, value in resources.items()}
        except Exception as exc:
            output["cloudflare_error"] = str(exc)
    await write_audit(db, "external_services_synced", details=output)
    await db.commit()
    return output


@router.post("/api/setup/enable-banking/generate-key")
async def generate_enable_banking_certificate(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await generate_enable_banking_keypair(db)
    await write_audit(
        db,
        "enable_banking_key_generated",
        entity_type="service",
        entity_id="enable_banking",
        details={
            "sha256_fingerprint": result["sha256_fingerprint"],
            "valid_until": result["valid_until"],
        },
    )
    await db.commit()
    return result


@router.get("/api/setup/sections")
async def setup_sections(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """Return mobile-renderable configuration forms without returning any saved secret."""
    sections = await section_status(db)
    base = str(settings.public_base_url).rstrip("/")
    for section in sections:
        if section["slug"] == "google":
            section["callback_url"] = f"{base}/api/google/callback"
        elif section["slug"] == "enable_banking":
            section["callback_url"] = f"{base}/api/banking/callback"
    return sections


@router.put("/api/setup/sections/{section_slug}")
async def configure_section(
    section_slug: str,
    payload: dict,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    section = CONFIG_SECTIONS.get(section_slug)
    if section is None:
        raise HTTPException(status_code=404, detail="Unknown setup section")
    allowed = {field["key"] for field in section["fields"]}
    values = {key: value for key, value in payload.items() if key in allowed}
    try:
        await set_runtime_values(db, values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated = next(item for item in await section_status(db) if item["slug"] == section_slug)
    await write_audit(db, "service_configuration_updated", entity_type="service", entity_id=section_slug)
    await db.commit()
    return updated


@router.delete("/api/setup/sections/{section_slug}")
async def disconnect_section(
    section_slug: str,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.models.entities import RuntimeSetting

    section = CONFIG_SECTIONS.get(section_slug)
    if section is None:
        raise HTTPException(status_code=404, detail="Unknown setup section")
    for field in section["fields"]:
        row = await db.get(RuntimeSetting, field["key"])
        if row is not None:
            await db.delete(row)
    if section_slug == "google":
        rows = (await db.execute(select(OAuthConnection).where(OAuthConnection.provider == "google"))).scalars().all()
        for row in rows:
            await db.delete(row)
    await write_audit(db, "service_disconnected", entity_type="service", entity_id=section_slug)
    await db.commit()
    return {"disconnected": True}


@router.post("/api/setup/sections/{section_slug}/test")
async def test_setup_section(
    section_slug: str,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    import httpx

    try:
        if section_slug == "automation":
            return {
                "live": True,
                "auto_pay_enabled": (await get_runtime_value(db, "auto_pay_enabled", "true")).lower() == "true",
                "auto_pay_days_before_due": int(await get_runtime_value(db, "auto_pay_days_before_due", "3")),
                "connector_automation_enabled": (await get_runtime_value(db, "connector_automation_enabled", "true")).lower() == "true",
            }
        if section_slug == "google":
            await ensure_google_configured(db)
            connected = bool((await db.execute(select(func.count()).select_from(OAuthConnection).where(OAuthConnection.provider == "google"))).scalar())
            return {"live": connected, "detail": "OAuth client configured" if not connected else "Google account authorized"}
        if section_slug == "ai":
            await ensure_ai_configured(db)
            base_url = await get_runtime_value(db, "ai_base_url", "https://api.openai.com/v1")
            key = await get_runtime_value(db, "ai_api_key")
            model = await get_runtime_value(db, "ai_model")
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
            return {
                "live": True,
                "model": model,
                "status": response.status_code,
                "usage": await ai_usage_status(db),
                "structured_output": "strict_json_schema"
                if "api.groq.com" in base_url.lower() and model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
                else "json_object",
            }
        if section_slug == "enable_banking":
            from app.integrations.enable_banking import verify_connection
            result = await verify_connection(db)
            return {"live": True, "institutions": len(result.get("aspsps") or result.get("items") or []) if isinstance(result, dict) else 0}
        if section_slug == "github":
            return {"live": True, "identity": await verify_github_connection(db)}
        if section_slug == "cloudflare":
            return {"live": True, "identity": await verify_cloudflare_connection(db)}
        if section_slug == "discord":
            return {"live": True, "identity": await verify_discord_connection(db)}
        raise HTTPException(status_code=404, detail="Unknown setup section")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/connectors/templates")
async def connector_templates(_: Device = Depends(require_device)) -> list[dict]:
    return [
        {"type": connector_type, **template}
        for connector_type, template in sorted(CONNECTOR_TEMPLATES.items())
    ]


@router.get("/api/connectors/presets")
async def connector_presets(_: Device = Depends(require_device)) -> list[dict]:
    return sorted(CONNECTOR_PRESETS, key=lambda item: (item["category"], item["title"]))


@router.get("/api/connectors")
async def connectors_list(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await list_connectors(db)


@router.put("/api/connectors/{slug}")
async def connector_configure(
    slug: str,
    payload: dict,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        connector = await upsert_connector(
            db,
            slug=slug,
            display_name=str(payload.get("display_name") or slug),
            connector_type=str(payload.get("connector_type") or ""),
            config=dict(payload.get("config") or {}),
            category=str(payload.get("category") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit(db, "connector_configured", entity_type="connector", entity_id=str(connector.id), details={"slug": slug, "type": connector.connector_type})
    await db.commit()
    return connector_public(connector)


@router.post("/api/connectors/{slug}/test")
async def connector_test(
    slug: str,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    connector = (await db.execute(select(ServiceConnector).where(ServiceConnector.slug == slug))).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    try:
        result = await test_connector(db, connector)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await write_audit(db, "connector_test_passed", entity_type="connector", entity_id=str(connector.id), details=result)
    await db.commit()
    return {"live": True, "result": result}


@router.post("/api/connectors/{slug}/execute")
async def connector_execute(
    slug: str,
    payload: dict,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    connector = (await db.execute(select(ServiceConnector).where(ServiceConnector.slug == slug))).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    operation = str(payload.get("operation") or "").strip()
    if not operation:
        raise HTTPException(status_code=422, detail="operation is required")
    try:
        result = await execute_connector(db, connector, operation, dict(payload.get("parameters") or {}))
    except Exception as exc:
        await write_audit(db, "connector_execution_failed", entity_type="connector", entity_id=str(connector.id), result="failed", details={"operation": operation, "error": str(exc)})
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await write_audit(db, "connector_executed", entity_type="connector", entity_id=str(connector.id), details={"operation": operation})
    await db.commit()
    return {"success": True, "result": result}


@router.get("/api/connectors/{slug}/oauth/start", response_model=ConnectionStartResponse)
async def connector_oauth_start(
    slug: str,
    request: Request,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> ConnectionStartResponse:
    connector = (await db.execute(select(ServiceConnector).where(ServiceConnector.slug == slug))).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    redirect_uri = str(request.url_for("connector_oauth_callback", slug=slug))
    try:
        url = await generic_oauth_start(db, connector, redirect_uri)
        return ConnectionStartResponse(authorization_url=url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/connectors/{slug}/oauth/callback", response_class=HTMLResponse)
async def connector_oauth_callback(
    slug: str, code: str, state: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    connector = (await db.execute(select(ServiceConnector).where(ServiceConnector.slug == slug))).scalar_one_or_none()
    if connector is None:
        return HTMLResponse("<h2>Connector not found</h2>", status_code=404)
    try:
        await generic_oauth_callback(db, connector, code=code, state=state)
        return HTMLResponse("<html><body><h2>Service connected</h2><p>You may return to Full-Time VA.</p></body></html>")
    except Exception as exc:
        return HTMLResponse(f"<html><body><h2>Connection failed</h2><p>{html.escape(str(exc))}</p></body></html>", status_code=400)


@router.delete("/api/connectors/{slug}")
async def connector_delete(
    slug: str,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    connector = (await db.execute(select(ServiceConnector).where(ServiceConnector.slug == slug))).scalar_one_or_none()
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    connector_id = connector.id
    await db.delete(connector)
    await write_audit(db, "connector_deleted", entity_type="connector", entity_id=str(connector_id))
    await db.commit()
    return {"deleted": True}
