from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

PAYABLE_INVOICE = "payable_invoice"
PAID_RECEIPT = "paid_receipt"
STATEMENT_OR_NOTICE = "statement_or_notice"
NOT_FINANCIAL = "none"

_GPA_RE = re.compile(r"\bGPA\.\d{4}-\d{4}-\d{4}-\d{5}\b", re.I)

# Signals that money is still owed. These are deliberately stronger than a bare
# word such as "invoice" because receipts and completed purchases often reuse an
# invoice/order identifier after payment has already happened.
_PAYABLE_TERMS = (
    "amount due",
    "total due",
    "balance due",
    "payment due",
    "due date",
    "pay by",
    "please pay",
    "please remit",
    "payment request",
    "outstanding balance",
    "outstanding amount",
    "openstaand bedrag",
    "te betalen",
    "vervaldatum",
    "betaal voor",
    "betaling vereist",
    "unpaid",
    "overdue",
    "past due",
    "achterstallig",
    "final reminder",
    "laatste herinnering",
)

# Signals that the purchase/payment has already occurred.
_PAID_TERMS = (
    "receipt",
    "purchase receipt",
    "purchase confirmation",
    "order confirmation",
    "payment confirmation",
    "payment received",
    "payment successful",
    "successful payment",
    "transaction completed",
    "thank you for your purchase",
    "your purchase",
    "you paid",
    "paid with",
    "was charged",
    "we charged",
    "charged to",
    "card purchase",
    "card payment",
    "card transaction",
    "kaartbetaling",
    "kaarttransactie",
    "aankoopbewijs",
    "aankoop bevestigd",
    "bestelbevestiging",
    "betalingsbevestiging",
    "betaling ontvangen",
    "betaling geslaagd",
    "betaald met",
    "werd aangerekend",
    "afgeschreven",
)

_STATEMENT_TERMS = (
    "statement",
    "account statement",
    "monthly statement",
    "transaction summary",
    "payment notice",
    "rekeningafschrift",
    "maandoverzicht",
    "transactieoverzicht",
    "betalingsmelding",
)

_RECURRING_TERMS = (
    "subscription",
    "subscription renewal",
    "renewal",
    "recurring",
    "monthly",
    "yearly",
    "annual",
    "abonnement",
    "verlenging",
    "maandelijks",
    "jaarlijks",
)

# Known commerce providers are used as context, never as an unconditional
# whitelist. Explicit payable evidence always wins.
_PROVIDER_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("google commerce", "google play", "payments-noreply@google.com"), "Google Commerce Limited"),
    (("apple.com", "app store", "itunes"), "Apple"),
    (("paypal",), "PayPal"),
    (("amazon",), "Amazon"),
    (("stripe",), "Stripe"),
    (("microsoft store", "microsoft.com"), "Microsoft"),
    (("paddle",), "Paddle"),
)


@dataclass(frozen=True)
class FinancialDocumentAssessment:
    document_type: str
    confidence: float
    provider_name: str
    order_number: str
    recurring: bool
    reasons: tuple[str, ...]

    @property
    def is_nonpayable(self) -> bool:
        return self.document_type in {PAID_RECEIPT, STATEMENT_OR_NOTICE}


def _provider_name(sender: str, subject: str, body: str) -> str:
    display_name, address = parseaddr(sender or "")
    haystack = f"{sender}\n{subject}\n{body[:4000]}".lower()
    for patterns, canonical in _PROVIDER_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            return canonical
    return display_name.strip() or address.strip() or (sender or "").strip()


def _hits(haystack: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in haystack]


