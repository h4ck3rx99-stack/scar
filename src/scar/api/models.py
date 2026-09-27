"""App API v1 data shapes. These models are the contract: the TypeScript types in app/src/api/types.ts are generated
from their JSON Schema (scripts/gen_api_types.py) and a test fails if the two drift."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ requests
class SubmitTask(_Model):
    objective: str = Field(min_length=1, max_length=8000)
    origin: Literal["text", "voice"] = "text"
    background: bool = False
    autonomy: int | None = Field(None, ge=0, le=4)
    dry_run: bool | None = None


class ApprovalAnswer(_Model):
    """A user's decision on one pending approval. ``args_hash`` must match the pending request (the UI echoes what it
    displayed), and ``user_gesture`` records that it came from a trusted click/keypress on the approval card."""

    response: Literal["allow_once", "allow_task", "allow_session", "allow_timed", "allow_always", "deny", "deny_always"]
    args_hash: str = Field(min_length=8, max_length=128)
    typed_confirmation: str | None = Field(None, max_length=64)
    user_gesture: bool = False


class QuestionAnswer(_Model):
    text: str = Field(max_length=4000)


class SettingsPatch(_Model):
    values: dict[str, Any] = Field(min_length=1, max_length=40)


class SecretValue(_Model):
    value: str = Field(min_length=1, max_length=4096)


class MemoryEdit(_Model):
    text: str = Field(min_length=1, max_length=4000)


class WipeMemory(_Model):
    confirm: Literal["FORGET EVERYTHING"]


class VoiceCommand(_Model):
    action: Literal["start", "stop", "push_to_talk", "mute", "unmute", "stop_speaking"]
    mode: Literal["ptt", "wake"] | None = None


class Subscribe(_Model):
    """Sent on the event stream to opt into high-rate topics (only while a screen that shows them is open)."""

    op: Literal["subscribe", "unsubscribe"]
    topics: list[Literal["resources", "mic_level"]]


# ------------------------------------------------------------------ responses
class Hello(_Model):
    api_version: str
    app_version: str
    pid: int
    data_dir: str


class ProviderSummary(_Model):
    level: Literal["cloud", "local", "degraded", "none"]
    summary: str  # plain words for the status panel
    retry_in_s: int | None = None


class ApprovalView(_Model):
    request_id: str
    task_id: str | None
    tool: str
    args_hash: str
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str
    reason: str
    details: dict[str, Any]
    critical: bool
    grantable: bool
    confirmation_code: str
    allowed_responses: list[str]
    created_at: str
    expires_at: str


class TaskView(_Model):
    task_id: str
    objective: str
    status: str
    result_summary: str
    verified: bool | None
    verification_note: str = ""
    failed_checks: list[str] = Field(default_factory=list)
    created_at: str | None = None
    finished_at: str | None = None
    background: bool = False
    origin: str = "text"


class StepView(_Model):
    tool: str
    title: str  # plain-language line ("Running tests")
    status: str
    summary: str
    risk: str = ""
    decision: str = ""
    duration_ms: int | None = None
    verified: bool | None = None


class TaskDetail(TaskView):
    steps: list[StepView] = Field(default_factory=list)


class Snapshot(_Model):
    """Everything a freshly connected client needs to render the current state (sent first on the event stream)."""

    state: Literal["ready", "working", "listening", "waiting", "speaking", "offline"]
    provider: ProviderSummary
    pending_approvals: list[ApprovalView]
    running_tasks: list[TaskView]
    voice: dict[str, Any]
    game_mode: bool
    killswitch_hotkey: str


class EventEnvelope(_Model):
    v: Literal[1] = 1
    seq: int
    type: Literal["snapshot", "event", "error"]
    event: dict[str, Any] | None = None
    snapshot: Snapshot | None = None


SCHEMA_MODELS: list[type[BaseModel]] = [
    SubmitTask, ApprovalAnswer, QuestionAnswer, SettingsPatch, SecretValue, MemoryEdit, WipeMemory, VoiceCommand,
    Subscribe, Hello, ProviderSummary, ApprovalView, TaskView, StepView, TaskDetail, Snapshot, EventEnvelope,
]
