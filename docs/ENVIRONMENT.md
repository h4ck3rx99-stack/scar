# Build Environment (recorded 2026-09-26)

Recorded during reconnaissance. SCAR detects all of these at runtime; nothing here is hardcoded.

| Item | Value |
|---|---|
| OS | Windows 11 Home Single Language 10.0.26200 (build 26200) |
| CPU | 13th Gen Intel Core i7-13620H |
| RAM | 23.6 GB visible |
| GPU | NVIDIA GeForce RTX 5050 Laptop GPU, compute capability 12.0 (Blackwell), 8151 MiB VRAM (~7.0 GB free at idle) |
| NVIDIA driver | 616.92, CUDA UMD 13.4 |
| Python | 3.11.9 (`C:\Users\Avani\AppData\Local\Programs\Python\Python311`) |
| uv | 0.12.18 |
| PowerShell | Windows PowerShell 5.1 (`powershell.exe`) and PowerShell 7.6.6 (`pwsh`) |
| git | 2.55.0.windows.5 |
| gh (GitHub CLI) | **not installed**. SCAR uses the GitHub REST API via `GITHUB_TOKEN`; `gh` is used when present |
| Node / npm | v24.14.1 |
| VS Code | `code` CLI at `%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code` |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Edge | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |
| Playwright browsers | `%LOCALAPPDATA%\ms-playwright` contains chromium-1234/1243 and headless shells |
| llama.cpp | winget package `ggml.llamacpp`, build 10488 (commit 9d77fa172), **Vulkan** backend (`ggml-vulkan.dll`), `llama-server.exe` on PATH |
| Server at 127.0.0.1:8080 | **none answering** at recon time |
| Ollama | 0.30.10 installed; models `qwen3:8b` (5.2 GB), `llama3.2:latest` (2.0 GB), `llava:latest` (4.7 GB + projector). OpenAI-compatible endpoint `http://127.0.0.1:11434/v1`. *Note: the recon command `ollama list` started the Ollama app/server, which was not running beforehand.* |
| GGUF files | Ollama blobs under `%USERPROFILE%\.ollama\models\blobs` (qwen3:8b = `sha256-a3de86cd…`, llava model `sha256-17037023…` + projector `sha256-72d6f08a…`); LM Studio bundled `nomic-embed-text-v1.5.Q4_K_M.gguf` |
| LM Studio | installed (`~/.lmstudio`) |
| Everything (`es.exe`) | not installed |
| Cloud provider keys | **none** present in environment (no GROQ/GEMINI/OPENROUTER/etc.) |
| Network | internet reachable (api.groq.com answered 401 without a key) |
| Monitors | 2 × 1920×1080: `DISPLAY1` primary at (0,0); `DISPLAY5` at (-1920,0). Virtual screen spans negative X |
| DPI | 96 (100 % scaling) |
| Audio input | HyperX Cloud Stinger 2 (default), Intel SST mic array, Iriun webcam, **Virtual Audio Cable Line 1** |
| Audio output | HyperX Cloud Stinger 2 (default), Realtek speaker, NVIDIA HDMI, Virtual Audio Cable Line 1 |
| Battery | present, 100 %, on AC |
| Disk | C: 167 GB free |
| onnxruntime providers | CPUExecutionProvider (plus Azure EP) |

## Implications for the build

* The llama.cpp build is **Vulkan**, not CUDA, so `-ngl` offload goes through Vulkan on the RTX 5050. No CUDA 12.8 Python wheels are needed. ONNX components (embeddings, VAD, wake word, Whisper via CTranslate2) run on CPU (ADR-0005).
* With no cloud keys, the live reasoning path is local: Ollama (`qwen3:8b`) or a SCAR-managed `llama-server` that loads the same GGUF blob. Every cloud adapter is fully implemented and marked IMPLEMENTED-UNVERIFIED until a key is supplied.
* Two monitors, one at negative virtual-screen coordinates, exercise the multi-monitor coordinate handling.
* Virtual Audio Cable makes a loopback voice test possible: TTS plays into VAC and STT captures from VAC.
