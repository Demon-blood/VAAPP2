from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Page, async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_text, encrypt_text
from app.models.entities import (
    BrowserCredential,
    BrowserPortal,
    PortalDocumentItem,
    PortalDocumentSource,
)
from app.schemas.api import PortalDocumentRecipe
from app.services.audit import write_audit
from app.services.browser_operator import (
    BrowserNeedsUserAuth,
    BrowserSecurityError,
    _credential_values,
    _detect_challenge,
    _first_visible_selector,
    _host_from_url,
    _host_resolves_private_or_local,
    _loads,
    _safe_url_for_log,
    _save_storage_state,
    _session_for_portal,
    _visible,
    assert_portal_url,
)
from app.services.document_ingestion import ingest_document_bytes, safe_document_name


def utcnow() -> datetime:
    return datetime.utcnow()


def _safe_error(exc: Exception) -> str:
    value = str(exc)
    return re.sub(r"https?://[^\s'\"]+", "<redacted-url>", value)[:4000]


def _failure_state(exc: Exception) -> tuple[str, str]:
    message = str(exc).casefold()
    if isinstance(exc, BrowserSecurityError):
        return "blocked_system", "security_boundary"
    if any(term in message for term in ("google", "drive", "oauth connection", "not configured")):
        return "blocked_capability", "storage_unavailable"
    if any(term in message for term in ("selector", "locator", "strict mode")):
        return "degraded", "recipe_drift"
    if any(term in message for term in ("timeout", "timed out", "network", "connection", "http status 5")):
        return "waiting_provider", "provider_network"
    return "failed", "internal_failure"


PORTAL_DOCUMENT_PRESETS: list[dict[str, Any]] = [
    {
        "key": "doccle",
        "name": "Doccle starter",
        "production_ready": False,
        "detail": "Conservative starter only. Verify selectors against your authenticated account before enabling sync.",
        "defaults": {
            "start_url": "https://secure.doccle.be/",
            "expected_mime_types": ["application/pdf"],
            "download_strategy": "direct_link",
        },
    }
]


def _recipe(source: PortalDocumentSource) -> PortalDocumentRecipe:
    return PortalDocumentRecipe.model_validate_json(source.recipe_json)


def source_public(source: PortalDocumentSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "portal_id": source.portal_id,
        "slug": source.slug,
        "name": source.name,
        "recipe": json.loads(source.recipe_json),
        "preset_key": source.preset_key,
        "account_scope": source.account_scope,
        "enabled": source.enabled,
        "sync_interval_minutes": source.sync_interval_minutes,
        "max_pages": source.max_pages,
        "max_documents_per_sync": source.max_documents_per_sync,
        "status": source.status,
        "last_error": source.last_error,
        "last_result": _loads(source.last_result_json, {}),
        "consecutive_failures": source.consecutive_failures,
        "challenge_type": source.challenge_type,
        "challenge_prompt": source.challenge_prompt,
        "last_sync_at": source.last_sync_at,
        "last_success_at": source.last_success_at,
        "last_discovery_at": source.last_discovery_at,
        "updated_at": source.updated_at,
    }


async def validate_source_recipe(
    db: AsyncSession, portal_id: int, recipe: PortalDocumentRecipe
) -> BrowserPortal:
    portal = await db.get(BrowserPortal, portal_id)
    if portal is None or not portal.enabled:
        raise ValueError("browser portal is missing or disabled")
    assert_portal_url(portal, recipe.start_url)
    return portal


