# Portal Document Sync

Portal Document Sync is VAAPP's generic path for retrieving durable documents from authenticated HTTPS inboxes. It is intentionally provider-neutral: Doccle is the first starter preset, not a hard-coded scraper.

## Architecture and ownership

A `BrowserPortal` defines where VAAPP may authenticate and operate: HTTPS URLs, explicit hosts, encrypted credentials, and encrypted browser session state. A separate `PortalDocumentSource` defines which document listing to inspect, how to identify stable provider items, how to download them, and how often to sync. Existing Portals do not become document sources automatically.

```text
Portal → Portal Document Source → candidate discovery → Portal Document Item
       → authenticated download → generic document ingestion → exact SHA-256 dedupe
       → Google Drive / DocumentRecord → Document Intelligence and Ownership
       → archive, Bill, obligation, form plan, deadline, or other evidence-backed outcome
```

Fulfillment is not part of retrieval. It may later execute a real-world cancellation, return, refund, purchasing, travel, logistics, or customer-service objective caused by a document.

## Durable data and provenance

- `portal_document_sources` stores the linked portal, validated recipe, scope, interval, limits, readiness, safe result/error information, challenge state, and sync timestamps.
- `portal_document_items` stores the stable external ID, encrypted download reference, safe provider metadata, lifecycle, checksum, attempts, and resulting document ID. `(source, external_id)` is unique.
- `document_source_references` records every source route to a `DocumentRecord`. If identical bytes arrive via Portal and Gmail in the same ownership scope, VAAPP uploads one Drive file and attaches both references.

Exact SHA-256 equality is the only file-level cross-source dedupe rule. Financial duplicate safeguards additionally use extracted amount/invoice evidence. No fuzzy title match collapses files.

## Declarative recipe

Recipes are validated Pydantic data, never Python or JavaScript. Common fields cover the HTTPS listing URL, item and external-ID selectors, optional title/provider/date/detail selectors, direct-link/browser-download/document-response strategy, link/download selector, MIME types, pagination, and source-level page/document caps.

Malformed recipes, HTTP URLs, and URLs outside the linked Portal allowlist fail closed. A detail-page click strategy requires a download selector. Stable provider IDs are preferred. Advanced recipes may explicitly enable a deterministic fallback derived from provider/title/date and the query-free document path; that is less robust when providers rename or move documents and is marked as derived evidence in discovery metadata.

## Security and authentication

Every listing, detail, download, and redirect must remain HTTPS and inside the linked Portal's explicit host allowlist. A legitimate CDN must be added explicitly. Literal private/local targets and hosts resolving to private/local addresses are rejected at runtime. MIME and size are checked; the coordinated limit remains 12 MB. Browser-event temporary files are removed deterministically and filenames are sanitized.

Credentials, cookies, session state, signed query strings, and document contents are not returned by APIs or placed in audits/workflow results. Download references are encrypted. Audits use only safe names, hashes, sizes, counts, and IDs.

Authenticated sessions are reused and expired sessions use the existing encrypted credential login. OTP/MFA and CAPTCHA are genuine `needs_user_auth` states; VAAPP does not bypass them and correlates them to the source. A broken recipe is `degraded`, not a fake user decision.

## Ingestion and downstream behavior

`document_ingestion.ingest_document_bytes` is shared by Gmail and Portal downloads. It enforces filename, MIME, size, retention, categorization, Drive upload, provenance, audit, and Document Intelligence behavior. Portal financial documents use the existing financial policy concepts. A payable invoice only becomes a Bill with deterministic amount and creditor evidence; an unknown creditor/IBAN remains `requires_review`, and payment/account policies are unchanged.

Stored does not mean paid, discovered does not mean downloaded, and a form found does not mean submitted. Existing Document Ownership verification remains authoritative.

## Test, sync, scheduler, and status

`Test` authenticates and returns bounded safe candidate metadata without downloading or ingesting history. `Sync now` enqueues `portal_documents.sync`, the same durable job used by the scheduler. Each due source receives a source/time-bucket idempotency key; the item ledger prevents redownloading ingested external IDs.

- `OFFLINE`: no compatible enabled source/Portal or Drive storage is unavailable.
- `READY`: source, recipe, Portal, and Drive are configured but no successful provider sync is observed.
- `LIVE`: at least one real source sync succeeded.
- `degraded`: a previously working recipe no longer produces expected evidence, or bounded item failures occurred.
- `needs_user_auth`: the provider presented a real authentication boundary.

## Configure and troubleshoot a provider

1. Add a Secure Portal with provider, authentication, and legitimate CDN hosts explicitly allowlisted.
2. Store credentials only when required and configure the Portal login recipe if automatic login needs selectors.
3. In Work → Documents, add a source, select Portal/scope, and fill structured recipe, interval, and limits.
4. Run Test and inspect titles/provider/dates only, then run Sync now.
5. Confirm Documents provenance and downstream ownership before enabling scheduled sync.

For drift, reverify the authenticated DOM selectors. For authentication, resolve the source-level challenge. For storage errors, reconnect Google Drive. Do not broaden the allowlist just to hide an error.

## Doccle example and status

```text
Doccle Portal
  → Doccle Document Source
  → discover unseen documents
  → download PDF
  → generic document ingestion
  → Document Intelligence
  → invoice / obligation / protected archive classification
```

The Doccle starter is deliberately `production_ready: false`. It suggests an HTTPS entry point, PDF expectation, and strategy, but contains no invented DOM selectors. A real authenticated Doccle account is required to verify selectors before Doccle can be considered LIVE.
