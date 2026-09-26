# Telegram

Two independent ways to use Telegram:

| Channel | What it is | Can message |
|---|---|---|
| `telegram` (recommended) | a **bot** via the Telegram Bot API (HTTPS) | people who started a chat with your bot, groups/channels the bot was added to |
| `telegram_user` (optional) | **your own account** via MTProto (Telethon) | anyone you can message yourself |

## Bot setup (channel `telegram`)

1. In Telegram, open **@BotFather** -> `/newbot` -> choose a name and a username ending
   in `bot`. BotFather replies with a token like `123456789:AA...`.
2. Store the token (never in config.toml):

   ```powershell
   scar config set-secret TELEGRAM_BOT_TOKEN
   ```

   or set the environment variable `TELEGRAM_BOT_TOKEN`.
3. Open your bot in Telegram and press **Start** (a bot cannot message you first).
4. Find your chat id: send the bot a message, then ask SCAR for recent Telegram messages
   (`message.recent` on `telegram`), or open
   `https://api.telegram.org/bot<token>/getUpdates` in a browser and read
   `message.chat.id`.
5. Send: "telegram 123456789 that I'm running late" - SCAR asks for approval (HIGH), sends
   via `sendMessage` and reports the returned `message_id`.

Recipients: numeric chat id (`123456789`, groups/channels `-100...`) or `@channelusername`.
`message.recent` uses `getUpdates`, which does not work while a webhook is set on the bot
(`deleteWebhook` to switch back) and only returns updates from the last 24 hours.

## User account (optional)

Channel `telegram_user`.

Use this only when a bot cannot do the job. It logs in **as you**.

1. Go to <https://my.telegram.org> -> log in -> **API development tools** -> create an app.
   Note the numeric **api_id** and the **api_hash**.
2. Configure:

   ```powershell
   scar config set telegram_api_id 1234567
   scar config set-secret TELEGRAM_API_HASH
   ```

3. Sign in: `scar auth telegram`. Enter your phone number (international format), the
   login code Telegram sends to your app, and your two-step verification password if you
   use one.
4. The session is saved at `<SCAR data folder>\telethon\scar.session`. **That file is a
   full login to your account** - keep it private. Revoke it any time in Telegram ->
   Settings -> Devices -> "SCAR".

Recipients: `@username`, a phone number in your contacts, or a numeric id.

**Terms of Service:** Telegram forbids spam, bulk or unsolicited messaging and abusive
automation from user accounts (see <https://telegram.org/tos> and the API ToS at
<https://core.telegram.org/api/terms>). Accounts that violate them get limited or banned.
SCAR sends only messages you approve one by one.

## Errors

| Message | Meaning |
|---|---|
| `TELEGRAM bot not configured: ...` | store `TELEGRAM_BOT_TOKEN` |
| `TELEGRAM bot token was rejected` | the token was revoked (`/revoke` in BotFather) - store the new one |
| `Telegram refused: ... (the user must start a chat with the bot first ...)` | press Start in the bot chat |
| `Telegram Bot API rate limit reached; retry after Ns` | Telegram flood control; wait |
