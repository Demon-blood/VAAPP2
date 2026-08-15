# VAAPP v1.0.5 base-locked delta

Baseline required: `af339ac48e600b38bebababbb048e77464be3900` (`v1.0.4 — Execution Readiness & Setup Assistant`), GitHub Actions run #45 successful.

This ZIP is intentionally a **delta**, because the connected ChatGPT GitHub account has read permission but no push permission. The apply script refuses to stack v1.0.5 on any other Git HEAD.

## Apply

1. In your local `Demon-blood/VAAPP2` checkout, confirm `git status` is clean and `git rev-parse HEAD` is the verified v1.0.4 commit above.
2. Extract this ZIP **over the repository root**.
3. Run:

```powershell
python .\apply_v105_human_boundary.py
```

The script checks the exact baseline, applies anchored changes, updates release metadata, and regenerates `FILE_MANIFEST.txt` / `SHA256SUMS.txt`. It locally excludes the two delta helper files from `git add .`.

## Local validation before push

From the repository root:

```powershell
python -m compileall -q backend\app backend\tests
python -m pytest -q backend\tests\test_v105_human_boundary_relationship_contract.py
python -m pytest -q backend\tests
```

If your local Python environment does not have the backend development dependencies installed, use the existing backend venv/development setup. GitHub Actions remains authoritative for the complete backend + Flutter + signed Android release gate.

Also inspect:

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

Expected candidate identity:

- backend `1.0.5`
- Android `1.0.5+47`
- APK identity `Full-Time-VA-Android-v1.0.5.apk`
- verified baseline remains v1.0.4 / run #45 until the new CI run is green

Suggested commit message:

```text
v1.0.5 — Human Boundary & Relationship-Aware Communications
```

## Release acceptance checks after GitHub CI is green

1. Old duplicate 8999 task/objective projections are superseded rather than merely hidden. If that protected provider notice has no concrete executable proposal, it remains VA-owned as `blocked_capability` instead of asking for a meaningless authorization.
2. A concrete material decision card shows the exact proposal plus **Authorize** and **Decline**. A provider SCA/MFA item instead shows **Open provider authorization** when a safe URL exists plus **Recheck after authorization**; it never uses a fake local approval.
3. Authorizing a saved Gmail reply resumes the durable Gmail executor; authorizing an SMS reply creates a durable pending device action. Neither authorization is completion evidence.
4. Declining cancels the exact proposal while retaining the source message/audit trail.
5. Work → Relationships → person → **Edit reply preferences** persists explicit preferences and applies them to future Gmail, SMS, WhatsApp, Signal, Telegram, Messenger and supported Messages-notification reply drafting. Notification-only display names must be explicitly linked to the relationship; the VA never merges people by name automatically.
6. Opting into **Learn how I write to this person** builds a bounded style profile from device-observed Android SMS sent history after excluding known VA-generated sends. It requires at least 3 safe samples, excludes successful VA-generated SMS replies and sensitive samples, and never learns from incoming messages.
7. Learned style is relationship-level and can inform Gmail/SMS/WhatsApp/Signal/Telegram/Messenger drafting after identity resolution; notification-only apps do not claim historical sent-message access. Explicit instructions/examples override learned style.
8. Relationship `routine_auto_send=true` never bypasses protected/financial/legal/security/material safety gates. Approval topics and `routine_auto_send=false` only make policy stricter.
9. Native SMS plus a mirrored Google/Samsung Messages notification produces one owned decision; two genuine same-channel identical SMS messages are not content-deduplicated.

Do not call v1.0.5 complete until the new GitHub Actions workflow has succeeded end-to-end and the prerelease APK is published.
