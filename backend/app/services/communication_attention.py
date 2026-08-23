from __future__ import annotations

import re
from typing import Any

_HISTORY_PROVIDERS = {"android_sms_history", "android_mms_history", "android_call_log"}
_OTP_TERMS = (
    "one-time code", "one time code", "otp", "verification code", "verificatiecode",
    "beveiligingscode", "security code", "2fa", "auth code", "authentication code",
    "code de vérification", "code de securite", "code de sécurité", "login code", "sign-in code",
)
_FRAUD_TERMS = (
    "fraud", "fraude", "unauthorized", "unauthorised", "not authorized", "not authorised",
    "niet door u", "niet door jou", "suspicious transaction", "verdachte transactie",
    "card stolen", "kaart gestolen", "account compromised", "rekening gehackt",
    "security breach", "beveiligingsincident", "unknown transaction", "onbekende transactie",
)
_FINANCIAL_CONFIRMATION_TERMS = (
    "payment received", "payment completed", "payment successful", "payment processed", "payment initiated",
    "betaling ontvangen", "betaling voltooid", "betaling geslaagd", "betaling verwerkt", "betaling gestart",
    "transfer completed", "transfer successful", "transfer initiated", "transfer received",
    "overschrijving uitgevoerd", "overschrijving voltooid", "overschrijving gestart", "overschrijving ontvangen",
    "withdrawal completed", "withdrawal processed", "withdrawal initiated", "withdrawal requested",
    "opname voltooid", "opname verwerkt", "opname gestart", "cash withdrawal", "geldopname",
    "purchase confirmed", "transaction completed", "transaction successful", "transactie voltooid", "card payment",
)
_ROUTINE_STATUS_TERMS = (
    "ticket confirmed", "booking confirmed", "reservation confirmed", "appointment confirmed",
    "afspraak bevestigd", "reservering bevestigd", "bestelling bevestigd", "order confirmed",
    "shipped", "dispatched", "out for delivery", "delivered", "verzonden", "geleverd",
    "ready for pickup", "klaar om af te halen", "your receipt", "uw ontvangstbewijs",
    "ticket is valid", "ticket valid", "geldig tot", "valid until", "expires on", "vervalt op",
    "your plan expires", "uw bundel vervalt", "information only", "ter informatie",
)
_REQUEST_TERMS = (
    "action required", "actie vereist", "please ", "can you", "could you", "would you", "will you",
    "reply", "respond", "laat weten", "kun je", "kan je", "kunt u", "wil je", "wilt u", "bevestig",
    "confirm this", "approve", "goedkeuren", "confirmez", "pouvez-vous", "répondez", "repondez",
)
_PROMO_TERMS = (
    "unsubscribe", "uitschrijven", "afmelden", "stop to opt out", "reply stop", "promo",
    "promotion", "aanbieding", "korting", "discount", "sale", "% off", "deal", "newsletter", "nieuwsbrief",
)
_PAID_SUBSCRIPTION_TERMS = (
    "renewal", "renews", "renewed", "verlenging", "wordt verlengd", "next charge",
    "volgende betaling", "billing", "factur", "charged", "in rekening", "monthly",
    "maandelijks", "annual", "yearly", "jaarlijks", "price", "prijs",
)
_AMOUNT_RE = re.compile(r"(?:€|eur\s*)\s*\d|\d[\d., ]*\s*(?:€|eur)\b", re.I)
_CODE_RE = re.compile(r"\b\d{4,8}\b")


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def normalize_communication_attention(payload: Any, decision: dict[str, Any]) -> dict[str, Any]:
    """Keep protected evidence separate from genuine human work and interruption."""
    normalized = dict(decision)
    body = str(getattr(payload, "body", "") or "")
    lower = body.casefold()
    provider = str(getattr(payload, "provider", "") or "").casefold()
    direction = str(getattr(payload, "direction", "incoming") or "incoming").casefold()
    channel = str(getattr(payload, "channel", "") or "").casefold()
    event_type = str(getattr(payload, "event_type", "") or "").casefold()

    protected = bool(normalized.get("protected"))
    action_required = bool(normalized.get("action_required"))
    interrupt = False
    reason = str(normalized.get("reasoning_summary") or "")[:500]

    if direction != "incoming" or provider in _HISTORY_PROVIDERS:
        action_required = False
        reason = "Historical/outgoing communication is retained as context, not turned into new user work."
    elif channel == "call":
        action_required = event_type == "missed_call" and bool(normalized.get("action_required"))
    elif _has_any(lower, _FRAUD_TERMS):
        protected = True
        action_required = True
        interrupt = True
        normalized["priority"] = "urgent"
        reason = "Credible fraud/security wording requires immediate human judgment."
    elif _has_any(lower, _OTP_TERMS) or (_CODE_RE.search(lower) and any(term in lower for term in ("code", "verific", "2fa", "login"))):
        protected = True
        action_required = False
        reason = "Authentication code retained as protected evidence; only an active authentication objective may ask the user to approve."
    elif _has_any(lower, _FINANCIAL_CONFIRMATION_TERMS) and not _has_any(lower, _REQUEST_TERMS):
        protected = True
        action_required = False
        reason = "Financial confirmation retained as evidence; it does not imply a reply or manual task."
    elif _has_any(lower, _ROUTINE_STATUS_TERMS) and not _has_any(lower, _REQUEST_TERMS):
        action_required = False
        reason = "Routine status information recorded without creating user work."
    elif str(normalized.get("category") or "").casefold() in {"private/hidden notification", "notification", "notifications"}:
        action_required = False
        reason = "Routine/hidden notification metadata is not itself a task."

    # If a safe executable reply already exists, the VA retains ownership.
    if normalized.get("auto_reply_safe") and normalized.get("reply_text"):
        action_required = False

    if normalized.get("relationship_review_required"):
        action_required = True
    if action_required and str(normalized.get("priority") or "").casefold() == "urgent":
        interrupt = True

    normalized["protected"] = protected
    normalized["action_required"] = action_required
    normalized["interrupt"] = interrupt
    normalized["routine_evidence"] = direction == "incoming" and not action_required
    normalized["attention_reason"] = reason
    if protected:
        normalized["auto_reply_safe"] = False
        normalized["reply_text"] = None
    normalized["delete_from_device"] = bool(
        direction == "incoming"
        and not protected
        and not action_required
        and (
            bool(normalized.get("spam"))
            or str(normalized.get("category") or "").casefold() in {"spam", "promotion", "promotions", "newsletters & promotions"}
        )
        and _has_any(lower, _PROMO_TERMS)
    )
    return normalized


def is_marketing_subscription_false_positive(
    *, sender: str, subject: str, body: str, subscription: dict[str, Any] | None
) -> bool:
    """Return True when mailing-list/footer language was mistaken for a paid subscription."""
    if not subscription:
        return False
    text = f"{sender}\n{subject}\n{body[:12000]}".casefold()
    if not _has_any(text, _PROMO_TERMS):
        return False
    if _has_any(text, _PAID_SUBSCRIPTION_TERMS) or _AMOUNT_RE.search(text):
        return False
    provider = str(subscription.get("provider_name") or subscription.get("provider") or "").casefold()
    description = str(subscription.get("description") or "").casefold()
    return any(term in text for term in ("unsubscribe", "uitschrijven", "afmelden", "newsletter", "nieuwsbrief")) or "newsletter" in provider + description
