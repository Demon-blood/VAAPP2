# v1.0.5 repair for failed partial upload

This repair is **only** for repository `Demon-blood/VAAPP2` at commit:

`3bf8794c21ac30f61e1ccf84e452fc65785187a4`

GitHub Actions run #46 failed because the previous upload committed only the additive v1.0.5 files and test contract. The original apply script was never executed, so existing v1.0.4 files remained unchanged.

## Apply

1. Start from a clean checkout of `main` at exactly `3bf8794c21ac30f61e1ccf84e452fc65785187a4`.
2. Extract this ZIP into the repository root.
3. Run:

   `python repair_v105_after_partial_upload.py`

4. Review `git status` and `git diff`. The repair intentionally deletes the accidentally committed `apply_v105_human_boundary.py` and `V105_DELTA_README.md`.
5. Validate locally if available:

   `python -m compileall -q backend/app backend/tests`

   `ruff check backend/app backend/tests --select E9,F63,F7,F82`

   `pytest -q backend/tests`

   From `android/`: `flutter analyze --no-fatal-infos` and `flutter test`.

6. Commit the repair, for example:

   `git add -A`

   `git commit -m "v1.0.5 — complete Human Boundary & Relationship-Aware Communications"`

   `git push origin main`

Do not rerun the old base-locked `apply_v105_human_boundary.py`; it correctly refuses to run after `main` advanced. GitHub Actions is the authoritative release gate.
