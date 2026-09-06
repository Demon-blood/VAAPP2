# VAAPP v1.0.22 briefing notification UX patch

Validated source baseline: `1111973780e3a8f3f6028da26f2003ece5d4ac36`

This bundle fixes the Android briefing presentation defect without changing briefing generation,
durable delivery acknowledgement, retry semantics, or backend ownership.

## Apply

From the VAAPP2 repository root:

```powershell
python .\apply_v122_briefing_notification_ux.py --repo .
```

If your branch has legitimately advanced beyond the validated baseline, inspect the intervening
changes first, then use `--allow-head-mismatch`. The applicator still fails closed if its exact
source contracts no longer match.

## Validate

```powershell
cd backend
pytest -q tests/test_v122_briefing_notification_contract.py
cd ..\android
flutter analyze
flutter test
```

The connected ChatGPT GitHub installation returned HTTP 403 for Contents/ref writes, so this
bundle is prepared for direct application in a writable checkout.
