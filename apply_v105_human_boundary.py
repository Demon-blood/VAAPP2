#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

BASE_COMMIT = "af339ac48e600b38bebababbb048e77464be3900"
ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise SystemExit(f"v1.0.5 delta aborted: {message}")


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        fail(f"missing expected baseline file: {path}")
    return target.read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        fail(f"expected exactly one patch anchor in {path}, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


def insert_after(path: str, anchor: str, addition: str) -> None:
    replace_once(path, anchor, anchor + addition)


def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception as exc:
        fail(f"run this script from the repository root after extracting the delta: {exc}")


def ensure_baseline() -> None:
    head = git_head()
    if head != BASE_COMMIT:
        fail(f"expected verified v1.0.4 baseline {BASE_COMMIT}, but HEAD is {head}. Rebuild the delta against latest main instead of stacking it.")
    for args, label in [
        (["git", "diff", "--quiet"], "tracked working tree"),
        (["git", "diff", "--cached", "--quiet"], "index"),
    ]:
        result = subprocess.run(args, cwd=ROOT, check=False)
        if result.returncode != 0:
            fail(f"{label} has existing changes. Apply this release only to a clean checkout of the verified baseline.")
    if 'APP_VERSION = "1.0.4"' not in read("backend/app/core/version.py"):
        fail("backend version is not the verified 1.0.4 baseline")
    if "version: 1.0.4+46" not in read("android/pubspec.yaml"):
        fail("Android version is not the verified 1.0.4+46 baseline")


def patch_release_identity() -> None:
    replace_once(
        "backend/app/core/version.py",
        'APP_VERSION = "1.0.4"\nREQUIRED_ANDROID_VERSION = "1.0.4"\n',
        'APP_VERSION = "1.0.5"\nREQUIRED_ANDROID_VERSION = "1.0.5"\n',
    )
    replace_once("backend/pyproject.toml", 'version = "1.0.4"', 'version = "1.0.5"')
    replace_once("android/pubspec.yaml", "version: 1.0.4+46", "version: 1.0.5+47")
    replace_once(
        "android/lib/release_contract.dart",
        "const String appRelease = '1.0.4';\nconst String minimumBackendVersion = '1.0.4';",
        "const String appRelease = '1.0.5';\nconst String minimumBackendVersion = '1.0.5';",
    )
    workflow = ".github/workflows/android-release.yml"
    text = read(workflow)
    for old, new in [
        ("backend 1.0.4", "backend 1.0.5"),
        ("Full-Time-VA-Android-v1.0.4.apk", "Full-Time-VA-Android-v1.0.5.apk"),
        ("Full-Time VA Android v1.0.4", "Full-Time VA Android v1.0.5"),
    ]:
        if old not in text:
            fail(f"workflow baseline anchor missing: {old}")
        text = text.replace(old, new)
    write(workflow, text)


def patch_main_router_and_repair() -> None:
    insert_after(
        "backend/app/main.py",
        "from app.api.fulfillment_routes import router as fulfillment_router\n",
        "from app.api.v105_routes import router as v105_router\n",
    )
    insert_after(
        "backend/app/main.py",
        "from app.services.operations_service import cleanup_low_value_documents\n",
        "from app.services.communication_correlation import repair_communication_correlation\n",
    )
    anchor = '''    # Remove legacy low-value attachments such as generic Terms of Service files that\n    # older versions may have archived before the retention policy was tightened.\n'''
    addition = '''    # Repair legacy communication task projections and native-SMS/Messages mirror\n    # duplicates before new autonomous work is surfaced. Source evidence is retained.\n    try:\n        async with SessionLocal() as db:\n            repaired_communications = await repair_communication_correlation(db)\n            if any(repaired_communications.values()):\n                logger.warning("v1.0.5 communication correlation repair: %s", repaired_communications)\n    except Exception:\n        logger.exception("Initial v1.0.5 communication correlation repair failed")\n'''
    replace_once("backend/app/main.py", anchor, addition + anchor)
    insert_after("backend/app/main.py", "app.include_router(fulfillment_router)\n", "app.include_router(v105_router)\n")


def patch_communications_service() -> None:
    insert_after(
        "backend/app/services/communications_service.py",
        "from app.services.communication_ownership import register_device_communication\n",
        "from app.services.communication_correlation import find_cross_transport_duplicate\n"
        "from app.services.relationship_preferences import relationship_reply_review_reason\n"
        "from app.services.relationship_style_learning import relationship_reply_context_for_party\n",
    )
    old = '''async def _decision_for(db: AsyncSession, payload: CommunicationIngestRequest) -> dict[str, Any]:\n    fallback = _local_decision(payload)\n    # Device-history catch-up can contain hundreds of records. It is evidence import,\n    # not a live reply opportunity, so keep it deterministic and avoid burning AI\n    # quota or holding one mobile HTTP request open across many provider calls.\n    if payload.provider in {"android_sms_history", "android_call_log"}:\n        return fallback\n    if payload.channel == "call" or payload.direction != "incoming" or not payload.body.strip():\n        return fallback\n    try:\n        decision = await analyze_communication(\n            db,\n            {\n                "channel": payload.channel,\n                "provider": payload.provider,\n                "sender": payload.sender,\n                "body": payload.body[:12000],\n                "event_type": payload.event_type,\n                "supports_direct_reply": payload.supports_direct_reply,\n            },\n            urgent=any(term in payload.body.casefold() for term in URGENT_TERMS),\n            sensitive=_text_sensitive(payload.body),\n        )\n    except (AIConfigurationError, AIQuotaDeferred, Exception):\n        decision = fallback\n    return _normalize_decision(payload, decision)\n'''
    new = '''async def _decision_for(db: AsyncSession, payload: CommunicationIngestRequest) -> dict[str, Any]:\n    fallback = _local_decision(payload)\n    # Device-history catch-up can contain hundreds of records. It is evidence import,\n    # not a live reply opportunity, so keep it deterministic and avoid burning AI\n    # quota or holding one mobile HTTP request open across many provider calls.\n    if payload.provider in {"android_sms_history", "android_call_log"}:\n        return fallback\n    relationship_preferences, relationship_reply_context = await relationship_reply_context_for_party(\n        db, payload.sender, channel=payload.channel, provider=payload.provider\n    )\n    if payload.channel == "call" or payload.direction != "incoming" or not payload.body.strip():\n        return fallback\n    try:\n        decision = await analyze_communication(\n            db,\n            {\n                "channel": payload.channel,\n                "provider": payload.provider,\n                "sender": payload.sender,\n                "body": payload.body[:12000],\n                "event_type": payload.event_type,\n                "supports_direct_reply": payload.supports_direct_reply,\n                "relationship_reply_preferences": relationship_reply_context,\n            },\n            urgent=any(term in payload.body.casefold() for term in URGENT_TERMS),\n            sensitive=_text_sensitive(payload.body),\n        )\n    except (AIConfigurationError, AIQuotaDeferred, Exception):\n        decision = fallback\n    normalized = _normalize_decision(payload, decision)\n    review_reason = relationship_reply_review_reason(\n        relationship_preferences,\n        incoming_text=payload.body,\n        proposed_reply=str(normalized.get("reply_text") or ""),\n    )\n    if review_reason:\n        normalized["relationship_review_required"] = True\n        normalized["relationship_review_reason"] = review_reason\n        normalized["action_required"] = True\n        normalized["auto_reply_safe"] = False\n    return normalized\n'''
    replace_once("backend/app/services/communications_service.py", old, new)

    anchor = '''    if existing is not None:\n        action = await _pending_action_for(db, existing.id)\n        return {\n            "event_id": existing.id,\n            "duplicate": True,\n            "decision": json.loads(existing.decision_json or "{}"),\n            "device_action": _action_payload(action) if action is not None else None,\n        }\n\n'''
    addition = '''    transport_duplicate = await find_cross_transport_duplicate(\n        db,\n        provider=payload.provider,\n        channel=payload.channel,\n        package_name=payload.package_name,\n        sender=payload.sender,\n        body=payload.body,\n        occurred_at=_database_datetime(payload.occurred_at),\n    )\n    if transport_duplicate is not None:\n        action = await _pending_action_for(db, transport_duplicate.id)\n        await db.commit()\n        return {\n            "event_id": transport_duplicate.id,\n            "duplicate": True,\n            "decision": json.loads(transport_duplicate.decision_json or "{}"),\n            "device_action": _action_payload(action) if action is not None else None,\n        }\n\n'''
    replace_once("backend/app/services/communications_service.py", anchor, anchor + addition)
    legacy_task_block = '''    if event.action_required:
        existing_task = (
            await db.execute(
                select(Task).where(
                    Task.source_type == "communication",
                    Task.source_id == str(event.id),
                    Task.status.in_(["open", "waiting"]),
                )
            )
        ).scalar_one_or_none()
        if existing_task is None:
            db.add(
                Task(
                    title=f"Follow up: {payload.sender or payload.channel}",
                    description=(payload.body[:1200] or decision["reasoning_summary"]),
                    source_type="communication",
                    source_id=str(event.id),
                    priority=event.priority,
                    requires_approval=event.protected,
                )
            )

'''
    replace_once(
        "backend/app/services/communications_service.py",
        legacy_task_block,
        "    # Phase-2 CommunicationEvent ownership is canonical. Do not create a\n"
        "    # parallel legacy Task projection for new device messages. v1.0.5 startup/core\n"
        "    # repair supersedes historical projections without deleting source evidence.\n\n",
    )


def patch_communication_ownership() -> None:
    old = '''            payload={\n                "thread_record_id": thread.id,\n                "communication_event_id": event.id,\n                "channel": event.channel,\n                "provider": event.provider,\n                "priority": event.priority,\n                "protected": event.protected,\n            },\n'''
    new = '''            payload={\n                "thread_record_id": thread.id,\n                "communication_event_id": event.id,\n                "channel": event.channel,\n                "provider": event.provider,\n                "priority": event.priority,\n                "protected": event.protected,\n                "requires_user_review": bool(_loads(event.decision_json).get("relationship_review_required")),\n                "proposed_reply": str(_loads(event.decision_json).get("reply_text") or ""),\n            },\n'''
    replace_once("backend/app/services/communication_ownership.py", old, new)
    action_old = '''                "protected": event.protected,
                "expect_reply": bool(event.action_required),
'''
    action_new = '''                # Source sensitivity remains on CommunicationEvent.protected. A prior
                # exact user authorization only prevents a second approval loop for
                # this newly persisted executor action; it does not erase classification.
                "protected": bool(event.protected and not _loads(event.decision_json).get("specific_authorized")),
                "source_protected": event.protected,
                "expect_reply": bool(event.action_required),
'''
    replace_once("backend/app/services/communication_ownership.py", action_old, action_new)


def patch_autonomous_core() -> None:
    replace_once(
        "backend/app/services/autonomous_core.py",
        "    for task in tasks:\n        event_type = \"task_needs_decision\" if task.requires_approval else \"task_pending\"\n",
        "    for task in tasks:\n        # CommunicationEvent is the canonical Phase-2 owner. Legacy communication\n        # Task rows are projections and must never seed a second objective.\n        if task.source_type == \"communication\":\n            continue\n        event_type = \"task_needs_decision\" if task.requires_approval else \"task_pending\"\n",
    )
    old = '''    elif event.event_type in {"email_actionable", "communication_actionable"}:\n        objective, _ = await _create_objective(\n            db,\n            event,\n            title=event.title,\n            goal=str(payload.get("reasoning_summary") or event.title),\n            category=str(payload.get("category") or event.event_type),\n            priority=str(payload.get("priority") or "normal"),\n            status="blocked_capability",\n            reason="The message is durably owned by the VA, but its requested domain action belongs to a later executor phase; it is not a user approval request.",\n        )\n        thread_record_id = int(payload.get("thread_record_id") or 0)\n        if thread_record_id > 0:\n            from app.services.communication_ownership import link_thread_objective\n            await link_thread_objective(db, thread_record_id=thread_record_id, objective_id=objective.id)\n'''
    new = '''    elif event.event_type in {"email_actionable", "communication_actionable"}:\n        communication_decision = event.event_type == "communication_actionable" and bool(\n            payload.get("requires_user_review")\n            or (payload.get("protected") and str(payload.get("proposed_reply") or "").strip())\n        )\n        objective, _ = await _create_objective(\n            db,\n            event,\n            title=event.title,\n            goal=str(payload.get("reasoning_summary") or event.title),\n            category=str(payload.get("category") or event.event_type),\n            priority=str(payload.get("priority") or "normal"),\n            risk_level="high" if communication_decision else "low",\n            status="needs_user" if communication_decision else "blocked_capability",\n            reason=(\n                "This communication contains a material decision or an explicit relationship-level review requirement."\n                if communication_decision\n                else "The message is durably owned by the VA, but its requested domain action has no configured real executor; it is not a fake user task."\n            ),\n        )\n        thread_record_id = int(payload.get("thread_record_id") or 0)\n        if thread_record_id > 0:\n            from app.services.communication_ownership import link_thread_objective\n            await link_thread_objective(db, thread_record_id=thread_record_id, objective_id=objective.id)\n'''
    replace_once("backend/app/services/autonomous_core.py", old, new)

    replace_once(
        "backend/app/services/autonomous_core.py",
        "async def run_core_cycle(db: AsyncSession, *, create_manual_run: bool = False) -> dict[str, Any]:\n    if create_manual_run:\n",
        "async def run_core_cycle(db: AsyncSession, *, create_manual_run: bool = False) -> dict[str, Any]:\n    from app.services.communication_correlation import repair_communication_correlation\n\n    communication_correlation = await repair_communication_correlation(db)\n    if create_manual_run:\n",
    )
    replace_once(
        "backend/app/services/autonomous_core.py",
        '''    return {\n        "seeded": seeded,\n''',
        '''    return {\n        "communication_correlation": communication_correlation,\n        "seeded": seeded,\n''',
    )
    anchor = '''    if include_timeline:\n'''
    addition = '''    if row.status == "needs_user":\n        from app.services.specific_authorization import user_action_for_objective\n\n        payload["user_action"] = await user_action_for_objective(db, row)\n'''
    replace_once("backend/app/services/autonomous_core.py", anchor, addition + anchor)


def patch_email_and_policy() -> None:
    insert_after("backend/app/services/email_processor.py", "import asyncio\n", "import hashlib\n")
    insert_after(
        "backend/app/services/email_processor.py",
        "from app.services.communication_ownership import register_email_inbound\n",
        "from app.services.relationship_preferences import preference_digest\n"
        "from app.services.relationship_style_learning import relationship_reply_context_for_party\n",
    )
    replace_once(
        "backend/app/services/email_processor.py",
        '''    extraction = local_extract(body_text, attachments)\n    fingerprint = content_fingerprint(record.sender, record.subject, body_text, attachments)\n    decision: AutomationDecision | None = await cached_decision(db, fingerprint)\n''',
        '''    extraction = local_extract(body_text, attachments)\n    relationship_preferences, relationship_reply_context = await relationship_reply_context_for_party(db, record.sender)\n    base_fingerprint = content_fingerprint(record.sender, record.subject, body_text, attachments)\n    fingerprint = hashlib.sha256(\n        f"{base_fingerprint}:{preference_digest(relationship_reply_context)}".encode("utf-8")\n    ).hexdigest()\n    decision: AutomationDecision | None = await cached_decision(db, fingerprint)\n''',
    )
    replace_once(
        "backend/app/services/email_processor.py",
        '''            "local_extraction": extraction,\n            "existing_gmail_labels": list(label_ids),\n''',
        '''            "local_extraction": extraction,\n            "existing_gmail_labels": list(label_ids),\n            "relationship_reply_preferences": relationship_reply_context,\n''',
    )

    insert_after(
        "backend/app/services/autonomy_policy.py",
        "from app.schemas.api import AutomationDecision\n",
        "from app.services.relationship_preferences import communication_preferences_for_party, relationship_reply_review_reason\n",
    )
    old = '''    explicit = await get_preference(db, "email_reply", f"sender:{sender}")\n    if explicit is not None and _decode(explicit.value_json).get("auto_send") is False:\n        return False, "explicit_block"\n\n    category_text = f"{decision.category} {' '.join(decision.labels)}".lower()\n    combined = f"{message.subject}\\n{message.snippet}\\n{body}\\n{decision.reasoning_summary}"\n'''
    new = '''    relationship_preferences = await communication_preferences_for_party(db, sender)\n    relationship_review = relationship_reply_review_reason(\n        relationship_preferences,\n        incoming_text=f"{message.subject}\\n{message.snippet}\\n{decision.reasoning_summary}",\n        proposed_reply=body,\n    )\n    if relationship_review:\n        return False, relationship_review\n\n    explicit = await get_preference(db, "email_reply", f"sender:{sender}")\n    if explicit is not None and _decode(explicit.value_json).get("auto_send") is False:\n        return False, "explicit_block"\n\n    category_text = f"{decision.category} {' '.join(decision.labels)}".lower()\n    combined = f"{message.subject}\\n{message.snippet}\\n{body}\\n{decision.reasoning_summary}"\n'''
    replace_once("backend/app/services/autonomy_policy.py", old, new)
    replace_once(
        "backend/app/services/autonomy_policy.py",
        '''    if explicit is not None and _decode(explicit.value_json).get("auto_send") is True:\n        return True, (\n''',
        '''    if relationship_preferences.get("routine_auto_send") is True:\n        return True, "explicit_relationship_preference"\n\n    if explicit is not None and _decode(explicit.value_json).get("auto_send") is True:\n        return True, (\n''',
    )


def patch_ai_prompts() -> None:
    replace_once(
        "backend/app/integrations/ai_client.py",
        '''payments are controlled by separate safety rules outside this model."""\n''',
        '''payments are controlled by separate safety rules outside this model. If the input contains\nrelationship_reply_preferences, use those explicit settings only to adapt reply language, tone,\nformality, greeting/sign-off and length. If learned_writing_style is present, imitate its bounded\nmessage-length, casing, punctuation, emoji and representative-example patterns without copying content\nblindly. Explicit relationship instructions/examples override learned style. Never infer sensitive\npersonal attributes from either source, and never treat style preferences as execution,\npayment, legal, security or other material authority."""\n''',
    )
    # Communication prompt wording varies independently from the email prompt. Add the
    # relationship rule immediately after its declaration so future prompt edits remain local.
    marker = 'COMMUNICATION_SYSTEM_PROMPT = """'
    text = read("backend/app/integrations/ai_client.py")
    if marker not in text:
        fail("communication AI prompt anchor missing")
    text = text.replace(
        marker,
        marker
        + "If relationship_reply_preferences are present, follow those explicit style settings for the reply only. "
        + "They never grant auto-send, payment, legal, security or material execution authority. "
        + "If learned_writing_style is present, imitate its bounded writing patterns without blindly copying old content; explicit instructions/examples override it. "
        + "Do not infer sensitive personal attributes.\\n",
        1,
    )
    write("backend/app/integrations/ai_client.py", text)


def patch_android_state() -> None:
    anchor = '''  Future<Map<String, dynamic>> recheckVaObjective(int objectiveId) async {\n    late Map<String, dynamic> result;\n    await _run(() async {\n      result = Map<String, dynamic>.from(\n        await api.postJson('/api/va/objectives/$objectiveId/recheck') as Map,\n      );\n      await refreshAll(showBusy: false);\n    });\n    return result;\n  }\n\n'''
    addition = '''  Future<Map<String, dynamic>> authorizeVaObjective(int objectiveId, String actionFingerprint) async {\n    late Map<String, dynamic> result;\n    await _run(() async {\n      result = Map<String, dynamic>.from(\n        await api.postJson('/api/va/objectives/$objectiveId/authorize', {\n          'action_fingerprint': actionFingerprint,\n          'reason': '',\n        }) as Map,\n      );\n      await _syncDeviceLink();\n      await refreshAll(showBusy: false);\n    });\n    return result;\n  }\n\n  Future<Map<String, dynamic>> declineVaObjective(\n    int objectiveId,\n    String actionFingerprint, {\n    String reason = '',\n  }) async {\n    late Map<String, dynamic> result;\n    await _run(() async {\n      result = Map<String, dynamic>.from(\n        await api.postJson('/api/va/objectives/$objectiveId/decline', {\n          'action_fingerprint': actionFingerprint,\n          'reason': reason,\n        }) as Map,\n      );\n      await refreshAll(showBusy: false);\n    });\n    return result;\n  }\n\n'''
    replace_once("android/lib/app_state.dart", anchor, anchor + addition)
    anchor2 = '''  Future<Map<String, dynamic>> relationshipDetail(int relationshipId) async {\n    return Map<String, dynamic>.from(\n      await api.getJson('/api/relationships/$relationshipId') as Map,\n    );\n  }\n\n'''
    addition2 = '''  Future<Map<String, dynamic>> relationshipCommunicationPreferences(int relationshipId) async {\n    return Map<String, dynamic>.from(\n      await api.getJson('/api/relationships/$relationshipId/communication-preferences') as Map,\n    );\n  }\n\n  Future<Map<String, dynamic>> updateRelationshipCommunicationPreferences(\n    int relationshipId,\n    Map<String, dynamic> values,\n  ) async {\n    late Map<String, dynamic> result;\n    await _run(() async {\n      result = Map<String, dynamic>.from(\n        await api.putJson('/api/relationships/$relationshipId/communication-preferences', values) as Map,\n      );\n      await refreshAll(showBusy: false);\n    });\n    return result;\n  }\n\n  Future<Map<String, dynamic>> relearnRelationshipCommunicationStyle(int relationshipId) async {\n    late Map<String, dynamic> result;\n    await _run(() async {\n      result = Map<String, dynamic>.from(\n        await api.postJson('/api/relationships/$relationshipId/communication-style/relearn') as Map,\n      );\n    });\n    return result;\n  }\n\n'''
    replace_once("android/lib/app_state.dart", anchor2, anchor2 + addition2)


def patch_android_operations() -> None:
    old_loop = '''            for (final raw in needsUser) ...[\n              _ObjectiveCard(\n                row: Map<String, dynamic>.from(raw),\n                onRecheck: () => _recheck(context, raw['id']),\n                needsUser: true,\n              ),\n              const SizedBox(height: 8),\n            ],\n'''
    new_loop = '''            for (final raw in needsUser) ...[\n              _ObjectiveCard(\n                row: Map<String, dynamic>.from(raw),\n                onRecheck: () => _recheck(context, raw['id']),\n                onAuthorize: () => _authorize(context, Map<String, dynamic>.from(raw)),\n                onDecline: () => _decline(context, Map<String, dynamic>.from(raw)),\n                onOpenAuthorization: () => _openProviderAuthorization(context, Map<String, dynamic>.from(raw)),\n                needsUser: true,\n              ),\n              const SizedBox(height: 8),\n            ],\n'''
    replace_once("android/lib/screens/va_operations_page.dart", old_loop, new_loop)
    method_anchor = '''  Future<void> _showCapabilitySetup(BuildContext context, Map<String, dynamic> row) async {\n'''
    methods = '''  Future<void> _openProviderAuthorization(BuildContext context, Map<String, dynamic> row) async {\n    final userAction = Map<String, dynamic>.from((row['user_action'] as Map?) ?? const {});\n    if (userAction['kind'] != 'external_authorization') return;\n    final rawUrl = '${userAction['authorization_url'] ?? ''}'.trim();\n    if (rawUrl.isEmpty) {\n      ScaffoldMessenger.of(context).showSnackBar(\n        const SnackBar(content: Text('This provider requires authorization, but no authorization URL is currently available. Open the provider or bank flow, then recheck.')),\n      );\n      return;\n    }\n    final uri = Uri.tryParse(rawUrl);\n    if (uri == null || uri.scheme != 'https') {\n      ScaffoldMessenger.of(context).showSnackBar(\n        const SnackBar(content: Text('The provider authorization URL is invalid or unsafe.')),\n      );\n      return;\n    }\n    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);\n    if (!opened && context.mounted) {\n      ScaffoldMessenger.of(context).showSnackBar(\n        const SnackBar(content: Text('Could not open the provider authorization page.')),\n      );\n    }\n  }\n\n  Future<void> _authorize(BuildContext context, Map<String, dynamic> row) async {\n    final objectiveId = (row['id'] as num?)?.toInt();\n    final userAction = Map<String, dynamic>.from((row['user_action'] as Map?) ?? const {});\n    final fingerprint = '${userAction['action_fingerprint'] ?? ''}';\n    if (objectiveId == null || fingerprint.isEmpty || userAction['kind'] != 'specific_authorization') return;\n    final proposal = Map<String, dynamic>.from((userAction['proposal'] as Map?) ?? const {});\n    final confirmed = await showDialog<bool>(\n          context: context,\n          builder: (dialogContext) => AlertDialog(\n            title: const Text('Authorize this exact action?'),\n            content: Text(\n              '${proposal['summary'] ?? row['title'] ?? 'Material VA action'}\\n\\n'\n              'This authorization is bound to this objective and proposal only. It is not standing authority and is not proof of completion.',\n            ),\n            actions: [\n              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),\n              FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Authorize')),\n            ],\n          ),\n        ) ??\n        false;\n    if (!confirmed || !context.mounted) return;\n    try {\n      await context.read<AppState>().authorizeVaObjective(objectiveId, fingerprint);\n      if (context.mounted) {\n        ScaffoldMessenger.of(context).showSnackBar(\n          const SnackBar(content: Text('Specific authorization recorded. The VA resumed automatically.')),\n        );\n      }\n    } catch (error) {\n      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));\n    }\n  }\n\n  Future<void> _decline(BuildContext context, Map<String, dynamic> row) async {\n    final objectiveId = (row['id'] as num?)?.toInt();\n    final userAction = Map<String, dynamic>.from((row['user_action'] as Map?) ?? const {});\n    final fingerprint = '${userAction['action_fingerprint'] ?? ''}';\n    if (objectiveId == null || fingerprint.isEmpty || userAction['kind'] != 'specific_authorization') return;\n    final confirmed = await showDialog<bool>(\n          context: context,\n          builder: (dialogContext) => AlertDialog(\n            title: const Text('Decline this action?'),\n            content: const Text('The exact proposed action will be cancelled. Original message/source evidence will be kept.'),\n            actions: [\n              TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Keep')),\n              FilledButton.tonal(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Decline')),\n            ],\n          ),\n        ) ??\n        false;\n    if (!confirmed || !context.mounted) return;\n    try {\n      await context.read<AppState>().declineVaObjective(objectiveId, fingerprint);\n    } catch (error) {\n      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$error')));\n    }\n  }\n\n'''
    replace_once("android/lib/screens/va_operations_page.dart", method_anchor, methods + method_anchor)

    start = read("android/lib/screens/va_operations_page.dart")
    old_class_start = "class _ObjectiveCard extends StatelessWidget {"
    old_class_end = "\nclass _StatusChip extends StatelessWidget {"
    first = start.find(old_class_start)
    last = start.find(old_class_end, first)
    if first < 0 or last < 0:
        fail("ObjectiveCard class boundaries not found")
    new_class = r'''class _ObjectiveCard extends StatelessWidget {
  const _ObjectiveCard({
    required this.row,
    this.onRecheck,
    this.onAuthorize,
    this.onDecline,
    this.onOpenAuthorization,
    this.needsUser = false,
  });
  final Map<String, dynamic> row;
  final VoidCallback? onRecheck;
  final VoidCallback? onAuthorize;
  final VoidCallback? onDecline;
  final VoidCallback? onOpenAuthorization;
  final bool needsUser;

  @override
  Widget build(BuildContext context) {
    final status = '${row['status'] ?? 'unknown'}';
    final accent = _statusColor(status);
    final reason = needsUser
        ? '${row['needs_user_reason'] ?? ''}'
        : '${row['blocked_reason'] ?? row['last_error'] ?? ''}';
    final steps = (row['steps'] as List? ?? const []).length;
    final evidence = (row['evidence_count'] as num?)?.toInt() ?? 0;
    final userAction = Map<String, dynamic>.from((row['user_action'] as Map?) ?? const {});
    final specific = needsUser && userAction['kind'] == 'specific_authorization';
    final external = needsUser && userAction['kind'] == 'external_authorization';
    final providerAuthUrl = '${userAction['authorization_url'] ?? ''}'.trim();
    final proposal = Map<String, dynamic>.from((userAction['proposal'] as Map?) ?? const {});
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(width: 4, height: 34, decoration: BoxDecoration(color: accent, borderRadius: BorderRadius.circular(4))),
                const SizedBox(width: 10),
                Expanded(child: Text('${row['title'] ?? 'VA objective'}', style: const TextStyle(fontWeight: FontWeight.w900))),
                _StatusChip(status: status, color: accent),
              ],
            ),
            if ('${row['goal'] ?? ''}'.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('${row['goal']}', style: const TextStyle(color: VaTheme.textMuted)),
            ],
            if (reason.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(reason, style: TextStyle(color: needsUser ? VaTheme.warning : VaTheme.textMuted, fontSize: 12)),
            ],
            if (specific) ...[
              const SizedBox(height: 10),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: VaTheme.warning.withValues(alpha: .08),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: VaTheme.warning.withValues(alpha: .25)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Proposed action', style: TextStyle(fontWeight: FontWeight.w900)),
                    const SizedBox(height: 4),
                    Text('${proposal['summary'] ?? row['goal'] ?? row['title'] ?? ''}'),
                    if ('${proposal['provider'] ?? ''}'.isNotEmpty)
                      Text('Provider: ${proposal['provider']}', style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
                    if ('${proposal['counterparty'] ?? ''}'.isNotEmpty)
                      Text('Counterparty: ${proposal['counterparty']}', style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
                    if ('${proposal['amount_mentioned'] ?? ''}'.isNotEmpty)
                      Text('Amount mentioned: ${proposal['amount_mentioned']} ${proposal['currency'] ?? ''}', style: const TextStyle(color: VaTheme.textMuted, fontSize: 12)),
                    if ('${proposal['proposed_reply'] ?? ''}'.isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Text('Reply: ${proposal['proposed_reply']}', maxLines: 5, overflow: TextOverflow.ellipsis),
                    ],
                    if ('${proposal['source_excerpt'] ?? ''}'.isNotEmpty && '${proposal['proposed_reply'] ?? ''}'.isEmpty) ...[
                      const SizedBox(height: 6),
                      Text('${proposal['source_excerpt']}', maxLines: 5, overflow: TextOverflow.ellipsis),
                    ],
                    const SizedBox(height: 6),
                    const Text(
                      'Authorize only grants permission for this exact proposal. Provider/source evidence is still required for completion.',
                      style: TextStyle(color: VaTheme.textMuted, fontSize: 11),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 9),
            Text('$steps persisted step${steps == 1 ? '' : 's'} · $evidence verified outcome${evidence == 1 ? '' : 's'}',
                style: const TextStyle(color: VaTheme.textMuted, fontSize: 11)),
            if (specific && onAuthorize != null && onDecline != null) ...[
              const SizedBox(height: 10),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  OutlinedButton.icon(
                    onPressed: onDecline,
                    icon: const Icon(Icons.close_rounded, size: 17),
                    label: const Text('Decline'),
                  ),
                  const SizedBox(width: 8),
                  FilledButton.icon(
                    onPressed: onAuthorize,
                    icon: const Icon(Icons.check_rounded, size: 17),
                    label: const Text('Authorize'),
                  ),
                ],
              ),
            ] else if (external && onRecheck != null) ...[
              const SizedBox(height: 10),
              Wrap(
                alignment: WrapAlignment.end,
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (onOpenAuthorization != null)
                    FilledButton.icon(
                      onPressed: onOpenAuthorization,
                      icon: const Icon(Icons.open_in_new_rounded, size: 17),
                      label: Text(providerAuthUrl.isEmpty ? 'Open provider / bank' : 'Open provider authorization'),
                    ),
                  TextButton.icon(
                    onPressed: onRecheck,
                    icon: const Icon(Icons.refresh_rounded, size: 17),
                    label: const Text('Recheck after authorization'),
                  ),
                ],
              ),
            ] else if (onRecheck != null) ...[
              const SizedBox(height: 10),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: onRecheck,
                  icon: const Icon(Icons.refresh_rounded, size: 17),
                  label: const Text('Recheck user action'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Color _statusColor(String status) => switch (status) {
        'completed' => VaTheme.success,
        'needs_user' => VaTheme.warning,
        'failed' || 'blocked_system' => VaTheme.danger,
        'verifying' || 'executing' => VaTheme.cyan,
        'waiting' || 'waiting_external' || 'waiting_provider' || 'blocked_capability' => VaTheme.secondary,
        _ => VaTheme.primaryBright,
      };
}
'''
    write("android/lib/screens/va_operations_page.dart", start[:first] + new_class + start[last:])


def patch_work_relationship_ui() -> None:
    insert_after(
        "android/lib/screens/work_page.dart",
        "import 'tasks_page.dart';\n",
        "import 'relationship_preferences_page.dart';\n",
    )
    anchor = '''            const SizedBox(height: 18),\n            Text('Verified identities', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),\n'''
    addition = '''            SizedBox(\n              width: double.infinity,\n              child: OutlinedButton.icon(\n                onPressed: () async {\n                  final relationshipId = (detail['id'] as num?)?.toInt();\n                  if (relationshipId == null) return;\n                  await Navigator.of(context).push<bool>(\n                    MaterialPageRoute(\n                      builder: (_) => RelationshipPreferencesPage(\n                        relationshipId: relationshipId,\n                        relationshipName: title,\n                      ),\n                    ),\n                  );\n                },\n                icon: const Icon(Icons.tune_rounded),\n                label: const Text('Edit reply preferences'),\n              ),\n            ),\n            const SizedBox(height: 18),\n'''
    replace_once("android/lib/screens/work_page.dart", anchor, addition + "            Text('Verified identities', style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w900)),\n")


def patch_readme_manifest_state() -> None:
    readme = "README.md"
    replace_once(readme, "# Full-Time VA v1.0.4 — Execution Readiness & Setup Assistant", "# Full-Time VA v1.0.5 — Human Boundary & Relationship-Aware Communications")
    replace_once(
        readme,
        "Full-Time VA v1.0.4 is the fourth maintenance release on the cumulative Phase 1–10 production baseline. It keeps the existing autonomous executors and adds capability-specific readiness checks plus in-app setup guidance so an OFFLINE badge explains exactly what is missing instead of forcing the user to reverse-engineer configuration.\n\nThe v1.0.4 patch does not add a simulated executor. It makes capability reporting stricter: Gmail push requires an active Gmail watch for the configured Pub/Sub topic and verification token, and fulfillment only reports available when an enabled provider is actually linked to an enabled browser portal or to a support phone with live telephony. The v1.0 evidence contract remains unchanged.",
        "Full-Time VA v1.0.5 is the fifth maintenance release on the cumulative Phase 1–10 production baseline. It tightens the human-decision boundary so material Needs You items expose exact objective-bound authorization/decline actions, repairs duplicate communication ownership at the source layer, and adds explicit plus opt-in learned per-relationship reply style on top of Phase-4 relationship memory.\n\nThe v1.0.5 patch does not add a simulated executor or weaken evidence rules. Authorization resumes only a real durable executor when one exists; otherwise the objective becomes blocked_capability. Learned style is built only from device-observed outgoing history after excluding known VA-generated sends and sensitive samples, and never grants financial, legal, security, browser, banking or other material execution authority.",
    )
    marker = "\n## v1.0.4 execution readiness and setup assistant\n"
    section = '''\n## v1.0.5 human boundary and relationship-aware communications\n\n- Needs You material decisions expose objective-bound **Authorize** and **Decline** actions using an exact action fingerprint. Authorization is audit/context data, never external completion evidence.\n- Authorized Gmail/SMS replies resume through their existing durable executors; missing executors become `blocked_capability` rather than fabricated completion.\n- Legacy communication Task projections no longer create a second VA objective. Existing duplicate task objectives are superseded by the source CommunicationEvent objective.\n- Native SMS and Google/Samsung Messages notification mirrors are correlated only as a known cross-transport pair; identical same-channel messages are never collapsed by content alone.\n- Phase-4 RelationshipFact provenance stores editable per-person reply language, tone, formality, greeting/sign-off, length, channel, instructions/examples, auto-send preference and approval topics.\n- Per relationship, **Learn how I write to this person** can build a bounded style profile from device-observed Android SMS sent history after excluding known VA-generated sends. VA-generated replies, protected messages and credential/financial-code-like samples are excluded; at least three safe samples are required.\n- The learned relationship profile supplies length/casing/punctuation/emoji tendencies plus a few bounded representative examples to Gmail/SMS/WhatsApp/Signal/Telegram/Messenger drafting; explicit instructions/examples override it.\n- Relationship style never grants payment, legal, security, browser, banking or other material execution authority; deterministic safety policy remains authoritative.\n\nSee `docs/V1.0.5_HUMAN_BOUNDARY_RELATIONSHIP_COMMUNICATIONS.md` for the maintenance contract.\n'''
    replace_once(readme, marker, section + marker)
    text = read(readme).replace("Backend `1.0.4` · Android `1.0.4+46`", "Backend `1.0.5` · Android `1.0.5+47`")
    text = text.replace("Full-Time-VA-Android-v1.0.4.apk", "Full-Time-VA-Android-v1.0.5.apk")
    text = text.replace(
        "Verified maintenance baseline: commit `fedf9a47864f1337c0100c1ed6d9b36daffb4017` (`v1.0.3 — Logistics Tracking Ownership`), GitHub Actions run #44 successful.",
        "Verified maintenance baseline: commit `af339ac48e600b38bebababbb048e77464be3900` (`v1.0.4 — Execution Readiness & Setup Assistant`), GitHub Actions run #45 successful.",
    )
    write(readme, text)

    manifest = {
        "release": "1.0.5",
        "android_version": "1.0.5+47",
        "baseline_repository": "Demon-blood/VAAPP2",
        "baseline_commit": "66c09040326ac553a1402cd06fa6771344195d45",
        "purpose": "v1.0.5 human boundary and relationship-aware communications: objective-bound authorization/decline, communication source deduplication, explicit per-relationship reply preferences, and bounded learned writing style from device-observed non-VA sent history",
        "database_strategy": "existing_phase4_relationship_facts_no_new_table",
        "phase": "maintenance",
        "phase_name": "v1.0.5 Human Boundary & Relationship-Aware Communications",
        "next_phase": "v1.x maintenance and real-world hardening",
        "version": "1.0.5",
        "maintenance_baseline_commit": BASE_COMMIT,
        "maintenance_baseline_version": "1.0.4",
        "maintenance_baseline_actions_run": 45,
        "maintenance_baseline_actions_conclusion": "success",
    }
    write("MANIFEST.json", json.dumps(manifest, indent=2) + "\n")

    state = {
        "updated": "2026-08-15",
        "repository": "Demon-blood/VAAPP2",
        "branch": "main",
        "verified_maintenance_commit": BASE_COMMIT,
        "verified_maintenance_version": "1.0.4",
        "verified_maintenance_android_version": "1.0.4+46",
        "verified_maintenance_actions_run": 45,
        "verified_maintenance_actions_conclusion": "success",
        "local_candidate_version": "1.0.5",
        "local_candidate_android_version": "1.0.5+47",
        "current_phase": "maintenance",
        "current_phase_name": "v1.0.5 Human Boundary & Relationship-Aware Communications",
        "phase_status": "implemented as a base-locked delta from verified v1.0.4; awaiting user upload and full GitHub CI",
        "next_phase": "v1.x maintenance and real-world hardening",
        "database_strategy": "reuse_existing_relationship_fact_table",
        "v105_features": [
            "objective-bound specific authorization and decline with stale-proposal fingerprints",
            "automatic resume into durable Gmail/SMS executors without treating authorization as completion evidence",
            "provider authorization cards can open safe HTTPS SCA/MFA URLs and then recheck provider state",
            "legacy communication Task projection supersession so one real-world decision has one VA objective",
            "bounded native-SMS versus Google/Samsung Messages cross-transport correlation without same-channel content deduplication",
            "editable user-explicit relationship reply preferences stored with Phase-4 RelationshipFact provenance",
            "relationship style/language/formality/greeting/sign-off/length/channel/instruction/example adaptation across Gmail, SMS, WhatsApp, Signal, Telegram, Messenger and supported Messages notification AI context",
            "opt-in per-relationship learned writing style from device-observed Android SMS sent history after excluding known VA-generated sends and sensitive samples",
            "explicit channel-scoped notification aliases avoid unsafe display-name auto-merging",
            "relationship review and approval-topic controls that can only make deterministic communication policy stricter",
        ],
    }
    write("VAAPP_PROJECT_STATE.json", json.dumps(state, indent=2) + "\n")

    handoff = read("VAAPP_PROJECT_HANDOFF.md")
    handoff = handoff.replace("Updated: 2026-08-14", "Updated: 2026-08-15", 1)
    start = handoff.find("## Verified source of truth\n")
    end = handoff.find("## Product objective\n")
    if start < 0 or end < 0 or end <= start:
        fail("VAAPP_PROJECT_HANDOFF.md baseline status block was not found")
    current = '''## Verified source of truth\n\nPhases 1–10 and production v1.0 are complete on GitHub. The current verified maintenance baseline is commit `af339ac48e600b38bebababbb048e77464be3900` (`v1.0.4 — Execution Readiness & Setup Assistant`). GitHub Actions run #45 completed successfully end-to-end, including the backend suite, Flutter analysis/tests, persistent-signing Android release build, and GitHub prerelease publication.\n\nVerified maintenance release: backend `1.0.4` / Android `1.0.4+46`.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current local candidate\n\nBackend `1.0.5` / Android `1.0.5+47`.\n\nCurrent maintenance candidate: **v1.0.5 — Human Boundary & Relationship-Aware Communications**.\nStatus: **implemented as a base-locked delta from verified v1.0.4; awaiting user upload and full GitHub CI.**\n\nThe candidate adds objective-bound Authorize/Decline decisions, repairs duplicate communication ownership/cross-transport SMS mirrors, adds user-explicit Phase-4 relationship reply preferences, and adds opt-in learned relationship writing style from device-observed non-VA sent history. Authorization remains distinct from completion evidence, and relationship style never grants financial/legal/security/browser/banking or other material execution authority.\n\nNext work after the v1.0.5 gate is green: **v1.x maintenance and real-world hardening**.\n\n'''
    handoff = handoff[:start] + current + handoff[end:]
    write("VAAPP_PROJECT_HANDOFF.md", handoff)


def copy_new_files() -> None:
    # New files are already present because the ZIP is overlaid at repository root.
    required = [
        "backend/app/services/relationship_preferences.py",
        "backend/app/services/relationship_style_learning.py",
        "backend/app/services/communication_correlation.py",
        "backend/app/services/specific_authorization.py",
        "backend/app/api/v105_routes.py",
        "backend/tests/test_v105_human_boundary_relationship_contract.py",
        "android/lib/screens/relationship_preferences_page.dart",
        "docs/V1.0.5_HUMAN_BOUNDARY_RELATIONSHIP_COMMUNICATIONS.md",
    ]
    for path in required:
        if not (ROOT / path).exists():
            fail(f"delta file missing after extraction: {path}")


def exclude_delta_helpers() -> None:
    exclude = ROOT / ".git" / "info" / "exclude"
    if not exclude.exists():
        return
    text = exclude.read_text(encoding="utf-8")
    entries = ["/apply_v105_human_boundary.py", "/V105_DELTA_README.md"]
    changed = False
    for entry in entries:
        if entry not in text.splitlines():
            text += ("" if text.endswith("\n") or not text else "\n") + entry + "\n"
            changed = True
    if changed:
        exclude.write_text(text, encoding="utf-8")


def regenerate_manifests() -> None:
    try:
        tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    except Exception as exc:
        fail(f"cannot list tracked files: {exc}")
    # Include new delta files before they are git-added.
    new_files = [
        "backend/app/services/relationship_preferences.py",
        "backend/app/services/relationship_style_learning.py",
        "backend/app/services/communication_correlation.py",
        "backend/app/services/specific_authorization.py",
        "backend/app/api/v105_routes.py",
        "backend/tests/test_v105_human_boundary_relationship_contract.py",
        "android/lib/screens/relationship_preferences_page.dart",
        "docs/V1.0.5_HUMAN_BOUNDARY_RELATIONSHIP_COMMUNICATIONS.md",
    ]
    paths = sorted(set(tracked + new_files) - {"FILE_MANIFEST.txt", "SHA256SUMS.txt", "apply_v105_human_boundary.py", "V105_DELTA_README.md"})
    visible = [path for path in paths if (ROOT / path).is_file()]
    write("FILE_MANIFEST.txt", "".join(f"./{path}\n" for path in visible))
    sums = []
    for path in visible:
        digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        sums.append(f"{digest}  ./{path}\n")
    write("SHA256SUMS.txt", "".join(sums))


def main() -> None:
    ensure_baseline()
    copy_new_files()
    patch_release_identity()
    patch_main_router_and_repair()
    patch_communications_service()
    patch_communication_ownership()
    patch_autonomous_core()
    patch_email_and_policy()
    patch_ai_prompts()
    patch_android_state()
    patch_android_operations()
    patch_work_relationship_ui()
    patch_readme_manifest_state()
    exclude_delta_helpers()
    regenerate_manifests()
    print("v1.0.5 delta applied successfully to verified v1.0.4 baseline.")
    print("Next: run the validation commands in V105_DELTA_README.md, inspect git diff, commit and push. GitHub CI remains authoritative.")


if __name__ == "__main__":
    main()
