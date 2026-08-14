from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.models.entities import (
    BrowserEvidence,
    BrowserOperation,
    BrowserPortal,
    EmailMessage,
    OrderRecord,
    SupportCase,
    VAObjective,
)
from app.models.fulfillment_entities import (
    FulfillmentAction,
    FulfillmentEvidence,
    FulfillmentObservation,
    FulfillmentProvider,
    FulfillmentRequest,
)
from app.models.telephony_entities import TelephonyCall
from app.services.audit import write_audit
from app.services.browser_operator import enqueue_browser_operation, prepare_browser_operation
from app.services.runtime_config import get_runtime_value
from app.services.telephony_service import create_outbound_call, reconcile_call


TERMINAL_REQUEST_STATES = {"completed", "cancelled", "dismissed", "failed"}
ORDER_TERMINAL_STATES = {"delivered", "completed", "cancelled", "canceled", "returned", "refunded"}
SUPPORT_TERMINAL_STATES = {"resolved", "closed"}
_BROWSER_SUCCESS = {"verified"}
_BROWSER_USER = {"needs_user", "needs_user_auth", "blocked_user", "awaiting_auth"}
_BROWSER_FAILURE = {"failed", "cancelled", "dead_letter", "creation_uncertain"}
_CALL_TERMINAL = {"completed", "failed", "busy", "no-answer", "no_answer", "cancelled", "canceled"}

_TRACKING_STATES = {
    "pre_transit",
    "in_transit",
    "out_for_delivery",
    "available_for_pickup",
    "delivered",
    "exception",
    "returned",
    "unknown",
}
_TRACKING_STATE_PRIORITY = (
    "delivered",
    "available_for_pickup",
    "exception",
    "returned",
    "out_for_delivery",
    "in_transit",
    "pre_transit",
)


_FULFILLMENT_SOURCE_TERMS = (
    "order confirmation", "order confirmed", "your order", "order #", "order no",
    "shipping", "shipped", "shipment", "delivery", "delivered", "dispatch",
    "parcel", "package", "tracking", "track & trace", "track and trace", "pickup",
    "bestelling", "bestelbevestiging", "verzonden", "verzending", "levering",
    "geleverd", "pakket", "track & trace", "afhaal",
)
_PAYMENT_ONLY_SOURCE_TERMS = (
    "payment confirmation", "payment receipt", "payment to", "you paid", "charged",
    "transaction", "receipt", "google play", "google payments", "purchase receipt",
    "betalingsbevestiging", "betaling aan", "betaald", "transactie", "aankoopbewijs",
)
_GOOGLE_PAYMENT_SENDER_TERMS = (
    "googlepayments", "payments-noreply", "googleplay", "google play", "google payments",
)


def utcnow() -> datetime:
    return datetime.utcnow()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _decrypt_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    decoded = _loads(decrypt_text(value), {})
    return decoded if isinstance(decoded, dict) else {}


def _decrypt_text(value: str) -> str:
    return decrypt_text(value) if value else ""


