from __future__ import annotations

from dataclasses import dataclass

from app.auth import hash_password, verify_password
from app.capability_contracts import CapabilityRegistry
from app.capability_service import CapabilityService
from app.conversation_service import ConversationService
from app.context_service import ContextPolicy, ContextService
from app.compreface_identity_provider import CompreFaceIdentityProvider
from app.identity_service import IdentityService
from app.job_service import JobService
from app.memory_service import MemoryService
from app.media_adapters import LocalImageProvider, OpenAIImageProvider, OpenAIVideoProvider
from app.media_catalog_service import MediaCatalogService
from app.media_journal_service import MediaJournalService
from app.media_library_service import MediaLibraryService
from app.pregeneration import PregenerationPolicy
from app.photo_set_service import PhotoSetService
from app.scene_backlog_service import SceneBacklogService
from app.scene_production import SceneProductionRunner
from app.media_service import MediaService
from app.ollama_provider import OllamaChatProvider
from app.operations_service import OperationsService
from app.provider_registry import ProviderRegistry
from app.provider_service import ProviderService
from app.resource_service import ResourceService
from app.resource_coordination import ResourceCoordinator
from app.runtime import AppConfig, AppRuntime
from app.secret_store import SecretStore
from app.security import LoginThrottle, ProviderUrlPolicy
from app.speech_service import SpeechService
from app.task_model_service import TaskModelService
from app.turn_events import TurnEventBroker


@dataclass
class ApplicationServices:
    runtime: AppRuntime
    providers: ProviderRegistry
    resources: ResourceService
    provider_service: ProviderService
    jobs: JobService
    conversations: ConversationService
    context: ContextService
    memory: MemoryService
    media: MediaService
    media_catalog: MediaCatalogService
    media_journal: MediaJournalService
    media_library: MediaLibraryService
    scene_backlog: SceneBacklogService
    photo_sets: PhotoSetService
    scene_production: SceneProductionRunner
    identity: IdentityService
    capabilities: CapabilityService
    task_models: TaskModelService
    speech: SpeechService
    operations: OperationsService
    resource_coordination: ResourceCoordinator
    broker: TurnEventBroker
    login_throttle: LoginThrottle
    provider_url_policy: ProviderUrlPolicy

    def start(self):
        self.runtime.start()
        self.operations.startup_maintenance()
        self.operations.start()
        self.resource_coordination.start()
        self.jobs.start()
        # Last: it asks the job queue whether the machine is busy, so the queue
        # has to be running before the first question is worth anything.
        self.scene_production.start()

    def stop(self):
        self.scene_production.stop()
        self.jobs.stop()
        self.operations.stop()
        self.resource_coordination.stop()
        self.broker.stop()
        self.runtime.stop()


