from datetime import datetime
from decimal import Decimal
from typing import Any

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
    account_scope: str
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
