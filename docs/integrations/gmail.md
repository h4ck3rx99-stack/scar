# Gmail (and Google Calendar / Contacts)

SCAR talks to Gmail through the Gmail REST API v1 with your own Google Cloud OAuth
client. One sign-in (`scar auth google`) also enables Google Calendar and Google
Contacts (People API).

## What SCAR asks for (scopes)

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | search and read mail, confirm a sent message carries the `SENT` label |
| `https://www.googleapis.com/auth/gmail.compose` | create drafts and send mail |
| `https://www.googleapis.com/auth/calendar.events` | list/create/update/delete events |
| `https://www.googleapis.com/auth/contacts.readonly` | look people up by name (People API `searchContacts`) |

SCAR never requests full mailbox deletion (`https://mail.google.com/`).

## 1. Create the OAuth client (one time)

1. Open <https://console.cloud.google.com/> and create (or pick) a project.
2. **APIs & Services -> Library**: enable **Gmail API**, **Google Calendar API** and
   **People API**.
3. **APIs & Services -> OAuth consent screen** (Google Auth Platform -> Branding / Audience):
   * User type **External** (personal account) or **Internal** (Google Workspace).
   * App name "SCAR", your email as support and developer contact.
   * **Data access / Scopes**: add the four scopes above.
   * **Audience / Test users**: add your own Google address while the app is in *Testing*.
4. **APIs & Services -> Credentials -> Create credentials -> OAuth client ID**:
   * Application type: **Desktop app** (this is required; "Web application" clients
     are rejected by SCAR).
   * Download the JSON (`client_secret_....json`) and keep it somewhere private, e.g.
     `%APPDATA%\SCAR\google_client.json`.

Desktop-app clients use a **loopback redirect**: SCAR starts a one-shot web server on
`http://127.0.0.1:<random port>/` and Google redirects your browser there. Nothing has
to be registered for it; do not add redirect URIs.

## 2. Configure SCAR

```powershell
scar config set google_oauth_client_file "%APPDATA%\SCAR\google_client.json"
scar config set email_provider gmail        # or leave "auto"
scar config set calendar_provider google    # or leave "auto"
```

(Environment-variable equivalents: `SCAR_GOOGLE_OAUTH_CLIENT_FILE`, `SCAR_EMAIL_PROVIDER`,
`SCAR_CALENDAR_PROVIDER`.)

## 3. Sign in

```powershell
scar auth google
```

Your browser opens Google's consent page (PKCE, authorization code flow). After you
approve, SCAR stores the **refresh token only in Windows Credential Manager** (entry
`SCAR` / `SCAR_GOOGLE_REFRESH_TOKEN`). No token is ever written to a file. Access
tokens live in memory and are refreshed automatically.

To sign out: remove the `SCAR` / `SCAR_GOOGLE_REFRESH_TOKEN` entry in *Control Panel ->
Credential Manager -> Windows Credentials* and revoke SCAR at
<https://myaccount.google.com/permissions>.

## The 7-day expiry trap ("Testing" status)

While the consent screen's **publishing status is "Testing"**, Google expires refresh
tokens **7 days** after they are issued. SCAR then reports
`Google sign-in expired or was revoked: run scar auth google`.

To avoid it, do one of:

* **Publish the app**: OAuth consent screen -> Audience -> **Publish app** ("In
  production"). For a personal app you do not need Google verification; you will see an
  "unverified app" warning during sign-in which you can accept for your own account
  (Advanced -> Go to SCAR).
* **Google Workspace**: choose user type **Internal**; internal apps have no 7-day limit.

## How sends are verified

`email.send` always needs your approval (HIGH risk). The approval shows each recipient's
name and address, the subject, the body (first 2000 characters) and every attachment
with its size. After sending, SCAR checks the message id returned by Gmail and then that
the message carries the `SENT` label.

## Troubleshooting

| Message | Fix |
|---|---|
| `GMAIL not configured: set SCAR_GOOGLE_OAUTH_CLIENT_FILE and run scar auth google` | steps 1-3 |
| `Google OAuth client file must be a 'Desktop app' client` | recreate the client with type Desktop app |
| `GMAIL denied access (missing permission/scope)` | enable the API in the console, then `scar auth google` again |
| `GMAIL rate limit reached; retry after Ns` | wait; SCAR retries short waits itself |

## IMAP SMTP alternative

For providers without an API (or if you prefer app passwords), SCAR speaks IMAP over SSL
and SMTP with STARTTLS (port 587) or implicit TLS (port 465).

1. Create an **app password** with your provider (Gmail: <https://myaccount.google.com/apppasswords>,
   requires 2-Step Verification; Outlook.com, iCloud, Fastmail and Yahoo have equivalents).
2. Configure:

```powershell
scar config set email_provider imap
scar config set imap_host imap.gmail.com
scar config set imap_port 993
scar config set imap_user you@gmail.com
scar config set smtp_host smtp.gmail.com
scar config set smtp_port 587
scar config set-secret SCAR_IMAP_PASSWORD      # prompts; stored in Credential Manager
scar config set-secret SCAR_SMTP_PASSWORD      # optional; defaults to the IMAP password
```

Search syntax for IMAP: `from:bob subject:"q3 report" since:2026-09-01 unread invoice`.
Sends are verified by the SMTP server's acceptance and, where the server files
submissions in the Sent folder (Gmail, iCloud, Fastmail), by finding the generated
`Message-ID` there; otherwise SCAR reports "sent, but couldn't confirm". Drafts for IMAP
are kept locally in SCAR's data folder.
