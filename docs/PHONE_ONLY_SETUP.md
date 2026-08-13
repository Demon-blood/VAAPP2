# Phone-only setup

## 1. Obtain the first APK

One bootstrap action cannot be eliminated: an APK must exist before Android can install and run the app.

From the phone:

1. Create a private GitHub repository.
2. Upload the project while preserving `.github/workflows/android-release.yml`, `android/`, and `backend/`.
3. Open the repository's **Actions** tab.
4. Open **Build Android APK** and select **Run workflow**.
5. After the run succeeds, open **Releases** and download `Full-Time-VA-Android-v0.9.4.apk`.
6. Install the downloaded `Full-Time-VA-Android-v0.9.4.apk`.
7. Allow installation from the browser or file manager when Android requests it.

The workflow performs dependency resolution, static analysis, tests, and the release APK build. A failed workflow does not publish an APK as a successful VA update.

## 2. Prepare Render using the phone browser

1. Create or sign in to Render.
2. Connect Render to the private GitHub repository.
3. Open Render account settings and create an API key.
4. Return to Full-Time VA.

The API key is used for the deployment request and is not persisted by the Android app.

## 3. Deploy from Android

On the first app screen:

1. Select **Always-on VA** or **Testing only**.
2. Paste the Render API key.
3. Tap **Verify key and load workspaces**.
4. Select the workspace returned by Render.
5. Enter the GitHub repository HTTPS URL.
6. Choose a unique service name.
7. Leave the PostgreSQL field empty to let the app provision the database, or enter an existing PostgreSQL URL.
8. Tap **Provision, deploy, verify and pair**.

Always-on mode shows a confirmation before requesting paid Render resources. The app then:

- Creates PostgreSQL when required.
- Generates a strong pairing secret.
- Generates the token-encryption key.
- Creates the backend service.
- Waits for the real `/health` endpoint.
- Pairs the Android device only after the endpoint succeeds.

## 4. Configure built-in services

Open **Services**. Each built-in card provides the relevant controls:

- **Configure** stores credentials encrypted on the backend.
- **Test** calls the real provider.
- **Connect** opens the provider's official authorization page.
- **Disconnect** revokes or disables the local connection.

Configure in this order:

1. AI provider
2. Google
3. Enable Banking
4. GitHub
5. Cloudflare and Discord when required

Google and banking callbacks use the deployed backend HTTPS address automatically.

## 5. Connect Google

1. Tap the provider setup button for Google.
2. In the phone browser, create a Web OAuth application.
3. Register the callback displayed by the VA backend: `<backend-address>/api/google/callback`.
4. Enter the client ID and secret in the app.
5. Tap **Connect Google**.
6. Complete Google's consent screen.
7. Return to the app and run the live test.

Gmail, Calendar, Drive, and Contacts only become connected after OAuth succeeds.

## 6. Connect Beobank and Revolut

1. Create and activate the Enable Banking application using its official portal.
2. In the Enable Banking service card, tap **Generate key + certificate**.
3. Copy the public certificate displayed by Android and upload it to the Enable Banking application. The 4096-bit private key remains encrypted on the backend and is never returned to the phone.
4. Enter the Enable Banking application ID in the app.
5. Copy the banking callback URL displayed by the service card and register it in the provider portal.
6. Run the live test.
7. Tap **Connect Beobank** and complete the bank's consent flow.
8. Tap **Connect Revolut** and complete the Revolut consent flow.
9. After synchronization, set each returned account ownership scope to **Personal** or **Pro** in Money → Accounts.
10. In Money → Budget, assign the Financial Autopilot role separately: operating, savings, reserve, tax, income, or disabled.
11. Set the cash reserve/payment permission for source accounts and surplus-receive policy for destination accounts.
12. Approve creditors by exact IBAN and specify their automatic-payment limits.

The VA never asks for a bank password, PIN, Itsme secret, or one-time bank code. Authentication remains inside the bank or Itsme flow.

## 7. Add other services

Use **Services → Choose service** for a guided preset. Every preset provides:

- An official provider-setup button.
- A copyable OAuth callback URL when the service uses OAuth.
- A generated configuration form.
- Secure secret fields.
- OAuth authorization when applicable.
- A live test.
- Executable operations.
- Automation-rule support.
- Remove/disconnect control.

Use **Add custom** when the provider is not in the catalog. Choose OAuth, client credentials, REST/XML, webhook, IMAP/SMTP, WebDAV, SFTP, RSS, Telegram, or Browserless.

Saving configuration does not mark it connected. Only a successful live provider test changes the status to **Live**.

## 8. Configure unattended work

Open **Automation** and create rules that define:

- Trigger or schedule.
- Connector and operation.
- Operation parameters.
- Whether the rule is enabled.
- Risk and approval boundaries.

Financial safety checks and provider-required authentication cannot be disabled by a connector rule.

## 9. Build updates from the phone

After GitHub is configured:

1. Open **Settings**.
2. Tap **Build Android update**.
3. Inspect the workflow run using **View build runs**.
4. Open the successful run in GitHub.
5. Download and install the new APK.

Android will always require user confirmation before replacing an installed application unless the device is managed through an authorized enterprise/device-owner deployment system.

## 10. Repair an outdated or incomplete backend

When the app reports that routes are missing or the backend version is too old:

1. Tap **Repair server** in the warning banner.
2. Confirm that this phone may be unpaired.
3. Enter the same Render API key, workspace, repository URL, and Render service name.
4. Leave the database field empty.
5. Tap **Provision, deploy, verify and pair**.

The wizard detects the existing Render service, preserves its database and encryption key, updates the repository, branch, and `backend` root directory, rotates the one-time pairing secret, triggers a clean-cache deployment, waits for `/api/system/info` to report backend 0.4.16 or newer, and then pairs the phone again. It does not create a duplicate service.


## Persistent Android update signing

Before building the first stable-signed APK, deploy backend 0.4.16 and open `https://<your-va-server>/setup/android-signing` on the phone. Enter the current Render `PAIRING_SECRET` and the GitHub repository (`owner/name`). The configured GitHub fine-grained token needs repository **Secrets: Read and write** permission. The backend generates one 4096-bit RSA PKCS#12 signing key, stores it encrypted in PostgreSQL, and installs these GitHub Actions secrets: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, and `ANDROID_KEY_PASSWORD`.

Do not rotate this key after installing the first stable-signed APK. Android requires all future update APKs to be signed by the same key.


## Free-tier AI configuration

For Groq free-tier operation, configure **Services → AI decision engine** with:

- Primary API base URL: `https://api.groq.com/openai/v1`
- Primary model: `openai/gpt-oss-20b`
- Primary API key: your Groq API key
- Daily request budget: `1000`
- Daily token budget: `200000`
- Requests reserved for urgent mail: `100`
- Tokens reserved for urgent mail: `25000`
- Historical emails allowed to use AI per day: `50`
- Allow fallback for sensitive mail: `false`

The VA applies deterministic rules, learned low-risk sender rules and content fingerprints before calling AI. The AI service card shows current local daily usage and how many calls were avoided. A fallback provider is optional and should remain blank unless you deliberately configure one.
