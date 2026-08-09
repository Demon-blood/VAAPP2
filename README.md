# Full-Time VA Android v0.4.17

## v0.4.17 Bills rendering hotfix

- Fixes the release-mode grey Bills tab when FastAPI/Pydantic serializes a Decimal bill amount as a JSON string such as `"89.99"`.
- Uses tolerant numeric parsing instead of a direct Dart `as num?` cast when calculating the outstanding total.
- Adds regression coverage for string, numeric, and European-formatted decimal values.
- Adds a branded render-error fallback so a future widget exception cannot degrade into an unexplained solid grey screen.
- Android `0.4.17+21` remains compatible with backend `0.4.16+`; no backend migration is required for this hotfix.

## v0.4.16 smart document retention + premium UI

- Filters generic Terms of Service, privacy/cookie policies, legal boilerplate, unsubscribe pages, calendar/vCard attachments, signatures, logos and tracking/branding assets before they can enter the durable document archive.
- Keeps actual invoices, receipts, statements, contracts, signed agreements, tax/legal/medical records and scanned receipts even when their filenames contain generic words such as “terms”.
- Cleans legacy low-value VA-managed files from both the document index and Google Drive at backend startup; **Run VA now** and **Clean document archive** retry cleanup later if Google was temporarily unavailable.
- `/api/documents` hides filtered boilerplate immediately so the Work > Documents screen never presents it as a retained document.
- Fixes the Money/Bills navigation state so stale `Not Found` errors are cleared, money data is refreshed on entry, and a failed bills endpoint is shown as an explicit load error instead of a false “No detected bills” state.
- Recreates the reference design as native Flutter UI: darker glass-like surfaces, purple/blue gradients, compact status cards, search/filter chips, richer Money/Work layouts and a runtime-drawn assistant mascot. No UI asset is cropped from the reference image.
- Adds searchable/filterable Documents and Inbox views while preserving real action execution, bank authorization and automation controls.

## v0.4.16 action centre + visual redesign

- Replaces the passive Today counters with tappable action cards for **Emails needing action**, **Unpaid bills**, **Open tasks**, and **Payment approvals**.
- Adds **Run VA now**, which executes the same safe Gmail, bank, auto-pay, payment-status, contacts, connector-rule, and reconciliation stages as the background scheduler.
- Reconciles older `action_required` email flags at backend startup and after Gmail/task/creditor/support changes so a counter can no longer exist without a concrete unresolved workflow behind it.
- Automatically clears email action flags after successful calendar creation, approved replies, validated bills, completed follow-up tasks, and resolved support cases.
- Creates explicit review tasks for unresolved replies, calendar events, creditor/IBAN reviews, support follow-ups, and generic actions that cannot be executed safely without confirmation.
- Approved reply tasks can now send the stored reply from the Tasks screen; approved calendar-review tasks can create the stored Google Calendar event.
- Bills include a **Run eligible auto-pay now** action. Payment approvals are sorted first and expose the real bank authorization URL when SCA is required.
- The dashboard card for each domain navigates directly to its actionable screen instead of acting as a read-only statistic.
- Adds a cohesive dark navy/purple visual system, redesigned action cards, richer status surfaces, a branded onboarding experience, and a new Full-Time VA launcher icon.
- The Android build workflow installs the persistent branded launcher icon after `flutter create`, so cloud builds no longer fall back to Flutter's default icon.

## v0.4.16 invoice amount extraction fix

- Prevents local EUR amount extraction from consuming newline characters or crossing line boundaries.
- Normalizes horizontal whitespace, including non-breaking spaces commonly used in European invoices.
- Adds regression coverage for newline-safe amount candidates before bill parsing and AI fallback.

## v0.4.16 free-tier AI optimization

- Uses deterministic Gmail signals and learned low-risk sender rules before calling the AI provider.
- Reuses cached decisions for identical message fingerprints so the same content is never charged twice.
- Strips quoted thread history and common signatures before AI submission.
- Extracts invoice hints (IBAN, amount, invoice number, structured reference and due-date candidates) locally before AI.
- Sends a compact email/attachment payload instead of whole 80k-character messages and PDF text.
- Uses Groq strict JSON Schema output automatically for `openai/gpt-oss-20b`/`120b`, with low reasoning effort.
- Tracks daily AI requests/tokens locally and reads Groq's rate-limit response headers.
- Defaults to a 1,000 request / 200,000 token daily budget, reserving 100 requests and 25,000 tokens for urgent mail.
- Limits older-message AI processing to 50 requests/day by default.
- Retries short 429 rate limits once, then safely defers AI-dependent work rather than failing the whole VA.
- Supports an optional OpenAI-compatible fallback provider; sensitive mail is excluded from fallback by default.
- When AI is unavailable, the deterministic safety layer can still classify routine mail, preserve protected mail, extract obvious invoice data, and keep the message queued for later AI completion.
- The Android AI card shows today's requests, tokens, rule shortcuts, fingerprint hits and deferred messages.

