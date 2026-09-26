# ADR 0006: OAuth: own PKCE loopback flow instead of Google/MSAL SDKs

Status: accepted (2026-09-26)

## Decision

One small installed-app OAuth2 client (authorization code + PKCE S256 + state, one-shot 127.0.0.1 redirect listener) serves Google and Microsoft. Refresh tokens are stored only in Windows Credential Manager via keyring.

## Alternatives and rationale

google-auth-oauthlib and msal work, but add dependency weight and store tokens in their own caches; a thin shared client keeps token storage under SCAR's control and is fully testable with HTTP mocks.