def build_services(
    config: AppConfig,
    *,
    secret_store: SecretStore | None = None,
    providers: ProviderRegistry | None = None,
    identity_providers: dict | None = None,
    resource_providers: dict | None = None,
    password_hasher=hash_password,
    password_verifier=verify_password,
) -> ApplicationServices:
    runtime = AppRuntime(config, secret_store=secret_store)
    provider_url_policy = ProviderUrlPolicy(config.provider_allowed_hosts)
    for label, endpoint in (
        ("Ollama", config.ollama_base_url),
        ("Automatic1111", config.automatic1111_base_url),
        ("ComfyUI", config.comfyui_base_url),
    ):
        provider_url_policy.normalize(endpoint, label=label)
    login_throttle = LoginThrottle(
        max_attempts=config.login_max_attempts,
        window_seconds=config.login_window_seconds,
        lockout_seconds=config.login_lockout_seconds,
    )
    registry = providers or ProviderRegistry(
        chat_providers={
            "ollama": OllamaChatProvider(
                config.ollama_base_url,
                timeout_seconds=config.generation_timeout_seconds,
                health_timeout_seconds=config.provider_timeout_seconds,
                metrics=runtime.metrics,
            )
        },
        media_providers={
            "openai-image": OpenAIImageProvider(),
            "local-image": LocalImageProvider(),
            "openai-video": OpenAIVideoProvider(),
        },
        # No OpenAI task provider is registered. The adapter still exists and is
        # still tested, so a later decision to allow it is a line of wiring
        # rather than a rewrite; what it is not is reachable today.
        task_providers={
            "ollama": OllamaChatProvider(
                config.ollama_base_url,
                timeout_seconds=config.generation_timeout_seconds,
                health_timeout_seconds=config.provider_timeout_seconds,
                metrics=runtime.metrics,
            ),
        },
    )
    broker = TurnEventBroker()
    resource_coordination = ResourceCoordinator(
        runtime.session_factory,
        runtime.secret_store,
        config,
        runtime.logger,
        providers=resource_providers,
        provider_url_policy=provider_url_policy,
    )
    jobs = JobService(
        runtime.session_factory,
        runtime.secret_store,
        broker,
        runtime.logger,
        {"interactive": config.interactive_workers, "media": config.media_workers},
        resource_coordinator=resource_coordination,
        metrics=runtime.metrics,
    )
    identity = IdentityService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        jobs,
        identity_providers if identity_providers is not None else {"compreface": CompreFaceIdentityProvider()},
        runtime.logger,
        provider_url_policy=provider_url_policy,
    )
    media_journal = MediaJournalService(
        runtime.session_factory,
        runtime.secret_store,
        runtime.logger,
        retention_days=config.media_journal_retention_days,
    )
    media_library = MediaLibraryService(
        runtime.session_factory,
        runtime.secret_store,
        runtime.logger,
        entry_limit=config.media_library_entry_limit,
        set_frame_limit=config.media_set_frames_per_reply,
    )
    media = MediaService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        registry,
        identity,
        runtime.logger,
        provider_url_policy=provider_url_policy,
        metrics=runtime.metrics,
        journal=media_journal,
        library=media_library,
    )
    media_catalog = MediaCatalogService(
        runtime.session_factory,
        runtime.secret_store,
        registry,
        runtime.logger,
    )
    context_policy = ContextPolicy(
        default_context_window_tokens=config.default_context_window_tokens,
        summary_trigger_ratio=config.context_summary_trigger_ratio,
        max_compaction_passes=config.context_max_compaction_passes,
    )
    resources = ResourceService(
        runtime.session_factory,
        runtime.secret_store,
        allow_public_signup=config.allow_public_signup,
        session_ttl_seconds=config.session_ttl_seconds,
        password_hasher=password_hasher,
        password_verifier=password_verifier,
        persona_delete_hook=identity.prepare_persona_deletion,
        provider_url_policy=provider_url_policy,
        media_catalog=media_catalog,
        context_policy=context_policy,
    )
    provider_service = ProviderService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        registry,
        runtime.logger,
        provider_url_policy=provider_url_policy,
    )
    task_models = TaskModelService(
        runtime.session_factory,
        runtime.secret_store,
        registry,
        runtime.logger,
    )
    scene_backlog = SceneBacklogService(
        runtime.session_factory,
        runtime.secret_store,
        runtime.logger,
        task_models=task_models,
        jobs=jobs,
        policy=PregenerationPolicy(
            enabled=config.pregeneration_enabled,
            start_hour=config.pregeneration_start_hour,
            end_hour=config.pregeneration_end_hour,
            max_per_run=config.pregeneration_max_per_run,
        ),
    )
    capabilities = CapabilityService(
        runtime.session_factory,
        runtime.secret_store,
        CapabilityRegistry(),
        jobs,
        media,
        media_catalog,
        runtime.logger,
        provider_url_policy=provider_url_policy,
        provider_service=provider_service,
        identity_service=identity,
        task_models=task_models,
    )
    context = ContextService(
        runtime.session_factory,
        runtime.secret_store,
        context_policy,
        task_models,
    )
    memory = MemoryService(
        runtime.session_factory,
        runtime.secret_store,
        task_models,
        jobs,
        runtime.logger,
        config.memory_candidate_limit,
        config.memory_candidate_min_confidence,
    )
    conversations = ConversationService(
        runtime.session_factory,
        runtime.secret_store,
        registry,
        jobs,
        broker,
        config.generation_timeout_seconds,
        context,
        memory,
        capabilities,
        task_models,
    )
    speech = SpeechService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        runtime.logger,
        provider_url_policy=provider_url_policy,
        metrics=runtime.metrics,
    )
    operations = OperationsService(config, runtime.logger, memory_maintenance=memory.prune_discarded)
    # Built after the capability service exists, because producing a scene goes
    # through the same request path a conversational picture does.
    scene_backlog.capabilities = capabilities
    photo_sets = PhotoSetService(
        runtime.session_factory,
        runtime.secret_store,
        runtime.logger,
        capabilities=capabilities,
        jobs=jobs,
    )
    scene_production = SceneProductionRunner(
        scene_backlog,
        runtime.logger,
        interval_seconds=config.pregeneration_poll_seconds,
        enabled=config.pregeneration_enabled,
    )
    return ApplicationServices(
        runtime=runtime,
        providers=registry,
        resources=resources,
        provider_service=provider_service,
        jobs=jobs,
        conversations=conversations,
        context=context,
        memory=memory,
        media=media,
        media_catalog=media_catalog,
        media_journal=media_journal,
        media_library=media_library,
        scene_backlog=scene_backlog,
        photo_sets=photo_sets,
        scene_production=scene_production,
        identity=identity,
        capabilities=capabilities,
        task_models=task_models,
        speech=speech,
        operations=operations,
        resource_coordination=resource_coordination,
        broker=broker,
        login_throttle=login_throttle,
        provider_url_policy=provider_url_policy,
    )