Recommended free primary configuration: `https://api.groq.com/openai/v1` + `openai/gpt-oss-20b`.


## v0.4.16 Open Banking authorization fix

- Resolves Beobank/Revolut against the exact Belgian ASPSP name returned by Enable Banking instead of assuming the display label is the API identifier.
- Sends `access.valid_until` as an RFC3339 timestamp and caps it to each ASPSP's `maximum_consent_validity`.
- Explicitly requests balances and transactions in the AIS consent.
- Uses `GET /sessions/{session_id}` plus `GET /accounts/{account_id}/details` after authorization, matching the current Enable Banking API instead of the obsolete/nonexistent account-list request.
- Reads session validity from `access.valid_until`.
- Android now shows the exact bank-start failure in a Snackbar and verifies that the returned authorization URL can actually be opened.
- Payment initiation also resolves the provider's exact current PIS ASPSP name.

## v0.4.16 CI regression-test fix

The GitHub-notification health isolation code was already correct, but its regression test required the Dart `if (!optional)` assignment to appear on one physical line. The implementation uses a normal braced block, so CI failed even though the behavior was correct. The test now checks the logic independent of whitespace/bracing.

## v0.4.16 GitHub notification health isolation

GitHub personal notifications are optional. The Android client no longer records `/api/github/notifications` failures as VA server-health failures, and the backend route now fails soft to an empty list for permission, rate-limit, transport, timeout, and provider-side errors. Repository, Actions, releases, issues, and persistent Android-signing automation are unaffected.

## v0.4.16 persistent Android update signing

Previous GitHub-hosted builds used Flutter's generated debug signing configuration. GitHub-hosted runners are ephemeral, so a different debug keystore can be created on different runs. Android rejects an APK update when its signing certificate differs from the installed APK.

Version 0.4.16 removes debug signing from release builds. Release APKs require one persistent PKCS#12 signing key. The VA backend can generate the key, keep an encrypted copy in PostgreSQL, and install the four required values as GitHub Actions repository secrets. The GitHub token must have repository **Secrets: Read and write** permission.

Phone-only bootstrap page after backend 0.4.16 is deployed:

`https://<your-va-server>/setup/android-signing`

The first move from an older temporary-signed APK to 0.4.16 still requires one uninstall because the previous signing private key is not recoverable from an ephemeral GitHub runner. After the first stable-signed 0.4.16 APK is installed, later APKs can update it normally as long as the signing key is never rotated and versionCode keeps increasing. Backend data, Google OAuth, banking connections, and automation settings remain on the server and are not deleted by uninstalling the Android client.

## v0.4.16 pairing repair fix

- Fixes `Invalid pairing secret` after repairing an existing Render backend.
- The phone now waits for the exact Render deploy triggered by the repair to reach `live` before it attempts pairing.
- This avoids pairing against Render's previous zero-downtime instance, which can still answer `/health` while a new instance with the rotated `PAIRING_SECRET` is building.
- Pairing retries briefly after cutover to cover propagation delays.
- Existing PostgreSQL data, OAuth tokens, connector settings, and `TOKEN_ENCRYPTION_KEY` remain preserved during repair.


## v0.4.16 resilient GitHub build

- The main Android workflow contains no `uses:` actions, so it does not depend on GitHub's action-download metadata phase.
- It checks out the repository with Git, installs Flutter directly from the official Flutter repository, tests the backend, runs Flutter analysis/tests, builds the release APK, and publishes the APK as a GitHub prerelease.
- A GitHub-hosted runner still has to start; a full GitHub Actions runner outage cannot be bypassed from inside a workflow.


## v0.4.16 Flutter analyzer cleanup

- Replaced the final conditional map entry with Dart null-aware map syntax (`'category': ?category`) so `flutter analyze` completes without the `use_null_aware_elements` finding.
- Retains the previous per-endpoint refresh isolation, local connector catalog, custom-connector dialogs, and phone-based Render repair flow.

