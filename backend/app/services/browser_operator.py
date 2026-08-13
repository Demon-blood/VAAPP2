from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.models.entities import (
    BrowserCredential,
    BrowserEvidence,
    BrowserOperation,
    BrowserPortal,
    BrowserSessionState,
    VAObjective,
    VAObjectiveStep,
)
from app.services.audit import write_audit
from app.services.workflow_engine import enqueue_job


class BrowserConfigurationError(RuntimeError):
    pass


class BrowserSecurityError(RuntimeError):
    pass


class BrowserNeedsUserAuth(RuntimeError):
    def __init__(self, challenge_type: str, prompt: str, selector: str = ""):
        super().__init__(prompt)
        self.challenge_type = challenge_type
        self.prompt = prompt
        self.selector = selector


def utcnow() -> datetime:
    return datetime.utcnow()


def _loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _safe_url_for_log(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def _normalized_host(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    if ":" in value and not value.startswith("["):
        value = value.split(":", 1)[0]
    return value


def _host_from_url(value: str) -> str:
    try:
        return _normalized_host(urlsplit(value).hostname or "")
    except ValueError:
        return ""


def _host_is_private_or_local(host: str) -> bool:
    host = _normalized_host(host)
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def _host_resolves_private_or_local(host: str, cache: dict[str, bool]) -> bool:
    """Resolve a hostname at execution time to reduce DNS-rebinding/SSRF risk."""

    host = _normalized_host(host)
    if _host_is_private_or_local(host):
        return True
    if host in cache:
        return cache[host]
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            None,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # A host that does not resolve cannot be reached by Chromium either. Keep
        # provider/network failures separate from the private-network policy.
        cache[host] = False
        return False
    private = False
    for record in records:
        address_text = str(record[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            private = True
            break
    cache[host] = private
    return private


def portal_allowed_hosts(portal: BrowserPortal) -> set[str]:
    hosts = {_host_from_url(portal.base_url), _host_from_url(portal.login_url)}
    raw = _loads(portal.allowed_hosts_json, [])
    if isinstance(raw, list):
        hosts.update(_normalized_host(str(item)) for item in raw)
    return {host for host in hosts if host}


def assert_portal_url(portal: BrowserPortal, url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise BrowserSecurityError("Portal URL is invalid") from exc
    if parsed.scheme != "https":
        raise BrowserSecurityError("Browser operator requires HTTPS portal URLs")
    if parsed.username or parsed.password:
        raise BrowserSecurityError("Credentials must not be embedded in portal URLs")
    host = _normalized_host(parsed.hostname or "")
    if _host_is_private_or_local(host):
        raise BrowserSecurityError("Browser operator refuses localhost/private-network targets")
    allowed = portal_allowed_hosts(portal)
    if not allowed:
        raise BrowserSecurityError("Portal has no allowlisted host")
    if not any(host == item or host.endswith("." + item) for item in allowed):
        raise BrowserSecurityError(f"Navigation to host {host or '<empty>'} is outside this portal's allowlist")


def _normalize_allowed_hosts(base_url: str, login_url: str, hosts: list[str]) -> list[str]:
    values = {_host_from_url(base_url), _host_from_url(login_url)}
    values.update(_normalized_host(item) for item in hosts)
    normalized = sorted(item for item in values if item)
    for host in normalized:
        if _host_is_private_or_local(host):
            raise BrowserSecurityError(f"Private/local host is not allowed: {host}")
    return normalized


def _material_commitment_from_plan(steps: list[dict[str, Any]]) -> bool:
    dangerous = re.compile(
        r"\b(pay|purchase|buy|place order|submit order|transfer|withdraw|sign|accept contract|"
        r"delete account|close account|change password|change security|authorize payment|confirm payment)\b",
        re.IGNORECASE,
    )
    for step in steps:
        if bool(step.get("material_commitment")):
            return True
        probe = " ".join(
            str(step.get(key) or "") for key in ("label", "description", "text", "selector", "name")
        )
        if dangerous.search(probe):
            return True
    return False


def validate_operation_plan(portal: BrowserPortal, steps: list[dict[str, Any]], verification: dict[str, Any]) -> dict[str, Any]:
    if not steps or len(steps) > 50:
        raise ValueError("Browser operation must contain between 1 and 50 steps")
    allowed_kinds = {"goto", "click", "fill", "select", "check", "uncheck", "press", "wait_for", "extract", "autofill_form", "click_action"}
    normalized_steps: list[dict[str, Any]] = []
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise ValueError(f"Browser step {index + 1} must be an object")
        step = dict(raw)
        kind = str(step.get("kind") or "").strip().lower()
        if kind not in allowed_kinds:
            raise ValueError(f"Unsupported browser step kind: {kind or '<empty>'}")
        step["kind"] = kind
        if kind == "goto":
            url = str(step.get("url") or "").strip()
            assert_portal_url(portal, url)
            step["url"] = url
        if kind in {"click", "fill", "select", "check", "uncheck", "wait_for", "extract"}:
            selector = str(step.get("selector") or "").strip()
            if not selector:
                raise ValueError(f"Browser step {index + 1} requires a selector")
            step["selector"] = selector[:2000]
        if kind == "autofill_form":
            raw_fields = step.get("fields") or []
            if not isinstance(raw_fields, list) or len(raw_fields) > 50:
                raise ValueError("autofill_form fields must be a list of at most 50 verified values")
            fields: list[dict[str, Any]] = []
            for raw_field in raw_fields:
                if not isinstance(raw_field, dict):
                    raise ValueError("autofill_form field entries must be objects")
                key = str(raw_field.get("key") or "").strip()[:120]
                aliases = raw_field.get("aliases") or []
                if not key or not isinstance(aliases, list) or not aliases:
                    raise ValueError("autofill_form fields require a key and aliases")
                fields.append({
                    "key": key,
                    "aliases": [str(item)[:160] for item in aliases[:12] if str(item).strip()],
                    "value": str(raw_field.get("value") or "")[:16000],
                })
            step["fields"] = fields
        if kind == "fill":
            value_from = str(step.get("value_from") or "")
            if value_from and value_from not in {"credential.username", "credential.password", "auth_code"}:
                raise ValueError("Browser fill value_from must reference a supported encrypted credential/auth value")
            if value_from and "value" in step:
                raise ValueError("Browser fill may use value or value_from, not both")
            if not value_from:
                step["value"] = str(step.get("value") or "")[:16000]
        if kind == "click_action":
            labels = step.get("labels") or ["Submit", "Send", "Continue"]
            if not isinstance(labels, list) or not labels:
                raise ValueError("click_action labels must be a non-empty list")
            step["labels"] = [str(item)[:120] for item in labels[:12] if str(item).strip()]
        if kind == "select":
            step["value"] = str(step.get("value") or "")[:4000]
        if kind == "press":
            step["key"] = str(step.get("key") or "Enter")[:80]
            selector = str(step.get("selector") or "").strip()
            if selector:
                step["selector"] = selector[:2000]
        if kind in {"click", "press", "click_action"}:
            # Clicks/Enter can submit forms. Fail closed unless the plan explicitly
            # says the action is replay-safe.
            step["side_effect"] = bool(step.get("side_effect", True))
        else:
            step["side_effect"] = bool(step.get("side_effect", False))
        step["replay_safe"] = bool(step.get("replay_safe", False))
        step["material_commitment"] = bool(step.get("material_commitment", False))
        normalized_steps.append(step)

    if not isinstance(verification, dict) or not verification:
        raise ValueError("Browser operation requires an explicit provider postcondition")
    supported_verification = {
        "url_contains",
        "title_contains",
        "text_contains",
        "text_any_contains",
        "selector",
        "selector_absent",
    }
    if not any(key in verification for key in supported_verification):
        raise ValueError("Browser verification must include a URL, title, text, or selector postcondition")
    return {
        "steps": normalized_steps,
        "material_commitment": _material_commitment_from_plan(normalized_steps),
    }


async def upsert_portal(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    base_url: str,
    login_url: str = "",
    allowed_hosts: list[str] | None = None,
    login_recipe: dict[str, Any] | None = None,
    account_scope: str = "personal",
    enabled: bool = True,
) -> BrowserPortal:
    normalized_hosts = _normalize_allowed_hosts(base_url, login_url, allowed_hosts or [])
    temporary = BrowserPortal(
        slug=slug,
        name=name,
        base_url=base_url,
        login_url=login_url,
        allowed_hosts_json=_dump(normalized_hosts),
    )
    assert_portal_url(temporary, base_url)
    if login_url:
        assert_portal_url(temporary, login_url)

    portal = (
        await db.execute(select(BrowserPortal).where(BrowserPortal.slug == slug).limit(1))
    ).scalar_one_or_none()
    if portal is None:
        portal = BrowserPortal(slug=slug, name=name, base_url=base_url)
        db.add(portal)
    portal.name = name.strip()[:255]
    portal.base_url = base_url.strip()
    portal.login_url = login_url.strip()
    portal.allowed_hosts_json = _dump(normalized_hosts)
    portal.login_recipe_json = _dump(login_recipe or {})
    portal.account_scope = account_scope
    portal.enabled = bool(enabled)
    await db.flush()
    await write_audit(
        db,
        "browser_portal_configured",
        entity_type="browser_portal",
        entity_id=str(portal.id),
        details={"slug": portal.slug, "allowed_hosts": normalized_hosts, "enabled": portal.enabled},
    )
    await db.commit()
    await db.refresh(portal)
    return portal


async def set_portal_credentials(
    db: AsyncSession,
    *,
    portal_id: int,
    username: str,
    password: str,
) -> BrowserCredential:
    portal = await db.get(BrowserPortal, portal_id)
    if portal is None:
        raise LookupError("Browser portal not found")
    row = (
        await db.execute(select(BrowserCredential).where(BrowserCredential.portal_id == portal_id).limit(1))
    ).scalar_one_or_none()
    if row is None:
        row = BrowserCredential(portal_id=portal_id)
        db.add(row)
    row.username_encrypted = encrypt_text(username) if username else ""
    row.password_encrypted = encrypt_text(password) if password else ""
    await write_audit(
        db,
        "browser_portal_credentials_updated",
        entity_type="browser_portal",
        entity_id=str(portal_id),
        details={"username_configured": bool(username), "password_configured": bool(password)},
    )
    await db.commit()
    await db.refresh(row)
    return row


async def _session_for_portal(db: AsyncSession, portal_id: int) -> BrowserSessionState:
    row = (
        await db.execute(select(BrowserSessionState).where(BrowserSessionState.portal_id == portal_id).limit(1))
    ).scalar_one_or_none()
    if row is None:
        row = BrowserSessionState(portal_id=portal_id)
        db.add(row)
        await db.flush()
    return row


async def prepare_browser_operation(
    db: AsyncSession,
    *,
    idempotency_key: str,
    portal_id: int,
    title: str,
    steps: list[dict[str, Any]],
    verification: dict[str, Any],
    objective_id: int | None = None,
    step_id: int | None = None,
) -> BrowserOperation:
    existing = (
        await db.execute(select(BrowserOperation).where(BrowserOperation.idempotency_key == idempotency_key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    portal = await db.get(BrowserPortal, portal_id)
    if portal is None or not portal.enabled:
        raise BrowserConfigurationError("Browser portal is missing or disabled")
    normalized = validate_operation_plan(portal, steps, verification)
    operation = BrowserOperation(
        idempotency_key=idempotency_key[:255],
        portal_id=portal.id,
        objective_id=objective_id,
        step_id=step_id,
        title=title[:2000],
        plan_json=_dump({"step_count": len(normalized["steps"]), "kinds": [step["kind"] for step in normalized["steps"]], "material_commitment": bool(normalized["material_commitment"])}),
        plan_encrypted=encrypt_text(_dump(normalized)),
        verification_json=_dump({"keys": sorted(verification.keys())}),
        verification_encrypted=encrypt_text(_dump(verification)),
        status="pending",
        verify_after=utcnow(),
    )
    db.add(operation)
    await db.flush()
    await write_audit(
        db,
        "browser_operation_prepared",
        entity_type="browser_operation",
        entity_id=str(operation.id),
        details={
            "portal_id": portal.id,
            "step_count": len(normalized["steps"]),
            "material_commitment": bool(normalized["material_commitment"]),
        },
    )
    await db.commit()
    await db.refresh(operation)
    return operation


async def enqueue_browser_operation(db: AsyncSession, operation: BrowserOperation) -> None:
    await enqueue_job(
        db,
        job_type="browser.operation.run",
        payload={"browser_operation_id": operation.id},
        idempotency_key=f"browser.operation:{operation.id}:resume:{operation.resume_sequence}",
        priority=25,
        max_attempts=max(1, operation.max_attempts),
    )


def _credential_values(row: BrowserCredential | None) -> tuple[str, str]:
    if row is None:
        return "", ""
    username = decrypt_text(row.username_encrypted) if row.username_encrypted else ""
    password = decrypt_text(row.password_encrypted) if row.password_encrypted else ""
    return username, password


async def _visible(page: Page, selector: str) -> bool:
    try:
        locator = page.locator(selector).first
        return bool(await locator.is_visible(timeout=400))
    except Exception:
        return False


async def _first_visible_selector(page: Page, selectors: list[str]) -> str:
    for selector in selectors:
        if await _visible(page, selector):
            return selector
    return ""


async def _detect_challenge(page: Page, recipe: dict[str, Any]) -> tuple[str, str, str] | None:
    captcha_selectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        '[data-sitekey]',
        '.g-recaptcha',
        '.h-captcha',
    ]
    captcha = await _first_visible_selector(page, captcha_selectors)
    if captcha:
        return (
            "captcha",
            "This portal presented a CAPTCHA. VAAPP will not bypass it; manual completion is required.",
            captcha,
        )

    otp_selectors = [
        str(recipe.get("otp_selector") or "").strip(),
        'input[autocomplete="one-time-code"]',
        'input[name*="otp" i]',
        'input[name*="code" i]',
        'input[id*="otp" i]',
        'input[id*="code" i]',
    ]
    otp = await _first_visible_selector(page, [item for item in otp_selectors if item])
    if otp:
        return ("otp", "Enter the one-time authentication code shown or sent by the portal.", otp)

    try:
        text = (await page.locator("body").inner_text(timeout=1500))[:16000].casefold()
    except Exception:
        text = ""
    if any(
        phrase in text
        for phrase in (
            "approve sign-in",
            "approve this sign in",
            "check your phone",
            "security key",
            "two-factor authentication",
            "two factor authentication",
            "multi-factor authentication",
        )
    ):
        return (
            "external_approval",
            "Approve the sign-in with the portal's normal authentication method, then ask VAAPP to resume.",
            "",
        )
    return None


async def _save_storage_state(
    db: AsyncSession,
    context: BrowserContext,
    session: BrowserSessionState,
    *,
    status: str,
    error: str = "",
) -> None:
    state = await context.storage_state()
    session.storage_state_encrypted = encrypt_text(_dump(state))
    session.status = status
    session.last_used_at = utcnow()
    if status == "authenticated":
        session.last_authenticated_at = utcnow()
    session.last_error = error[:4000]
    await db.commit()


async def _add_evidence(
    db: AsyncSession,
    operation: BrowserOperation,
    page: Page,
    *,
    evidence_type: str,
    step_index: int | None,
    details: dict[str, Any] | None = None,
    screenshot: bool = False,
) -> BrowserEvidence:
    safe_url = _safe_url_for_log(page.url)
    try:
        title = (await page.title())[:1000]
    except Exception:
        title = ""
    payload = ""
    digest = ""
    if screenshot:
        image = await page.screenshot(type="png", full_page=False)
        digest = hashlib.sha256(image).hexdigest()
        payload = encrypt_text(base64.b64encode(image).decode("ascii"))
    evidence = BrowserEvidence(
        browser_operation_id=operation.id,
        evidence_type=evidence_type,
        step_index=step_index,
        url=safe_url,
        title=title,
        sha256=digest,
        details_json=_dump(details or {}),
        payload_encrypted=payload,
    )
    db.add(evidence)
    await db.flush()
    return evidence


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


async def verify_page(page: Page, portal: BrowserPortal, verification: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    assert_portal_url(portal, page.url)
    results: dict[str, Any] = {}
    passed = True
    safe_url = _safe_url_for_log(page.url)
    title = await page.title()

    for needle in _as_list(verification.get("url_contains")):
        matched = needle in page.url
        results.setdefault("url_contains", []).append({"value": needle, "matched": matched})
        passed = passed and matched
    for needle in _as_list(verification.get("title_contains")):
        matched = needle.casefold() in title.casefold()
        results.setdefault("title_contains", []).append({"value": needle, "matched": matched})
        passed = passed and matched

    text_needles = _as_list(verification.get("text_contains"))
    text_any_needles = _as_list(verification.get("text_any_contains"))
    body_text = ""
    if text_needles or text_any_needles:
        try:
            body_text = (await page.locator("body").inner_text(timeout=2000))[:200000]
        except Exception:
            body_text = ""
    for needle in text_needles:
        matched = needle.casefold() in body_text.casefold()
        results.setdefault("text_contains", []).append({"matched": matched, "sha256": hashlib.sha256(needle.encode()).hexdigest()})
        passed = passed and matched
    if text_any_needles:
        any_match = False
        rows = []
        for needle in text_any_needles:
            matched = needle.casefold() in body_text.casefold()
            rows.append({"matched": matched, "sha256": hashlib.sha256(needle.encode()).hexdigest()})
            any_match = any_match or matched
        results["text_any_contains"] = rows
        passed = passed and any_match

    for selector in _as_list(verification.get("selector")):
        matched = await _visible(page, selector)
        results.setdefault("selector", []).append({"selector": selector, "matched": matched})
        passed = passed and matched
    for selector in _as_list(verification.get("selector_absent")):
        matched = not await _visible(page, selector)
        results.setdefault("selector_absent", []).append({"selector": selector, "matched": matched})
        passed = passed and matched

    results["url"] = safe_url
    results["title"] = title[:1000]
    return passed, results


async def _resolve_fill_value(
    step: dict[str, Any],
    *,
    username: str,
    password: str,
    auth_code: str,
) -> str:
    source = str(step.get("value_from") or "")
    if source == "credential.username":
        return username
    if source == "credential.password":
        return password
    if source == "auth_code":
        return auth_code
    return str(step.get("value") or "")


async def _candidate_field_locator(page: Page, aliases: list[str]):
    for alias in aliases:
        for locator in (
            page.get_by_label(alias, exact=False),
            page.get_by_placeholder(alias, exact=False),
        ):
            try:
                first = locator.first
                if await first.is_visible(timeout=250):
                    return first
            except Exception:
                pass
        token = re.sub(r"[^a-z0-9]+", "", alias.casefold())
        if token:
            css = f'input[name*="{token}" i], textarea[name*="{token}" i], select[name*="{token}" i], input[id*="{token}" i], textarea[id*="{token}" i], select[id*="{token}" i]'
            try:
                first = page.locator(css).first
                if await first.is_visible(timeout=250):
                    return first
            except Exception:
                pass
    return None


async def _required_form_fields_missing(page: Page) -> list[str]:
    missing: list[str] = []
    locator = page.locator('input[required], textarea[required], select[required]')
    try:
        count = min(await locator.count(), 100)
    except Exception:
        return missing
    for index in range(count):
        item = locator.nth(index)
        try:
            if not await item.is_visible(timeout=100):
                continue
            tag = await item.evaluate("el => el.tagName.toLowerCase()")
            input_type = (await item.get_attribute("type") or "").lower()
            if input_type in {"checkbox", "radio"}:
                empty = not await item.is_checked()
            else:
                empty = not str(await item.input_value()).strip()
            if not empty:
                continue
            label = (await item.get_attribute("aria-label") or await item.get_attribute("placeholder") or await item.get_attribute("name") or await item.get_attribute("id") or tag or "required field")
            missing.append(str(label)[:120])
        except Exception:
            continue
    return missing[:12]


async def _perform_autofill_form(page: Page, step: dict[str, Any], timeout: int) -> dict[str, Any]:
    filled: list[str] = []
    for field in step.get("fields") or []:
        aliases = [str(item) for item in field.get("aliases") or [] if str(item)]
        locator = await _candidate_field_locator(page, aliases)
        if locator is None:
            continue
        value = str(field.get("value") or "")
        try:
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            input_type = (await locator.get_attribute("type") or "").lower()
            if tag == "select":
                try:
                    await locator.select_option(label=value, timeout=timeout)
                except Exception:
                    await locator.select_option(value=value, timeout=timeout)
            elif input_type in {"checkbox", "radio"}:
                if value.casefold() in {"1", "true", "yes", "y", "ja"}:
                    await locator.check(timeout=timeout)
            else:
                await locator.fill(value, timeout=timeout)
            filled.append(str(field.get("key") or "")[:120])
        except Exception:
            continue

    missing = await _required_form_fields_missing(page)
    if missing:
        raise BrowserNeedsUserAuth(
            "form_input",
            "The form needs verified information that VAAPP does not have yet: " + ", ".join(missing),
        )
    return {"filled_keys": filled, "required_fields_satisfied": True}


async def _perform_click_action(page: Page, step: dict[str, Any], timeout: int) -> dict[str, Any]:
    for label in [str(item) for item in step.get("labels") or [] if str(item)]:
        candidates = [page.get_by_role("button", name=label, exact=False), page.get_by_text(label, exact=False)]
        for candidate in candidates:
            try:
                first = candidate.first
                if await first.is_visible(timeout=250):
                    await first.click(timeout=timeout)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
                    except Exception:
                        pass
                    return {"action_label_sha256": hashlib.sha256(label.encode()).hexdigest(), "clicked": True}
            except Exception:
                continue
    raise BrowserConfigurationError("No recognizable submit/continue action was found on the form")


async def _perform_step(
    page: Page,
    portal: BrowserPortal,
    step: dict[str, Any],
    *,
    username: str,
    password: str,
    auth_code: str,
) -> dict[str, Any]:
    kind = str(step["kind"])
    timeout = max(1000, min(int(step.get("timeout_ms") or 15000), 60000))
    if kind == "goto":
        assert_portal_url(portal, str(step["url"]))
        await page.goto(str(step["url"]), wait_until="domcontentloaded", timeout=timeout)
    elif kind == "click":
        await page.locator(str(step["selector"])).first.click(timeout=timeout)
    elif kind == "fill":
        value = await _resolve_fill_value(step, username=username, password=password, auth_code=auth_code)
        if not value and str(step.get("value_from") or ""):
            raise BrowserNeedsUserAuth("credentials", "Stored portal credentials are incomplete.")
        await page.locator(str(step["selector"])).first.fill(value, timeout=timeout)
    elif kind == "select":
        await page.locator(str(step["selector"])).first.select_option(str(step.get("value") or ""), timeout=timeout)
    elif kind == "check":
        await page.locator(str(step["selector"])).first.check(timeout=timeout)
    elif kind == "uncheck":
        await page.locator(str(step["selector"])).first.uncheck(timeout=timeout)
    elif kind == "press":
        selector = str(step.get("selector") or "")
        locator = page.locator(selector).first if selector else page.locator("body")
        await locator.press(str(step.get("key") or "Enter"), timeout=timeout)
    elif kind == "wait_for":
        await page.locator(str(step["selector"])).first.wait_for(state=str(step.get("state") or "visible"), timeout=timeout)
    elif kind == "extract":
        locator = page.locator(str(step["selector"])).first
        text = (await locator.inner_text(timeout=timeout))[:50000]
        return {"sha256": hashlib.sha256(text.encode()).hexdigest(), "length": len(text)}
    elif kind == "autofill_form":
        return await _perform_autofill_form(page, step, timeout)
    elif kind == "click_action":
        return await _perform_click_action(page, step, timeout)
    await page.wait_for_timeout(150)
    assert_portal_url(portal, page.url)
    return {"url": _safe_url_for_log(page.url)}


async def _auto_login_if_needed(
    db: AsyncSession,
    operation: BrowserOperation,
    portal: BrowserPortal,
    page: Page,
    context: BrowserContext,
    session: BrowserSessionState,
    credential: BrowserCredential | None,
) -> tuple[str, str]:
    recipe = _loads(portal.login_recipe_json, {})
    recipe = recipe if isinstance(recipe, dict) else {}
    username, password = _credential_values(credential)
    auth_code = decrypt_text(operation.pending_auth_value_encrypted) if operation.pending_auth_value_encrypted else ""

    # Resume a persisted OTP challenge before attempting another credential login.
    if operation.challenge_type == "otp" and auth_code:
        selector = operation.challenge_selector or str(recipe.get("otp_selector") or "")
        if not selector:
            raise BrowserNeedsUserAuth("otp", "The portal still requires a one-time code, but no OTP input could be identified.")
        await page.locator(selector).first.fill(auth_code, timeout=15000)
        submit = str(recipe.get("otp_submit_selector") or "").strip()
        if submit:
            await page.locator(submit).first.click(timeout=15000)
        else:
            await page.locator(selector).first.press("Enter", timeout=15000)
        operation.pending_auth_value_encrypted = ""
        operation.challenge_type = ""
        operation.challenge_prompt = ""
        operation.challenge_selector = ""
        await db.commit()
        await page.wait_for_timeout(1000)
        challenge = await _detect_challenge(page, recipe)
        if challenge:
            raise BrowserNeedsUserAuth(*challenge)
        await _save_storage_state(db, context, session, status="authenticated")
        return username, password

    if session.storage_state_encrypted:
        password_visible = await _first_visible_selector(page, ['input[type="password"]', 'input[autocomplete="current-password"]'])
        login_host = _host_from_url(portal.login_url)
        login_path = urlsplit(portal.login_url).path if portal.login_url else ""
        current = urlsplit(page.url)
        on_login_page = bool(login_host and _normalized_host(current.hostname or "") == login_host and login_path and current.path.startswith(login_path))
        if not password_visible and not on_login_page:
            return username, password
    if not portal.login_url:
        return username, password

    await page.goto(portal.login_url, wait_until="domcontentloaded", timeout=20000)
    assert_portal_url(portal, page.url)
    challenge = await _detect_challenge(page, recipe)
    if challenge and challenge[0] not in {"otp"}:
        raise BrowserNeedsUserAuth(*challenge)

    user_selector = str(recipe.get("username_selector") or "").strip() or await _first_visible_selector(
        page,
        [
            'input[type="email"]',
            'input[autocomplete="username"]',
            'input[name*="email" i]',
            'input[name*="user" i]',
            'input[type="text"]',
        ],
    )
    password_selector = str(recipe.get("password_selector") or "").strip() or await _first_visible_selector(
        page,
        ['input[type="password"]', 'input[autocomplete="current-password"]'],
    )
    submit_selector = str(recipe.get("submit_selector") or "").strip() or await _first_visible_selector(
        page,
        ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Log in")'],
    )
    if not username or not password:
        raise BrowserNeedsUserAuth("credentials", "This portal requires stored username/password credentials before VAAPP can sign in.")

    if user_selector:
        await page.locator(user_selector).first.fill(username, timeout=10000)
    if not password_selector and submit_selector:
        await page.locator(submit_selector).first.click(timeout=10000)
        await page.wait_for_timeout(800)
        password_selector = str(recipe.get("password_selector") or "").strip() or await _first_visible_selector(
            page,
            ['input[type="password"]', 'input[autocomplete="current-password"]'],
        )
        submit_selector = str(recipe.get("submit_selector") or "").strip() or await _first_visible_selector(
            page,
            ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Log in")'],
        )
    if not password_selector:
        raise BrowserNeedsUserAuth("credentials", "VAAPP could not identify the portal password field safely.")
    await page.locator(password_selector).first.fill(password, timeout=10000)
    if submit_selector:
        await page.locator(submit_selector).first.click(timeout=10000)
    else:
        await page.locator(password_selector).first.press("Enter", timeout=10000)
    await page.wait_for_timeout(1200)
    challenge = await _detect_challenge(page, recipe)
    if challenge:
        await _save_storage_state(db, context, session, status="challenge")
        raise BrowserNeedsUserAuth(*challenge)

    authenticated_selector = str(recipe.get("authenticated_selector") or "").strip()
    authenticated_url = str(recipe.get("authenticated_url_contains") or "").strip()
    if authenticated_selector and not await _visible(page, authenticated_selector):
        raise BrowserNeedsUserAuth("credentials", "Portal login did not reach the configured authenticated state.")
    if authenticated_url and authenticated_url not in page.url:
        raise BrowserNeedsUserAuth("credentials", "Portal login did not reach the configured authenticated URL.")
    await _save_storage_state(db, context, session, status="authenticated")
    return username, password


async def execute_browser_operation(db: AsyncSession, operation_id: int) -> dict[str, Any]:
    operation = await db.get(BrowserOperation, operation_id)
    if operation is None:
        raise LookupError("Browser operation not found")
    if operation.status == "verified":
        return {"operation_id": operation.id, "status": operation.status, "already_verified": True}
    portal = await db.get(BrowserPortal, operation.portal_id)
    if portal is None or not portal.enabled:
        operation.status = "blocked_capability"
        operation.last_error = "Browser portal is missing or disabled"
        await db.commit()
        return {"operation_id": operation.id, "status": operation.status}

    try:
        plan = _loads(decrypt_text(operation.plan_encrypted), {}) if operation.plan_encrypted else _loads(operation.plan_json, {})
        verification = _loads(decrypt_text(operation.verification_encrypted), {}) if operation.verification_encrypted else _loads(operation.verification_json, {})
    except RuntimeError as exc:
        operation.status = "failed"
        operation.last_error = "Stored browser operation plan could not be decrypted"
        await db.commit()
        return {"operation_id": operation.id, "status": operation.status, "error": str(exc)}
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not isinstance(verification, dict):
        operation.status = "failed"
        operation.last_error = "Persisted browser operation plan is invalid"
        await db.commit()
        return {"operation_id": operation.id, "status": operation.status}

    session = await _session_for_portal(db, portal.id)
    credential = (
        await db.execute(select(BrowserCredential).where(BrowserCredential.portal_id == portal.id).limit(1))
    ).scalar_one_or_none()
    storage_state: dict[str, Any] | None = None
    if session.storage_state_encrypted:
        try:
            decoded = _loads(decrypt_text(session.storage_state_encrypted), {})
            storage_state = decoded if isinstance(decoded, dict) else None
        except RuntimeError:
            session.storage_state_encrypted = ""
            session.status = "empty"
            session.last_error = "Stored browser session could not be decrypted and was discarded"
            await db.commit()

    operation.attempts += 1
    operation.status = "running"
    operation.last_error = ""
    await db.commit()

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            try:
                context = await browser.new_context(
                    storage_state=storage_state,
                    accept_downloads=False,
                    ignore_https_errors=False,
                )
                blocked_navigation: list[str] = []
                resolved_private_hosts: dict[str, bool] = {}

                async def _guard_main_frame_navigation(route) -> None:
                    request = route.request
                    request_host = _host_from_url(request.url)
                    if request.url.startswith(("http://", "https://")) and await _host_resolves_private_or_local(
                        request_host, resolved_private_hosts
                    ):
                        blocked_navigation.append(_safe_url_for_log(request.url))
                        await route.abort("blockedbyclient")
                        return
                    is_main_navigation = False
                    if request.is_navigation_request():
                        try:
                            is_main_navigation = request.frame.parent_frame is None
                        except Exception:
                            is_main_navigation = True
                    if is_main_navigation:
                        try:
                            assert_portal_url(portal, request.url)
                        except BrowserSecurityError:
                            blocked_navigation.append(_safe_url_for_log(request.url))
                            await route.abort("blockedbyclient")
                            return
                    await route.continue_()

                await context.route("**/*", _guard_main_frame_navigation)
                page = await context.new_page()
                start_url = decrypt_text(operation.resume_url_encrypted) if operation.resume_url_encrypted else (operation.last_url or portal.base_url)
                assert_portal_url(portal, start_url)
                try:
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=20000)
                except Exception as exc:
                    if blocked_navigation:
                        raise BrowserSecurityError(
                            f"Portal navigation attempted to leave the allowlist: {blocked_navigation[-1]}"
                        ) from exc
                    raise
                assert_portal_url(portal, page.url)

                # If a worker died after a potentially mutating click/submit, reconcile
                # against the explicit postcondition before doing anything else.
                if operation.status == "dispatching" or (
                    operation.side_effect_step is not None and operation.current_step == operation.side_effect_step
                ):
                    verified, verification_details = await verify_page(page, portal, verification)
                    if verified:
                        operation.status = "verified"
                        operation.verified_at = utcnow()
                        operation.last_url = _safe_url_for_log(page.url)
                        operation.resume_url_encrypted = encrypt_text(page.url)
                        operation.page_title = (await page.title())[:1000]
                        operation.side_effect_step = None
                        operation.side_effect_started_at = None
                        await _add_evidence(
                            db,
                            operation,
                            page,
                            evidence_type="browser_postcondition_verified",
                            step_index=operation.current_step,
                            details=verification_details,
                            screenshot=True,
                        )
                        await _save_storage_state(db, context, session, status="authenticated")
                        await db.commit()
                        return {"operation_id": operation.id, "status": operation.status, "reconciled": True}
                    operation.status = "creation_uncertain"
                    operation.last_error = (
                        "A prior browser side effect has an ambiguous outcome and the provider postcondition is not yet visible; "
                        "VAAPP will not blindly replay it."
                    )
                    await _add_evidence(
                        db,
                        operation,
                        page,
                        evidence_type="browser_ambiguous_outcome",
                        step_index=operation.current_step,
                        details={"postcondition_verified": False},
                        screenshot=True,
                    )
                    await _save_storage_state(db, context, session, status="ambiguous", error=operation.last_error)
                    await db.commit()
                    return {"operation_id": operation.id, "status": operation.status}

                username, password = await _auto_login_if_needed(
                    db, operation, portal, page, context, session, credential
                )
                auth_code = ""

                while operation.current_step < len(steps):
                    index = operation.current_step
                    step = dict(steps[index])
                    side_effect = bool(step.get("side_effect"))
                    replay_safe = bool(step.get("replay_safe"))
                    if side_effect and not replay_safe:
                        operation.status = "dispatching"
                        operation.side_effect_step = index
                        operation.side_effect_started_at = utcnow()
                        operation.last_url = _safe_url_for_log(page.url)
                        operation.resume_url_encrypted = encrypt_text(page.url)
                        await db.commit()
                    try:
                        result = await _perform_step(
                            page,
                            portal,
                            step,
                            username=username,
                            password=password,
                            auth_code=auth_code,
                        )
                    except BrowserNeedsUserAuth:
                        raise
                    except Exception as exc:
                        if blocked_navigation:
                            operation.status = "creation_uncertain" if side_effect and not replay_safe else "failed"
                            operation.last_error = (
                                f"Portal navigation was blocked outside the allowlist: {blocked_navigation[-1]}"
                            )[:4000]
                            await _add_evidence(
                                db,
                                operation,
                                page,
                                evidence_type="browser_navigation_blocked",
                                step_index=index,
                                details={"blocked_url": blocked_navigation[-1], "side_effect": side_effect},
                                screenshot=True,
                            )
                            await _save_storage_state(db, context, session, status="blocked", error=operation.last_error)
                            await db.commit()
                            return {"operation_id": operation.id, "status": operation.status}
                        if side_effect and not replay_safe:
                            operation.status = "creation_uncertain"
                            operation.last_error = f"Browser side-effect outcome is ambiguous: {exc}"[:4000]
                            await _add_evidence(
                                db,
                                operation,
                                page,
                                evidence_type="browser_ambiguous_outcome",
                                step_index=index,
                                details={"error_type": type(exc).__name__},
                                screenshot=True,
                            )
                            await _save_storage_state(db, context, session, status="ambiguous", error=operation.last_error)
                            await db.commit()
                            return {"operation_id": operation.id, "status": operation.status}
                        raise

                    operation.current_step = index + 1
                    operation.side_effect_step = None
                    operation.side_effect_started_at = None
                    operation.last_url = _safe_url_for_log(page.url)
                    operation.resume_url_encrypted = encrypt_text(page.url)
                    operation.page_title = (await page.title())[:1000]
                    operation.status = "running"
                    await _add_evidence(
                        db,
                        operation,
                        page,
                        evidence_type="browser_step_observed",
                        step_index=index,
                        details={"kind": step.get("kind"), "result": result, "side_effect": side_effect},
                        screenshot=side_effect,
                    )
                    await _save_storage_state(db, context, session, status="authenticated")
                    await db.commit()

                    challenge = await _detect_challenge(page, _loads(portal.login_recipe_json, {}))
                    if challenge:
                        raise BrowserNeedsUserAuth(*challenge)

                operation.status = "verifying"
                verified, verification_details = await verify_page(page, portal, verification)
                if verified:
                    operation.status = "verified"
                    operation.verified_at = utcnow()
                    operation.last_error = ""
                    await _add_evidence(
                        db,
                        operation,
                        page,
                        evidence_type="browser_postcondition_verified",
                        step_index=len(steps),
                        details=verification_details,
                        screenshot=True,
                    )
                    await _save_storage_state(db, context, session, status="authenticated")
                else:
                    operation.status = "failed"
                    operation.last_error = "Browser plan ran, but the required provider postcondition was not verified"
                    await _add_evidence(
                        db,
                        operation,
                        page,
                        evidence_type="browser_postcondition_failed",
                        step_index=len(steps),
                        details=verification_details,
                        screenshot=True,
                    )
                    await _save_storage_state(db, context, session, status="ready", error=operation.last_error)
                await db.commit()
                return {"operation_id": operation.id, "status": operation.status}
            except BrowserNeedsUserAuth as challenge:
                operation.status = "needs_user_auth"
                operation.challenge_type = challenge.challenge_type
                operation.challenge_prompt = challenge.prompt[:4000]
                operation.challenge_selector = challenge.selector[:2000]
                operation.last_url = _safe_url_for_log(page.url)
                operation.resume_url_encrypted = encrypt_text(page.url)
                operation.page_title = (await page.title())[:1000]
                operation.last_error = challenge.prompt[:4000]
                await _add_evidence(
                    db,
                    operation,
                    page,
                    evidence_type="browser_auth_challenge",
                    step_index=operation.current_step,
                    details={"challenge_type": challenge.challenge_type},
                    screenshot=True,
                )
                await _save_storage_state(db, context, session, status="challenge", error=challenge.prompt)
                await db.commit()
                return {"operation_id": operation.id, "status": operation.status, "challenge_type": operation.challenge_type}
            finally:
                await browser.close()
    except BrowserSecurityError as exc:
        operation.status = "failed"
        operation.last_error = str(exc)[:4000]
        await db.commit()
        return {"operation_id": operation.id, "status": operation.status}
    except PlaywrightTimeoutError as exc:
        operation.last_error = f"Browser provider timeout: {exc}"[:4000]
        operation.status = "retry" if operation.attempts < operation.max_attempts else "failed"
        operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))
        await db.commit()
        if operation.status == "retry":
            raise
        return {"operation_id": operation.id, "status": operation.status}
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message.lower():
            operation.status = "blocked_capability"
            operation.last_error = "Chromium browser runtime is not installed in the backend deployment"
            await db.commit()
            return {"operation_id": operation.id, "status": operation.status}
        operation.last_error = message[:4000]
        operation.status = "retry" if operation.attempts < operation.max_attempts else "failed"
        operation.verify_after = utcnow() + timedelta(seconds=min(120, 10 * max(1, operation.attempts)))
        await db.commit()
        if operation.status == "retry":
            raise
        return {"operation_id": operation.id, "status": operation.status}




def operation_requires_material_decision(operation: BrowserOperation) -> bool:
    summary = _loads(operation.plan_json, {})
    return bool(isinstance(summary, dict) and summary.get("material_commitment")) and operation.material_approved_at is None


async def approve_material_operation(db: AsyncSession, operation_id: int) -> BrowserOperation:
    operation = await db.get(BrowserOperation, operation_id)
    if operation is None:
        raise LookupError("Browser operation not found")
    summary = _loads(operation.plan_json, {})
    if not bool(isinstance(summary, dict) and summary.get("material_commitment")):
        raise ValueError("Browser operation does not require a material approval")
    operation.material_approved_at = utcnow()
    if operation.step_id:
        step = await db.get(VAObjectiveStep, operation.step_id)
        if step is not None and step.status == "blocked_user":
            step.status = "pending"
            step.run_after = utcnow()
            step.last_error = ""
    if operation.objective_id:
        objective = await db.get(VAObjective, operation.objective_id)
        if objective is not None and objective.status == "needs_user":
            objective.status = "planned"
            objective.needs_user_reason = ""
            objective.blocked_reason = ""
            objective.last_error = ""
    await write_audit(
        db,
        "browser_material_operation_approved",
        entity_type="browser_operation",
        entity_id=str(operation.id),
        details={"approved_at": operation.material_approved_at},
    )
    await db.commit()
    return operation


async def submit_auth_code(db: AsyncSession, operation_id: int, code: str) -> BrowserOperation:
    operation = await db.get(BrowserOperation, operation_id)
    if operation is None:
        raise LookupError("Browser operation not found")
    if operation.status != "needs_user_auth" or operation.challenge_type != "otp":
        raise ValueError("This browser operation is not waiting for a one-time code")
    operation.pending_auth_value_encrypted = encrypt_text(code)
    operation.resume_sequence += 1
    operation.status = "pending"
    operation.last_error = ""
    operation.verify_after = utcnow()
    if operation.step_id:
        step = await db.get(VAObjectiveStep, operation.step_id)
        if step is not None:
            step.status = "verifying"
            step.run_after = utcnow() + timedelta(seconds=5)
            step.last_error = ""
    if operation.objective_id:
        objective = await db.get(VAObjective, operation.objective_id)
        if objective is not None:
            objective.status = "verifying"
            objective.needs_user_reason = ""
            objective.blocked_reason = ""
            objective.last_error = ""
    await write_audit(
        db,
        "browser_auth_code_supplied",
        entity_type="browser_operation",
        entity_id=str(operation.id),
        details={"challenge_type": "otp", "resume_sequence": operation.resume_sequence},
    )
    await db.commit()
    await enqueue_browser_operation(db, operation)
    return operation


async def resume_browser_operation(db: AsyncSession, operation_id: int) -> BrowserOperation:
    operation = await db.get(BrowserOperation, operation_id)
    if operation is None:
        raise LookupError("Browser operation not found")
    if operation.status not in {"needs_user_auth", "retry", "failed"}:
        raise ValueError("Browser operation is not resumable in its current state")
    if operation.status == "failed" and operation.side_effect_step is not None:
        raise ValueError("A failed operation with an ambiguous side effect cannot be blindly retried")
    operation.resume_sequence += 1
    operation.status = "pending"
    operation.last_error = ""
    operation.verify_after = utcnow()
    if operation.step_id:
        step = await db.get(VAObjectiveStep, operation.step_id)
        if step is not None:
            step.status = "verifying"
            step.run_after = utcnow() + timedelta(seconds=5)
            step.last_error = ""
    if operation.objective_id:
        objective = await db.get(VAObjective, operation.objective_id)
        if objective is not None:
            objective.status = "verifying"
            objective.needs_user_reason = ""
            objective.blocked_reason = ""
    await db.commit()
    await enqueue_browser_operation(db, operation)
    return operation


async def browser_status(db: AsyncSession) -> dict[str, Any]:
    portal_count = int((await db.execute(select(func.count(BrowserPortal.id)).where(BrowserPortal.enabled.is_(True)))).scalar_one())
    total_operations = int((await db.execute(select(func.count(BrowserOperation.id)))).scalar_one())
    auth_required = int(
        (await db.execute(select(func.count(BrowserOperation.id)).where(BrowserOperation.status == "needs_user_auth"))).scalar_one()
    )
    ambiguous = int(
        (await db.execute(select(func.count(BrowserOperation.id)).where(BrowserOperation.status == "creation_uncertain"))).scalar_one()
    )
    verified = int(
        (await db.execute(select(func.count(BrowserOperation.id)).where(BrowserOperation.status == "verified"))).scalar_one()
    )
    return {
        "executor": "playwright_chromium",
        "configured_portals": portal_count,
        "operations": total_operations,
        "verified": verified,
        "needs_user_auth": auth_required,
        "ambiguous_outcomes": ambiguous,
        "security": {
            "encrypted_credentials": True,
            "encrypted_session_state": True,
            "host_allowlist": True,
            "private_network_block": True,
            "captcha_bypass": False,
            "blind_side_effect_replay": False,
        },
    }


def portal_public(portal: BrowserPortal, *, credential: BrowserCredential | None = None, session: BrowserSessionState | None = None) -> dict[str, Any]:
    return {
        "id": portal.id,
        "slug": portal.slug,
        "name": portal.name,
        "base_url": _safe_url_for_log(portal.base_url),
        "login_url": _safe_url_for_log(portal.login_url),
        "allowed_hosts": sorted(portal_allowed_hosts(portal)),
        "account_scope": portal.account_scope,
        "enabled": portal.enabled,
        "credentials_configured": bool(
            credential and credential.username_encrypted and credential.password_encrypted
        ),
        "session_status": session.status if session else "empty",
        "last_authenticated_at": session.last_authenticated_at if session else None,
        "last_used_at": session.last_used_at if session else None,
        "last_error": session.last_error if session else "",
    }


async def list_portals(db: AsyncSession) -> list[dict[str, Any]]:
    portals = list((await db.execute(select(BrowserPortal).order_by(BrowserPortal.name, BrowserPortal.id))).scalars())
    credentials = {
        row.portal_id: row for row in (await db.execute(select(BrowserCredential))).scalars()
    }
    sessions = {
        row.portal_id: row for row in (await db.execute(select(BrowserSessionState))).scalars()
    }
    return [portal_public(row, credential=credentials.get(row.id), session=sessions.get(row.id)) for row in portals]


def operation_public(row: BrowserOperation) -> dict[str, Any]:
    return {
        "id": row.id,
        "idempotency_key": row.idempotency_key,
        "portal_id": row.portal_id,
        "objective_id": row.objective_id,
        "step_id": row.step_id,
        "title": row.title,
        "status": row.status,
        "current_step": row.current_step,
        "attempts": row.attempts,
        "last_url": row.last_url,
        "page_title": row.page_title,
        "challenge_type": row.challenge_type,
        "challenge_prompt": row.challenge_prompt,
        "material_approval_required": operation_requires_material_decision(row),
        "material_approved_at": row.material_approved_at,
        "last_error": row.last_error,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "verified_at": row.verified_at,
    }


async def list_operations(db: AsyncSession, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(
                select(BrowserOperation).order_by(BrowserOperation.updated_at.desc(), BrowserOperation.id.desc()).limit(max(1, min(limit, 500)))
            )
        ).scalars()
    )
    return [operation_public(row) for row in rows]


async def operation_detail(db: AsyncSession, operation_id: int) -> dict[str, Any]:
    row = await db.get(BrowserOperation, operation_id)
    if row is None:
        raise LookupError("Browser operation not found")
    evidence = list(
        (
            await db.execute(
                select(BrowserEvidence)
                .where(BrowserEvidence.browser_operation_id == row.id)
                .order_by(BrowserEvidence.id.desc())
                .limit(50)
            )
        ).scalars()
    )
    result = operation_public(row)
    result["evidence"] = [
        {
            "id": item.id,
            "type": item.evidence_type,
            "step_index": item.step_index,
            "url": item.url,
            "title": item.title,
            "sha256": item.sha256,
            "details": _loads(item.details_json, {}),
            "has_screenshot": bool(item.payload_encrypted),
            "created_at": item.created_at,
        }
        for item in evidence
    ]
    return result


async def evidence_png(db: AsyncSession, evidence_id: int) -> bytes:
    row = await db.get(BrowserEvidence, evidence_id)
    if row is None or not row.payload_encrypted:
        raise LookupError("Browser screenshot evidence not found")
    try:
        return base64.b64decode(decrypt_text(row.payload_encrypted))
    except Exception as exc:
        raise RuntimeError("Stored browser screenshot evidence could not be decoded") from exc
