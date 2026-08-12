from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class PairDeviceRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=120)
    pairing_secret: str
    fcm_token: str | None = None


class PairDeviceResponse(BaseModel):
    device_token: str


class DashboardResponse(BaseModel):
    open_tasks: int
    action_emails: int
    unpaid_bills: int
    payments_requiring_action: int
    connected_services: dict[str, bool]


class ConnectionStartResponse(BaseModel):
    authorization_url: str


class TaskResponse(BaseModel):
    id: int
    title: str
    description: str
    source_type: str
    source_id: str | None
    due_at: datetime | None
    priority: str
    status: str
    requires_approval: bool

    model_config = {"from_attributes": True}


class EmailResponse(BaseModel):
    id: int
    provider_message_id: str
    sender: str
    subject: str
    snippet: str
    received_at: datetime | None
    category: str
    priority: str
    action_required: bool
    status: str
    analysis_json: str

    model_config = {"from_attributes": True}


class BillResponse(BaseModel):
    id: int
    creditor_name: str
    iban: str | None
    amount: Decimal
    currency: str
    due_at: datetime | None
    reference: str
    invoice_number: str
    account_scope: str
    status: str
    risk_reason: str

    model_config = {"from_attributes": True}


class FinancialRecordResponse(BaseModel):
    id: int
    source_message_id: str
    record_type: str
    provider_name: str
    description: str
    order_number: str
    amount: Decimal | None
    currency: str
    occurred_at: datetime | None
    status: str
    account_scope: str
    subscription_id: int | None
    matched_bank_account_id: int | None
    matched_transaction_id: str
    matched_at: datetime | None

    model_config = {"from_attributes": True}


class AccountResponse(BaseModel):
    id: int
    name: str
    iban: str
    account_scope: str
    currency: str
    current_balance: Decimal | None
    available_balance: Decimal | None
    safety_reserve: Decimal
    enabled_for_payments: bool
    last_synced_at: datetime | None

    model_config = {"from_attributes": True}


class PaymentResponse(BaseModel):
    id: int
    bill_id: int
    amount: Decimal
    currency: str
    status: str
    authorization_url: str | None
    requires_user_action: bool
    failure_reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StartBankAuthRequest(BaseModel):
    institution_country: str = Field(min_length=2, max_length=2)
    institution_name: str
    psu_type: str = "personal"


class CreatePaymentRequest(BaseModel):
    bill_id: int
    bank_account_id: int


class AutomationDecision(BaseModel):
    category: str
    financial_document_type: Literal["none", "payable_invoice", "paid_receipt", "statement_or_notice"] = "none"
    priority: str = "normal"
    action_required: bool = False
    preserve: bool = False
    archive: bool = False
    trash: bool = False
    labels: list[str] = []
    task: dict[str, Any] | None = None
    bill: dict[str, Any] | None = None
    calendar_event: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None
    support_case: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    subscription: dict[str, Any] | None = None
    archive_attachments: bool = False
    reasoning_summary: str = ""

class CreditorUpsertRequest(BaseModel):
    name: str
    iban: str
    account_scope: str = "personal"
    auto_pay_enabled: bool = False
    max_auto_amount: Decimal = Decimal("0.00")
    normal_min_amount: Decimal | None = None
    normal_max_amount: Decimal | None = None
    notes: str = ""


class AccountPolicyRequest(BaseModel):
    account_scope: Literal["personal", "pro"]
    safety_reserve: Decimal
    enabled_for_payments: bool


class AutomationRuleRequest(BaseModel):
    rule_type: str
    name: str
    conditions: dict[str, Any]
    actions: dict[str, Any]
    enabled: bool = True


class DeviceFcmRequest(BaseModel):
    fcm_token: str


class DiscordMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    channel_id: str | None = None


class GitHubIssueRequest(BaseModel):
    repository: str
    title: str = Field(min_length=1, max_length=256)
    body: str = ""
    labels: list[str] = []


class CommunicationIngestRequest(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    channel: Literal["sms", "whatsapp", "signal", "telegram", "messenger", "call", "notification"]
    provider: str = Field(default="device", max_length=80)
    package_name: str = Field(default="", max_length=255)
    thread_key: str = Field(default="", max_length=255)
    sender: str = ""
    recipient: str = ""
    body: str = Field(default="", max_length=16000)
    direction: Literal["incoming", "outgoing"] = "incoming"
    event_type: str = Field(default="message", max_length=40)
    occurred_at: datetime | None = None
    supports_direct_reply: bool = False
    allow_action: bool = True


class CommunicationBatchRequest(BaseModel):
    events: list[CommunicationIngestRequest] = Field(default_factory=list, max_length=500)


class CommunicationActionResultRequest(BaseModel):
    status: Literal["completed", "failed", "cancelled"]
    failure_reason: str = Field(default="", max_length=2000)


class CommunicationEventResponse(BaseModel):
    id: int
    external_id: str
    channel: str
    provider: str
    package_name: str
    thread_key: str
    sender: str
    recipient: str
    body: str
    direction: str
    event_type: str
    occurred_at: datetime | None
    category: str
    priority: str
    action_required: bool
    protected: bool
    status: str
    decision_json: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CommunicationRuleRequest(BaseModel):
    channel: Literal["call"] = "call"
    contact_key: str = Field(min_length=1, max_length=255)
    disposition: Literal["allow", "silence", "block"] = "allow"
    auto_reply_enabled: bool = False
    source: str = Field(default="manual", max_length=40)


class CommunicationRuleResponse(BaseModel):
    id: int
    channel: str
    contact_key: str
    disposition: str
    auto_reply_enabled: bool
    source: str
    confidence: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BankAutopilotPolicyRequest(BaseModel):
    role: Literal["operating", "savings", "reserve", "tax", "income", "disabled"] = "operating"
    internal_transfers_enabled: bool = False
    target_floor: Decimal = Decimal("0.00")
    target_ceiling: Decimal = Decimal("0.00")
    accept_surplus: bool = False
    monthly_outbound_limit: Decimal = Decimal("5000.00")
    min_transfer_amount: Decimal = Decimal("50.00")


class BankAutopilotPolicyResponse(BaseModel):
    id: int
    bank_account_id: int
    role: str
    internal_transfers_enabled: bool
    target_floor: Decimal
    target_ceiling: Decimal
    accept_surplus: bool
    monthly_outbound_limit: Decimal
    min_transfer_amount: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BudgetEnvelopeRequest(BaseModel):
    account_scope: Literal["personal", "pro"] = "personal"
    category: str = Field(min_length=1, max_length=80)
    monthly_limit: Decimal = Decimal("0.00")
    reserve_target: Decimal = Decimal("0.00")
    income_allocation_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    priority: int = Field(default=50, ge=0, le=100)
    enabled: bool = True


class BudgetEnvelopeResponse(BaseModel):
    id: int
    account_scope: str
    category: str
    monthly_limit: Decimal
    reserve_target: Decimal
    income_allocation_percent: Decimal
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OwnAccountTransferResponse(BaseModel):
    id: int
    source_account_id: int
    destination_account_id: int
    amount: Decimal
    currency: str
    reason: str
    status: str
    authorization_url: str | None
    requires_user_action: bool
    failure_reason: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