- Refresh no longer fails all tabs because one optional endpoint returns 404.
- Every backend endpoint is loaded independently and diagnostics identify the exact missing route.
- The connector templates and 36-service catalog are bundled in the APK, so **Add custom** and **Choose service** open even before the server catalog loads.
- A public `/api/system/info` endpoint verifies the deployed backend version.
- The app can repair an existing Render service from the phone: it updates the repository/root directory, preserves the database and encryption key, rotates the pairing secret, clears the build cache, redeploys, verifies backend 0.4.16, and pairs again.
- The deployment wizard reuses an existing service and database instead of creating duplicates.


## v0.4.3 Render provisioning fix

- Uses Render's current native-runtime service schema by nesting `buildCommand` and `startCommand` under `serviceDetails.envSpecificDetails`.
- Reuses an already-created PostgreSQL database with the same service-derived name, so retrying after a failed service request does not create duplicate databases.
- Keeps the phone-only provision, deploy, verify, and pair flow intact.

Full-Time VA is an Android-first personal operations system. The phone is the control surface; a private cloud backend performs continuous work when the phone is closed, sleeping, or offline.

## v0.4.3 analyzer compatibility fix

- Migrated `flutter_local_notifications` calls to the named-parameter API used by version 22.2.0.
- Migrated deprecated `DropdownButtonFormField.value` uses to `initialValue`.
- Cleared the reported Dart 3.10 collection, wildcard-variable, and redundant-cast lints.
- Keeps the GitHub Actions build pinned to Flutter 3.38.7 for reproducible builds.

The project contains no sample inbox, invented bank account, simulated balance, fake invoice, pretend connector, or fabricated success result. A service remains **Not configured**, **Configured**, or **Error** until its real provider connection passes a live test. A payment remains pending until the banking provider reports its actual status.

## What the VA performs

### Communications and scheduling

- Processes Gmail in Dutch and English.
- Retrieves message bodies and attachments, including PDF text.
- Applies protected email handling for legal, government, debt-collection, financial, security, family, and medical correspondence.
- Labels, archives, follows up, and performs guarded deletion under explicit rules.
- Creates tasks and calendar events from sufficiently certain commitments and deadlines.
- Sends routine replies only when an enabled automation rule authorizes sending.
- Synchronizes Google contacts and archives documents to Google Drive.

### Financial administration

- Extracts invoices, creditors, IBANs, amounts, references, and due dates.
- Detects probable duplicates and previously initiated payments.
- Separates Beobank, Revolut Personal, and Revolut Pro account roles.
- Enforces exact-IBAN creditor approval, creditor amount limits, permitted funding accounts, and minimum account reserves.
- Initiates eligible payments through Enable Banking when production payment initiation is available.
- Opens the real bank authorization flow when Beobank or Revolut requires Strong Customer Authentication.
- Reconciles actual payment status and records every financial action in the audit log.

### Ongoing VA work

- Tracks tasks, documents, orders, deliveries, subscriptions, support cases, and follow-ups.
- Runs scheduled connector operations.
- Administers GitHub workflows, issues, repositories, and Android cloud builds.
- Reads Cloudflare resource inventory and sends Discord operational notifications.
- Produces Android notifications only for meaningful exceptions and required intervention.

## Phone-only operation

After the first APK has been installed, the following can be done from inside the Android app:

- Verify a Render API key and load its available workspaces.
- Select testing or always-on hosting.
- Provision PostgreSQL automatically.
- Create and verify the FastAPI backend.
- Pair the phone with the new backend.
- Enter, replace, and live-test Google, AI, Open Banking, GitHub, Cloudflare, and Discord credentials.
- Generate the Enable Banking 4096-bit private key on the backend and copy only its public certificate from Android.
- Copy exact Google, banking, and generic OAuth callback URLs from the app.
- Connect Google, Beobank, Revolut Personal, and Revolut Pro through their official authorization pages.
- Add, configure, authorize, test, execute, schedule, disable, or remove third-party connectors.
- Trigger future Android builds through GitHub Actions and inspect their runs from the phone.

The only unavoidable bootstrap is obtaining and installing the first APK. An application cannot build or install itself before it exists. The included GitHub Actions workflow performs that first Flutter build in the cloud, and the resulting APK can be downloaded and installed using the phone.

## In-app service catalog

The catalog currently contains 36 guided presets:

