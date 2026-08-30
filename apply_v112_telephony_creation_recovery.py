from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

EXPECTED_BASELINE = "221205e82444f9c0bff2589cf3ffc015408e664a"
BUNDLE_ROOT = Path(__file__).resolve().parent
EXPECTED_PREVIEW_SHA256 = {
    'preview/backend/tests/test_v112_telephony_creation_recovery.py': 'ef39041b347cb161afd71cefe1974e3b6d32a2afd00bfa5f3d67f381d909d7b2',
    'preview/backend/tests/test_v112_telephony_creation_recovery_contract.py': 'be7a0e3adaac13282ba28438d185f5ca0315c51905b9347e7d91fc18d061534c',
    'preview/docs/V1.0.12_TELEPHONY_CREATION_RECOVERY.md': 'fe2828ab5ada9f9568a60978487dd6d5d670edbd4f60ef0cb11c67427e7ce27f',
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def replace_once(path: Path, old: str, new: str) -> None:
    text = read_text(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one anchor in {path}: found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def verify_bundle() -> None:
    for relative, expected in EXPECTED_PREVIEW_SHA256.items():
        path = BUNDLE_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"missing prepared bundle file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"bundle integrity mismatch for {relative}: {actual}")


def verify_repo(root: Path) -> None:
    if not (root / ".git").exists():
        raise RuntimeError(f"{root} is not a git working tree")
    head = run_git(root, "rev-parse", "HEAD")
    if head != EXPECTED_BASELINE:
        raise RuntimeError(
            f"refusing to patch unexpected HEAD {head}; expected v1.0.11 baseline {EXPECTED_BASELINE}"
        )
    if run_git(root, "status", "--porcelain"):
        raise RuntimeError("refusing to patch a dirty worktree")
    if read_text(root / "backend/app/core/version.py") != (
        'APP_VERSION = "1.0.11"\nREQUIRED_ANDROID_VERSION = "1.0.11"\n'
    ):
        raise RuntimeError("v1.0.11 backend baseline identity mismatch")
    if "version: 1.0.11+54" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.11 Android baseline identity mismatch")


def copy_prepared(root: Path, source: str, destination: str) -> None:
    src = BUNDLE_ROOT / source
    dst = root / destination
    if dst.exists():
        raise RuntimeError(f"refusing to overwrite existing additive file: {destination}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def patch_telephony_service(root: Path) -> None:
    path = root / "backend/app/services/telephony_service.py"

    replace_once(
        path,
        "from datetime import datetime, timedelta\n",
        "from datetime import UTC, datetime, timedelta\nfrom email.utils import parsedate_to_datetime\n",
    )

    helper = '''\n\ndef _twilio_provider_timestamp(value: Any) -> datetime | None:\n    text = str(value or "").strip()\n    if not text:\n        return None\n    parsed: datetime | None = None\n    try:\n        parsed = parsedate_to_datetime(text)\n    except (TypeError, ValueError):\n        try:\n            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))\n        except ValueError:\n            return None\n    if parsed.tzinfo is not None:\n        parsed = parsed.astimezone(UTC).replace(tzinfo=None)\n    return parsed\n\n\nasync def _twilio_creation_candidates(\n    db: AsyncSession, call: TelephonyCall\n) -> list[dict[str, Any]]:\n    config = await _twilio_config(db)\n    if not config["account_sid"] or not config["auth_token"]:\n        raise ValueError("Twilio reconciliation credentials are not configured")\n    target = normalize_e164(decrypt_text(call.target_encrypted))\n    from_number = normalize_e164(decrypt_text(call.from_number_encrypted))\n    reference = call.started_at or call.created_at\n    if reference.tzinfo is not None:\n        reference = reference.astimezone(UTC).replace(tzinfo=None)\n    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{config['account_sid']}/Calls.json"\n    params = {\n        "To": target,\n        "From": from_number,\n        "StartTimeAfter": (reference - timedelta(days=1)).date().isoformat(),\n        "StartTimeBefore": (reference + timedelta(days=2)).date().isoformat(),\n        "PageSize": "100",\n    }\n    async with httpx.AsyncClient(timeout=15.0) as client:\n        response = await client.get(\n            endpoint,\n            auth=httpx.BasicAuth(config["account_sid"], config["auth_token"]),\n            params=params,\n        )\n        response.raise_for_status()\n        payload = response.json()\n    if not isinstance(payload, dict) or not isinstance(payload.get("calls"), list):\n        raise ValueError("Twilio call-list response did not contain a calls list")\n    if payload.get("next_page_uri"):\n        raise ValueError(\n            "Twilio call-list recovery exceeded one bounded page; a unique provider match cannot be proven"\n        )\n\n    candidates: list[dict[str, Any]] = []\n    for raw in payload["calls"]:\n        if not isinstance(raw, dict):\n            continue\n        sid = str(raw.get("sid") or "").strip()\n        if not sid:\n            continue\n        try:\n            candidate_to = normalize_e164(str(raw.get("to") or ""))\n            candidate_from = normalize_e164(str(raw.get("from") or ""))\n        except ValueError:\n            continue\n        if candidate_to != target or candidate_from != from_number:\n            continue\n        if str(raw.get("direction") or "").strip().lower() != "outbound-api":\n            continue\n        observed_at = _twilio_provider_timestamp(raw.get("date_created") or raw.get("start_time"))\n        if observed_at is None:\n            continue\n        if abs((observed_at - reference).total_seconds()) > 10 * 60:\n            continue\n        already_bound = (\n            await db.execute(\n                select(TelephonyCall.id).where(\n                    TelephonyCall.external_call_sid == sid,\n                    TelephonyCall.id != call.id,\n                ).limit(1)\n            )\n        ).scalar_one_or_none()\n        if already_bound is not None:\n            continue\n        candidates.append(raw)\n    return candidates\n\n\nasync def _recover_uncertain_call_creation(db: AsyncSession, call: TelephonyCall) -> bool:\n    if call.direction != "outbound" or call.external_call_sid or call.status != "creation_uncertain":\n        return False\n    try:\n        candidates = await _twilio_creation_candidates(db, call)\n    except (httpx.HTTPError, ValueError) as exc:\n        call.status = "creation_uncertain"\n        call.failure_reason = (\n            f"Twilio creation recovery is temporarily unavailable: {exc}. "\n            "The original call remains VA-owned and blind redial remains blocked."\n        )[:4000]\n        await _set_objective_state(\n            db,\n            call,\n            "verifying",\n            reason="Provider call creation remains ambiguous",\n            error=call.failure_reason,\n        )\n        await write_audit(\n            db,\n            "telephony_call_creation_recovery_deferred",\n            entity_type="telephony_call",\n            entity_id=str(call.id),\n            result="blocked",\n            details={"retry_suppressed": True, "provider": "twilio"},\n        )\n        await db.commit()\n        return False\n\n    if len(candidates) != 1:\n        if candidates:\n            reason = (\n                f"Multiple Twilio calls match this ambiguous creation intent ({len(candidates)} candidates); "\n                "provider identity cannot be bound safely and blind redial remains blocked."\n            )\n            event_type = "provider_create_recovery_ambiguous"\n        else:\n            reason = (\n                "No unique Twilio call can yet prove this ambiguous creation intent; "\n                "blind redial remains blocked while VAAPP keeps the original intent."\n            )\n            event_type = "provider_create_recovery_pending"\n        call.status = "creation_uncertain"\n        call.failure_reason = reason[:4000]\n        await _set_objective_state(db, call, "verifying", reason=reason, error=reason)\n        await _record_evidence(\n            db,\n            call,\n            event_key=f"twilio-create-recovery:{call.id}:{len(candidates)}",\n            event_type=event_type,\n            signature_verified=False,\n            details={\n                "provider": "twilio",\n                "provider_api_authenticated": True,\n                "candidate_count": len(candidates),\n                "retry_suppressed": True,\n            },\n        )\n        await db.commit()\n        return False\n\n    candidate = candidates[0]\n    sid = str(candidate.get("sid") or "").strip()\n    await _bind_sid(db, call, sid)\n    call.failure_reason = ""\n    provider_status = str(candidate.get("status") or "").strip().lower()\n    if provider_status:\n        call.provider_status = provider_status\n    await _record_evidence(\n        db,\n        call,\n        event_key=f"twilio-create-recovered:{sid}",\n        event_type="provider_create_recovered",\n        provider_status=provider_status,\n        external_ref=sid,\n        signature_verified=False,\n        details={\n            "provider": "twilio",\n            "provider_api_authenticated": True,\n            "match_basis": ["exact_to", "exact_from", "outbound_api", "creation_time_window"],\n            "retry_suppressed": True,\n        },\n    )\n    await write_audit(\n        db,\n        "telephony_call_creation_recovered",\n        entity_type="telephony_call",\n        entity_id=str(call.id),\n        result="success",\n        details={"provider": "twilio", "external_ref": sid, "retry_suppressed": True},\n    )\n    if provider_status:\n        await _apply_provider_status(db, call, provider_status, candidate)\n    await db.commit()\n    return True\n'''
    replace_once(path, "\n\nasync def reconcile_call(db: AsyncSession, call: TelephonyCall) -> TelephonyCall:\n", helper + "\n\nasync def reconcile_call(db: AsyncSession, call: TelephonyCall) -> TelephonyCall:\n")

    replace_once(
        path,
        '''async def reconcile_call(db: AsyncSession, call: TelephonyCall) -> TelephonyCall:\n    if not call.external_call_sid:\n        if call.status == "creating" and call.updated_at < utcnow() - timedelta(minutes=15):\n            call.status = "creation_uncertain"\n            call.failure_reason = "Call creation was interrupted before a Twilio CallSid was recorded; blind retry is blocked."\n            await _set_objective_state(db, call, "verifying", reason=call.failure_reason)\n            await db.commit()\n        return call\n''',
        '''async def reconcile_call(db: AsyncSession, call: TelephonyCall) -> TelephonyCall:\n    if not call.external_call_sid:\n        if call.status == "creating" and call.updated_at < utcnow() - timedelta(minutes=15):\n            call.status = "creation_uncertain"\n            call.failure_reason = "Call creation was interrupted before a Twilio CallSid was recorded; blind retry is blocked."\n            await _set_objective_state(db, call, "verifying", reason=call.failure_reason)\n            await db.commit()\n        if call.status == "creation_uncertain":\n            await _recover_uncertain_call_creation(db, call)\n        return call\n''',
    )

    replace_once(
        path,
        '''async def _create_retry_call(db: AsyncSession, parent: TelephonyCall) -> TelephonyCall | None:\n    next_attempt = parent.attempt + 1\n    if next_attempt > parent.max_attempts or parent.needs_user or parent.verification_status == "verified":\n''',
        '''async def _create_retry_call(db: AsyncSession, parent: TelephonyCall) -> TelephonyCall | None:\n    next_attempt = parent.attempt + 1\n    if not parent.external_call_sid or parent.provider_status not in PROVIDER_TERMINAL:\n        # Never create a new provider side effect while the previous attempt might\n        # still exist or still be active. Keep the scheduled retry for later reconciliation.\n        return None\n    if next_attempt > parent.max_attempts or parent.needs_user or parent.verification_status == "verified":\n''',
    )

    replace_once(
        path,
        '''async def reconcile_telephony(db: AsyncSession) -> dict[str, int]:\n    result = {"reconciled": 0, "creation_uncertain": 0, "retries_started": 0}\n''',
        '''async def reconcile_telephony(db: AsyncSession) -> dict[str, int]:\n    result = {\n        "reconciled": 0,\n        "creation_uncertain": 0,\n        "creation_recovered": 0,\n        "creation_unresolved": 0,\n        "retries_started": 0,\n    }\n''',
    )

    replace_once(
        path,
        '''    if stale:\n        await db.commit()\n\n    active = list(\n''',
        '''    if stale:\n        await db.commit()\n\n    uncertain = list(\n        (\n            await db.execute(\n                select(TelephonyCall).where(\n                    TelephonyCall.direction == "outbound",\n                    TelephonyCall.status == "creation_uncertain",\n                    TelephonyCall.external_call_sid.is_(None),\n                ).order_by(TelephonyCall.id.asc()).limit(50)\n            )\n        ).scalars()\n    )\n    for call in uncertain:\n        recovered = await _recover_uncertain_call_creation(db, call)\n        if recovered:\n            result["creation_recovered"] += 1\n        else:\n            result["creation_unresolved"] += 1\n\n    active = list(\n''',
    )

    replace_once(
        path,
        '''                select(TelephonyCall).where(\n                    TelephonyCall.direction == "outbound",\n                    TelephonyCall.next_retry_at.is_not(None),\n                    TelephonyCall.next_retry_at <= utcnow(),\n                    TelephonyCall.attempt < TelephonyCall.max_attempts,\n                    TelephonyCall.needs_user.is_(False),\n                    TelephonyCall.verification_status != "verified",\n                ).order_by(TelephonyCall.next_retry_at.asc()).limit(20)\n''',
        '''                select(TelephonyCall).where(\n                    TelephonyCall.direction == "outbound",\n                    TelephonyCall.external_call_sid.is_not(None),\n                    TelephonyCall.provider_status.in_(PROVIDER_TERMINAL),\n                    TelephonyCall.next_retry_at.is_not(None),\n                    TelephonyCall.next_retry_at <= utcnow(),\n                    TelephonyCall.attempt < TelephonyCall.max_attempts,\n                    TelephonyCall.needs_user.is_(False),\n                    TelephonyCall.verification_status != "verified",\n                ).order_by(TelephonyCall.next_retry_at.asc()).limit(20)\n''',
    )


def write_new_files(root: Path) -> None:
    copy_prepared(
        root,
        "preview/backend/tests/test_v112_telephony_creation_recovery.py",
        "backend/tests/test_v112_telephony_creation_recovery.py",
    )
    copy_prepared(
        root,
        "preview/backend/tests/test_v112_telephony_creation_recovery_contract.py",
        "backend/tests/test_v112_telephony_creation_recovery_contract.py",
    )
    copy_prepared(
        root,
        "preview/docs/V1.0.12_TELEPHONY_CREATION_RECOVERY.md",
        "docs/V1.0.12_TELEPHONY_CREATION_RECOVERY.md",
    )


def patch_project_metadata(root: Path) -> None:
    status_path = root / "STATUS.md"
    if "# VAAPP v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression" not in read_text(status_path):
        raise RuntimeError("unexpected STATUS.md baseline")
    status = '''# VAAPP v1.0.12 — Telephony Creation Recovery & Retry Integrity\n\nUpdated: 2026-08-30\n\n## Source of truth\n\n- Repository: `Demon-blood/VAAPP2`\n- Branch: `main`\n- Verified v1.0.11 source baseline: `221205e82444f9c0bff2589cf3ffc015408e664a`\n- Verified v1.0.11 GitHub Actions run: `33331650005` — success\n- Verified v1.0.11 prerelease tag: `va-android-111-2-1`\n- v1.0.11 release identity: backend `1.0.11`, Android `1.0.11+54`\n\nThe operator subsequently reported production deployment and phone smoke testing complete for v1.0.11.\n\n## v1.0.12 maintenance scope\n\nv1.0.12 recovers ambiguous outbound Twilio call creation without ever converting uncertainty into a blind redial.\n\n- A lost Twilio create response remains `creation_uncertain` and VA-owned.\n- VAAPP queries the authenticated Twilio Calls resource for exact To/From provider evidence.\n- Twilio's day-level filters are narrowed locally by exact normalized numbers, `outbound-api` direction, and a ten-minute durable creation-time window.\n- A candidate CallSid already bound to another durable call intent is excluded.\n- Exactly one candidate is required before VAAPP binds a missing CallSid.\n- Zero candidates remain unresolved without a retry.\n- Multiple candidates remain unresolved without guessing.\n- Provider lookup failure remains a system-owned verification issue and creates no Needs You work.\n- A retry child can be created only after the previous call has a real CallSid and a terminal Twilio provider status.\n- Existing material payment/legal/medical/binding/authentication boundaries during the conversation are unchanged.\n- No database schema migration is required; recovery uses the existing TelephonyEvidence ledger.\n\n## Release identity\n\n- Backend: `1.0.12`\n- Required Android: `1.0.12`\n- Android: `1.0.12+55`\n\nThis status file is committed only by the guarded v1.0.12 installer after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and the signed release APK build have passed. GitHub prerelease publication remains a separate final workflow step and must be independently verified after the run.\n'''
    status_path.write_text(status, encoding="utf-8")

    state_path = root / "VAAPP_PROJECT_STATE.json"
    state = json.loads(read_text(state_path))
    if state.get("current_version") != "1.0.11":
        raise RuntimeError("unexpected VAAPP_PROJECT_STATE.json baseline")
    state.update(
        {
            "updated": "2026-08-30",
            "verified_baseline_commit": EXPECTED_BASELINE,
            "verified_baseline_version": "1.0.11",
            "verified_baseline_android_version": "1.0.11+54",
            "verified_maintenance_actions_run_id": 33331650005,
            "verified_baseline_release_tag": "va-android-111-2-1",
            "current_phase": "maintenance",
            "current_phase_name": "v1.0.12 Telephony Creation Recovery & Retry Integrity",
            "current_version": "1.0.12",
            "current_android_version": "1.0.12+55",
            "phase_status": "source commit is gated by full GitHub Actions validation before publication",
            "next_phase": "v1.x maintenance and real-world hardening",
            "v112_features": [
                "ambiguous outbound Twilio creation is reconciled against authenticated provider call history",
                "provider recovery requires exact To and From numbers plus outbound-api direction and a narrow creation-time match",
                "exactly one unbound provider CallSid is required before a durable call intent can be recovered",
                "zero or multiple provider candidates remain VA-owned without guessing or redialing",
                "provider lookup failure remains system-owned and creates no fake Needs You",
                "follow-up retries require a bound provider CallSid and terminal provider state",
                "existing material voice-conversation boundaries remain unchanged",
            ],
        }
    )
    invariants = list(state.get("invariants") or [])
    new_invariant = (
        "an outbound call with unknown provider creation outcome is never redialed before provider identity is resolved"
    )
    if new_invariant not in invariants:
        invariants.append(new_invariant)
    state["invariants"] = invariants
    # Preserve the original production v1.0 compatibility evidence exactly.
    if state.get("verified_baseline_actions_run") != 41:
        raise RuntimeError("original v1.0 verified baseline run must remain 41")
    if state.get("verified_baseline_actions_conclusion") != "success":
        raise RuntimeError("original v1.0 verified baseline conclusion must remain success")
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    handoff = root / "VAAPP_PROJECT_HANDOFF.md"
    old_prefix = '''# VAAPP project handoff\n\nUpdated: 2026-08-30\nRepository: `Demon-blood/VAAPP2`  \nBranch: `main`\n\n## Verified source of truth\n\nPhases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `4b3b38903545c8598695660c666c3080aff171e2` (`v1.0.10 — Payment Recovery & Human Boundary Integrity`). GitHub Actions run `33328116694` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-110-4-1`.\n\nVerified v1.0.10 release identity: backend `1.0.10` / Android `1.0.10+53`. The operator subsequently reported production deployment and phone smoke testing complete.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current maintenance candidate\n\nBackend `1.0.11` / Android `1.0.11+54`.\n\nCurrent candidate: **v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression**.\n\nv1.0.11 closes a duplicate-execution boundary in browser-backed Fulfillment. If a non-replay-safe provider action may already have happened but its postcondition is not yet visible, VAAPP retains the original action and browser operation, marks the outcome `creation_uncertain`, and performs verification-only revisits. Security boundaries, provider timeouts, and runtime errors during those revisits preserve the uncertainty state rather than reopening replay. It never creates a replacement business action merely because confirmation was delayed. Provider/system ambiguity remains VA-owned; genuine portal authentication remains a separate human boundary.\n\nThe guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.\n\nNext work after the v1.0.11 gate is green: **v1.x maintenance and real-world hardening**.\n\n## Product objective\n'''
    new_prefix = '''# VAAPP project handoff\n\nUpdated: 2026-08-30\nRepository: `Demon-blood/VAAPP2`  \nBranch: `main`\n\n## Verified source of truth\n\nPhases 1–10 and production v1.0 are complete. The verified maintenance baseline for this release is commit `221205e82444f9c0bff2589cf3ffc015408e664a` (`v1.0.11 — Fulfillment Side-Effect Recovery & Duplicate Suppression`). GitHub Actions run `33331650005` completed successfully end-to-end, including backend tests, Ruff gates, Flutter analysis/tests, persistent signing, signed Android APK build, source verification, and prerelease publication under tag `va-android-111-2-1`.\n\nVerified v1.0.11 release identity: backend `1.0.11` / Android `1.0.11+54`. The operator subsequently reported production deployment and phone smoke testing complete.\n\nOriginal production v1.0 baseline remains commit `66c09040326ac553a1402cd06fa6771344195d45`; GitHub Actions run #41 completed successfully.\n\n## Current maintenance candidate\n\nBackend `1.0.12` / Android `1.0.12+55`.\n\nCurrent candidate: **v1.0.12 — Telephony Creation Recovery & Retry Integrity**.\n\nv1.0.12 closes the remaining ambiguity gap in outbound Twilio call creation. If the irreversible provider create request may have succeeded but VAAPP missed both the response and the normal CallSid callback, VAAPP searches authenticated Twilio call history using exact source/destination numbers and a narrow local creation-time match. Exactly one unbound `outbound-api` candidate is required to recover the existing call intent. Zero, multiple, paginated, or unavailable provider evidence remains VA-owned and cannot trigger a redial. Follow-up retry is permitted only after the previous attempt has a real CallSid and a terminal provider state. Genuine material voice-conversation decisions and authentication remain separate human boundaries.\n\nThe guarded installer commits this candidate only after backend tests, Ruff gates, Flutter analysis/tests, Android signing checks, and a signed release APK build pass. Prerelease publication remains separately verifiable after the source commit.\n\nNext work after the v1.0.12 gate is green: **v1.x maintenance and real-world hardening**.\n\n## Product objective\n'''
    replace_once(handoff, old_prefix, new_prefix)


def bump_versions(root: Path) -> None:
    replace_once(
        root / "backend/app/core/version.py",
        'APP_VERSION = "1.0.11"\nREQUIRED_ANDROID_VERSION = "1.0.11"\n',
        'APP_VERSION = "1.0.12"\nREQUIRED_ANDROID_VERSION = "1.0.12"\n',
    )
    replace_once(root / "backend/pyproject.toml", 'version = "1.0.11"', 'version = "1.0.12"')
    replace_once(root / "android/pubspec.yaml", "version: 1.0.11+54", "version: 1.0.12+55")
    replace_once(
        root / "android/lib/release_contract.dart",
        "const String appRelease = '1.0.11';\nconst String minimumBackendVersion = '1.0.11';\n",
        "const String appRelease = '1.0.12';\nconst String minimumBackendVersion = '1.0.12';\n",
    )

    replacements = (
        ('APP_VERSION = "1.0.11"', 'APP_VERSION = "1.0.12"'),
        ('REQUIRED_ANDROID_VERSION = "1.0.11"', 'REQUIRED_ANDROID_VERSION = "1.0.12"'),
        ('version = "1.0.11"', 'version = "1.0.12"'),
        ('version: 1.0.11+54', 'version: 1.0.12+55'),
        ("appRelease = '1.0.11'", "appRelease = '1.0.12'"),
        ("minimumBackendVersion = '1.0.11'", "minimumBackendVersion = '1.0.12'"),
        ('APP_VERSION == "1.0.11"', 'APP_VERSION == "1.0.12"'),
    )
    updated = 0
    for test_path in sorted((root / "backend/tests").glob("test_*.py")):
        if test_path.name.startswith("test_v112_"):
            continue
        text = read_text(test_path)
        new_text = text
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            test_path.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated < 1:
        raise RuntimeError("expected at least one living release contract to advance to v1.0.12")


def verify_diff(root: Path) -> None:
    run_git(root, "diff", "--check")
    tracked = [line for line in run_git(root, "diff", "--name-only").splitlines() if line]
    untracked = [
        line
        for line in run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
        if line
    ]
    changed = sorted(set(tracked + untracked))
    if not changed:
        raise RuntimeError("patch produced no changes")
    forbidden = [name for name in changed if name.startswith(".github/workflows/")]
    if forbidden:
        raise RuntimeError(f"workflow files changed unexpectedly: {forbidden}")
    required = {
        "backend/app/services/telephony_service.py",
        "backend/tests/test_v112_telephony_creation_recovery.py",
        "backend/tests/test_v112_telephony_creation_recovery_contract.py",
        "docs/V1.0.12_TELEPHONY_CREATION_RECOVERY.md",
        "backend/app/core/version.py",
        "backend/pyproject.toml",
        "android/pubspec.yaml",
        "android/lib/release_contract.dart",
        "STATUS.md",
        "VAAPP_PROJECT_STATE.json",
        "VAAPP_PROJECT_HANDOFF.md",
    }
    missing = sorted(required.difference(changed))
    if missing:
        raise RuntimeError(f"required v1.0.12 changes missing from diff: {missing}")

    service = read_text(root / "backend/app/services/telephony_service.py")
    for marker in (
        "async def _twilio_creation_candidates",
        "async def _recover_uncertain_call_creation",
        'str(raw.get("direction") or "").strip().lower() != "outbound-api"',
        "abs((observed_at - reference).total_seconds()) > 10 * 60",
        "blind redial remains blocked",
        "if not parent.external_call_sid or parent.provider_status not in PROVIDER_TERMINAL:",
        "TelephonyCall.provider_status.in_(PROVIDER_TERMINAL)",
    ):
        if marker not in service:
            raise RuntimeError(f"v1.0.12 telephony marker missing: {marker}")
    if 'APP_VERSION = "1.0.12"' not in read_text(root / "backend/app/core/version.py"):
        raise RuntimeError("v1.0.12 backend version missing")
    if "version: 1.0.12+55" not in read_text(root / "android/pubspec.yaml"):
        raise RuntimeError("v1.0.12 Android version missing")
    state = read_text(root / "VAAPP_PROJECT_STATE.json")
    handoff = read_text(root / "VAAPP_PROJECT_HANDOFF.md")
    if '"verified_baseline_actions_run": 41' not in state:
        raise RuntimeError("original production v1 baseline run was not preserved")
    if '"verified_baseline_actions_conclusion": "success"' not in state:
        raise RuntimeError("original production v1 baseline conclusion was not preserved")
    if "GitHub Actions run #41" not in handoff:
        raise RuntimeError("original production v1 handoff evidence was not preserved")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    verify_bundle()
    verify_repo(root)
    patch_telephony_service(root)
    write_new_files(root)
    patch_project_metadata(root)
    bump_versions(root)
    verify_diff(root)
    print("v1.0.12 source patch prepared. Changed files:")
    tracked = run_git(root, "diff", "--name-only").splitlines()
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    for name in sorted(set(tracked + untracked)):
        if name:
            print(f"  {name}")


if __name__ == "__main__":
    main()