async def upsert_source(db: AsyncSession, *, source_id: int | None = None, **values: Any) -> PortalDocumentSource:
    recipe = values.pop("recipe")
    if not isinstance(recipe, PortalDocumentRecipe):
        recipe = PortalDocumentRecipe.model_validate(recipe)
    portal = await validate_source_recipe(db, int(values["portal_id"]), recipe)
    if values["account_scope"] != portal.account_scope:
        raise ValueError("document source account scope must match the linked portal scope")
    row = await db.get(PortalDocumentSource, source_id) if source_id is not None else None
    if source_id is not None and row is None:
        raise LookupError("portal document source not found")
    conflict = (
        await db.execute(
            select(PortalDocumentSource).where(
                PortalDocumentSource.portal_id == int(values["portal_id"]),
                PortalDocumentSource.slug == str(values["slug"]),
                PortalDocumentSource.id != (source_id or 0),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if conflict is not None:
        raise ValueError("a document source with this slug already exists for the linked portal")
    if row is None and source_id is None:
        row = (
            await db.execute(
                select(PortalDocumentSource).where(
                    PortalDocumentSource.portal_id == int(values["portal_id"]),
                    PortalDocumentSource.slug == str(values["slug"]),
                ).limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        row = PortalDocumentSource(portal_id=int(values["portal_id"]), slug=str(values["slug"]))
        db.add(row)
    serialized_recipe = recipe.model_dump_json()
    configuration_changed = bool(
        row.id is not None
        and (row.portal_id != int(values["portal_id"]) or row.recipe_json != serialized_recipe)
    )
    for key in (
        "name", "preset_key", "account_scope", "enabled", "sync_interval_minutes",
        "max_pages", "max_documents_per_sync",
    ):
        setattr(row, key, values[key])
    row.portal_id = int(values["portal_id"])
    row.slug = str(values["slug"])
    row.recipe_json = serialized_recipe
    row.status = "ready"
    row.last_error = ""
    row.consecutive_failures = 0
    row.challenge_type = ""
    row.challenge_prompt = ""
    row.challenge_selector = ""
    row.pending_auth_value_encrypted = ""
    if configuration_changed:
        row.last_success_at = None
        row.last_discovery_at = None
    await db.commit()
    await db.refresh(row)
    return row


async def list_sources(db: AsyncSession) -> list[dict[str, Any]]:
    rows = list((await db.execute(select(PortalDocumentSource).order_by(PortalDocumentSource.name))).scalars())
    result: list[dict[str, Any]] = []
    for row in rows:
        items = list(
            (
                await db.execute(
                    select(PortalDocumentItem.status).where(PortalDocumentItem.portal_document_source_id == row.id)
                )
            ).scalars()
        )
        public = source_public(row)
        public["known_documents"] = len(items)
        public["ingested_documents"] = sum(1 for status in items if status == "ingested")
        public["pending_documents"] = sum(1 for status in items if status in {"discovered", "retry", "downloaded"})
        result.append(public)
    return result


async def delete_source(db: AsyncSession, source_id: int) -> None:
    row = await db.get(PortalDocumentSource, source_id)
    if row is None:
        raise LookupError("portal document source not found")
    await db.delete(row)
    await db.commit()


async def _authenticate(
    db: AsyncSession,
    source: PortalDocumentSource,
    portal: BrowserPortal,
    page: Page,
    context: BrowserContext,
) -> None:
    def clear_source_challenge() -> None:
        source.challenge_type = ""
        source.challenge_prompt = ""
        source.challenge_selector = ""
        source.pending_auth_value_encrypted = ""

    session = await _session_for_portal(db, portal.id)
    login_recipe = _loads(portal.login_recipe_json, {})
    login_recipe = login_recipe if isinstance(login_recipe, dict) else {}
    if source.challenge_type == "otp" and source.pending_auth_value_encrypted:
        selector = source.challenge_selector or str(login_recipe.get("otp_selector") or "")
        if not selector:
            raise BrowserNeedsUserAuth("otp", "The portal still requires a one-time code, but no input could be identified.")
        code = decrypt_text(source.pending_auth_value_encrypted)
        await page.locator(selector).first.fill(code, timeout=15000)
        submit = str(login_recipe.get("otp_submit_selector") or "")
        if submit:
            await page.locator(submit).first.click(timeout=15000)
        else:
            await page.locator(selector).first.press("Enter", timeout=15000)
        source.pending_auth_value_encrypted = ""
        source.challenge_type = ""
        source.challenge_prompt = ""
        source.challenge_selector = ""
        await page.wait_for_timeout(1000)
        challenge = await _detect_challenge(page, login_recipe)
        if challenge:
            raise BrowserNeedsUserAuth(*challenge)
        await _save_storage_state(db, context, session, status="authenticated")
        return
    if not portal.login_url:
        clear_source_challenge()
        return
    password_visible = await _first_visible_selector(page, ['input[type="password"]', 'input[autocomplete="current-password"]'])
    if session.storage_state_encrypted and not password_visible and _host_from_url(page.url) != _host_from_url(portal.login_url):
        clear_source_challenge()
        return
    await page.goto(portal.login_url, wait_until="domcontentloaded", timeout=20000)
    assert_portal_url(portal, page.url)
    challenge = await _detect_challenge(page, login_recipe)
    if challenge and challenge[0] != "otp":
        raise BrowserNeedsUserAuth(*challenge)
    credential = (
        await db.execute(select(BrowserCredential).where(BrowserCredential.portal_id == portal.id).limit(1))
    ).scalar_one_or_none()
    username, password = _credential_values(credential)
    if not username or not password:
        raise BrowserNeedsUserAuth("credentials", "This portal requires stored username/password credentials.")
    user_selector = str(login_recipe.get("username_selector") or "") or await _first_visible_selector(
        page, ['input[type="email"]', 'input[autocomplete="username"]', 'input[type="text"]']
    )
    password_selector = str(login_recipe.get("password_selector") or "") or await _first_visible_selector(
        page, ['input[type="password"]', 'input[autocomplete="current-password"]']
    )
    if not password_selector:
        raise BrowserNeedsUserAuth("credentials", "VAAPP could not identify the portal password field safely.")
    if user_selector:
        await page.locator(user_selector).first.fill(username, timeout=10000)
    await page.locator(password_selector).first.fill(password, timeout=10000)
    submit = str(login_recipe.get("submit_selector") or "") or await _first_visible_selector(
        page, ['button[type="submit"]', 'input[type="submit"]']
    )
    if submit:
        await page.locator(submit).first.click(timeout=10000)
    else:
        await page.locator(password_selector).first.press("Enter", timeout=10000)
    await page.wait_for_timeout(1200)
    challenge = await _detect_challenge(page, login_recipe)
    if challenge:
        await _save_storage_state(db, context, session, status="challenge")
        raise BrowserNeedsUserAuth(*challenge)
    authenticated_selector = str(login_recipe.get("authenticated_selector") or "")
    if authenticated_selector and not await _visible(page, authenticated_selector):
        raise BrowserNeedsUserAuth("credentials", "Portal login did not reach the configured authenticated state.")
    clear_source_challenge()
    await _save_storage_state(db, context, session, status="authenticated")


async def extract_candidates(page: Page, recipe: PortalDocumentRecipe) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    items = page.locator(recipe.item_selector)
    for index in range(await items.count()):
        item = items.nth(index)

        async def value(selector: str, attribute: str = "") -> str:
            locator = item.locator(selector).first if selector else item
            raw = await locator.get_attribute(attribute) if attribute else await locator.inner_text()
            return str(raw or "").strip()

        external_id = await value(recipe.external_id_selector, recipe.external_id_attribute) if recipe.external_id_selector else ""
        link_selector = recipe.detail_link_selector or recipe.link_selector
        link = await value(link_selector, recipe.link_attribute)
        if not link:
            continue
        absolute_link = urljoin(page.url, link)
        title = (await value(recipe.title_selector, recipe.title_attribute))[:1000] if recipe.title_selector else ""
        provider_name = (await value(recipe.provider_selector, recipe.provider_attribute))[:255] if recipe.provider_selector else ""
        document_date_text = (await value(recipe.date_selector, recipe.date_attribute))[:120] if recipe.date_selector else ""
        folded_title = title.casefold()
        if recipe.include_title_terms and not any(term.casefold() in folded_title for term in recipe.include_title_terms):
            continue
        if any(term.casefold() in folded_title for term in recipe.exclude_title_terms):
            continue
        derived_identity = False
        if not external_id and recipe.allow_derived_external_id:
            stable_evidence = "|".join((provider_name, title, document_date_text, _safe_url_for_log(absolute_link)))
            external_id = "derived:" + hashlib.sha256(stable_evidence.encode()).hexdigest()
            derived_identity = True
        if not external_id:
            continue
        candidates.append(
            {
                "external_id": external_id[:320],
                "title": title or external_id[:1000],
                "provider_name": provider_name,
                "document_date_text": document_date_text,
                "derived_identity": derived_identity,
                "reference": {"url": absolute_link, "page_url": page.url, "item_index": index},
            }
        )
    return candidates


async def _secure_context(db: AsyncSession, source: PortalDocumentSource, *, accept_downloads: bool = True):
    portal = await db.get(BrowserPortal, source.portal_id)
    if portal is None or not portal.enabled:
        raise ValueError("browser portal is missing or disabled")
    session = await _session_for_portal(db, portal.id)
    storage_state = None
    if session.storage_state_encrypted:
        try:
            storage_state = _loads(decrypt_text(session.storage_state_encrypted), {})
        except RuntimeError:
            session.storage_state_encrypted = ""
            session.status = "empty"
    manager = async_playwright()
    playwright = await manager.start()
    browser = await playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
    context = await browser.new_context(storage_state=storage_state, accept_downloads=accept_downloads, ignore_https_errors=False)
    resolved: dict[str, bool] = {}
    blocked: list[str] = []

    async def guard(route) -> None:
        request = route.request
        host = _host_from_url(request.url)
        if request.url.startswith(("http://", "https://")) and await _host_resolves_private_or_local(host, resolved):
            blocked.append(_safe_url_for_log(request.url))
            await route.abort("blockedbyclient")
            return
        if request.is_navigation_request() or request.resource_type == "document":
            try:
                assert_portal_url(portal, request.url)
            except BrowserSecurityError:
                blocked.append(_safe_url_for_log(request.url))
                await route.abort("blockedbyclient")
                return
        await route.continue_()

    await context.route("**/*", guard)
    return manager, playwright, browser, context, portal, blocked


async def discover_source(db: AsyncSession, source_id: int, *, persist: bool = True) -> dict[str, Any]:
    source = await db.get(PortalDocumentSource, source_id)
    if source is None or not source.enabled:
        raise LookupError("portal document source is missing or disabled")
    recipe = _recipe(source)
    await validate_source_recipe(db, source.portal_id, recipe)
    manager = playwright = browser = context = None
    try:
        manager, playwright, browser, context, portal, blocked = await _secure_context(db, source)
        page = await context.new_page()
        await page.goto(recipe.start_url, wait_until="domcontentloaded", timeout=20000)
        await _authenticate(db, source, portal, page, context)
        if page.url != recipe.start_url:
            await page.goto(recipe.start_url, wait_until="domcontentloaded", timeout=20000)
        assert_portal_url(portal, page.url)
        found: list[dict[str, Any]] = []
        for page_number in range(source.max_pages):
            for candidate in await extract_candidates(page, recipe):
                assert_portal_url(portal, candidate["reference"]["url"])
                found.append(candidate)
                if len(found) >= source.max_documents_per_sync:
                    break
            if len(found) >= source.max_documents_per_sync or not recipe.next_page_selector:
                break
            next_link = page.locator(recipe.next_page_selector).first
            if not await next_link.is_visible():
                break
            await next_link.click()
            await page.wait_for_load_state("domcontentloaded")
            assert_portal_url(portal, page.url)
        created = 0
        if persist:
            for candidate in found:
                row = (
                    await db.execute(
                        select(PortalDocumentItem).where(
                            PortalDocumentItem.portal_document_source_id == source.id,
                            PortalDocumentItem.external_id == candidate["external_id"],
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = PortalDocumentItem(
                        portal_document_source_id=source.id,
                        external_id=candidate["external_id"],
                    )
                    db.add(row)
                    created += 1
                row.title = candidate["title"]
                row.provider_name = candidate["provider_name"]
                if row.status == "needs_user_auth" and not source.challenge_type:
                    row.status = "retry"
                raw_date = candidate["document_date_text"]
                parsed_date = None
                for fmt in (None, "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
                    try:
                        parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).replace(tzinfo=None) if fmt is None else datetime.strptime(raw_date, fmt)
                        break
                    except ValueError:
                        continue
                row.document_date = parsed_date
                row.reference_encrypted = encrypt_text(json.dumps(candidate["reference"]))
            source.status = "ready"
            source.last_error = ""
            source.last_discovery_at = utcnow()
            if not found and source.last_success_at is not None:
                source.status = "degraded"
                source.last_error = "Recipe drift suspected: no document items matched the configured listing selector"
                source.consecutive_failures += 1
            await db.commit()
        return {
            "source_id": source.id,
            "source_name": source.name,
            "portal_login": "ready",
            "listing_page": "reached",
            "download_executor": "ready",
            "found": len(found),
            "created": created,
            "items": [{k: v for k, v in item.items() if k != "reference"} for item in found[:25]],
        }
    except BrowserNeedsUserAuth as exc:
        source.status = "needs_user_auth"
        source.challenge_type = exc.challenge_type
        source.challenge_prompt = exc.prompt
        source.challenge_selector = exc.selector
        source.last_error = exc.prompt
        source.consecutive_failures += 1
        await db.commit()
        return {"source_id": source.id, "status": source.status, "challenge_type": exc.challenge_type, "prompt": exc.prompt}
    except Exception as exc:
        source.status, category = _failure_state(exc)
        source.last_error = _safe_error(exc)
        source.consecutive_failures += 1
        source.last_result_json = json.dumps({"source_id": source.id, "failure_category": category})
        await db.commit()
        raise
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


async def _download_item(db: AsyncSession, source: PortalDocumentSource, item: PortalDocumentItem) -> tuple[bytes, str, str]:
    recipe = _recipe(source)
    reference = json.loads(decrypt_text(item.reference_encrypted))
    url = str(reference.get("url") or "")
    manager = playwright = browser = context = None
    try:
        manager, playwright, browser, context, portal, blocked = await _secure_context(db, source)
        assert_portal_url(portal, url)
        page = await context.new_page()
        await page.goto(recipe.start_url, wait_until="domcontentloaded", timeout=20000)
        await _authenticate(db, source, portal, page, context)
        if recipe.download_strategy == "click":
            page_url = str(reference.get("page_url") or recipe.start_url)
            assert_portal_url(portal, page_url)
            await page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
            if recipe.detail_link_selector:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                assert_portal_url(portal, page.url)
                locator = page.locator(recipe.download_selector).first
            else:
                locator = page.locator(recipe.item_selector).nth(int(reference.get("item_index") or 0)).locator(recipe.link_selector).first
            try:
                async with page.expect_download(timeout=30000) as pending:
                    await locator.click()
            except Exception:
                login_recipe = _loads(portal.login_recipe_json, {})
                challenge = await _detect_challenge(
                    page,
                    login_recipe if isinstance(login_recipe, dict) else {},
                )
                if challenge:
                    session = await _session_for_portal(db, portal.id)
                    await _save_storage_state(db, context, session, status="challenge")
                    raise BrowserNeedsUserAuth(*challenge)
                raise
            download = await pending.value
            download_url = download.url
            assert_portal_url(portal, download_url)
            if await _host_resolves_private_or_local(_host_from_url(download_url), {}):
                raise BrowserSecurityError("portal download resolved to a private/local network target")
            path = await download.path()
            if not path:
                raise ValueError("portal download did not produce a temporary file")
            try:
                if Path(path).stat().st_size > 12 * 1024 * 1024:
                    raise ValueError("portal document exceeds the 12 MB limit")
                content = Path(path).read_bytes()
            finally:
                Path(path).unlink(missing_ok=True)
            filename = download.suggested_filename or item.title or "document"
            mime = "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream"
        else:
            resolved: dict[str, bool] = {}
            request_url = url
            response = None
            for _ in range(6):
                assert_portal_url(portal, request_url)
                if await _host_resolves_private_or_local(_host_from_url(request_url), resolved):
                    raise BrowserSecurityError("portal download resolved to a private/local network target")
                response = await context.request.get(
                    request_url,
                    timeout=30000,
                    fail_on_status_code=False,
                    max_redirects=0,
                )
                if response.status not in {301, 302, 303, 307, 308}:
                    break
                location = str(response.headers.get("location") or "")
                if not location:
                    raise BrowserSecurityError("portal download redirect did not provide a target")
                request_url = urljoin(request_url, location)
            else:
                raise BrowserSecurityError("portal download exceeded the redirect limit")
            if response is None or not response.ok:
                raise ValueError(f"portal download failed with HTTP status {response.status if response else 'unknown'}")
            final_url = response.url
            assert_portal_url(portal, final_url)
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > 12 * 1024 * 1024:
                raise ValueError("portal document exceeds the 12 MB limit")
            content = await response.body()
            if len(content) > 12 * 1024 * 1024:
                raise ValueError("portal document exceeds the 12 MB limit")
            mime = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            disposition = str(response.headers.get("content-disposition") or "")
            filename = item.title or url.rsplit("/", 1)[-1] or "document"
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].strip(' "')
        if mime not in recipe.expected_mime_types:
            raise ValueError(f"download MIME type {mime or 'missing'} is not allowed by the recipe")
        return content, safe_document_name(filename), mime
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()


async def sync_source(db: AsyncSession, source_id: int) -> dict[str, Any]:
    source = await db.get(PortalDocumentSource, source_id)
    if source is None or not source.enabled:
        raise LookupError("portal document source is missing or disabled")
    source.status = "syncing"
    source.last_error = ""
    await db.commit()
    discovery = await discover_source(db, source_id, persist=True)
    if discovery.get("status") == "needs_user_auth":
        return discovery
    rows = list(
        (
            await db.execute(
                select(PortalDocumentItem).where(
                    PortalDocumentItem.portal_document_source_id == source.id,
                    PortalDocumentItem.status.in_(["discovered", "retry", "downloaded", "downloading"]),
                ).order_by(PortalDocumentItem.id).limit(source.max_documents_per_sync)
            )
        ).scalars()
    )
    result = {
        "source_id": source.id,
        "discovered": discovery["found"],
        "downloaded": 0,
        "duplicates": 0,
        "failed": 0,
        "failure_categories": {},
    }
    for item in rows:
        try:
            item.status = "downloading"
            item.attempts += 1
            await db.commit()
            content, filename, mime = await _download_item(db, source, item)
            item.status = "downloaded"
            item.checksum_sha256 = hashlib.sha256(content).hexdigest()
            item.downloaded_at = utcnow()
            await db.commit()
            ingested = await ingest_document_bytes(
                db,
                content=content,
                filename=filename,
                mime_type=mime,
                source_type="portal",
                source_id=f"{source.id}:{item.external_id}",
                source_name=item.provider_name or source.name,
                source_metadata={"portal_id": source.portal_id, "source_id": source.id, "external_id": item.external_id},
                category="portal_documents",
                account_scope=source.account_scope,
                document_date=item.document_date,
            )
            item.document_id = ingested.document.id
            item.status = "ingested"
            item.ingested_at = utcnow()
            item.last_error = ""
            result["downloaded" if ingested.created else "duplicates"] += 1
        except BrowserNeedsUserAuth as exc:
            source.status = "needs_user_auth"
            source.challenge_type = exc.challenge_type
            source.challenge_prompt = exc.prompt
            source.challenge_selector = exc.selector
            source.last_error = exc.prompt
            item.status = "needs_user_auth"
            item.last_error = exc.prompt
            break
        except Exception as exc:
            item.status = "retry"
            item.last_error = _safe_error(exc)
            result["failed"] += 1
            _, category = _failure_state(exc)
            categories = result["failure_categories"]
            categories[category] = int(categories.get(category) or 0) + 1
        await db.commit()
    source.last_sync_at = utcnow()
    if source.status == "degraded":
        result["recipe_drift"] = 1
    elif source.status != "needs_user_auth":
        source.status = "ready" if not result["failed"] else "partial"
        source.last_success_at = (
            utcnow()
            if not result["failed"] and int(discovery.get("found") or 0) > 0
            else source.last_success_at
        )
        source.last_error = "" if not result["failed"] else f"{result['failed']} document(s) failed"
        source.consecutive_failures = 0 if not result["failed"] else source.consecutive_failures + 1
    source.last_result_json = json.dumps(result, ensure_ascii=False)
    await write_audit(db, "portal_document_source_synced", entity_type="portal_document_source", entity_id=str(source.id), details=result)
    await db.commit()
    if result["failed"]:
        raise RuntimeError(
            f"portal document sync has {result['failed']} retryable item failure(s): "
            f"{json.dumps(result['failure_categories'], sort_keys=True)}"
        )
    return {**result, "status": source.status}


async def set_source_auth_code(db: AsyncSession, source_id: int, code: str) -> PortalDocumentSource:
    source = await db.get(PortalDocumentSource, source_id)
    if source is None:
        raise LookupError("portal document source not found")
    if source.status != "needs_user_auth" or source.challenge_type != "otp":
        raise ValueError("source is not waiting for an OTP code")
    source.pending_auth_value_encrypted = encrypt_text(code)
    source.status = "ready"
    await db.commit()
    await db.refresh(source)
    return source