- Microsoft 365 / Outlook / OneDrive
- Dropbox
- Slack
- Notion
- Todoist
- Trello
- Airtable
- HubSpot
- Calendly
- Zoom
- LinkedIn
- Facebook / Instagram Graph
- WhatsApp Cloud API
- Telegram Bot
- Stripe
- Mollie
- PayPal
- Shopify
- WooCommerce
- Twilio
- Pushover
- Home Assistant
- Nextcloud / ownCloud
- Asana
- ClickUp
- monday.com
- GitLab
- Google Sheets
- Google Tasks
- SendGrid
- Brevo
- Zapier webhook
- Make webhook
- n8n webhook
- Pipedream HTTP workflow
- Browserless website automation

Built-in connectors are also provided for Google, GitHub, Cloudflare, Discord, and Enable Banking.

## Universal connection methods

A provider does not need a hard-coded screen to be usable. The app can create and test these connector types from the phone:

- OAuth 2.0 authorization-code flow
- OAuth 2.0 PKCE using S256
- OAuth 2.0 client credentials
- REST or GraphQL over HTTP
- Raw XML/SOAP requests
- Incoming service webhooks
- IMAP and SMTP
- WebDAV
- SFTP
- RSS and Atom
- Telegram Bot API
- Browserless content, Puppeteer-function, and BrowserQL workflows

This covers services that expose an API, OAuth application, webhook, standard mail/file protocol, feed, or permitted browser workflow. A provider that offers none of these mechanisms—or whose terms prohibit automation—cannot be truthfully automated. Such a service remains unavailable rather than being represented by a fake button.

## First build from the phone

1. Put this project in a private GitHub repository while using the phone.
2. Open **Actions → Build Android APK → Run workflow**.
3. Open the completed workflow run.
4. Open the run summary or **Releases**, then download `Full-Time-VA-Android-v0.4.16.apk`.
5. Extract and install `app-release.apk`.
6. Confirm Android's installation prompt.

See `docs/PHONE_ONLY_SETUP.md` for the complete phone workflow.

## Backend deployment from the app

The onboarding screen supports two modes:

- **Always-on VA:** creates a paid Render web service and persistent PostgreSQL database. A confirmation dialog appears before any paid resources are requested.
- **Testing only:** creates free resources. Free hosting can sleep, and free database availability or retention is not suitable for dependable full-time operation.

The app generates the pairing secret and encryption key locally, passes them to Render as environment variables, polls the real health endpoint, and pairs only after the backend responds successfully. The Render API key is held only for the deployment operation and is not saved by the app.

## External provider authorization

Setup is initiated from buttons inside the app, but the provider still controls these mandatory steps:

- Creating a developer application or API credential.
- Accepting provider terms and permissions.
- KYC or business verification.
- Google OAuth consent.
- Open Banking consent.
- Beobank, Revolut, or Itsme authentication.
- Android confirmation before installing an APK.

Passwords, bank PINs, Itsme credentials, recovery codes, and one-time authentication codes must never be entered into the VA app.

## Financial execution policy

Automatic payment is rejected unless all applicable checks pass:

- A real invoice with a valid amount and IBAN exists.
- The exact creditor IBAN has been approved.
- Automatic payment is enabled for that creditor.
- The amount is within the creditor-specific limit.
- The invoice has not already produced a non-failed payment.
- The selected bank account is explicitly permitted for payments.
- The remaining balance stays above the configured safety reserve.
- The Open Banking provider and bank accept the request.

New beneficiaries, changed IBANs, duplicates, insufficient reserves, provider errors, and bank-required authentication remain exceptions.

## Verification included with this package

- Python source compilation.
- Backend automated tests.
- Connector-catalog integrity tests.
- YAML parsing for GitHub Actions and Render configuration.
- Source-level Dart delimiter validation.

A native APK was not compiled in this environment because Flutter and the Android SDK were not installed. The included GitHub Actions workflow runs `flutter pub get`, `flutter analyze`, `flutter test`, and `flutter build apk --release` in the cloud before producing the installable artifact.

## Optional recovery tools

The PowerShell scripts remain in the package for recovery or development on Windows. They are not required by the phone-first deployment path.

## Android release build requirement

The build workflow applies `android/tooling/app-build.gradle.kts` after `flutter create`.
This enables Java 17 core-library desugaring and adds
`com.android.tools:desugar_jdk_libs:2.1.4`, which is required by
`flutter_local_notifications` during `checkReleaseAarMetadata`.
