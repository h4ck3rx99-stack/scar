"""Constructs the core service graph (used by the runtime and by tests)."""

from __future__ import annotations

from pathlib import Path

from scar.agent.clarify import QuestionBroker
from scar.config import paths as cfg_paths
from scar.config.settings import Settings
from scar.core.artifacts import ArtifactStore
from scar.core.events import EventBus
from scar.core.ids import new_id
from scar.observability.metrics import Metrics
from scar.observability.tracing import Tracer
from scar.providers.capabilities import Catalog
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
from scar.runtime.services import Services
from scar.security.approval import ApprovalBroker
from scar.security.audit import AuditLog
from scar.security.command_guard import CommandGuard
from scar.security.grants import GrantStore
from scar.security.killswitch import KillSwitch
from scar.security.path_guard import PathGuard
from scar.security.policy import PolicyEngine
from scar.security.privacy import EgressTracker, PrivacyPolicy
from scar.security.ps_parser import PowerShellParser
from scar.security.secrets import SecretStore
from scar.storage.db import Database


def build_services(
    settings: Settings,
    *,
    secrets: SecretStore | None = None,
    policy_file: Path | None = None,
    providers_file: Path | None = None,
    session_id: str | None = None,
) -> Services:
    data = settings.data_path
    data.mkdir(parents=True, exist_ok=True)
    secrets = secrets or SecretStore()
    secrets.preload_for_redaction()
    session = session_id or new_id("sess")
    db = Database(settings.db_path)
    bus = EventBus()
    artifacts = ArtifactStore(settings.artifacts_path)
    metrics = Metrics(db)
    tracer = Tracer(settings.log_path / "traces")
    tracer.attach(bus)
    audit = AuditLog(db)
    path_guard = PathGuard(settings.effective_allowed_roots, settings.protected_paths, scar_data_dir=data,
                           scar_config_dir=cfg_paths.config_dir())
    command_guard = CommandGuard(path_guard, PowerShellParser())
    grants = GrantStore(db, session)
    policy = PolicyEngine.load(grants, policy_file)
    approvals = ApprovalBroker(bus, grants, audit, settings.approval_timeout)
    privacy = PrivacyPolicy(settings.privacy_mode, dict(settings.privacy_overrides))
    egress = EgressTracker(db, session)
    monitor = ResourceMonitor(interval=10.0)
    admission = AdmissionController(settings, monitor)
    processes = ProcessManager()
    local = LocalModelManager(settings, admission, processes)
    health = HealthTracker(db)
    catalog = Catalog.load(providers_file)
    router = ProviderRouter(settings, secrets, health, bus, privacy, egress, local, catalog)
    stt = SttService(settings, secrets, health, bus, privacy, egress, local, catalog)
    tts = TtsService(settings, secrets, health, bus, privacy, egress, local, catalog)
    embeddings = EmbeddingService(settings, local)
    search = SearchService(settings, secrets, health, bus, egress, catalog)
    killswitch = KillSwitch(settings.kill_switch_hotkey)
    services = Services(
        settings=settings, secrets=secrets, db=db, bus=bus, artifacts=artifacts, metrics=metrics, tracer=tracer,
        audit=audit, path_guard=path_guard, command_guard=command_guard, grants=grants, policy=policy,
        approvals=approvals, privacy=privacy, egress=egress, monitor=monitor, admission=admission,
        processes=processes, local_models=local, health=health, router=router, stt=stt, tts=tts,
        embeddings=embeddings, search=search, killswitch=killswitch, session_id=session,
    )
    services.questions = QuestionBroker(bus)
    return services
