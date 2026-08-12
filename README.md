# Full-Time VA v0.7.2 - Revolut Dual-Format Financial History

v0.7.2 extends the verified historical-finance importer with deterministic Revolut XLSX/PDF support while preserving Enable Banking as the ongoing live banking feed.

## Highlights

- Revolut XLSX imports are parsed without OCR and without adding a spreadsheet runtime dependency.
- `COMPLETED` rows are authoritative; `REVERTED` rows are ignored.
- Revolut cash movement is computed as `Amount - Fee`, which correctly handles plan fees, transfer fees, and fee refunds.
- A paired Revolut PDF enriches the XLSX ledger with transaction IDs, counterparties, IBAN/reference information, card metadata, FX detail, and additional account sections.
- XLSX and PDF for the same Revolut primary account resolve to one canonical statement and cannot double-count the ledger.
- Beobank -> Revolut top-ups can be confirmed as own-account transfers by matching both imported histories, even before a live Revolut connection supplies enough counterparty metadata.
- Confirmed historical internal transfers propagate to their matched Enable Banking rows so deduplication cannot accidentally turn a top-up into income.
- Revolut FX-pocket movements and Robo portfolio transfers are excluded from consumption.
- Refunds reverse learned spending instead of becoming ordinary income.
- The Money -> Budget importer accepts `.pdf` and `.xlsx` files.
- Existing Beobank parsing, document retention cleanup, budgeting, communications, and guarded money movement remain intact.

**Release identity:** backend `0.7.2`, Android `0.7.2+30`, APK `Full-Time-VA-Android-v0.7.2.apk`.

See `docs/V0.7.2_REVOLUT_DUAL_FORMAT.md` and `docs/V0.7.2_VALIDATION.md`.
