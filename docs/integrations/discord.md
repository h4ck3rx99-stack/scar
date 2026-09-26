# Discord

SCAR posts to Discord **only as a bot** (REST API v10) or through a **webhook**.
Automating a normal user account ("self-bot", using your user token) violates Discord's
Terms of Service and can get the account banned - SCAR does not support it and never
will.

## Bot setup

1. Open <https://discord.com/developers/applications> -> **New Application** -> name it
   "SCAR".
2. **Bot** tab -> **Reset Token** -> copy the token. Leave **Public Bot** off.
   *Privileged Gateway Intents*: sending needs none (SCAR uses only REST calls). Enable
   **Message Content Intent** if you want `message.recent` to return the text of other
   people's messages - without it Discord returns an empty `content` for them.
3. Store the token:

   ```powershell
   scar config set-secret DISCORD_BOT_TOKEN
   ```

4. Invite the bot: **OAuth2 -> URL Generator** -> scopes `bot`; bot permissions
   **View Channels**, **Send Messages**, **Read Message History**. Open the generated URL
   (it looks like
   `https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=68608`)
   and add the bot to your server.
5. Get ids: Discord -> User Settings -> Advanced -> **Developer Mode** on; right-click a
   channel or user -> **Copy ID**.
6. Optional default channel: `scar config set discord_default_channel <channel id>`.

## Recipients for `message.send` / `message.recent` (channel `discord`)

| Recipient | Meaning |
|---|---|
| `123456789012345678` or `channel:123...` | post in that channel |
| `user:123...` | DM that user (opens a DM channel first; the user must share a server with the bot and allow DMs) |
| `webhook` | post through the webhook in `DISCORD_WEBHOOK_URL` |
| empty | `discord_default_channel` |

Messages are limited to 2000 characters. SCAR disables mention parsing
(`allowed_mentions: {parse: []}`) so a message never pings `@everyone` or roles.

## Webhook (no bot)

Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL, then:

```powershell
scar config set-secret DISCORD_WEBHOOK_URL
```

Webhooks can post but cannot read messages.

## Verification and errors

After a bot send SCAR reads the message back (`GET /channels/{id}/messages/{id}`).
`401` means the token is wrong/reset (store it again), `403 Missing Access` means the bot
lacks permission in that channel, `429` responses are surfaced with Discord's
`Retry-After`.
