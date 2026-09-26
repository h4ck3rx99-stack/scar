# WhatsApp

WhatsApp has no API for personal accounts. SCAR offers two providers for channel
`whatsapp`:

| Provider | Used when | Sends from |
|---|---|---|
| **Cloud API** (recommended) | `whatsapp_phone_number_id` and `WHATSAPP_CLOUD_TOKEN` are set | a WhatsApp **Business** number you register with Meta |
| **Desktop** (opt-in) | Cloud API not configured and `whatsapp_desktop_enabled = true` | your own WhatsApp Desktop app, via UI automation |

## WhatsApp Business Cloud API

1. Create a Meta developer account and an app of type **Business** at
   <https://developers.facebook.com/apps> -> add the **WhatsApp** product.
2. **WhatsApp -> API Setup** shows a test number and its **Phone number ID**. Add your own
   phone as a test recipient (development mode only allows up to 5 verified recipients),
   or add and verify a real business number.
3. Create a permanent token: Meta Business Suite -> Business settings -> **System users** ->
   add a system user, assign the app with **whatsapp_business_messaging** permission ->
   **Generate token**. (The temporary token on the API Setup page expires after 24 hours.)
4. Configure:

   ```powershell
   scar config set whatsapp_phone_number_id 106540352242922
   scar config set-secret WHATSAPP_CLOUD_TOKEN
   ```

Recipients are international numbers (`+15551234567`, spaces/dashes are removed).

### Constraints you must know

* **24-hour customer service window**: free-form text can only be sent within 24 hours
  after the recipient last messaged your business number. Outside the window Meta
  rejects it (error 131047, SCAR reports `WindowClosed`) and you must send an approved
  **message template** (create templates in WhatsApp Manager; SCAR's integration exposes
  `send_template(phone, template, language, parameters)`).
* Development mode: only allowed test recipients (error 131030 -> `RecipientNotAllowed`).
* Messages are billed by Meta per conversation category outside the free tier.
* The Cloud API cannot read message history; incoming messages go only to a webhook, so
  `message.recent` is unavailable for WhatsApp.

## Desktop provider (opt-in)

For personal use without a business number SCAR can drive **WhatsApp Desktop**
(Microsoft Store version) on this PC:

```powershell
scar config set whatsapp_desktop_enabled true
```

How it works: SCAR opens `whatsapp://send?phone=<number>&text=<message>` (WhatsApp shows
the chat with the text pre-filled), finds the WhatsApp window with Windows UI Automation,
invokes the **Send** button, then checks that the text appears in the chat and the
compose box is empty.

Caveats:

* Off by default. WhatsApp Desktop must be installed, logged in, and the PC unlocked;
  SCAR briefly takes focus of the WhatsApp window.
* It depends on WhatsApp's accessibility names (English UI: "Send"). If the Send button
  cannot be found the message is **not** sent and SCAR says so.
* No message id exists. If SCAR cannot confirm the message in the chat it reports
  **"sent but couldn't confirm"** (`verified = None`) instead of claiming success.
* Automating a personal account is at your own risk under WhatsApp's Terms of Service;
  use it for occasional personal messages only. Every send still needs your approval.

## Testing safety

Under the test suite (`SCAR_TESTING=1`) WhatsApp sends are blocked unless
`SCAR_LIVE_TESTS=1` **and** the recipient equals `SCAR_TEST_WHATSAPP_TO` (digits only).
