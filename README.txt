VAAPP v1.0.9 GitHub Actions installer
=====================================

The prepared patch bundle is already on repository main.
Add this file to the repository at:

  .github/workflows/apply-v109-briefing-ledger.yml

Committing that workflow to main triggers it automatically. It can also be run with workflow_dispatch.
The workflow fails closed if application source has drifted from the verified v1.0.8 baseline.
