# Outlook / Microsoft 365 (mail, calendar, contacts via Microsoft Graph)

SCAR uses Microsoft Graph v1.0 with a **public client** app registration and the OAuth
authorization-code flow with PKCE (no MSAL, no client secret). Works with personal
Microsoft accounts (outlook.com, hotmail.com) and work/school accounts.

## Scopes

| Scope | Why |
|---|---|
| `offline_access` | refresh token so you do not sign in every hour |
| `User.Read` | your own address (excluded from reply-all) |
| `Mail.ReadWrite` | search/read mail, create drafts, confirm the message reached **Sent Items** |
| `Mail.Send` | send mail |
| `Calendars.ReadWrite` | calendar events |
| `Contacts.Read` | look people up in your contacts |

## 1. Register the app (one time)

1. Open <https://entra.microsoft.com/> (or <https://portal.azure.com/> -> **Microsoft Entra ID**)
   -> **App registrations** -> **New registration**.
2. Name "SCAR". Supported account types:
   * personal accounts only or both: **Accounts in any organizational directory and personal
     Microsoft accounts** (tenant `common`);
   * only your organisation: **Single tenant** (use your tenant id as `ms_tenant`).
3. Redirect URI: platform **Public client/native (mobile & desktop)**, value
   `http://localhost`. (The identity platform ignores the port for loopback redirects;
   SCAR listens on `127.0.0.1` with a random port.)
4. After creation: **Authentication** -> **Advanced settings** -> **Allow public client
   flows** = **Yes** -> Save.
5. **API permissions** -> Add a permission -> Microsoft Graph -> **Delegated** -> add the
   scopes above. (Work accounts: an admin may need to *Grant admin consent*.)
6. Copy the **Application (client) ID** from the Overview page.

No client secret or certificate is needed; do not create one.

## 2. Configure SCAR

```powershell
scar config set ms_client_id 00000000-0000-0000-0000-000000000000
scar config set ms_tenant common          # or organizations / consumers / <tenant-id>
scar config set email_provider outlook    # or "auto"
scar config set calendar_provider outlook # or "auto"
```

Environment equivalents: `SCAR_MS_CLIENT_ID`, `SCAR_MS_TENANT`.

## 3. Sign in

```powershell
scar auth microsoft
```

The browser opens `login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize`. After you
consent, the refresh token is stored **only** in Windows Credential Manager
(`SCAR` / `SCAR_MS_REFRESH_TOKEN`). Microsoft rotates refresh tokens; each new one
replaces the old entry automatically. Refresh tokens expire after 90 days of inactivity
(or per your organisation's policy) - then run `scar auth microsoft` again.

## Sending and verification

Graph's `sendMail` returns no id, so SCAR creates the message (`POST /me/messages`,
attachments up to 3 MB inline, larger ones through an upload session), sends it
(`POST /me/messages/{id}/send`) and then looks up its `internetMessageId` in
**Sent Items** (retrying for a few seconds). Replies use `createReply`/`createReplyAll`
so Outlook keeps the conversation thread.

## Troubleshooting

| Message | Fix |
|---|---|
| `OUTLOOK not configured: set SCAR_MS_CLIENT_ID and run scar auth microsoft` | steps 1-3 |
| `AADSTS7000218: ... client_assertion or client_secret` | enable **Allow public client flows** |
| `AADSTS50011: redirect URI mismatch` | add `http://localhost` under *Mobile and desktop applications* |
| `Microsoft Graph denied access (missing permission/scope)` | add the permission / ask an admin for consent, sign in again |
