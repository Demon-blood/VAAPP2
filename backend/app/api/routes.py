from __future__ import annotations

import html
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_device
from app.core.crypto import hash_token, new_token
from app.core.database import get_db
from app.core.settings import get_settings
from app.core.version import APP_VERSION, REQUIRED_ANDROID_VERSION
from app.integrations.ai_client import AIConfigurationError, ai_usage_status, ensure_ai_configured
from app.integrations.enable_banking import EnableBankingConfigurationError
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
    create_google_authorization,
    ensure_google_configured,
    send_gmail_message,
)
from app.models.entities import (
    AuditLog,
    AutomationRule,
    BankAccount,
    BankAutopilotPolicy,
    BankTransaction,
    BudgetEnvelope,
    CommunicationAction,
    CommunicationEvent,
    CommunicationRule,
    OwnAccountTransfer,
    Bill,
    Creditor,
    Device,
    DocumentRecord,
    ContactRecord,
    EmailMessage,
    FinancialRecord,
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
    BankAutopilotPolicyRequest,
    BankAutopilotPolicyResponse,
    BudgetEnvelopeRequest,
    BudgetEnvelopeResponse,
    BrowserAuthCodeRequest,
    BrowserCredentialRequest,
    BrowserOperationRequest,
    BrowserPortalRequest,
    CalendarObjectiveRequest,
    CommunicationActionResultRequest,
    CommunicationBatchRequest,
    CommunicationEventResponse,
    CommunicationIngestRequest,
    CommunicationRuleRequest,
    CommunicationRuleResponse,
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
    FinancialRecordResponse,
    GitHubIssueRequest,
    PairDeviceRequest,
    PairDeviceResponse,
    PaymentResponse,
    OwnAccountTransferResponse,
    StartBankAuthRequest,
    TaskResponse,
    UserProfileFactRequest,
)
from app.services.audit import write_audit
from app.services.autonomous_core import (
    get_objective as get_va_objective,
    list_objectives as list_va_objectives,
    record_event as record_va_event,
    run_core_cycle,
    va_overview,
)
from app.services.capability_registry import capability_matrix as va_capability_matrix
from app.services.autonomy_policy import learn_successful_reply
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
from app.services.communications_service import (
    complete_communication_action,
    device_call_policy,
    ingest_communication,
    pending_communication_actions,
)
from app.services.financial_autopilot import (
    complete_own_transfer_authorization,
    ensure_account_autopilot_policies,
    finance_overview,
    refresh_all_own_account_transfers,
    refresh_own_account_transfer,
    run_budget_autopilot,
    sync_bank_transactions,
)
from app.services.bank_statement_import import (
    StatementImportError,
    import_statement_file_bytes,
    list_statement_imports,
    reconcile_statement_transactions_with_bank,
    statement_history_summary,
)
from app.services.investment_service import (
    InvestmentImportError,
    import_revolut_investment_file_bytes,
    investment_history_summary,
)
from app.services.revolut_investment_parser import looks_like_revolut_investment
from app.services.investment_autopilot import (
    complete_kraken_funding_authorization,
    investment_funding_transfer_summary,
    refresh_all_kraken_funding,
    run_kraken_funding_autopilot,
)
from app.services.action_reconciler import reconcile_action_queue
from app.services.email_processor import sync_gmail
from app.services.document_policy import document_retention_decision
from app.services.financial_reconciliation import (
    reconcile_receipts_with_bank_transactions,
    reclassify_existing_nonpayable_bills,
)
from app.services.operations_service import cleanup_low_value_documents, sync_google_contacts
from app.services.relationship_memory import (
    list_relationships as list_relationship_memory,
    reconcile_relationship_memory,
    relationship_detail,
    relationship_memory_status,
)
from app.services.browser_operator import (
    approve_material_operation,
    browser_status,
    evidence_png as browser_evidence_png,
    list_operations as list_browser_operations,
    list_portals as list_browser_portals,
    operation_detail as browser_operation_detail,
    operation_public as browser_operation_public,
    operation_requires_material_decision,
    portal_public as browser_portal_public,
    prepare_browser_operation,
    resume_browser_operation,
    set_portal_credentials,
    submit_auth_code,
    upsert_portal as upsert_browser_portal,
)
from app.services.document_ownership import (
    document_obligation_detail,
    document_ownership_status,
    list_document_obligations,
    list_user_profile_facts,
    reconcile_document_ownership,
    set_user_profile_fact,
)
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
            "calendar_scheduling_agent",
            "relationship_memory",
            "secure_browser_portal_operator",
            "document_forms_deadlines",
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
            "budgeting_autopilot",
            "own_account_transfers",
            "communications_autopilot",
            "sms_management",
            "messaging_notification_management",
            "call_screening",
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
        await db.execute(
            select(func.count()).select_from(Bill).where(
                Bill.status.not_in(["paid", "cancelled", "reclassified_nonpayable"])
            )
        )
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
        await learn_successful_reply(db, message=email, mode="manual_approval")
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
        plan = dict(decision.calendar_event)
        plan.setdefault("operation", "create")
        plan.setdefault("source_message_id", email.provider_message_id)
        plan.setdefault("priority", email.priority or "normal")
        plan.setdefault("avoid_conflicts", True)
        event, created = await record_va_event(
            db,
            event_key=f"task:{task.id}:calendar-plan:v1",
            source_type="email",
            source_id=email.provider_message_id,
            event_type="calendar_event_planned",
            title=f"Schedule: {plan.get('summary') or email.subject or 'calendar event'}",
            payload=plan,
            occurred_at=email.received_at,
        )
        task.status = "waiting"
        task.requires_approval = False
        await write_audit(
            db,
            "calendar_event_queued",
            entity_type="email",
            entity_id=email.provider_message_id,
            details={"va_event_id": event.id, "task_id": task.id, "created": created, "manual_trigger": True},
        )
        await db.commit()
        await reconcile_action_queue(db)
        return {"executed": True, "action": "calendar_event", "message": "Calendar objective queued for verified execution"}

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
    return list(
        (
            await db.execute(
                select(Bill)
                .where(Bill.status != "reclassified_nonpayable")
                .order_by(Bill.due_at.asc().nullslast(), Bill.id.desc())
            )
        ).scalars()
    )


