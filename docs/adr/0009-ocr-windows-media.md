# ADR 0009: OCR: Windows.Media.Ocr through PyWinRT

Status: accepted (2026-09-26)

## Decision

Built-in, free, CPU-light and offline (verified 56 ms on a test image). Pixels go into a SoftwareBitmap via DataWriter; small crops are upscaled 2× for accuracy; coordinates are mapped back to virtual-screen pixels.

## Alternatives and rationale

RapidOCR (ONNX) remains the documented alternative if the OCR language pack is missing; doctor reports it.
