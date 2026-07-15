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
        self.resource_coordination.start()
        self.jobs.start()

    def stop(self):
        self.jobs.stop()
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
    media = MediaService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        registry,
        identity,
        runtime.logger,
        provider_url_policy=provider_url_policy,
        metrics=runtime.metrics,
    )
    media_catalog = MediaCatalogService(
        runtime.session_factory,
        runtime.secret_store,
        registry,
        runtime.logger,
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
    )
    task_models = TaskModelService(
        runtime.session_factory,
        runtime.secret_store,
        registry,
        runtime.logger,
    )
    context = ContextService(
        runtime.session_factory,
        runtime.secret_store,
        ContextPolicy(
            default_context_window_tokens=config.default_context_window_tokens,
            summary_trigger_ratio=config.context_summary_trigger_ratio,
            max_compaction_passes=config.context_max_compaction_passes,
        ),
        task_models,
    )
    memory = MemoryService(
        runtime.session_factory,
        runtime.secret_store,
        task_models,
        jobs,
        runtime.logger,
        config.memory_candidate_limit,
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
    provider_service = ProviderService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        registry,
        runtime.logger,
        provider_url_policy=provider_url_policy,
    )
    speech = SpeechService(
        runtime.session_factory,
        runtime.secret_store,
        config,
        runtime.logger,
        provider_url_policy=provider_url_policy,
        metrics=runtime.metrics,
    )
    operations = OperationsService(config, runtime.logger)
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
