"""Provider-neutral request/response types for chat, vision, speech and search."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ImagePart(BaseModel):
    data: bytes
    mime: str = "image/png"

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    images: list[ImagePart] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] = Field(default_factory=list)  # OpenAI function schemas
    tool_choice: str | None = None  # "auto" | "required" | "none"
    json_schema: dict[str, Any] | None = None
    json_schema_name: str = "response"
    temperature: float = 0.2
    max_tokens: int = 2048
    reasoning_effort: str | None = None

    def approx_tokens(self) -> int:
        chars = sum(len(m.content) + sum(len(tc.raw_arguments) for tc in m.tool_calls) for m in self.messages)
        chars += sum(len(str(t)) for t in self.tools)
        images = sum(len(m.images) for m in self.messages)
        return chars // 4 + images * 1100 + 16

    @property
    def has_images(self) -> bool:
        return any(m.images for m in self.messages)


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = Field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0


class ChatClient(Protocol):
    provider: str

    async def chat(self, model: str, request: ChatRequest) -> ChatResponse: ...

    async def list_models(self) -> list[str] | None: ...

    async def aclose(self) -> None: ...


@dataclass
class Transcription:
    text: str
    language: str | None = None
    duration_s: float | None = None
    provider: str = ""
    model: str = ""


@dataclass
class SpeechAudio:
    """Synthesised speech: PCM16 mono samples, or an encoded file for players."""

    pcm16: bytes = b""
    sample_rate: int = 24000
    encoded: bytes = b""
    encoding: str = ""  # "mp3", "wav" ...
    provider: str = ""
    played_directly: bool = False  # provider played the audio itself (SAPI)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
