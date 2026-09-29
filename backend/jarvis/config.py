"""Central settings. Every value can be overridden in backend/.env or the environment."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = BACKEND_DIR / "models"
LOG_DIR = BACKEND_DIR / "logs"
CAPTURE_DIR = BACKEND_DIR / "captures"
load_dotenv(BACKEND_DIR / ".env")


def _env(name, default, cast=str):
    raw = os.environ.get(name)
    return default if raw in (None, "") else cast(raw)


def _bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")


# --- Network -----------------------------------------------------------------
WS_HOST = "127.0.0.1"
WS_PORT = _env("JARVIS_WS_PORT", 8765, int)
ALLOWED_ORIGINS = {"file://", "http://localhost:5173", "http://127.0.0.1:5173", "null"}
# Dev only: lets the Vite dev page in a normal browser connect without the token.
DEV_NO_TOKEN = _bool(_env("JARVIS_DEV", "false"))
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}

APPDATA_DIR = Path(os.environ.get("APPDATA", BACKEND_DIR)) / "jarvis"
TOKEN_FILE = APPDATA_DIR / "token"


def session_token() -> str:
    """Shared secret the Electron app reads from %APPDATA%\\jarvis\\token."""
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_hex(24))
    return TOKEN_FILE.read_text().strip()


# --- Audio -------------------------------------------------------------------
SAMPLE_RATE = 16000
BLOCK = 512                      # 32 ms, Silero's native chunk
INPUT_DEVICE = _env("JARVIS_INPUT_DEVICE", None)     # name substring or index
OUTPUT_DEVICE = _env("JARVIS_OUTPUT_DEVICE", None)
PREROLL_S = 1.5
ECHO_CANCEL = _bool(_env("JARVIS_ECHO_CANCEL", "true"))   # subtract Scar's own voice from the mic

# --- Identity ----------------------------------------------------------------
# The assistant's name doubles as a wake word: "Scar" / "Hey Scar" / "Scar, open YouTube".
NAME = _env("JARVIS_NAME", "Scar")
NAME_WAKE = _bool(_env("JARVIS_NAME_WAKE", "true"))   # listen for the name (Whisper keyword spotting)

# --- Voice FSM timings (ms) ----------------------------------------------------
VAD_ON = _env("JARVIS_VAD_ON", 0.6, float)
VAD_ON_FOLLOW_UP = _env("JARVIS_VAD_ON_FOLLOW_UP", 0.75, float)  # stricter: follow-up is open to the whole room
VAD_OFF = 0.35
SPEECH_ONSET_MS = 150            # speech must persist this long to count
END_SILENCE_MS = _env("JARVIS_END_SILENCE_MS", 550, int)
MAX_UTTERANCE_MS = 15000
MAX_FOLLOW_UP_UTTERANCE_MS = 8000   # longer = somebody else talking / a video playing
MAX_FOLLOW_UP_CHAIN = 4             # after this many wake-word-free turns, the wake word is needed again
# Voice ID: cosine similarity to your enrolled voiceprint needed to count as you (after "learn my voice").
VOICE_MATCH = _env("JARVIS_VOICE_MATCH", 0.45, float)
ONLY_MY_VOICE = _bool(_env("JARVIS_ONLY_MY_VOICE", "true"))  # once enrolled, ignore other people's voices
# Follow-up speech quieter than this fraction of your usual voice level is treated as someone else in the room.
FOLLOW_UP_MIN_LEVEL = _env("JARVIS_FOLLOW_UP_MIN_LEVEL", 0.5, float)
NO_SPEECH_TIMEOUT_MS = 4000      # after wake word, give up if nothing is said
FOLLOW_UP_MS = _env("JARVIS_FOLLOW_UP_MS", 5000, int)
FOLLOW_UP_TAIL_MS = 250          # wait after playback drains before opening the hot mic
SLEEP_AFTER_S = _env("JARVIS_SLEEP_AFTER_S", 180, int)
WAKE_THRESHOLD = _env("JARVIS_WAKE_THRESHOLD", 0.5, float)

# --- Models ------------------------------------------------------------------
WAKE_MODEL = MODELS_DIR / "wakeword" / "hey_jarvis_v0.1.onnx"
VAD_MODEL = MODELS_DIR / "wakeword" / "silero_vad.onnx"
WHISPER_MODEL = _env("JARVIS_WHISPER_MODEL", str(MODELS_DIR / "whisper" / "large-v3-turbo"))  # or .../small.en (lighter)
WHISPER_DEVICE = _env("JARVIS_WHISPER_DEVICE", "auto")   # auto | cuda | cpu
KOKORO_MODEL = MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS_DIR / "kokoro" / "voices-v1.0.bin"
KOKORO_VOICE = _env("JARVIS_VOICE", "am_michael")
KOKORO_SPEED = _env("JARVIS_VOICE_SPEED", 1.1, float)

# --- LLM ---------------------------------------------------------------------
LLAMA_SERVER = _env("JARVIS_LLAMA_SERVER", "llama-server")
LLM_GGUF = _env("JARVIS_LLM_GGUF", str(MODELS_DIR / "llm" / "Qwen3.5-4B-Q4_K_M.gguf"))
LLM_MMPROJ = _env("JARVIS_LLM_MMPROJ", str(MODELS_DIR / "llm" / "Qwen3.5-4B-mmproj-F16.gguf"))  # vision encoder
LLM_PORT = _env("JARVIS_LLM_PORT", 8080, int)
LLM_URL = f"http://127.0.0.1:{LLM_PORT}/v1"
LLM_MODEL = "local"
MANAGE_LLAMA_SERVER = _bool(_env("JARVIS_MANAGE_LLAMA_SERVER", "true"))

# Optional cloud LLM (any OpenAI-compatible endpoint: Groq, Gemini, OpenRouter, Cerebras...).
# Falls back to the local model on error / rate limit / offline.
CLOUD_LLM_URL = _env("JARVIS_CLOUD_LLM_URL", "")
CLOUD_LLM_KEY = _env("JARVIS_CLOUD_LLM_KEY", "")
CLOUD_LLM_MODEL = _env("JARVIS_CLOUD_LLM_MODEL", "")

HISTORY_TURNS = 6

# --- Hands -------------------------------------------------------------------
REAL_CLICK = _bool(_env("JARVIS_REAL_CLICK", "false"))
DESTINATION_TIMEOUT_S = 1.5