def _render(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in variables.items():
            result = result.replace("{{" + str(key) + "}}", "" if replacement is None else str(replacement))
        return result
    if isinstance(value, list):
        return [_render(item, variables) for item in value]
    if isinstance(value, dict):
        return {str(key): _render(item, variables) for key, item in value.items()}
    return value


def _host(value: str) -> str:
    try:
        return (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def provider_templates() -> list[dict[str, Any]]:
    """Built-in conservative starter recipes for common fulfillment providers.

    Templates never bypass the Secure Browser allowlist. The bpost template follows an
    already-known tracking URL from order/source evidence; it does not invent a barcode or
    claim delivery from navigation alone.
    """
    return [
        {
            "key": "bpost_track_trace",
            "name": "bpost Track & Trace",
            "provider_name": "bpost",
            "slug": "bpost",
            "provider_type": "carrier",
            "portal_name": "bpost Track & Trace",
            "portal_base_url": "https://track.bpost.cloud/btr/web/",
            "required_variables": ["tracking_url"],
            "recipe": {
                "track": {
                    "mode": "observe",
                    "steps": [
                        {
                            "kind": "goto",
                            "url": "{{tracking_url}}",
                            "replay_safe": True,
                            "side_effect": False,
                            "timeout_ms": 30000,
                        }
                    ],
                    "verification": {
                        "url_contains": "track.bpost.cloud",
                        "settle_ms": 3500,
                        "observe_text_any": {
                            "delivered": [
                                "your parcel has been delivered",
                                "the parcel has been delivered",
                                "je pakje is geleverd",
                                "het pakje is geleverd",
                                "votre colis a été livré",
                                "le colis a été livré",
                            ],
                            "available_for_pickup": [
                                "available at a pick-up point",
                                "ready for collection",
                                "klaar om af te halen",
                                "beschikbaar in een afhaalpunt",
                                "disponible dans un point d'enlèvement",
                                "prêt à être retiré",
                            ],
                            "out_for_delivery": [
                                "out for delivery",
                                "being delivered today",
                                "wordt vandaag geleverd",
                                "onderweg voor levering",
                                "en cours de livraison",
                            ],
                            "in_transit": [
                                "in transit",
                                "your parcel is on its way",
                                "je pakje is onderweg",
                                "votre colis est en route",
                            ],
                            "pre_transit": [
                                "being prepared by the sender",
                                "not yet handed over to bpost",
                                "wordt voorbereid door de afzender",
                                "nog niet overhandigd aan bpost",
                                "préparé par l'expéditeur",
                            ],
                            "exception": [
                                "could not be delivered",
                                "delivery failed",
                                "kon niet worden geleverd",
                                "levering mislukt",
                                "n'a pas pu être livré",
                            ],
                            "returned": [
                                "returned to sender",
                                "teruggestuurd naar de afzender",
                                "renvoyé à l'expéditeur",
                            ],
                        },
                    },
                    "tracking": {
                        "recheck_minutes": 180,
                        "out_for_delivery_recheck_minutes": 60,
                        "pickup_recheck_minutes": 360,
                        "unknown_recheck_minutes": 90,
                        "error_recheck_minutes": 60,
                        "stalled_after_hours": 120,
                    },
                }
            },
            "notes": (
                "Uses the source-backed bpost tracking URL. Delivery is complete only after "
                "the provider page observation matches a delivered state; navigation alone is not completion evidence."
            ),
        }
    ]


def _tracking_config(recipe: dict[str, Any]) -> dict[str, int]:
    raw = recipe.get("tracking") if isinstance(recipe, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    def bounded(key: str, default: int, minimum: int = 5, maximum: int = 10080) -> int:
        try:
            value = int(raw.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    return {
        "recheck_minutes": bounded("recheck_minutes", 180),
        "out_for_delivery_recheck_minutes": bounded("out_for_delivery_recheck_minutes", 60),
        "pickup_recheck_minutes": bounded("pickup_recheck_minutes", 360),
        "unknown_recheck_minutes": bounded("unknown_recheck_minutes", 90),
        "error_recheck_minutes": bounded("error_recheck_minutes", 60),
        "stalled_after_hours": bounded("stalled_after_hours", 120, minimum=12, maximum=720),
    }


def _tracking_state_from_matches(matches: dict[str, Any]) -> str:
    for state in _TRACKING_STATE_PRIORITY:
        value = matches.get(state)
        if isinstance(value, dict) and bool(value.get("matched")):
            return state
        if value is True:
            return state
    return "unknown"


def _tracking_recheck_at(state: str, config: dict[str, int], *, now: datetime | None = None) -> datetime:
    current = now or utcnow()
    if state == "out_for_delivery":
        minutes = config["out_for_delivery_recheck_minutes"]
    elif state == "available_for_pickup":
        minutes = config["pickup_recheck_minutes"]
    elif state == "unknown":
        minutes = config["unknown_recheck_minutes"]
    else:
        minutes = config["recheck_minutes"]
    return current + timedelta(minutes=minutes)


async def _latest_browser_observation_matches(
    db: AsyncSession, operation: BrowserOperation
) -> dict[str, Any]:
    evidence = (
        await db.execute(
            select(BrowserEvidence)
            .where(
                BrowserEvidence.browser_operation_id == operation.id,
                BrowserEvidence.evidence_type == "browser_postcondition_verified",
            )
            .order_by(BrowserEvidence.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if evidence is None:
        return {}
    details = _loads(evidence.details_json, {})
    observed = details.get("observe_text_any") if isinstance(details, dict) else None
    return observed if isinstance(observed, dict) else {}


async def _tracking_is_stalled(
    db: AsyncSession,
    request: FulfillmentRequest,
    *,
    state: str,
    threshold_hours: int,
) -> bool:
    if state in {"unknown", "delivered", "available_for_pickup", "out_for_delivery"}:
        return False
    rows = list(
        (
            await db.execute(
                select(FulfillmentObservation)
                .where(FulfillmentObservation.request_id == request.id)
                .order_by(FulfillmentObservation.observed_at.desc(), FulfillmentObservation.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    if not rows or rows[0].state != state:
        return False
    same_since = rows[0].observed_at
    for row in rows[1:]:
        if row.state != state:
            break
        same_since = row.observed_at
    return (utcnow() - same_since) >= timedelta(hours=threshold_hours)


async def _record_tracking_observation(
    db: AsyncSession,
    request: FulfillmentRequest,
    action: FulfillmentAction,
    *,
    provider: FulfillmentProvider,
    operation: BrowserOperation,
    state: str,
    matches: dict[str, Any],
    stalled: bool,
) -> FulfillmentObservation:
    key = f"fulfillment:{request.id}:tracking:{operation.id}"[:255]
    existing = (
        await db.execute(
            select(FulfillmentObservation)
            .where(FulfillmentObservation.observation_key == key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = FulfillmentObservation(
        request_id=request.id,
        action_id=action.id,
        observation_key=key,
        provider=provider.slug[:80],
        state=state if state in _TRACKING_STATES else "unknown",
        terminal=state == "delivered",
        stalled=stalled,
        external_ref=str(operation.id),
        details_encrypted=encrypt_text(
            _dump(
                {
                    "browser_operation_id": operation.id,
                    "matched_states": sorted(
                        name
                        for name, value in matches.items()
                        if (isinstance(value, dict) and bool(value.get("matched"))) or value is True
                    ),
                    "provider_page_verified": True,
                    "stalled": stalled,
                }
            )
        ),
    )
    db.add(row)
    await db.flush()
    await _record_evidence(
        db,
        request,
        action=action,
        evidence_type="tracking_state_observed",
        provider=provider.slug,
        external_ref=str(operation.id),
        details={
            "state": row.state,
            "stalled": stalled,
            "provider_page_verified": True,
            "browser_operation_id": operation.id,
        },
        evidence_key=f"fulfillment:{request.id}:tracking-state:{operation.id}",
    )
    return row


async def _record_evidence(
    db: AsyncSession,
    request: FulfillmentRequest,
    *,
    evidence_type: str,
    provider: str,
    external_ref: str,
    details: dict[str, Any],
    action: FulfillmentAction | None = None,
    evidence_key: str | None = None,
) -> FulfillmentEvidence:
    key = (evidence_key or f"fulfillment:{request.id}:{evidence_type}:{external_ref}")[:255]
    existing = (
        await db.execute(select(FulfillmentEvidence).where(FulfillmentEvidence.evidence_key == key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = FulfillmentEvidence(
        request_id=request.id,
        action_id=action.id if action else None,
        evidence_key=key,
        evidence_type=evidence_type[:80],
        provider=provider[:80],
        external_ref=external_ref[:500],
        details_encrypted=encrypt_text(_dump(details)),
    )
    db.add(row)
    await db.flush()
    return row


async def _ensure_va_objective(db: AsyncSession, request: FulfillmentRequest) -> VAObjective:
    if request.va_objective_id:
        existing = await db.get(VAObjective, request.va_objective_id)
        if existing is not None:
            return existing
    key = f"fulfillment:{request.id}"
    objective = (
        await db.execute(select(VAObjective).where(VAObjective.correlation_key == key).limit(1))
    ).scalar_one_or_none()
    goal = _decrypt_text(request.goal_encrypted) or request.title
    if objective is None:
        objective = VAObjective(
            correlation_key=key,
            source_type="fulfillment",
            source_id=str(request.id),
            title=request.title,
            goal=goal,
            category=f"fulfillment_{request.request_type}",
            priority=request.priority,
            risk_level=request.risk_level,
            status=request.status,
            needs_user_reason=request.needs_user_reason if request.status == "needs_user" else "",
            blocked_reason=request.needs_user_reason if request.status.startswith("blocked") else "",
            context_json=_dump({"fulfillment_request_id": request.id, "request_type": request.request_type}),
        )
        db.add(objective)
        await db.flush()
    request.va_objective_id = objective.id
    return objective


async def _sync_va_state(db: AsyncSession, request: FulfillmentRequest) -> None:
    objective = await _ensure_va_objective(db, request)
    mapping = {
        "planned": "planned",
        "dispatching": "executing",
        "waiting_provider": "waiting",
        "verifying": "verifying",
        "needs_user": "needs_user",
        "blocked_capability": "blocked_capability",
        "blocked_system": "blocked_system",
        "failed": "failed",
        "cancelled": "cancelled",
        "dismissed": "cancelled",
        "completed": "completed",
    }
    target = mapping.get(request.status, "planned")
    objective.status = target
    objective.needs_user_reason = request.needs_user_reason if target == "needs_user" else ""
    objective.blocked_reason = request.needs_user_reason if target.startswith("blocked") else ""
    objective.last_error = request.last_error[:8000]
    if target in {"completed", "cancelled", "failed"}:
        objective.finished_at = objective.finished_at or utcnow()
    else:
        objective.finished_at = None


async def upsert_provider(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    provider_type: str,
    browser_portal_id: int | None,
    account_scope: str,
    support_phone: str,
    recipe: dict[str, Any],
    enabled: bool,
) -> FulfillmentProvider:
    slug = slug.strip().lower().replace(" ", "-")[:120]
    if not slug or not name.strip():
        raise ValueError("Provider slug and name are required")
    if account_scope not in {"personal", "pro"}:
        raise ValueError("Provider account scope must be personal or pro")
    if browser_portal_id is not None:
        portal = await db.get(BrowserPortal, browser_portal_id)
        if portal is None or not portal.enabled:
            raise ValueError("Fulfillment provider must reference an enabled browser portal")
        if portal.account_scope != account_scope:
            raise ValueError("Fulfillment provider and browser portal must use the same account scope")
    if not isinstance(recipe, dict):
        raise ValueError("Provider recipe must be an object")
    row = (
        await db.execute(select(FulfillmentProvider).where(FulfillmentProvider.slug == slug).limit(1))
    ).scalar_one_or_none()
    if row is None:
        row = FulfillmentProvider(slug=slug, name=name.strip()[:255])
        db.add(row)
    row.name = name.strip()[:255]
    row.provider_type = provider_type.strip().lower()[:40] or "merchant"
    row.browser_portal_id = browser_portal_id
    row.account_scope = account_scope
    row.support_phone_encrypted = encrypt_text(support_phone.strip()) if support_phone.strip() else ""
    row.recipe_encrypted = encrypt_text(_dump(recipe))
    row.enabled = bool(enabled)
    await db.flush()
    await write_audit(
        db,
        "fulfillment_provider_configured",
        entity_type="fulfillment_provider",
        entity_id=str(row.id),
        details={
            "slug": row.slug,
            "provider_type": row.provider_type,
            "browser_portal_id": row.browser_portal_id,
            "account_scope": row.account_scope,
            "support_phone_configured": bool(support_phone.strip()),
            "recipe_actions": sorted(recipe.keys()),
            "enabled": row.enabled,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def list_providers(db: AsyncSession) -> list[dict[str, Any]]:
    rows = list((await db.execute(select(FulfillmentProvider).order_by(FulfillmentProvider.name))).scalars())
    portal_ids = {row.browser_portal_id for row in rows if row.browser_portal_id}
    portals = {
        portal.id: portal
        for portal in (
            await db.execute(select(BrowserPortal).where(BrowserPortal.id.in_(portal_ids)))
            if portal_ids
            else []
        ).scalars()
    } if portal_ids else {}
    result: list[dict[str, Any]] = []
    for row in rows:
        recipe = _decrypt_json(row.recipe_encrypted)
        portal = portals.get(row.browser_portal_id)
        result.append(
            {
                "id": row.id,
                "slug": row.slug,
                "name": row.name,
                "provider_type": row.provider_type,
                "browser_portal_id": row.browser_portal_id,
                "browser_portal_name": portal.name if portal else "",
                "account_scope": row.account_scope,
                "support_phone": _decrypt_text(row.support_phone_encrypted),
                "support_phone_configured": bool(row.support_phone_encrypted),
                "recipe": recipe,
                "recipe_actions": sorted(recipe.keys()),
                "enabled": row.enabled,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    return result


async def order_is_fulfillment_candidate(
    db: AsyncSession, order: OrderRecord
) -> tuple[bool, str]:
    """Return whether an OrderRecord represents a real fulfillment lifecycle.

    Payment processors and app-store receipts often expose transaction IDs that an AI can
    mistake for order numbers. Fulfillment must require source-backed shipping/order evidence
    rather than treating every paid receipt as logistics work.
    """
    status = (order.status or "").casefold().strip()
    if status == "not_order":
        return False, "Order was explicitly dismissed as a non-order"
    if status == "confirmed_order":
        return True, "Order was explicitly confirmed by the user"
    if (order.tracking_url or "").strip() or order.expected_delivery_at is not None:
        return True, "Tracking URL or expected delivery evidence is present"
    if status in {"shipped", "dispatched", "in_transit", "in transit", "out_for_delivery", "out for delivery", "delivered"}:
        return True, f"Order ledger has fulfillment status: {order.status}"

    source = None
    if order.source_message_id:
        source = (
            await db.execute(
                select(EmailMessage)
                .where(EmailMessage.provider_message_id == order.source_message_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    if source is None:
        # Do not destroy an older/manual order merely because its source email is unavailable.
        return True, "Source email is unavailable; retain existing order conservatively"

    sender = (source.sender or "").casefold()
    source_text = " ".join(
        part for part in (source.sender, source.subject, source.snippet) if part
    ).casefold()
    has_fulfillment_signal = any(term in source_text for term in _FULFILLMENT_SOURCE_TERMS)
    google_payment_source = any(term in sender or term in source_text for term in _GOOGLE_PAYMENT_SENDER_TERMS)
    google_purchase_id = (order.order_number or "").strip().upper().startswith("GPA.")
    payment_only_signal = any(term in source_text for term in _PAYMENT_ONLY_SOURCE_TERMS)

    if (google_payment_source or google_purchase_id) and not has_fulfillment_signal:
        return False, "Google payment/app-store receipt has no shipping, delivery, pickup or tracking evidence"
    if payment_only_signal and not has_fulfillment_signal:
        return False, "Payment/receipt evidence is present without a fulfillment lifecycle"
    if has_fulfillment_signal:
        return True, "Source email contains order/shipping/delivery evidence"
    return False, "No source-backed shipping, delivery, pickup or tracking evidence"


async def dismiss_order_record(
    db: AsyncSession, order: OrderRecord, *, reason: str, explicit: bool = False
) -> dict[str, Any]:
    order.status = "not_order"
    requests = list(
        (
            await db.execute(
                select(FulfillmentRequest).where(
                    (FulfillmentRequest.order_id == order.id)
                    | (
                        (FulfillmentRequest.source_type == "order")
                        & (FulfillmentRequest.source_id == str(order.id))
                    )
                )
            )
        ).scalars()
    )
    dismissed = 0
    for request in requests:
        if request.status == "dismissed":
            continue
        request.status = "dismissed"
        request.requires_user_action = False
        request.needs_user_reason = ""
        request.next_action_at = None
        request.last_error = reason[:8000]
        request.completed_at = request.completed_at or utcnow()
        await _sync_va_state(db, request)
        actions = list(
            (
                await db.execute(
                    select(FulfillmentAction).where(FulfillmentAction.request_id == request.id)
                )
            ).scalars()
        )
        for action in actions:
            if action.status not in {"completed", "failed", "cancelled"}:
                action.status = "cancelled"
                action.last_error = reason[:8000]
                action.completed_at = action.completed_at or utcnow()
        dismissed += 1
    await write_audit(
        db,
        "order_reclassified_as_non_fulfillment",
        entity_type="order",
        entity_id=str(order.id),
        details={"reason": reason, "explicit": explicit, "fulfillment_requests_dismissed": dismissed},
    )
    await db.commit()
    return {"order_id": order.id, "dismissed_requests": dismissed, "reason": reason}


async def restore_order_record(db: AsyncSession, order: OrderRecord) -> dict[str, Any]:
    order.status = "confirmed_order"
    requests = list(
        (await db.execute(select(FulfillmentRequest).where(FulfillmentRequest.order_id == order.id))).scalars()
    )
    restored = 0
    for request in requests:
        if request.status != "dismissed":
            continue
        request.status = "planned"
        request.last_error = ""
        request.completed_at = None
        request.next_action_at = utcnow()
        await _sync_va_state(db, request)
        restored += 1
    await write_audit(
        db,
        "order_restored_as_fulfillment",
        entity_type="order",
        entity_id=str(order.id),
        details={"fulfillment_requests_restored": restored},
    )
    await db.commit()
    return {"order_id": order.id, "restored_requests": restored}


async def _provider_for_order(db: AsyncSession, order: OrderRecord) -> FulfillmentProvider | None:
    providers = list(
        (
            await db.execute(
                select(FulfillmentProvider).where(
                    FulfillmentProvider.enabled.is_(True),
                    FulfillmentProvider.account_scope == order.account_scope,
                )
            )
        ).scalars()
    )
    merchant = order.merchant.casefold().strip()
    tracking_host = _host(order.tracking_url)
    for provider in providers:
        if provider.name.casefold() in merchant or provider.slug.casefold().replace("-", " ") in merchant:
            return provider
        if tracking_host and provider.browser_portal_id:
            portal = await db.get(BrowserPortal, provider.browser_portal_id)
            if portal is not None and tracking_host == _host(portal.base_url):
                return provider
    return None


async def _provider_for_support(db: AsyncSession, case: SupportCase) -> FulfillmentProvider | None:
    providers = list((await db.execute(select(FulfillmentProvider).where(FulfillmentProvider.enabled.is_(True)))).scalars())
    haystack = f"{case.requester} {case.subject}".casefold()
    return next(
        (
            provider
            for provider in providers
            if provider.name.casefold() in haystack or provider.slug.casefold().replace("-", " ") in haystack
        ),
        None,
    )


async def create_request(
    db: AsyncSession,
    *,
    idempotency_key: str,
    request_type: str,
    title: str,
    goal: str,
    provider_id: int | None,
    account_scope: str,
    amount: Decimal | None,
    currency: str,
    details: dict[str, Any],
    priority: str = "normal",
    source_type: str = "manual",
    source_id: str = "",
    order_id: int | None = None,
    support_case_id: int | None = None,
) -> FulfillmentRequest:
    idempotency_key = idempotency_key.strip()[:255]
    request_type = request_type.strip().lower()
    allowed_types = {"purchase", "travel", "logistics", "return", "refund", "cancel", "customer_service"}
    if len(idempotency_key) < 8:
        raise ValueError("A stable idempotency key of at least 8 characters is required")
    if request_type not in allowed_types:
        raise ValueError(f"Unsupported fulfillment request type: {request_type}")
    if account_scope not in {"personal", "pro"}:
        raise ValueError("Account scope must be personal or pro")
    existing = (
        await db.execute(select(FulfillmentRequest).where(FulfillmentRequest.idempotency_key == idempotency_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    provider = await db.get(FulfillmentProvider, provider_id) if provider_id else None
    if provider is not None and (not provider.enabled or provider.account_scope != account_scope):
        raise ValueError("Selected fulfillment provider is disabled or belongs to a different account scope")
    risk = "medium" if request_type in {"purchase", "travel"} else "low"
    row = FulfillmentRequest(
        idempotency_key=idempotency_key,
        request_type=request_type,
        provider_id=provider_id,
        account_scope=account_scope,
        title=title.strip()[:2000] or request_type.replace("_", " ").title(),
        goal_encrypted=encrypt_text(goal.strip()[:8000]),
        details_encrypted=encrypt_text(_dump(details)),
        amount=amount,
        currency=(currency or "EUR").upper()[:3],
        status="planned",
        priority=priority[:20],
        risk_level=risk,
        source_type=source_type[:60],
        source_id=source_id[:255],
        order_id=order_id,
        support_case_id=support_case_id,
        next_action_at=utcnow(),
    )
    db.add(row)
    await db.flush()
    await _ensure_va_objective(db, row)
    await write_audit(
        db,
        "fulfillment_request_created",
        entity_type="fulfillment_request",
        entity_id=str(row.id),
        details={
            "request_type": row.request_type,
            "provider_id": row.provider_id,
            "account_scope": row.account_scope,
            "amount": str(row.amount) if row.amount is not None else None,
            "currency": row.currency,
            "source_type": row.source_type,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def _monthly_committed_eur(db: AsyncSession, request: FulfillmentRequest) -> Decimal:
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    value = (
        await db.execute(
            select(func.coalesce(func.sum(FulfillmentRequest.amount), 0)).where(
                FulfillmentRequest.id != request.id,
                FulfillmentRequest.account_scope == request.account_scope,
                FulfillmentRequest.currency == "EUR",
                FulfillmentRequest.request_type.in_(["purchase", "travel"]),
                FulfillmentRequest.status.not_in(["cancelled", "failed"]),
                FulfillmentRequest.created_at >= month_start,
            )
        )
    ).scalar_one()
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


async def _standing_policy_authorizes(db: AsyncSession, request: FulfillmentRequest) -> tuple[bool, str]:
    if request.material_authorized_at is not None:
        return True, request.authorization_basis or "specific_user_authorization"
    if request.request_type not in {"purchase", "travel"}:
        return False, "non_purchase_material_action_requires_specific_authorization"
    if request.currency != "EUR" or request.amount is None or request.amount <= 0:
        return False, "A known positive EUR amount is required before a material purchase can be authorized"
    enabled_key = "fulfillment_auto_travel_enabled" if request.request_type == "travel" else "fulfillment_auto_purchase_enabled"
    max_key = "fulfillment_max_single_travel_eur" if request.request_type == "travel" else "fulfillment_max_single_purchase_eur"
    enabled = (await get_runtime_value(db, enabled_key, "false")).lower() == "true"
    if not enabled:
        return False, f"Standing {request.request_type} authorization is disabled"
    single = _decimal(await get_runtime_value(db, max_key, "0")) or Decimal("0.00")
    monthly_limit = _decimal(await get_runtime_value(db, "fulfillment_monthly_purchase_limit_eur", "0")) or Decimal("0.00")
    if single <= 0 or request.amount > single:
        return False, f"Amount {request.amount} EUR exceeds the configured single {request.request_type} limit {single} EUR"
    committed = await _monthly_committed_eur(db, request)
    if monthly_limit <= 0 or committed + request.amount > monthly_limit:
        return False, f"Monthly fulfillment spend would exceed the configured {monthly_limit} EUR limit"
    return True, "standing_spend_policy"


async def authorize_request(db: AsyncSession, request: FulfillmentRequest) -> FulfillmentRequest:
    if request.request_type not in {"purchase", "travel"}:
        raise ValueError("Only purchase/travel payment commitments use explicit fulfillment authorization")
    if request.currency != "EUR" or request.amount is None or request.amount <= 0:
        raise ValueError("A specific payment authorization requires a known positive EUR amount")
    request.material_authorized_at = utcnow()
    request.authorization_basis = "specific_user_authorization"
    request.requires_user_action = False
    request.needs_user_reason = ""
    if request.status == "needs_user":
        request.status = "planned"
        request.next_action_at = utcnow()
    await _record_evidence(
        db,
        request,
        evidence_type="specific_payment_authorization",
        provider="vaapp",
        external_ref=str(request.id),
        details={"authorized_at": request.material_authorized_at, "amount": str(request.amount), "currency": request.currency},
        evidence_key=f"fulfillment:{request.id}:specific-payment-authorization",
    )
    await _sync_va_state(db, request)
    await write_audit(
        db,
        "fulfillment_payment_authorized",
        entity_type="fulfillment_request",
        entity_id=str(request.id),
        details={"amount": str(request.amount) if request.amount is not None else None, "currency": request.currency},
    )
    await db.commit()
    return request


async def _ensure_action(
    db: AsyncSession,
    request: FulfillmentRequest,
    *,
    action_type: str,
    details: dict[str, Any],
) -> FulfillmentAction:
    existing = (
        await db.execute(
            select(FulfillmentAction)
            .where(FulfillmentAction.request_id == request.id)
            .order_by(FulfillmentAction.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status not in {"completed", "failed", "cancelled"}:
        return existing
    sequence = (existing.sequence + 1) if existing is not None else 1
    key = f"fulfillment:{request.id}:action:{sequence}:{action_type}"[:255]
    action = FulfillmentAction(
        request_id=request.id,
        sequence=sequence,
        idempotency_key=key,
        action_type=action_type[:60],
        status="planned",
        details_encrypted=encrypt_text(_dump(details)),
        run_after=utcnow(),
    )
    db.add(action)
    await db.flush()
    # Persist the local intent before any provider-specific preparation or POST.
    await db.commit()
    return action


async def _recipe_for(provider: FulfillmentProvider, request_type: str) -> dict[str, Any]:
    recipe = _decrypt_json(provider.recipe_encrypted)
    aliases = {
        "logistics": ["track", "logistics"],
        "customer_service": ["support", "customer_service"],
        "cancel": ["cancel"],
        "return": ["return"],
        "refund": ["refund"],
        "purchase": ["purchase"],
        "travel": ["travel"],
    }
    for key in aliases.get(request_type, [request_type]):
        value = recipe.get(key)
        if isinstance(value, dict):
            return value
    return {}


async def _dispatch_browser_action(
    db: AsyncSession,
    request: FulfillmentRequest,
    provider: FulfillmentProvider,
    action: FulfillmentAction,
) -> FulfillmentAction:
    recipe = await _recipe_for(provider, request.request_type)
    steps = recipe.get("steps")
    verification = recipe.get("verification")
    if not provider.browser_portal_id or not isinstance(steps, list) or not isinstance(verification, dict):
        raise ValueError(f"Provider {provider.name} has no executable {request.request_type} browser recipe")
    details = _decrypt_json(request.details_encrypted)
    variables: dict[str, Any] = {
        **details,
        "request_id": request.id,
        "title": request.title,
        "goal": _decrypt_text(request.goal_encrypted),
        "amount": request.amount,
        "currency": request.currency,
        "account_scope": request.account_scope,
    }
    if request.order_id:
        order = await db.get(OrderRecord, request.order_id)
        if order is not None:
            variables.update(
                {
                    "merchant": order.merchant,
                    "order_number": order.order_number,
                    "tracking_url": order.tracking_url,
                    "expected_delivery_at": order.expected_delivery_at,
                }
            )
    rendered_steps = _render(steps, variables)
    rendered_verification = _render(verification, variables)
    operation = None
    if action.browser_operation_id:
        operation = await db.get(BrowserOperation, action.browser_operation_id)
    if operation is None:
        operation = (
            await db.execute(
                select(BrowserOperation).where(BrowserOperation.idempotency_key == action.idempotency_key).limit(1)
            )
        ).scalar_one_or_none()
    if operation is None:
        operation = await prepare_browser_operation(
            db,
            idempotency_key=action.idempotency_key,
            portal_id=provider.browser_portal_id,
            title=request.title,
            steps=rendered_steps,
            verification=rendered_verification,
            objective_id=request.va_objective_id,
        )
    action.browser_operation_id = operation.id
    action.status = "dispatching"
    action.started_at = action.started_at or utcnow()
    request.status = "dispatching"
    request.started_at = request.started_at or utcnow()

    summary = _loads(operation.plan_json, {})
    material = bool(isinstance(summary, dict) and summary.get("material_commitment"))
    if material and operation.material_approved_at is None:
        allowed, basis = await _standing_policy_authorizes(db, request)
        if not allowed:
            request.status = "needs_user"
            request.requires_user_action = True
            request.needs_user_reason = basis
            action.status = "needs_user"
            action.last_error = basis
            await _sync_va_state(db, request)
            await db.commit()
            return action
        operation.material_approved_at = utcnow()
        request.material_authorized_at = request.material_authorized_at or operation.material_approved_at
        request.authorization_basis = request.authorization_basis or basis
        await _record_evidence(
            db,
            request,
            action=action,
            evidence_type="standing_payment_authorization",
            provider="vaapp",
            external_ref=str(operation.id),
            details={
                "basis": basis,
                "amount": str(request.amount) if request.amount is not None else None,
                "currency": request.currency,
                "browser_operation_id": operation.id,
            },
            evidence_key=f"fulfillment:{request.id}:browser:{operation.id}:authorization",
        )
        await write_audit(
            db,
            "browser_material_operation_preauthorized_by_fulfillment_policy",
            entity_type="browser_operation",
            entity_id=str(operation.id),
            details={"fulfillment_request_id": request.id, "basis": basis},
        )
    await _sync_va_state(db, request)
    await db.commit()
    await enqueue_browser_operation(db, operation)
    return action


async def _dispatch_support_call(
    db: AsyncSession,
    request: FulfillmentRequest,
    provider: FulfillmentProvider,
    action: FulfillmentAction,
) -> FulfillmentAction:
    if not provider.support_phone_encrypted:
        raise ValueError(f"Provider {provider.name} has no support phone configured")
    phone = decrypt_text(provider.support_phone_encrypted)
    call = None
    if action.telephony_call_id:
        call = await db.get(TelephonyCall, action.telephony_call_id)
    if call is None:
        call = await create_outbound_call(
            db,
            target=phone,
            purpose=_decrypt_text(request.goal_encrypted) or request.title,
            expected_outcome=str(_decrypt_json(request.details_encrypted).get("expected_outcome") or request.title),
            idempotency_key=action.idempotency_key,
            objective_id=request.va_objective_id,
        )
    action.telephony_call_id = call.id
    action.status = "waiting_provider"
    action.started_at = action.started_at or utcnow()
    request.status = "waiting_provider"
    request.started_at = request.started_at or utcnow()
    await _sync_va_state(db, request)
    await db.commit()
    return action


async def _complete_request(
    db: AsyncSession,
    request: FulfillmentRequest,
    *,
    provider: str,
    external_ref: str,
    details: dict[str, Any],
) -> None:
    request.status = "completed"
    request.requires_user_action = False
    request.needs_user_reason = ""
    request.last_error = ""
    request.completed_at = request.completed_at or utcnow()
    request.next_action_at = None
    await _record_evidence(
        db,
        request,
        evidence_type="objective_completed",
        provider=provider,
        external_ref=external_ref,
        details=details,
        evidence_key=f"fulfillment:{request.id}:completed:{provider}:{external_ref}"[:255],
    )
    await _sync_va_state(db, request)


async def _reconcile_tracking_browser_action(
    db: AsyncSession,
    request: FulfillmentRequest,
    action: FulfillmentAction,
    operation: BrowserOperation,
) -> None:
    provider = await db.get(FulfillmentProvider, request.provider_id) if request.provider_id else None
    if provider is None or not provider.enabled:
        action.status = "blocked_capability"
        action.last_error = "Tracking provider is missing or disabled"
        request.status = "blocked_capability"
        request.needs_user_reason = action.last_error
        await _sync_va_state(db, request)
        return

    recipe = await _recipe_for(provider, "logistics")
    config = _tracking_config(recipe)
    matches = await _latest_browser_observation_matches(db, operation)
    state = _tracking_state_from_matches(matches)
    stalled = await _tracking_is_stalled(
        db,
        request,
        state=state,
        threshold_hours=config["stalled_after_hours"],
    )
    observation = await _record_tracking_observation(
        db,
        request,
        action,
        provider=provider,
        operation=operation,
        state=state,
        matches=matches,
        stalled=stalled,
    )
    action.status = "completed"
    action.completed_at = action.completed_at or utcnow()
    action.last_error = ""

    order = await db.get(OrderRecord, request.order_id) if request.order_id else None
    if order is not None and state != "unknown":
        order.status = "exception" if state == "returned" else state

    if state == "delivered":
        await _complete_request(
            db,
            request,
            provider=provider.slug,
            external_ref=str(observation.id),
            details={
                "tracking_observation_id": observation.id,
                "tracking_state": state,
                "browser_operation_id": operation.id,
                "provider_postcondition_verified": True,
            },
        )
        return

    request.completed_at = None
    request.last_error = ""
    request.next_action_at = _tracking_recheck_at(state, config)
    request.requires_user_action = False
    request.needs_user_reason = ""
    if state == "available_for_pickup":
        request.status = "needs_user"
        request.requires_user_action = True
        request.needs_user_reason = (
            "The carrier reports the parcel is ready for physical pickup. VAAPP will keep monitoring, "
            "but collection requires a person or another real-world executor."
        )
    else:
        request.status = "waiting_provider"
        if state in {"exception", "returned"}:
            request.last_error = (
                f"Carrier tracking reports {state.replace('_', ' ')}. VAAPP retains ownership and will recheck; "
                "provider-specific support escalation can be configured separately."
            )
        elif stalled:
            request.last_error = (
                f"Carrier tracking has remained {state.replace('_', ' ')} beyond the configured stall threshold; "
                "VAAPP will continue bounded rechecks instead of declaring the objective complete."
            )
    await _sync_va_state(db, request)


async def _tracking_browser_failure(
    db: AsyncSession,
    request: FulfillmentRequest,
    action: FulfillmentAction,
    operation: BrowserOperation,
) -> None:
    if operation.status == "creation_uncertain":
        action.status = "blocked_system"
        action.last_error = operation.last_error or operation.status
        request.status = "blocked_system"
        request.last_error = action.last_error
        request.next_action_at = utcnow() + timedelta(hours=12)
        await _sync_va_state(db, request)
        return

    provider = await db.get(FulfillmentProvider, request.provider_id) if request.provider_id else None
    recipe = await _recipe_for(provider, "logistics") if provider is not None else {}
    config = _tracking_config(recipe)
    recent = list(
        (
            await db.execute(
                select(FulfillmentAction)
                .where(FulfillmentAction.request_id == request.id)
                .order_by(FulfillmentAction.sequence.desc())
                .limit(8)
            )
        ).scalars()
    )
    consecutive_failures = 1
    for previous in recent:
        if previous.id == action.id:
            continue
        if previous.status != "failed":
            break
        consecutive_failures += 1
    base = config["error_recheck_minutes"]
    retry_minutes = min(720, base * (2 ** min(consecutive_failures - 1, 3)))
    action.status = "failed"
    action.completed_at = action.completed_at or utcnow()
    action.last_error = operation.last_error or operation.status
    request.status = "waiting_provider"
    request.requires_user_action = False
    request.needs_user_reason = ""
    request.last_error = (
        f"Carrier tracking check failed ({action.last_error}); retry {consecutive_failures} is scheduled automatically."
    )[:8000]
    request.next_action_at = utcnow() + timedelta(minutes=retry_minutes)
    await _sync_va_state(db, request)


async def _reconcile_existing_action(db: AsyncSession, request: FulfillmentRequest, action: FulfillmentAction) -> None:
    if action.browser_operation_id:
        operation = await db.get(BrowserOperation, action.browser_operation_id)
        if operation is None:
            action.status = "blocked_system"
            action.last_error = "Linked browser operation no longer exists"
            request.status = "blocked_system"
            request.last_error = action.last_error
            await _sync_va_state(db, request)
            return
        if operation.status in _BROWSER_SUCCESS or operation.verified_at is not None:
            if request.request_type == "logistics":
                await _reconcile_tracking_browser_action(db, request, action, operation)
                return
            action.status = "completed"
            action.completed_at = action.completed_at or utcnow()
            await _record_evidence(
                db,
                request,
                action=action,
                evidence_type="browser_postcondition_verified",
                provider="secure_browser",
                external_ref=str(operation.id),
                details={"browser_operation_id": operation.id, "status": operation.status, "verified_at": operation.verified_at},
            )
            await _complete_request(
                db,
                request,
                provider="secure_browser",
                external_ref=str(operation.id),
                details={"browser_operation_id": operation.id, "provider_postcondition_verified": True},
            )
            return
        if operation.status in _BROWSER_USER:
            action.status = "needs_user"
            request.status = "needs_user"
            request.requires_user_action = True
            request.needs_user_reason = operation.challenge_prompt or "Provider authentication is required to continue this browser operation"
            await _sync_va_state(db, request)
            return
        if operation.status in _BROWSER_FAILURE:
            if request.request_type == "logistics":
                await _tracking_browser_failure(db, request, action, operation)
                return
            action.status = "blocked_system" if operation.status == "creation_uncertain" else "failed"
            action.last_error = operation.last_error or operation.status
            request.status = "blocked_system" if operation.status == "creation_uncertain" else "failed"
            request.last_error = action.last_error
            await _sync_va_state(db, request)
            return
        action.status = "waiting_provider"
        request.status = "verifying" if operation.status == "verifying" else "waiting_provider"
        await _sync_va_state(db, request)
        return

    if action.telephony_call_id:
        call = await db.get(TelephonyCall, action.telephony_call_id)
        if call is None:
            action.status = "blocked_system"
            action.last_error = "Linked telephony call no longer exists"
            request.status = "blocked_system"
            request.last_error = action.last_error
            await _sync_va_state(db, request)
            return
        call = await reconcile_call(db, call)
        if call.verification_status == "verified":
            action.status = "completed"
            action.completed_at = action.completed_at or utcnow()
            await _record_evidence(
                db,
                request,
                action=action,
                evidence_type="counterparty_outcome_verified",
                provider="twilio",
                external_ref=call.external_call_sid or str(call.id),
                details={"telephony_call_id": call.id, "verification_status": call.verification_status},
            )
            await _complete_request(
                db,
                request,
                provider="twilio",
                external_ref=call.external_call_sid or str(call.id),
                details={"telephony_call_id": call.id, "counterparty_outcome_verified": True},
            )
            return
        if call.status == "needs_user":
            action.status = "needs_user"
            request.status = "needs_user"
            request.requires_user_action = True
            request.needs_user_reason = call.failure_reason or "The support call reached a material/authentication step requiring the account holder"
            await _sync_va_state(db, request)
            return
        if call.status in _CALL_TERMINAL:
            action.status = "waiting_provider"
            request.status = "waiting_provider"
            request.next_action_at = utcnow() + timedelta(hours=24)
            request.last_error = call.failure_reason or "Call ended without verified support outcome; VA retains ownership for follow-up"
            await _sync_va_state(db, request)
            return
        action.status = "waiting_provider"
        request.status = "waiting_provider"
        await _sync_va_state(db, request)


async def run_request(db: AsyncSession, request: FulfillmentRequest) -> FulfillmentRequest:
    if request.status in TERMINAL_REQUEST_STATES:
        return request
    if request.order_id:
        order = await db.get(OrderRecord, request.order_id)
        if order is not None and order.status.casefold() in ORDER_TERMINAL_STATES:
            await _complete_request(
                db,
                request,
                provider="order_ledger",
                external_ref=str(order.id),
                details={"order_id": order.id, "order_status": order.status},
            )
            await db.commit()
            return request
    if request.support_case_id:
        case = await db.get(SupportCase, request.support_case_id)
        if case is not None and case.status.casefold() in SUPPORT_TERMINAL_STATES:
            await _complete_request(
                db,
                request,
                provider="support_case_ledger",
                external_ref=str(case.id),
                details={"support_case_id": case.id, "support_status": case.status},
            )
            await db.commit()
            return request

    active = (
        await db.execute(
            select(FulfillmentAction)
            .where(FulfillmentAction.request_id == request.id)
            .order_by(FulfillmentAction.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None and active.status not in {"completed", "failed", "cancelled"}:
        await _reconcile_existing_action(db, request, active)
        await db.commit()
        return request

    provider = await db.get(FulfillmentProvider, request.provider_id) if request.provider_id else None
    if provider is None or not provider.enabled:
        request.status = "blocked_capability"
        request.requires_user_action = False
        request.needs_user_reason = "No enabled fulfillment provider is configured for this objective"
        await _sync_va_state(db, request)
        await db.commit()
        return request

    auto_service = (await get_runtime_value(db, "fulfillment_auto_service_enabled", "true")).lower() == "true"
    auto_tracking = (await get_runtime_value(db, "fulfillment_tracking_enabled", "true")).lower() == "true"
    auto_returns = (await get_runtime_value(db, "fulfillment_auto_returns_enabled", "true")).lower() == "true"
    if request.request_type == "logistics" and not auto_tracking:
        request.status = "blocked_capability"
        request.needs_user_reason = "Automatic logistics tracking is disabled"
        await _sync_va_state(db, request)
        await db.commit()
        return request
    if request.request_type in {"return", "refund", "cancel"} and not auto_returns:
        request.status = "blocked_capability"
        request.needs_user_reason = "Automatic returns/refunds/cancellations are disabled"
        await _sync_va_state(db, request)
        await db.commit()
        return request
    if request.request_type == "customer_service" and not auto_service:
        request.status = "blocked_capability"
        request.needs_user_reason = "Automatic customer service ownership is disabled"
        await _sync_va_state(db, request)
        await db.commit()
        return request

    recipe = await _recipe_for(provider, request.request_type)
    if recipe and provider.browser_portal_id:
        action = await _ensure_action(db, request, action_type="browser_operation", details={"provider_id": provider.id})
        try:
            await _dispatch_browser_action(db, request, provider, action)
        except Exception as exc:
            request.status = "blocked_capability" if isinstance(exc, ValueError) else "blocked_system"
            request.last_error = str(exc)[:8000]
            request.needs_user_reason = request.last_error if request.status == "blocked_capability" else ""
            action.status = request.status
            action.last_error = request.last_error
            await _sync_va_state(db, request)
            await db.commit()
        return request

    if request.request_type == "customer_service" and provider.support_phone_encrypted:
        action = await _ensure_action(db, request, action_type="telephony_support", details={"provider_id": provider.id})
        try:
            await _dispatch_support_call(db, request, provider, action)
        except Exception as exc:
            request.status = "blocked_capability" if isinstance(exc, ValueError) else "blocked_system"
            request.last_error = str(exc)[:8000]
            request.needs_user_reason = request.last_error if request.status == "blocked_capability" else ""
            action.status = request.status
            action.last_error = request.last_error
            await _sync_va_state(db, request)
            await db.commit()
        return request

    request.status = "blocked_capability"
    request.requires_user_action = False
    request.needs_user_reason = f"Provider {provider.name} has no real executor recipe or support number for {request.request_type}"
    await _sync_va_state(db, request)
    await db.commit()
    return request


async def ingest_existing_operations(db: AsyncSession) -> dict[str, int]:
    created = {"orders": 0, "support_cases": 0}
    orders = list((await db.execute(select(OrderRecord).order_by(OrderRecord.id.desc()).limit(500))).scalars())
    for order in orders:
        if not order.merchant or not order.order_number:
            continue
        candidate, reason = await order_is_fulfillment_candidate(db, order)
        if not candidate:
            if order.status != "not_order":
                await dismiss_order_record(db, order, reason=reason, explicit=False)
            continue
        key = f"order:{order.merchant.casefold()}:{order.order_number}"[:255]
        existing = (
            await db.execute(select(FulfillmentRequest).where(FulfillmentRequest.idempotency_key == key).limit(1))
        ).scalar_one_or_none()
        if existing is None:
            provider = await _provider_for_order(db, order)
            row = await create_request(
                db,
                idempotency_key=key,
                request_type="logistics",
                title=f"Track {order.merchant} order {order.order_number}",
                goal="Track delivery through completion and own delays or delivery exceptions.",
                provider_id=provider.id if provider else None,
                account_scope=order.account_scope if order.account_scope in {"personal", "pro"} else "personal",
                amount=order.total_amount,
                currency=order.currency,
                details={
                    "merchant": order.merchant,
                    "order_number": order.order_number,
                    "tracking_url": order.tracking_url,
                    "expected_delivery_at": order.expected_delivery_at,
                },
                source_type="order",
                source_id=str(order.id),
                order_id=order.id,
            )
            if order.status.casefold() in ORDER_TERMINAL_STATES:
                await _complete_request(
                    db,
                    row,
                    provider="order_ledger",
                    external_ref=str(order.id),
                    details={"order_status": order.status},
                )
                await db.commit()
            created["orders"] += 1
        elif existing.provider_id is None:
            provider = await _provider_for_order(db, order)
            if provider is not None:
                existing.provider_id = provider.id
                if existing.status == "blocked_capability":
                    existing.status = "planned"
                    existing.needs_user_reason = ""
                    existing.next_action_at = utcnow()
                await db.commit()

    cases = list((await db.execute(select(SupportCase).order_by(SupportCase.id.desc()).limit(500))).scalars())
    for case in cases:
        key = f"support-case:{case.id}"
        existing = (
            await db.execute(select(FulfillmentRequest).where(FulfillmentRequest.idempotency_key == key).limit(1))
        ).scalar_one_or_none()
        if existing is None:
            provider = await _provider_for_support(db, case)
            row = await create_request(
                db,
                idempotency_key=key,
                request_type="customer_service",
                title=case.subject or f"Customer service case {case.id}",
                goal=f"Own this customer-service case through a verified resolution: {case.subject}",
                provider_id=provider.id if provider else None,
                account_scope="personal",
                amount=None,
                currency="EUR",
                details={
                    "requester": case.requester,
                    "category": case.category,
                    "expected_outcome": "The provider confirms that the support issue is resolved or provides a concrete next step and deadline.",
                },
                priority=case.priority,
                source_type="support_case",
                source_id=str(case.id),
                support_case_id=case.id,
            )
            if case.status.casefold() in SUPPORT_TERMINAL_STATES:
                await _complete_request(
                    db,
                    row,
                    provider="support_case_ledger",
                    external_ref=str(case.id),
                    details={"support_status": case.status},
                )
                await db.commit()
            created["support_cases"] += 1
        elif existing.provider_id is None:
            provider = await _provider_for_support(db, case)
            if provider is not None:
                existing.provider_id = provider.id
                if existing.status == "blocked_capability":
                    existing.status = "planned"
                    existing.needs_user_reason = ""
                    existing.next_action_at = utcnow()
                await db.commit()
    return created


async def reconcile_fulfillment(db: AsyncSession, *, limit: int = 100) -> dict[str, int]:
    ingested = await ingest_existing_operations(db)
    now = utcnow()
    rows = list(
        (
            await db.execute(
                select(FulfillmentRequest)
                .where(
                    FulfillmentRequest.status.not_in(list(TERMINAL_REQUEST_STATES)),
                    (FulfillmentRequest.next_action_at.is_(None) | (FulfillmentRequest.next_action_at <= now)),
                )
                .order_by(FulfillmentRequest.priority.desc(), FulfillmentRequest.id.asc())
                .limit(max(1, min(limit, 250)))
            )
        ).scalars()
    )
    outcome = {
        "ingested_orders": ingested["orders"],
        "ingested_support_cases": ingested["support_cases"],
        "processed": 0,
        "completed": 0,
        "needs_user": 0,
        "blocked": 0,
    }
    for row in rows:
        previous = row.status
        try:
            await run_request(db, row)
        except Exception as exc:
            await db.rollback()
            fresh = await db.get(FulfillmentRequest, row.id)
            if fresh is not None:
                fresh.status = "blocked_system"
                fresh.last_error = str(exc)[:8000]
                await _sync_va_state(db, fresh)
                await db.commit()
                row = fresh
        outcome["processed"] += 1
        outcome["completed"] += int(row.status == "completed" and previous != "completed")
        outcome["needs_user"] += int(row.status == "needs_user")
        outcome["blocked"] += int(row.status.startswith("blocked"))
    return outcome


async def cancel_request(db: AsyncSession, request: FulfillmentRequest) -> FulfillmentRequest:
    if request.status == "completed":
        raise ValueError("A completed fulfillment objective cannot be cancelled retroactively")
    request.status = "cancelled"
    request.completed_at = request.completed_at or utcnow()
    request.next_action_at = None
    request.requires_user_action = False
    request.needs_user_reason = ""
    await _sync_va_state(db, request)
    await write_audit(
        db,
        "fulfillment_request_cancelled",
        entity_type="fulfillment_request",
        entity_id=str(request.id),
    )
    await db.commit()
    return request


async def serialize_request(db: AsyncSession, request: FulfillmentRequest, *, include_actions: bool = True) -> dict[str, Any]:
    provider = await db.get(FulfillmentProvider, request.provider_id) if request.provider_id else None
    payload: dict[str, Any] = {
        "id": request.id,
        "idempotency_key": request.idempotency_key,
        "request_type": request.request_type,
        "provider_id": request.provider_id,
        "provider_name": provider.name if provider else "",
        "account_scope": request.account_scope,
        "title": request.title,
        "goal": _decrypt_text(request.goal_encrypted),
        "details": _decrypt_json(request.details_encrypted),
        "amount": request.amount,
        "currency": request.currency,
        "status": request.status,
        "priority": request.priority,
        "risk_level": request.risk_level,
        "source_type": request.source_type,
        "source_id": request.source_id,
        "va_objective_id": request.va_objective_id,
        "order_id": request.order_id,
        "support_case_id": request.support_case_id,
        "requires_user_action": request.requires_user_action,
        "needs_user_reason": request.needs_user_reason,
        "material_authorized_at": request.material_authorized_at,
        "authorization_basis": request.authorization_basis,
        "last_error": request.last_error,
        "next_action_at": request.next_action_at,
        "started_at": request.started_at,
        "completed_at": request.completed_at,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }
    if request.request_type == "logistics":
        latest_observation = (
            await db.execute(
                select(FulfillmentObservation)
                .where(FulfillmentObservation.request_id == request.id)
                .order_by(FulfillmentObservation.observed_at.desc(), FulfillmentObservation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        payload["tracking"] = (
            {
                "state": latest_observation.state,
                "provider": latest_observation.provider,
                "terminal": latest_observation.terminal,
                "stalled": latest_observation.stalled,
                "external_ref": latest_observation.external_ref,
                "details": _decrypt_json(latest_observation.details_encrypted),
                "observed_at": latest_observation.observed_at,
                "next_check_at": request.next_action_at,
            }
            if latest_observation is not None
            else {
                "state": "not_observed",
                "terminal": False,
                "stalled": False,
                "next_check_at": request.next_action_at,
            }
        )
    if include_actions:
        actions = list(
            (
                await db.execute(
                    select(FulfillmentAction)
                    .where(FulfillmentAction.request_id == request.id)
                    .order_by(FulfillmentAction.sequence.asc())
                )
            ).scalars()
        )
        payload["actions"] = [
            {
                "id": action.id,
                "sequence": action.sequence,
                "action_type": action.action_type,
                "status": action.status,
                "browser_operation_id": action.browser_operation_id,
                "telephony_call_id": action.telephony_call_id,
                "details": _decrypt_json(action.details_encrypted),
                "verification_type": action.verification_type,
                "last_error": action.last_error,
                "run_after": action.run_after,
                "started_at": action.started_at,
                "completed_at": action.completed_at,
            }
            for action in actions
        ]
        evidence = list(
            (
                await db.execute(
                    select(FulfillmentEvidence)
                    .where(FulfillmentEvidence.request_id == request.id)
                    .order_by(FulfillmentEvidence.id.desc())
                    .limit(50)
                )
            ).scalars()
        )
        payload["evidence"] = [
            {
                "id": item.id,
                "evidence_type": item.evidence_type,
                "provider": item.provider,
                "external_ref": item.external_ref,
                "details": _decrypt_json(item.details_encrypted),
                "observed_at": item.observed_at,
            }
            for item in evidence
        ]
        if request.request_type == "logistics":
            observations = list(
                (
                    await db.execute(
                        select(FulfillmentObservation)
                        .where(FulfillmentObservation.request_id == request.id)
                        .order_by(FulfillmentObservation.observed_at.desc(), FulfillmentObservation.id.desc())
                        .limit(30)
                    )
                ).scalars()
            )
            payload["observations"] = [
                {
                    "id": item.id,
                    "provider": item.provider,
                    "state": item.state,
                    "terminal": item.terminal,
                    "stalled": item.stalled,
                    "external_ref": item.external_ref,
                    "details": _decrypt_json(item.details_encrypted),
                    "observed_at": item.observed_at,
                }
                for item in observations
            ]
    return payload


async def list_requests(db: AsyncSession, *, limit: int = 200, status: str | None = None) -> list[dict[str, Any]]:
    query = select(FulfillmentRequest).order_by(FulfillmentRequest.updated_at.desc(), FulfillmentRequest.id.desc()).limit(
        max(1, min(limit, 500))
    )
    if status:
        query = query.where(FulfillmentRequest.status == status)
    else:
        query = query.where(FulfillmentRequest.status != "dismissed")
    rows = list((await db.execute(query)).scalars())
    visible: list[FulfillmentRequest] = []
    for row in rows:
        if row.order_id and row.status != "dismissed":
            order = await db.get(OrderRecord, row.order_id)
            if order is not None:
                candidate, reason = await order_is_fulfillment_candidate(db, order)
                if not candidate:
                    # Reclassification is deterministic reconciliation, not a user-facing
                    # side effect. Correct stale Phase-9 logistics rows while they are read
                    # so payment receipts disappear immediately rather than waiting for the
                    # five-minute scheduler. Source Gmail/financial evidence remains intact.
                    await dismiss_order_record(db, order, reason=reason, explicit=False)
                    if not status:
                        continue
        visible.append(row)
    return [await serialize_request(db, row, include_actions=False) for row in visible]


async def fulfillment_status(db: AsyncSession) -> dict[str, Any]:
    total = int((await db.execute(select(func.count(FulfillmentRequest.id)))).scalar_one())
    open_count = int(
        (
            await db.execute(
                select(func.count(FulfillmentRequest.id)).where(
                    FulfillmentRequest.status.not_in(list(TERMINAL_REQUEST_STATES))
                )
            )
        ).scalar_one()
    )
    needs_user = int(
        (
            await db.execute(
                select(func.count(FulfillmentRequest.id)).where(FulfillmentRequest.status == "needs_user")
            )
        ).scalar_one()
    )
    blocked = int(
        (
            await db.execute(
                select(func.count(FulfillmentRequest.id)).where(
                    FulfillmentRequest.status.in_(["blocked_capability", "blocked_system"])
                )
            )
        ).scalar_one()
    )
    providers = int(
        (
            await db.execute(
                select(func.count(FulfillmentProvider.id)).where(FulfillmentProvider.enabled.is_(True))
            )
        ).scalar_one()
    )
    tracking_waiting = int(
        (
            await db.execute(
                select(func.count(FulfillmentRequest.id)).where(
                    FulfillmentRequest.request_type == "logistics",
                    FulfillmentRequest.status.in_(["waiting_provider", "verifying", "needs_user"]),
                )
            )
        ).scalar_one()
    )
    return {
        "status": "needs_user" if needs_user else "blocked" if blocked and open_count else "active",
        "total": total,
        "open": open_count,
        "needs_user": needs_user,
        "blocked": blocked,
        "enabled_providers": providers,
        "tracking_waiting": tracking_waiting,
        "auto_purchase_enabled": (await get_runtime_value(db, "fulfillment_auto_purchase_enabled", "false")).lower() == "true",
        "auto_travel_enabled": (await get_runtime_value(db, "fulfillment_auto_travel_enabled", "false")).lower() == "true",
        "tracking_enabled": (await get_runtime_value(db, "fulfillment_tracking_enabled", "true")).lower() == "true",
        "auto_service_enabled": (await get_runtime_value(db, "fulfillment_auto_service_enabled", "true")).lower() == "true",
        "checked_at": utcnow().isoformat() + "Z",
    }
