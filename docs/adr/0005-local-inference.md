# ADR 0005: Local inference: llama.cpp llama-server (Vulkan build) managed by SCAR; Ollama reused if running

Status: accepted (2026-09-26)

## Decision

The installed llama.cpp is the Vulkan build, so GPU offload works on the RTX 5050 without CUDA Python wheels. SCAR reuses a healthy server at SCAR_LOCAL_LLM_URL (never killing it), otherwise starts one on demand in a Job Object, sizes -ngl from NVML free VRAM and SCAR_MAX_LOCAL_VRAM, and stops it after SCAR_MODEL_IDLE_TIMEOUT. A running Ollama is used through its native /api/chat (not the OpenAI endpoint, which cannot set num_ctx), and models SCAR used are unloaded with keep_alive=0 when idle. ONNX components (VAD, wake word, embeddings) and faster-whisper (CTranslate2 int8) run on CPU.

## Alternatives and rationale

Avoided PyTorch entirely (multi-GB, Blackwell wheel uncertainty). Verified: -ngl 99 offload of qwen3-8B Q4_K_M, and llava + mmproj for local vision.
