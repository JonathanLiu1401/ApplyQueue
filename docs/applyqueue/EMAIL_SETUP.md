# Linking a mailbox (optional)

ApplyQueue uses **your own** OAuth app, so no shared client or server sits between you and your mail.
Tokens are stored only on this computer in `<data>/vault/email_accounts.json` (owner-only permissions)
and are never synced.

## Gmail
1. Google Cloud Console → create a project → enable the **Gmail API**.
2. OAuth consent screen → External → add yourself as a test user. Scope: `gmail.modify`.
3. Credentials → Create credentials → OAuth client ID → **Desktop app**.
4. Settings → Linked email → Gmail: paste the client ID and secret → Connect.

While the consent screen is in **Testing**, Google expires the refresh token after 7 days.
ApplyQueue shows a one-click **Reconnect** banner from day 6 (and immediately if Google
answers `invalid_grant`). Untick "Testing mode" once your app is published.

## Outlook / Microsoft 365
1. Microsoft Entra admin center → App registrations → New registration.
2. Supported account types: as needed (personal accounts need "…and personal Microsoft accounts").
3. Platform: **Mobile and desktop applications**, redirect URI `http://localhost/email/oauth/callback`
   (Microsoft allows any port on `localhost` for this platform).
4. API permissions → Microsoft Graph → delegated `Mail.ReadWrite`, `User.Read`, `offline_access`.
5. Settings → Linked email → Outlook: paste the Application (client) ID; tenant `common` unless
   your organization requires its own → Connect. No client secret is used.

## What ApplyQueue does with the mailbox
- Reads verification codes for the application it is running (no webmail browsing).
- Marks a job **confirmed** when its confirmation email arrives (only if the page already showed a
  confirmation), adds the configured label/category and archives it.
- Labels and archives verification/security-code emails for that application.
- Touches nothing it cannot match to a job you applied to, and stores no email bodies.
