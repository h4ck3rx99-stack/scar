# ADR 0016: License policy for the shipped app

Status: accepted (2026-09-27)

## Decision

SCAR is MIT. The installer and the first-run runtime may include permissive licenses (MIT, BSD, Apache-2.0, ISC,
Zlib, Unicode, PSF), weak copyleft at file level (MPL-1.1/2.0, used unmodified), and these LGPL-3.0 Python packages:

- `edge-tts` (cloud text-to-speech)
- `fpdf2` (PDF export)
- `pynput` (hotkeys and input)

The LGPL packages are installed as separate, unmodified wheels into SCAR's private environment and imported at run
time, so a user can replace them. The installer never statically bundles them. GPL and AGPL components are not shipped.

`scripts/gen_notices.py` writes `THIRD_PARTY_NOTICES.md` from the locked Python set, the frontend's production
dependencies and the Rust crates. It fails the build when a GPL/AGPL component, or an LGPL one missing from the list
above, appears without an update to this ADR.

## Consequences

- A new dependency with a copyleft license needs this ADR changed first.
- Multi-licensed crates (for example `r-efi`: MIT OR Apache-2.0 OR LGPL) are used under a permissive option.
