"""SCAR configuration.

Precedence (highest first): CLI flags (init kwargs) > environment variables >
``.env`` > user config file (%APPDATA%\\SCAR\\config.toml) > built-in defaults.

Secrets are never read from the config file; see ``scar.security.secrets``.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    InitSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from scar.config import paths
from scar.core.errors import ConfigError

# Names that are secrets and therefore forbidden in config.toml.
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "CEREBRAS_API_KEY",
        "MISTRAL_API_KEY",
        "GITHUB_TOKEN",
        "NVIDIA_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "HF_TOKEN",
        "AZURE_SPEECH_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_HASH",
        "DISCORD_BOT_TOKEN",
        "BRAVE_API_KEY",
        "TAVILY_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "WHATSAPP_CLOUD_TOKEN",
        "SCAR_IMAP_PASSWORD",
        "SCAR_SMTP_PASSWORD",
    }
)

StrList = Annotated[list[str], NoDecode]


def _split_list(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        sep = ";" if ";" in stripped else ","
        return [part.strip() for part in stripped.split(sep) if part.strip()]
    return value


class _UserTomlSource(PydanticBaseSettingsSource):
    """Reads %APPDATA%\\SCAR\\config.toml. Keys are field names (``autonomy_level``)."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self.path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"cannot parse {path}: {exc}") from exc
            flat: dict[str, Any] = {}
            for key, val in raw.items():
                if isinstance(val, dict) and key != "privacy_overrides":
                    for sub, subval in val.items():
                        flat[str(sub).lower()] = subval
                else:
                    flat[str(key).lower()] = val
            leaked = {
                k
                for k in flat
                if k.upper() in SECRET_KEYS
                or f"SCAR_{k.upper()}" in SECRET_KEYS
                or any(marker in k.lower() for marker in ("password", "api_key", "secret", "token"))
            }
            if leaked:
                raise ConfigError(
                    f"{path} contains secret keys {sorted(leaked)}; secrets must come from environment "
                    "variables or the Windows Credential Manager (scar config set-secret)."
                )
            self._data = flat

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        names = set(self.settings_cls.model_fields)
        return {k: v for k, v in self._data.items() if k in names}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_default=True,
    )

    # --- reasoning ---
    llm_provider: str = "auto"
    llm_model: str = ""
    llm_fallbacks: StrList = Field(default_factory=list)
    fast_llm_provider: str = "auto"
    fast_llm_model: str = ""
    # --- vision ---
    vision_provider: str = "auto"
    vision_model: str = ""
    # --- voice ---
    stt_provider: str = "auto"
    stt_model: str = ""
    local_stt_model: str = "base"
    tts_provider: str = "auto"
    tts_voice: str = ""
    voice_enabled: bool = False
    wake_word: str = "hey_jarvis"
    wake_word_threshold: float = 0.5
    ptt_hotkey: str = "<ctrl>+<alt>+<space>"
    mic_device: str = ""
    speaker_device: str = ""
    voice_session_timeout: float = 30.0
    max_utterance_seconds: float = 20.0
    voice_approval_window: float = 8.0
    # --- retrieval ---
    embeddings_provider: str = "fastembed"
    embeddings_model: str = "BAAI/bge-small-en-v1.5"
    search_provider: str = "auto"
    searxng_url: str = ""
    memory_backend: Literal["faiss", "fts_only"] = "faiss"
    # --- local models ---
    local_llm_url: str = "http://127.0.0.1:8080"
    local_llm_server_bin: str = ""
    local_llm_model_path: str = ""
    local_llm_ctx: int = 16384
    local_vlm_model_path: str = ""
    local_vlm_mmproj_path: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    # --- control & permissions ---
    require_approval: bool = False
    autonomy_level: int = Field(default=3, ge=0, le=4)
    pc_control_enabled: bool = True
    browser_enabled: bool = True
    terminal_enabled: bool = True
    allowed_roots: StrList = Field(default_factory=list)
    protected_paths: StrList = Field(default_factory=list)
    dry_run: bool = False
    kill_switch_hotkey: str = "<ctrl>+<alt>+<shift>+k"
    approval_timeout: float = 300.0
    bulk_threshold: int = 20
    # --- resources ---
    max_local_vram: int = Field(default=6144, description="MiB of VRAM SCAR may use for local models")
    max_local_ram: int = Field(default=8192, description="MiB of RAM SCAR may use for local models")
    gpu_usage_threshold: int = Field(default=70, ge=0, le=100)
    background_task_policy: Literal["minimal", "balanced", "performance"] = "balanced"
    model_idle_timeout: float = 300.0
    local_inference_policy: Literal["never", "fallback_only", "prefer", "always"] = "fallback_only"
    battery_saver_threshold: int = Field(default=25, ge=0, le=100)
    # --- privacy ---
    privacy_mode: Literal["open", "balanced", "strict"] = "balanced"
    privacy_overrides: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    # --- budgets ---
    max_steps: int = 40
    max_retries: int = 3
    task_timeout: float = 900.0
    task_token_budget: int = 400_000
    command_timeout: float = 120.0
    command_timeout_max: float = 1800.0
    output_cap_bytes: int = 64 * 1024
    max_concurrent_tasks: int = 4
    max_subagents: int = 3
    # --- browser ---
    browser_channel: Literal["auto", "chrome", "msedge", "chromium"] = "auto"
    browser_headless: bool = False
    downloads_dir: str = ""
    # --- integrations (non-secret parts) ---
    google_oauth_client_file: str = ""
    ms_client_id: str = ""
    ms_tenant: str = "common"
    email_provider: Literal["auto", "gmail", "outlook", "imap"] = "auto"
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    calendar_provider: Literal["auto", "google", "outlook", "local"] = "auto"
    telegram_api_id: str = ""
    discord_default_channel: str = ""
    whatsapp_desktop_enabled: bool = False
    whatsapp_phone_number_id: str = ""
    # --- test recipients (live tests only) ---
    test_telegram_chat: str = ""
    test_email_to: str = ""
    test_discord_channel: str = ""
    # --- operations ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    data_dir: str = ""
    models_dir: str = ""  # downloaded speech/embedding/wake-word models; default <data dir>\models
    live_tests: bool = False
    debug: bool = False
    verbose: bool = False
    timezone: str = ""

    _split = field_validator("llm_fallbacks", "allowed_roots", "protected_paths", mode="before")(_split_list)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v

    @field_validator("privacy_overrides", mode="before")
    @classmethod
    def _parse_overrides(cls, v: Any) -> Any:
        if isinstance(v, str):
            result: dict[str, str] = {}
            for part in _split_list(v):
                if "=" not in part:
                    raise ValueError(f"privacy override {part!r} must look like class=policy")
                k, _, val = part.partition("=")
                result[k.strip()] = val.strip()
            return result
        return v

    @model_validator(mode="after")
    def _check(self) -> Settings:
        valid = {"cloud_allowed", "cloud_redacted", "local_only"}
        for cls_name, pol in self.privacy_overrides.items():
            if pol not in valid:
                raise ValueError(f"privacy override for {cls_name!r} must be one of {sorted(valid)}, got {pol!r}")
        if self.command_timeout > self.command_timeout_max:
            raise ValueError("command_timeout cannot exceed command_timeout_max")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        assert isinstance(init_settings, InitSettingsSource)
        assert isinstance(env_settings, EnvSettingsSource)
        assert isinstance(dotenv_settings, DotEnvSettingsSource)
        return (init_settings, env_settings, dotenv_settings, _UserTomlSource(settings_cls, paths.config_file()))

    # ----- derived paths -----
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser() if self.data_dir else paths.default_data_dir()

    @property
    def log_path(self) -> Path:
        return self.data_path / "logs" if self.data_dir else paths.default_log_dir()

    @property
    def models_path(self) -> Path:
        return Path(self.models_dir).expanduser() if self.models_dir else self.data_path / "models"

    @property
    def db_path(self) -> Path:
        return self.data_path / "scar.db"

    @property
    def artifacts_path(self) -> Path:
        return self.data_path / "artifacts"

    @property
    def backups_path(self) -> Path:
        return self.data_path / "backups"

    @property
    def browser_profile_path(self) -> Path:
        return self.data_path / "browser-profile"

    @property
    def downloads_path(self) -> Path:
        return Path(self.downloads_dir).expanduser() if self.downloads_dir else self.data_path / "downloads"

    @property
    def effective_allowed_roots(self) -> list[Path]:
        if self.allowed_roots:
            return [Path(os.path.expandvars(r)).expanduser() for r in self.allowed_roots]
        return [Path.home()]


def load_settings(**overrides: Any) -> Settings:
    """Load settings; ``overrides`` are CLI flags (highest precedence)."""
    clean = {k: v for k, v in overrides.items() if v is not None}
    from pydantic_settings.exceptions import SettingsError

    try:
        return Settings(**clean)
    except SettingsError as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "settings"
            lines.append(f"  {loc}: {err['msg']} (env var SCAR_{loc.upper()})")
        raise ConfigError("invalid configuration:\n" + "\n".join(lines)) from exc
