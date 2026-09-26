# GitHub

Git itself needs no setup: SCAR runs the `git` CLI in your repositories (status, diff, log, branch, commit, stash,
fetch/pull/merge/rebase, push, clone). Push is HIGH risk and force push is CRITICAL.

GitHub features (issues, pull requests, comments, CI status) use the REST API with a **fine-grained personal access
token**:

1. github.com → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
2. Repository access: only the repositories SCAR should touch.
3. Permissions: **Issues: Read and write**, **Pull requests: Read and write**, **Contents: Read**,
   **Metadata: Read**, **Checks: Read** (for CI status). Nothing else.
4. Store it: `scar config set-secret GITHUB_TOKEN` (Windows Credential Manager).
5. Check: `scar doctor`, then ask "what's the CI status of this repo?"

SCAR works out `owner/repo` from the `origin` remote of the local clone, or you can name it. Creating issues or pull
requests and commenting publish to GitHub, so they are HIGH risk and need approval. An expired or revoked token is
reported as unavailable with this page as the fix. `gh` is not required.
