# Production requirements

## Required for dependable full-time operation

- A stable public HTTPS backend.
- An always-on service plan.
- Persistent PostgreSQL storage.
- Database backups and restore testing.
- A rotated pairing secret and token-encryption key.
- Monitoring for failed schedules, OAuth expiry, bank consent expiry, payment rejection, and backup failure.
- A real AI provider account or private compatible model endpoint.
- Google OAuth credentials and any Google verification required for the selected scopes and distribution.
- Enable Banking production activation, valid key material, contract/KYC completion, and the actual account/payment capabilities enabled for the application.

## Service-specific requirements

Each external platform can impose its own:

- Developer application registration.
- Paid plan or API quota.
- Permission review.
- Business verification.
- Redirect URL requirements.
- Webhook verification.
- OAuth consent renewal.
- Data-retention and privacy obligations.
- Prohibition or limitation on browser automation.

The VA must follow those requirements. It does not bypass provider restrictions or represent an unavailable capability as connected.

## Security boundaries

- Do not store bank passwords, card PINs, Itsme credentials, recovery codes, or one-time authentication codes.
- Keep Render, GitHub, Google, payment-provider, and other administrative credentials narrowly scoped.
- Use separate production and testing credentials.
- Revoke unused connectors and rotate leaked credentials immediately.
- Review creditor allowlists and payment limits regularly.
- Keep personal and Revolut Pro activity separated.
- Require explicit approval for new beneficiaries, changed IBANs, high-value payments, legal commitments, and other irreversible actions.

## Phone-only boundary

All VA configuration and ordinary operation can be initiated from Android. External provider pages remain necessary for actions the provider controls, including developer registration, KYC, OAuth consent, Open Banking consent, Strong Customer Authentication, billing acceptance, and APK installation confirmation.