def assess_financial_document(
    *,
    sender: str,
    subject: str,
    body: str,
    extraction: dict[str, Any] | None = None,
    bill: dict[str, Any] | None = None,
) -> FinancialDocumentAssessment:
    extraction = extraction or {}
    bill = bill or {}
    combined = f"{sender}\n{subject}\n{body}"[:50000]
    haystack = combined.lower()
    provider = _provider_name(sender, subject, body)
    gpa_match = _GPA_RE.search(combined)
    order_number = gpa_match.group(0).upper() if gpa_match else ""

    payable_hits = _hits(haystack, _PAYABLE_TERMS)
    paid_hits = _hits(haystack, _PAID_TERMS)
    statement_hits = _hits(haystack, _STATEMENT_TERMS)
    recurring = any(term in haystack for term in _RECURRING_TERMS)

    cues = {str(value).lower() for value in (extraction.get("cues") or [])}
    has_invoice_word = "invoice" in cues or any(
        term in haystack for term in ("invoice", "factuur", "billing invoice")
    )
    has_amount = bool(extraction.get("amount_candidates")) or bool(bill.get("amount"))
    has_due_date = bool(extraction.get("due_date_candidates")) or bool(bill.get("due_at"))
    has_iban = bool(extraction.get("iban_candidates")) or bool(bill.get("iban"))

    strong_payable = bool(payable_hits) or (has_invoice_word and (has_due_date or has_iban))

    # Explicit evidence that payment is still owed always wins over provider heuristics.
    if strong_payable and has_amount:
        reasons = tuple(["explicit outstanding-payment signal"] + payable_hits[:3])
        return FinancialDocumentAssessment(
            PAYABLE_INVOICE,
            0.98 if payable_hits else 0.94,
            provider,
            order_number,
            recurring,
            reasons,
        )

    # Google Play GPA identifiers identify completed Google Play orders/transactions in
    # normal receipt mail. A genuine payable Google invoice with explicit due evidence
    # was already caught above.
    if gpa_match and "google" in provider.lower():
        return FinancialDocumentAssessment(
            PAID_RECEIPT,
            0.995,
            provider,
            order_number,
            recurring,
            ("Google Play GPA order identifier", "no outstanding-payment evidence"),
        )

    if paid_hits and not strong_payable:
        confidence = 0.97 if len(paid_hits) >= 2 else 0.92
        return FinancialDocumentAssessment(
            PAID_RECEIPT,
            confidence,
            provider,
            order_number,
            recurring,
            tuple(["completed-payment/purchase language"] + paid_hits[:3]),
        )

    known_provider = provider in {
        "Google Commerce Limited",
        "Apple",
        "PayPal",
        "Amazon",
        "Stripe",
        "Microsoft",
        "Paddle",
    }
    order_like = bool(order_number) or "order" in cues or any(
        term in haystack for term in ("order number", "bestelnummer", "purchase", "aankoop")
    )
    if known_provider and order_like and has_amount and not strong_payable:
        return FinancialDocumentAssessment(
            PAID_RECEIPT,
            0.90,
            provider,
            order_number,
            recurring,
            ("commerce-provider purchase/order evidence", "no outstanding-payment evidence"),
        )

    if statement_hits:
        return FinancialDocumentAssessment(
            STATEMENT_OR_NOTICE,
            0.94,
            provider,
            order_number,
            recurring,
            tuple(["statement/notice language"] + statement_hits[:3]),
        )

    # A bare invoice label without evidence that money remains due is not enough to
    # authorize payment. Preserve it as an informational financial record instead.
    if has_invoice_word and has_amount and not strong_payable:
        return FinancialDocumentAssessment(
            STATEMENT_OR_NOTICE,
            0.82,
            provider,
            order_number,
            recurring,
            ("invoice-like document without due/payment instructions",),
        )

    finance_like = bool(
        has_amount
        and (
            order_like
            or "payment" in haystack
            or "betaling" in haystack
            or "transaction" in haystack
            or "transactie" in haystack
        )
    )
    if finance_like and not strong_payable:
        return FinancialDocumentAssessment(
            STATEMENT_OR_NOTICE,
            0.72,
            provider,
            order_number,
            recurring,
            ("financial information without outstanding-payment evidence",),
        )

    return FinancialDocumentAssessment(
        NOT_FINANCIAL,
        0.0,
        provider,
        order_number,
        recurring,
        (),
    )


def receipt_label(document_type: str) -> str:
    if document_type == PAID_RECEIPT:
        return "Mail/02 Geldzaken & betalingen/Bonnen & betalingsbewijzen"
    if document_type == STATEMENT_OR_NOTICE:
        return "Mail/02 Geldzaken & betalingen/Overzichten & meldingen"
    return "Mail/02 Geldzaken & betalingen/Facturen & betalingen"


def infer_recurring_subscription(
    *,
    subject: str,
    body: str,
    assessment: FinancialDocumentAssessment,
    amount: str | None,
    currency: str = "EUR",
    account_scope: str = "personal",
) -> dict[str, Any] | None:
    """Build subscription data only when the source explicitly identifies a recurring item.

    This deliberately refuses to invent a generic provider subscription because one provider
    can bill several unrelated products. The caller can still use AI-provided structured
    subscription data when it is available.
    """

    if not assessment.recurring:
        return None
    combined = f"{subject}\n{body[:12000]}"
    description = ""
    patterns = (
        r"(?im)^\s*(?:product|item|plan|service|subscription|abonnement)\s*[:\-]\s*([^\n]{2,140})$",
        r"(?im)^\s*(?:renewal for|verlenging van)\s+([^\n]{2,140})$",
    )
    for pattern in patterns:
        match = re.search(pattern, combined)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
        if candidate.lower() in {
            "active",
            "renewed",
            "renewal",
            "subscription",
            "abonnement",
            "monthly",
            "yearly",
            "annual",
        }:
            continue
        description = candidate[:255]
        break
    if not description:
        return None

    lower = combined.lower()
    if any(term in lower for term in ("monthly", "maandelijks", "per month", "per maand")):
        billing_cycle = "monthly"
    elif any(term in lower for term in ("yearly", "annual", "jaarlijks", "per year", "per jaar")):
        billing_cycle = "yearly"
    else:
        billing_cycle = "unknown"

    return {
        "provider_name": assessment.provider_name,
        "description": description,
        "amount": amount,
        "currency": (currency or "EUR").upper()[:3],
        "billing_cycle": billing_cycle,
        "next_charge_at": None,
        "status": "active",
        "account_scope": account_scope or "personal",
    }