@router.get("/api/financial-records", response_model=list[FinancialRecordResponse])
async def list_financial_records(
    record_type: str | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=1000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[FinancialRecord]:
    query = (
        select(FinancialRecord)
        .order_by(FinancialRecord.occurred_at.desc().nullslast(), FinancialRecord.id.desc())
        .limit(limit)
    )
    if record_type:
        query = query.where(FinancialRecord.record_type == record_type)
    return list((await db.execute(query)).scalars())


@router.post("/api/financial-records/reconcile")
async def reconcile_financial_records_now(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    reclassified = await reclassify_existing_nonpayable_bills(db)
    bank_matches = await reconcile_receipts_with_bank_transactions(db)
    await reconcile_action_queue(db)
    return {"reclassified_bills": reclassified, "bank_matches": bank_matches}


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


@router.get("/api/finance/account-policies", response_model=list[BankAutopilotPolicyResponse])
async def list_finance_account_policies(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[BankAutopilotPolicy]:
    await ensure_account_autopilot_policies(db)
    return list((await db.execute(select(BankAutopilotPolicy).order_by(BankAutopilotPolicy.bank_account_id))).scalars())


@router.put("/api/finance/account-policies/{account_id}", response_model=BankAutopilotPolicyResponse)
async def update_finance_account_policy(
    account_id: int,
    payload: BankAutopilotPolicyRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> BankAutopilotPolicy:
    account = await db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if min(payload.target_floor, payload.target_ceiling, payload.monthly_outbound_limit, payload.min_transfer_amount) < 0:
        raise HTTPException(status_code=422, detail="Financial automation amounts cannot be negative")
    if payload.target_ceiling > 0 and payload.target_ceiling < payload.target_floor:
        raise HTTPException(status_code=422, detail="Target ceiling must be zero (dynamic) or at least the target floor")
    await ensure_account_autopilot_policies(db)
    policy = (
        await db.execute(select(BankAutopilotPolicy).where(BankAutopilotPolicy.bank_account_id == account_id))
    ).scalar_one()
    if payload.internal_transfers_enabled and not account.enabled_for_payments:
        raise HTTPException(
            status_code=422,
            detail="Enable payment execution on this bank account before enabling automatic own-account transfers",
        )
    policy.role = payload.role
    policy.internal_transfers_enabled = payload.internal_transfers_enabled and payload.role != "disabled"
    policy.target_floor = payload.target_floor
    policy.target_ceiling = payload.target_ceiling
    policy.accept_surplus = payload.accept_surplus and payload.role != "disabled"
    policy.monthly_outbound_limit = payload.monthly_outbound_limit
    policy.min_transfer_amount = payload.min_transfer_amount
    await write_audit(
        db,
        "bank_autopilot_policy_updated",
        entity_type="bank_account",
        entity_id=str(account_id),
        details={
            "role": policy.role,
            "internal_transfers_enabled": policy.internal_transfers_enabled,
            "accept_surplus": policy.accept_surplus,
        },
    )
    await db.commit()
    await db.refresh(policy)
    return policy


@router.get("/api/finance/budgets", response_model=list[BudgetEnvelopeResponse])
async def list_budget_envelopes(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[BudgetEnvelope]:
    await finance_overview(db)
    return list((await db.execute(select(BudgetEnvelope).order_by(BudgetEnvelope.account_scope, BudgetEnvelope.priority, BudgetEnvelope.category))).scalars())


@router.post("/api/finance/budgets", response_model=BudgetEnvelopeResponse)
async def upsert_budget_envelope(
    payload: BudgetEnvelopeRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> BudgetEnvelope:
    if payload.monthly_limit < 0 or payload.reserve_target < 0:
        raise HTTPException(status_code=422, detail="Budget amounts cannot be negative")
    if payload.income_allocation_percent < 0 or payload.income_allocation_percent > 100:
        raise HTTPException(status_code=422, detail="Income allocation percentage must be between 0 and 100")
    category = payload.category.strip().lower().replace(" ", "_")[:80]
    row = (
        await db.execute(
            select(BudgetEnvelope).where(
                BudgetEnvelope.account_scope == payload.account_scope,
                BudgetEnvelope.category == category,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = BudgetEnvelope(account_scope=payload.account_scope, category=category)
        db.add(row)
    row.monthly_limit = payload.monthly_limit
    row.reserve_target = payload.reserve_target
    row.income_allocation_percent = payload.income_allocation_percent
    row.priority = payload.priority
    row.enabled = payload.enabled
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/api/finance/statements/import")
async def import_financial_history_statements(
    files: list[UploadFile] = File(...),
    account_scope: str = Form(default="personal"),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if account_scope not in {"personal", "pro"}:
        raise HTTPException(status_code=422, detail="Statement account scope must be Personal or Pro")
    if not files or len(files) > 12:
        raise HTTPException(status_code=422, detail="Select between 1 and 12 bank or investment statements per import")
    results: list[dict] = []
    errors: list[dict] = []
    pending: list[tuple[str, bytes]] = []
    for upload in files:
        filename = (upload.filename or "statement.pdf")[:1000]
        content = await upload.read(15 * 1024 * 1024 + 1)
        if len(content) > 15 * 1024 * 1024:
            errors.append({"filename": filename, "error": "File exceeds the 15 MB statement limit"})
            continue
        pending.append((filename, content))
    # Structured XLSX is authoritative. For investments, process account history
    # before P&L so realised rows can be matched to the correct Brokerage/Robo portfolio.
    def _import_rank(item: tuple[str, bytes]) -> tuple[int, int, str]:
        name = item[0].casefold()
        investment = looks_like_revolut_investment(item[0], item[1])
        if investment and "account" in name and name.endswith(".xlsx"):
            return (0, 0, name)
        if investment and "account" in name and name.endswith(".pdf"):
            return (0, 1, name)
        if investment and "pnl" in name and name.endswith(".xlsx"):
            return (0, 2, name)
        if investment and "pnl" in name:
            return (0, 3, name)
        return (1, 0 if name.endswith(".xlsx") else 1, name)

    pending.sort(key=_import_rank)
    for filename, content in pending:
        try:
            if looks_like_revolut_investment(filename, content):
                results.extend(
                    await import_revolut_investment_file_bytes(
                        db,
                        filename=filename,
                        content=content,
                        account_scope=account_scope,
                    )
                )
            else:
                results.extend(
                    await import_statement_file_bytes(
                        db,
                        filename=filename,
                        content=content,
                        fallback_scope=account_scope,
                    )
                )
        except (StatementImportError, InvestmentImportError) as exc:
            errors.append({"filename": filename, "error": str(exc)})
    if not results and errors:
        raise HTTPException(status_code=422, detail={"message": "No statements could be imported", "files": errors})
    return {
        "imported": sum(1 for item in results if not item.get("duplicate")),
        "duplicates": sum(1 for item in results if item.get("duplicate")),
        "files": results,
        "errors": errors,
        "history": await statement_history_summary(db),
        "investments": await investment_history_summary(db),
    }


@router.get("/api/finance/statements")
async def get_financial_history_statements(
    limit: int = Query(default=100, ge=1, le=500),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return {
        "history": await statement_history_summary(db),
        "imports": await list_statement_imports(db, limit=limit),
    }


@router.get("/api/finance/investments")
async def get_finance_investments(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    result = await investment_history_summary(db)
    result["funding_transfers"] = await investment_funding_transfer_summary(db)
    result["autopilot"] = {
        "kraken_monthly_target_eur": await get_runtime_value(db, "kraken_monthly_target_eur", "0"),
        "kraken_auto_fund_enabled": (await get_runtime_value(db, "kraken_auto_fund_enabled", "false")).lower() == "true",
        "kraken_auto_trade_enabled": (await get_runtime_value(db, "kraken_auto_trade_enabled", "false")).lower() == "true",
        "kraken_default_pair": await get_runtime_value(db, "kraken_default_pair", "XBTEUR"),
        "kraken_max_auto_trade_eur": await get_runtime_value(db, "kraken_max_auto_trade_eur", "250"),
        "revolut_execution": "revolut_managed_schedule",
    }
    try:
        from app.integrations.kraken_api import get_eur_valued_balances

        result["kraken"] = await get_eur_valued_balances(db)
    except Exception as exc:
        message = str(exc)
        result["kraken"] = {
            "status": "configuration_required" if "key and secret are required" in message.lower() else "unavailable",
            "estimated_total_eur": "0.00",
            "assets": [],
            "asset_count": 0,
            "unvalued_asset_count": 0,
            "detail": "Configure Kraken in Services/Settings to show live holdings."
            if "key and secret are required" in message.lower()
            else message[:240],
        }
    return result


@router.get("/api/finance/overview")
async def get_finance_overview(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    return await finance_overview(db)


@router.get("/api/finance/transfers", response_model=list[OwnAccountTransferResponse])
async def list_own_account_transfers(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[OwnAccountTransfer]:
    return list((await db.execute(select(OwnAccountTransfer).order_by(OwnAccountTransfer.id.desc()).limit(250))).scalars())


@router.post("/api/finance/transfers/{transfer_id}/refresh", response_model=OwnAccountTransferResponse)
async def refresh_internal_transfer(
    transfer_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> OwnAccountTransfer:
    transfer = await db.get(OwnAccountTransfer, transfer_id)
    if transfer is None:
        raise HTTPException(status_code=404, detail="Transfer not found")
    return await refresh_own_account_transfer(db, transfer)


@router.post("/api/finance/autopilot/run")
async def run_finance_autopilot_now(
    request: Request,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        accounts_synced = await sync_all_banks(db)
        transactions = await sync_bank_transactions(db)
        statement_reconciliation = await reconcile_statement_transactions_with_bank(db)
        transfer_refresh = await refresh_all_own_account_transfers(db)
        kraken_refresh = await refresh_all_kraken_funding(db)
        budget = await run_budget_autopilot(
            db, redirect_url=str(request.url_for("own_transfer_authorization_callback"))
        )
        kraken = await run_kraken_funding_autopilot(
            db, redirect_url=str(request.url_for("kraken_funding_authorization_callback"))
        )
        return {
            "accounts_synced": accounts_synced,
            "transactions": transactions,
            "statement_reconciliation": statement_reconciliation,
            "transfer_refresh": transfer_refresh,
            "kraken_refresh": kraken_refresh,
            "budget": budget,
            "kraken_investment": kraken,
        }
    except EnableBankingConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


@router.post("/api/communications/ingest")
async def communication_ingest(
    payload: CommunicationIngestRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await ingest_communication(db, payload)


@router.post("/api/communications/batch")
async def communication_batch_ingest(
    payload: CommunicationBatchRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    processed = 0
    duplicates = 0
    for event in payload.events:
        result = await ingest_communication(db, event)
        processed += 1
        duplicates += int(result.get("duplicate") is True)
    return {"processed": processed, "duplicates": duplicates}


@router.get("/api/communications/events", response_model=list[CommunicationEventResponse])
async def list_communication_events(
    limit: int = Query(default=200, ge=1, le=1000),
    channel: str | None = None,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[CommunicationEvent]:
    query = select(CommunicationEvent).order_by(CommunicationEvent.occurred_at.desc().nullslast(), CommunicationEvent.id.desc()).limit(limit)
    if channel:
        query = query.where(CommunicationEvent.channel == channel)
    return list((await db.execute(query)).scalars())


@router.post("/api/communications/actions/{action_id}/result")
async def communication_action_result(
    action_id: int,
    payload: CommunicationActionResultRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        action = await complete_communication_action(
            db,
            action_id,
            status=payload.status,
            failure_reason=payload.failure_reason,
            external_ref=payload.external_ref,
            details=payload.details,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": action.id, "status": action.status}


@router.get("/api/communications/actions/pending")
async def communication_pending_actions(
    limit: int = Query(default=50, ge=1, le=200),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return {"actions": await pending_communication_actions(db, limit=limit)}


@router.get("/api/communications/threads")
async def communication_threads(
    limit: int = Query(default=100, ge=1, le=500),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.communication_ownership import communication_threads_overview

    rows = await communication_threads_overview(db, limit=limit)
    return {
        "threads": rows,
        "waiting_on_counterparty": sum(1 for row in rows if row.get("waiting_on") == "counterparty"),
        "owned": sum(1 for row in rows if row.get("objective_id") is not None),
    }


@router.get("/api/communications/device-policy")
async def communication_device_policy(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    return await device_call_policy(db)


@router.get("/api/communications/rules", response_model=list[CommunicationRuleResponse])
async def list_communication_rules(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> list[CommunicationRule]:
    return list((await db.execute(select(CommunicationRule).order_by(CommunicationRule.channel, CommunicationRule.contact_key))).scalars())


@router.post("/api/communications/rules", response_model=CommunicationRuleResponse)
async def upsert_communication_rule(
    payload: CommunicationRuleRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> CommunicationRule:
    contact_key = "".join(character for character in payload.contact_key.strip() if character.isdigit() or character == "+")
    if not contact_key:
        raise HTTPException(status_code=422, detail="A phone number is required")
    row = (
        await db.execute(
            select(CommunicationRule).where(
                CommunicationRule.channel == payload.channel, CommunicationRule.contact_key == contact_key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = CommunicationRule(channel=payload.channel, contact_key=contact_key)
        db.add(row)
    row.disposition = payload.disposition
    row.auto_reply_enabled = payload.auto_reply_enabled
    row.source = payload.source
    row.confidence = 1
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/api/communications/rules/{rule_id}")
async def delete_communication_rule(
    rule_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(CommunicationRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Communication rule not found")
    await db.delete(row)
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


@router.get("/api/va/overview")
async def fulltime_va_overview(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await va_overview(db)


@router.get("/api/va/capabilities")
async def fulltime_va_capabilities(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await va_capability_matrix(db)


@router.get("/api/va/objectives")
async def fulltime_va_objectives(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_va_objectives(db, status=status, limit=limit)


@router.get("/api/va/objectives/{objective_id}")
async def fulltime_va_objective_detail(
    objective_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await get_va_objective(db, objective_id)
    if row is None:
        raise HTTPException(status_code=404, detail="VA objective not found")
    return row


@router.post("/api/va/objectives/{objective_id}/recheck")
async def fulltime_va_objective_recheck(
    objective_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if await get_va_objective(db, objective_id) is None:
        raise HTTPException(status_code=404, detail="VA objective not found")
    await run_core_cycle(db)
    row = await get_va_objective(db, objective_id)
    assert row is not None
    return row


@router.post("/api/va/run")
async def run_fulltime_va_core(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cycle = await run_core_cycle(db, create_manual_run=True)
    return {"cycle": cycle, "overview": await va_overview(db)}


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
            result["bank_transactions"] = await sync_bank_transactions(db)
            result["statement_reconciliation"] = await reconcile_statement_transactions_with_bank(db)
            result["receipt_reconciliation"] = await reconcile_receipts_with_bank_transactions(db)
            result["auto_pay"] = await auto_pay_eligible_bills(
                db, redirect_url=str(request.url_for("payment_authorization_callback"))
            )
            result["payments_refreshed"] = await refresh_all_payments(db)
            result["internal_transfers_refreshed"] = await refresh_all_own_account_transfers(db)
            result["budget_autopilot"] = await run_budget_autopilot(
                db, redirect_url=str(request.url_for("own_transfer_authorization_callback"))
            )
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
        result["financial_reclassification"] = await reclassify_existing_nonpayable_bills(db)
    except Exception as exc:
        await db.rollback()
        errors["financial_reclassification"] = str(exc)

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
        synced = await sync_all_banks(db)
        transactions = await sync_bank_transactions(db)
        statement_reconciliation = await reconcile_statement_transactions_with_bank(db)
        receipts = await reconcile_receipts_with_bank_transactions(db)
        transfers = await refresh_all_own_account_transfers(db)
        return {
            "accounts_synced": synced,
            "transactions": transactions,
            "statement_reconciliation": statement_reconciliation,
            "receipt_reconciliation": receipts,
            "internal_transfers_refreshed": transfers,
        }
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
        receipt_reconciliation = await reconcile_receipts_with_bank_transactions(db)
        auto_pay = await auto_pay_eligible_bills(
            db, redirect_url=str(request.url_for("payment_authorization_callback"))
        )
        refreshed = await refresh_all_payments(db)
        await reconcile_action_queue(db)
        return {
            "accounts_synced": synced,
            "receipt_reconciliation": receipt_reconciliation,
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


@router.get("/api/banking/transfer-callback", response_class=HTMLResponse, name="own_transfer_authorization_callback")
async def own_transfer_authorization_callback(
    state: str,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    try:
        transfer = await complete_own_transfer_authorization(
            db, state=state, error=error, error_description=error_description
        )
        if error:
            return HTMLResponse(
                f"<html><body><h2>Transfer was not authorized</h2><p>{html.escape(error_description or error)}</p>"
                "<p>You may return to Full-Time VA.</p></body></html>",
                status_code=400,
            )
        status = transfer.status if transfer is not None else "authorization returned"
        return HTMLResponse(
            f"<html><body><h2>Transfer authorization completed</h2><p>Status: {html.escape(str(status))}</p>"
            "<p>You may return to Full-Time VA.</p></body></html>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><h2>Transfer status check failed</h2><p>{html.escape(str(exc))}</p></body></html>",
            status_code=400,
        )


@router.get("/api/banking/investment-callback", response_class=HTMLResponse, name="kraken_funding_authorization_callback")
async def kraken_funding_authorization_callback(
    state: str,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    try:
        transfer = await complete_kraken_funding_authorization(
            db, state=state, error=error, error_description=error_description
        )
        if error:
            return HTMLResponse(
                f"<html><body><h2>Investment funding was not authorized</h2><p>{html.escape(error_description or error)}</p>"
                "<p>You may return to Full-Time VA.</p></body></html>",
                status_code=400,
            )
        status = transfer.status if transfer is not None else "authorization returned"
        return HTMLResponse(
            f"<html><body><h2>Investment funding authorization completed</h2><p>Status: {html.escape(str(status))}</p>"
            "<p>You may return to Full-Time VA.</p></body></html>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><h2>Investment funding status check failed</h2><p>{html.escape(str(exc))}</p></body></html>",
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

@router.get("/api/calendar/status")
async def calendar_ownership_status(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    from app.services.calendar_ownership import calendar_status

    try:
        return await calendar_status(db)
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/calendar/events")
async def calendar_events(
    days: int = Query(default=60, ge=1, le=730),
    limit: int = Query(default=250, ge=1, le=1000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from app.services.calendar_ownership import list_calendar_mirror, sync_calendar

    # First-use recovery: populate the mirror if the scheduler has not run yet.
    rows = await list_calendar_mirror(db, days=days, limit=limit)
    if not rows:
        try:
            await sync_calendar(db, days_back=30, days_forward=max(365, days))
            rows = await list_calendar_mirror(db, days=days, limit=limit)
        except GoogleConfigurationError:
            return []
    return rows


@router.get("/api/calendar/availability")
async def calendar_availability(
    start: str = Query(min_length=8),
    end: str = Query(min_length=8),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.calendar_ownership import find_calendar_conflicts

    try:
        conflicts = await find_calendar_conflicts(db, start=start, end=end)
        return {"available": not conflicts, "conflicts": conflicts, "start": start, "end": end}
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/calendar/sync")
async def calendar_sync_now(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    from app.services.calendar_ownership import sync_calendar

    try:
        return await sync_calendar(db)
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/calendar/objectives")
async def create_calendar_objective(
    payload: CalendarObjectiveRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    data = payload.model_dump()
    event, created = await record_va_event(
        db,
        event_key=f"calendar-request:{payload.idempotency_key}",
        source_type="calendar_request",
        source_id=payload.provider_event_id or payload.idempotency_key,
        event_type="calendar_event_planned",
        title=f"Calendar {payload.operation}: {payload.summary or payload.provider_event_id or 'event'}",
        payload=data,
    )
    return {"event_id": event.id, "created": created, "status": event.status}


@router.post("/api/google/watch")
async def enable_google_watch(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    from app.services.gmail_sync_service import ensure_gmail_watch

    try:
        return await ensure_gmail_watch(db, force=True)
    except (GoogleConfigurationError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/google/mailbox-status")
async def google_mailbox_status(
    _: Device = Depends(require_device), db: AsyncSession = Depends(get_db)
) -> dict:
    from app.services.gmail_sync_service import mailbox_status

    try:
        return await mailbox_status(db)
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/google/pubsub")
async def google_pubsub(
    request: Request,
    token: str = Query(default=""),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.gmail_sync_service import decode_pubsub_notification
    from app.services.workflow_engine import enqueue_job

    async with SessionLocal() as db:
        expected_token = await get_runtime_value(db, "google_pubsub_verification_token", "")
        if not expected_token or token != expected_token:
            raise HTTPException(status_code=403, detail="Invalid Pub/Sub verification token")
        try:
            body = await request.json()
            notification = decode_pubsub_notification(body)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Pub/Sub delivery is acknowledged only after a durable history-sync job is
        # persisted.  Processing happens asynchronously in the workflow worker, so
        # Google does not retry simply because AI/Gmail processing takes longer.
        message_id = notification["pubsub_message_id"] or notification["history_id"]
        job, created = await enqueue_job(
            db,
            job_type="gmail.history.sync",
            payload={
                "email": notification["email"],
                "history_id": notification["history_id"],
                "pubsub_message_id": notification["pubsub_message_id"],
                "publish_time": notification["publish_time"],
            },
            idempotency_key=f"gmail.history.push:{message_id}"[:255],
            priority=8,
            max_attempts=8,
        )
    return {"acknowledged": True, "queued": created, "job_id": job.id}

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
        contacts_synced = await sync_google_contacts(db)
        relationship_result = await reconcile_relationship_memory(db)
        return {"contacts_synced": contacts_synced, "relationship_memory": relationship_result}
    except GoogleConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/relationships/status")
async def relationship_status_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await relationship_memory_status(db)


@router.get("/api/relationships")
async def relationship_list_route(
    limit: int = Query(default=250, ge=1, le=1000),
    q: str = Query(default="", max_length=200),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_relationship_memory(db, limit=limit, query=q)


@router.get("/api/relationships/{relationship_id}")
async def relationship_detail_route(
    relationship_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await relationship_detail(db, relationship_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/relationships/reconcile")
async def relationship_reconcile_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await reconcile_relationship_memory(db)


@router.get("/api/documents/ownership/status")
async def document_ownership_status_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await document_ownership_status(db)


@router.get("/api/documents/obligations")
async def document_obligations_route(
    limit: int = Query(default=250, ge=1, le=1000),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_document_obligations(db, limit=limit)


@router.get("/api/documents/obligations/{obligation_id}")
async def document_obligation_detail_route(
    obligation_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await document_obligation_detail(db, obligation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/documents/reconcile")
async def document_reconcile_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await reconcile_document_ownership(db, limit=100)


@router.get("/api/documents/profile-facts")
async def document_profile_facts_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_user_profile_facts(db)


@router.put("/api/documents/profile-facts")
async def document_profile_fact_update_route(
    payload: UserProfileFactRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await set_user_profile_fact(db, key=payload.key, value=payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/browser/status")
async def browser_operator_status_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await browser_status(db)


@router.get("/api/browser/portals")
async def browser_portal_list_route(
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_browser_portals(db)


@router.post("/api/browser/portals")
async def browser_portal_upsert_route(
    payload: BrowserPortalRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        portal = await upsert_browser_portal(db, **payload.model_dump())
        return browser_portal_public(portal)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/api/browser/portals/{portal_id}/credentials")
async def browser_portal_credentials_route(
    portal_id: int,
    payload: BrowserCredentialRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        row = await set_portal_credentials(
            db,
            portal_id=portal_id,
            username=payload.username,
            password=payload.password,
        )
        return {
            "portal_id": row.portal_id,
            "username_configured": bool(row.username_encrypted),
            "password_configured": bool(row.password_encrypted),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/browser/operations")
async def browser_operation_list_route(
    limit: int = Query(default=100, ge=1, le=500),
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await list_browser_operations(db, limit=limit)


@router.post("/api/browser/operations")
async def browser_operation_create_route(
    payload: BrowserOperationRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        operation = await prepare_browser_operation(
            db,
            idempotency_key=payload.idempotency_key,
            portal_id=payload.portal_id,
            title=payload.title,
            steps=payload.steps,
            verification=payload.verification,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    material_commitment = operation_requires_material_decision(operation)
    event, created = await record_va_event(
        db,
        event_key=f"browser-request:{payload.idempotency_key}",
        source_type="browser_request",
        source_id=str(operation.id),
        event_type="browser_portal_operation_planned",
        title=payload.title,
        payload={
            "browser_operation_id": operation.id,
            "portal_id": operation.portal_id,
            "goal": payload.goal,
            "priority": payload.priority,
            "risk_level": payload.risk_level,
            "material_commitment": material_commitment,
        },
    )
    await db.commit()
    return {
        "operation": browser_operation_public(operation),
        "event_id": event.id,
        "event_created": created,
        "material_approval_required": material_commitment,
    }


@router.get("/api/browser/operations/{operation_id}")
async def browser_operation_detail_route(
    operation_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await browser_operation_detail(db, operation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/browser/operations/{operation_id}/auth-code")
async def browser_operation_auth_code_route(
    operation_id: int,
    payload: BrowserAuthCodeRequest,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        row = await submit_auth_code(db, operation_id, payload.code)
        return browser_operation_public(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/browser/operations/{operation_id}/resume")
async def browser_operation_resume_route(
    operation_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        row = await resume_browser_operation(db, operation_id)
        return browser_operation_public(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/browser/operations/{operation_id}/approve")
async def browser_operation_approve_route(
    operation_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        row = await approve_material_operation(db, operation_id)
        return browser_operation_public(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/browser/evidence/{evidence_id}.png")
async def browser_evidence_png_route(
    evidence_id: int,
    _: Device = Depends(require_device),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        content = await browser_evidence_png(db, evidence_id)
        return Response(
            content=content,
            media_type="image/png",
            headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"},
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
        if section_slug == "kraken":
            from app.integrations.kraken_api import get_balances, verify_connection as verify_kraken_connection
            identity = await verify_kraken_connection(db)
            balances = await get_balances(db)
            return {
                "live": True,
                "identity": {
                    "apiKeyName": identity.get("apiKeyName") if isinstance(identity, dict) else None,
                    "permissions": identity.get("permissions") if isinstance(identity, dict) else [],
                },
                "assets": len([value for value in balances.values() if value != 0]),
            }
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
