"""Service container handed to tools and agents (frozen interface).

The composition root (``scar.runtime.runtime``) constructs every service and
fills this container. Tools access what they need through ``ctx.services``.
Optional services are ``None`` when their subsystem failed to start; tools
must then return ``CapabilityUnavailable`` rather than crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scar.config.settings import Settings
    from scar.core.artifacts import ArtifactStore
    from scar.core.events import EventBus
    from scar.observability.metrics import Metrics
    from scar.observability.tracing import Tracer
    from scar.providers.embeddings.service import EmbeddingService
    from scar.providers.health import HealthTracker
    from scar.providers.local.model_lifecycle import LocalModelManager
    from scar.providers.router import ProviderRouter
    from scar.providers.search.service import SearchService
    from scar.providers.stt.service import SttService
    from scar.providers.tts.service import TtsService
    from scar.resources.admission import AdmissionController
    from scar.resources.monitor import ResourceMonitor
    from scar.runtime.jobs import ProcessManager
    from scar.security.approval import ApprovalBroker
    from scar.security.audit import AuditLog
    from scar.security.command_guard import CommandGuard
    from scar.security.grants import GrantStore
    from scar.security.killswitch import KillSwitch
    from scar.security.path_guard import PathGuard
    from scar.security.policy import PolicyEngine
    from scar.security.privacy import EgressTracker, PrivacyPolicy
    from scar.security.secrets import SecretStore
    from scar.storage.db import Database


@dataclass
class Services:
    settings: Settings
    secrets: SecretStore
    db: Database
    bus: EventBus
    artifacts: ArtifactStore
    metrics: Metrics
    tracer: Tracer
    audit: AuditLog
    path_guard: PathGuard
    command_guard: CommandGuard
    grants: GrantStore
    policy: PolicyEngine
    approvals: ApprovalBroker
    privacy: PrivacyPolicy
    egress: EgressTracker
    monitor: ResourceMonitor
    admission: AdmissionController
    processes: ProcessManager
    local_models: LocalModelManager
    health: HealthTracker
    router: ProviderRouter
    stt: SttService
    tts: TtsService
    embeddings: EmbeddingService
    search: SearchService
    killswitch: KillSwitch
    session_id: str
    # subsystems built after the core (set by the composition root)
    memory: Any = None  # scar.memory.store.MemoryStore
    contacts: Any = None  # scar.tools.contacts.resolver.ContactResolver
    app_index: Any = None  # scar.tools.apps.index.AppIndex
    uia: Any = None  # scar.tools.windows.uia.UiaWorker
    ocr: Any = None  # scar.tools.screen.ocr.OcrEngine
    vision: Any = None  # scar.tools.vision.service.VisionService
    browser: Any = None  # scar.tools.browser.manager.BrowserManager
    scheduler: Any = None  # scar.tools.scheduler.service.Scheduler
    monitors: Any = None  # scar.tools.monitor.service.MonitorService
    notifier: Any = None  # scar.tools.notify.service.Notifier
    devservers: Any = None  # scar.tools.dev.devserver.DevServerManager
    email: Any = None  # scar.integrations.email.EmailService
    messaging: Any = None  # scar.integrations.messaging.MessagingService
    calendar: Any = None  # scar.integrations.calendar.CalendarService
    github: Any = None  # scar.tools.dev.github.GitHubClient
    tasks: Any = None  # scar.agent.runner.TaskManager
    voice: Any = None  # scar.voice.session.VoiceSession
    questions: Any = None  # scar.agent.clarify.QuestionBroker
    extras: dict[str, Any] = field(default_factory=dict)
