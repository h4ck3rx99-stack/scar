# ADR 0003: Local embeddings: fastembed BAAI/bge-small-en-v1.5 (ONNX, CPU)

Status: accepted (2026-09-26)

## Decision

Memory embeddings run locally on CPU through fastembed (onnxruntime), 384 dimensions, ~130 MB, lazily loaded and idle-unloaded. No personal memory text is sent to a cloud embedding API by default.

## Alternatives and rationale

Cloud embeddings would add a privacy exposure for memory, which is the most personal data class, for little quality gain at this corpus size.
