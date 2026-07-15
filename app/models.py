from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Session(Base):
    __tablename__ = "sessions"
    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Persona(Base):
    __tablename__ = "personas"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    personality_details: Mapped[str | None] = mapped_column(Text)
    traits_json: Mapped[str] = mapped_column(Text, default="{}")
    default_model: Mapped[str | None] = mapped_column(Text)
    preferred_voice: Mapped[str | None] = mapped_column(Text)
    preferred_tts_model: Mapped[str | None] = mapped_column(Text)
    preferred_tts_speed: Mapped[str | None] = mapped_column(Text)
    preferred_voice_openai: Mapped[str | None] = mapped_column(Text)
    preferred_tts_model_openai: Mapped[str | None] = mapped_column(Text)
    preferred_tts_speed_openai: Mapped[str | None] = mapped_column(Text)
    preferred_voice_local: Mapped[str | None] = mapped_column(Text)
    preferred_tts_model_local: Mapped[str | None] = mapped_column(Text)
    preferred_tts_speed_local: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonaWorkspaceLink(Base):
    __tablename__ = "persona_workspace_links"
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)


class Chat(Base):
    __tablename__ = "chats"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    persona_id: Mapped[str | None] = mapped_column(ForeignKey("personas.id", ondelete="SET NULL"))
    model_override: Mapped[str | None] = mapped_column(Text)
    memory_mode: Mapped[str] = mapped_column(Text, default="saved")
    title: Mapped[str | None] = mapped_column(Text)
    hidden_in_ui: Mapped[int] = mapped_column(Integer, default=0)
    last_turn_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_conversation_turns_status",
        ),
        Index("idx_conversation_turns_user_chat", "user_id", "chat_id", "created_at"),
        Index("idx_conversation_turns_user_status", "user_id", "status", "created_at"),
        UniqueConstraint("chat_id", "sequence_number", name="uq_conversation_turns_chat_sequence"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    assistant_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), unique=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)
    context_summary_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_summaries.id", ondelete="SET NULL"))
    context_window_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_budget_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens_estimated: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens_actual: Mapped[int | None] = mapped_column(Integer)
    included_message_count: Mapped[int | None] = mapped_column(Integer)
    omitted_message_count: Mapped[int | None] = mapped_column(Integer)
    included_memory_count: Mapped[int | None] = mapped_column(Integer)
    omitted_memory_count: Mapped[int | None] = mapped_column(Integer)
    context_degraded_reason: Mapped[str | None] = mapped_column(Text)


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        Index("idx_conversation_summaries_user_chat", "user_id", "chat_id", "created_at"),
        UniqueConstraint("chat_id", "sequence_number", name="uq_conversation_summaries_chat_sequence"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False)
    previous_summary_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_summaries.id", ondelete="SET NULL")
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    through_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_digest: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("tier IN ('global','workspace','persona','chat')", name="ck_memories_tier"),
        CheckConstraint(
            "status IN ('pending','active','rejected','forgotten','superseded')",
            name="ck_memories_status",
        ),
        CheckConstraint(
            "source_type IN ('legacy','manual','conversation','edit')",
            name="ck_memories_source_type",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_memories_confidence",
        ),
        Index("idx_memories_user_status_updated", "user_id", "status", "updated_at"),
        Index("idx_memories_user_scope_status", "user_id", "tier", "tier_ref_id", "status"),
        Index("idx_memories_source_turn", "source_turn_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    tier_ref_id: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    source_type: Mapped[str] = mapped_column(Text, default="legacy", nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    source_turn_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_turns.id", ondelete="SET NULL"))
    confidence: Mapped[float | None] = mapped_column(Float)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id", ondelete="SET NULL"))
    extractor_provider: Mapped[str | None] = mapped_column(Text)
    extractor_model: Mapped[str | None] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[int | None] = mapped_column(Integer)
    forgotten_at: Mapped[int | None] = mapped_column(Integer)


class MemoryEvent(Base):
    __tablename__ = "memory_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('migrated','created','candidate_created','approved','rejected','forgotten','edited',"
            "'superseded','scope_archived','undo_edit','undo_approved','undo_rejected','undo_forgotten')",
            name="ck_memory_events_action",
        ),
        Index("idx_memory_events_memory_created", "memory_id", "created_at"),
        Index("idx_memory_events_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False)
    related_memory_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    undone_at: Mapped[int | None] = mapped_column(Integer)


class AppSetting(Base):
    __tablename__ = "app_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    global_default_model: Mapped[str | None] = mapped_column(Text)
    default_memory_mode: Mapped[str] = mapped_column(Text, default="saved")
    stt_provider: Mapped[str] = mapped_column(Text, default="disabled")
    tts_provider: Mapped[str] = mapped_column(Text, default="disabled")
    tts_format: Mapped[str] = mapped_column(Text, default="wav")
    openai_api_key: Mapped[str | None] = mapped_column(Text)
    openai_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    onboarding_done: Mapped[int] = mapped_column(Integer, default=0)
    preferences_json: Mapped[str] = mapped_column(Text, default="{}")


class SettingValue(Base):
    __tablename__ = "setting_values"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_setting_values_user_key"),
        Index("idx_setting_values_user", "user_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class TaskModelProfile(Base):
    __tablename__ = "task_model_profiles"
    __table_args__ = (
        CheckConstraint(
            "role IN ('title_generation','conversation_summary','memory_extraction','capability_planning')",
            name="ck_task_model_profiles_role",
        ),
        CheckConstraint("enabled IN (0,1)", name="ck_task_model_profiles_enabled"),
        CheckConstraint(
            "fallback_policy IN ('deterministic','skip','fail')",
            name="ck_task_model_profiles_fallback_policy",
        ),
        CheckConstraint("max_input_tokens BETWEEN 128 AND 262144", name="ck_task_model_profiles_input_budget"),
        CheckConstraint("max_output_tokens BETWEEN 16 AND 8192", name="ck_task_model_profiles_output_budget"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 600", name="ck_task_model_profiles_timeout"),
        CheckConstraint("temperature BETWEEN 0 AND 2", name="ck_task_model_profiles_temperature"),
        UniqueConstraint("user_id", "role", name="uq_task_model_profiles_user_role"),
        Index("idx_task_model_profiles_user", "user_id", "role"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    fallback_provider: Mapped[str | None] = mapped_column(Text)
    fallback_model: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    fallback_policy: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class TaskModelRun(Base):
    __tablename__ = "task_model_runs"
    __table_args__ = (
        CheckConstraint(
            "role IN ('title_generation','conversation_summary','memory_extraction','capability_planning')",
            name="ck_task_model_runs_role",
        ),
        CheckConstraint(
            "status IN ('running','completed','fallback','failed')",
            name="ck_task_model_runs_status",
        ),
        CheckConstraint("fallback_used IN (0,1)", name="ck_task_model_runs_fallback_used"),
        Index("idx_task_model_runs_user_started", "user_id", "started_at"),
        Index("idx_task_model_runs_user_role", "user_id", "role", "started_at"),
        Index("idx_task_model_runs_turn", "turn_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_turns.id", ondelete="SET NULL"))
    requested_provider: Mapped[str | None] = mapped_column(Text)
    requested_model: Mapped[str | None] = mapped_column(Text)
    executed_provider: Mapped[str | None] = mapped_column(Text)
    executed_model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    input_tokens_estimated: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens_estimated: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(Integer)


class MediaCatalogSetting(Base):
    __tablename__ = "media_catalog_settings"
    __table_args__ = (
        CheckConstraint("vram_budget_mb BETWEEN 0 AND 131072", name="ck_media_catalog_vram_budget"),
        CheckConstraint("max_loras BETWEEN 0 AND 8", name="ck_media_catalog_max_loras"),
        CheckConstraint("legacy_imported IN (0,1)", name="ck_media_catalog_legacy_imported"),
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    vram_budget_mb: Mapped[int] = mapped_column(Integer, default=10240, nullable=False)
    max_loras: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    legacy_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class MediaCatalogResource(Base):
    __tablename__ = "media_catalog_resources"
    __table_args__ = (
        CheckConstraint("resource_type IN ('model','lora','workflow')", name="ck_media_resource_type"),
        CheckConstraint("kind IN ('image','video')", name="ck_media_resource_kind"),
        CheckConstraint(
            "provider_key IN ('openai-image','local-image','openai-video')",
            name="ck_media_resource_provider",
        ),
        CheckConstraint(
            "backend IN ('openai','automatic1111','comfyui')",
            name="ck_media_resource_backend",
        ),
        CheckConstraint("enabled IN (0,1)", name="ck_media_resource_enabled"),
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_media_resource_priority"),
        CheckConstraint("estimated_vram_mb BETWEEN 0 AND 131072", name="ck_media_resource_vram"),
        CheckConstraint("estimated_load_seconds BETWEEN 0 AND 3600", name="ck_media_resource_load"),
        UniqueConstraint(
            "user_id", "resource_type", "provider_key", "backend", "external_id", name="uq_media_resource_external"
        ),
        Index("idx_media_resources_user_enabled", "user_id", "enabled", "kind"),
        Index("idx_media_resources_user_type", "user_id", "resource_type", "kind"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_key: Mapped[str] = mapped_column(Text, nullable=False)
    backend: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    operations_json: Mapped[str] = mapped_column(Text, default='["generate"]', nullable=False)
    domains_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    content_tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    features_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    estimated_vram_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_load_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    default_settings_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class MediaResourceCompatibility(Base):
    __tablename__ = "media_resource_compatibility"
    __table_args__ = (
        UniqueConstraint("resource_id", "model_resource_id", name="uq_media_resource_compatibility"),
        CheckConstraint("resource_id <> model_resource_id", name="ck_media_resource_not_self_compatible"),
        Index("idx_media_compatibility_model", "model_resource_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("media_catalog_resources.id", ondelete="CASCADE"), nullable=False
    )
    model_resource_id: Mapped[str] = mapped_column(
        ForeignKey("media_catalog_resources.id", ondelete="CASCADE"), nullable=False
    )


class MediaExecutionPlan(Base):
    __tablename__ = "media_execution_plans"
    __table_args__ = (
        CheckConstraint("source IN ('coordinator','manual')", name="ck_media_plan_source"),
        CheckConstraint("status IN ('ready','blocked')", name="ck_media_plan_status"),
        CheckConstraint("kind IN ('image','video')", name="ck_media_plan_kind"),
        Index("idx_media_plans_user_created", "user_id", "created_at"),
        Index("idx_media_plans_capability", "capability_request_id"),
        Index("idx_media_plans_persona_created", "user_id", "persona_id", "created_at"),
        Index("idx_media_plans_identity_reference", "identity_reference_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    capability_request_id: Mapped[str] = mapped_column(
        ForeignKey("capability_requests.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    requirements_json: Mapped[str] = mapped_column(Text, nullable=False)
    selected_resources_json: Mapped[str] = mapped_column(Text, nullable=False)
    execution_options_json: Mapped[str] = mapped_column(Text, nullable=False)
    explanation_json: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_vram_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    block_code: Mapped[str | None] = mapped_column(Text)
    block_message: Mapped[str | None] = mapped_column(Text)
    persona_id: Mapped[str | None] = mapped_column(Text)
    identity_profile_id: Mapped[str | None] = mapped_column(Text)
    identity_profile_revision: Mapped[int | None] = mapped_column(Integer)
    identity_reference_id: Mapped[str | None] = mapped_column(Text)
    identity_reference_sha256: Mapped[str | None] = mapped_column(Text)
    identity_conditioning_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class AudioFile(Base):
    __tablename__ = "audio_files"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    persona_id: Mapped[str | None] = mapped_column(ForeignKey("personas.id", ondelete="SET NULL"))
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    format: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (
        Index("idx_media_files_kind_filename", "kind", "filename"),
        Index("idx_media_files_generation_plan", "generation_plan_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    generation_plan_id: Mapped[str | None] = mapped_column(ForeignKey("media_execution_plans.id", ondelete="SET NULL"))
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class IdentityValidationSetting(Base):
    __tablename__ = "identity_validation_settings"
    __table_args__ = (
        CheckConstraint("provider IN ('disabled','compreface')", name="ck_identity_settings_provider"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 120", name="ck_identity_settings_timeout"),
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="disabled")
    base_url: Mapped[str | None] = mapped_column(Text)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=15.0)
    last_validation_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonaVisualIdentity(Base):
    __tablename__ = "persona_visual_identities"
    __table_args__ = (
        CheckConstraint("status IN ('draft','active','disabled')", name="ck_visual_identity_status"),
        CheckConstraint(
            "consent_status IN ('not_granted','granted','withdrawn')",
            name="ck_visual_identity_consent",
        ),
        CheckConstraint("acceptance_threshold BETWEEN 0 AND 1", name="ck_visual_identity_threshold"),
        CheckConstraint("max_generation_attempts BETWEEN 1 AND 10", name="ck_visual_identity_attempts"),
        CheckConstraint(
            "failure_policy IN ('block_claim','show_unverified')",
            name="ck_visual_identity_failure_policy",
        ),
        UniqueConstraint("user_id", "persona_id", name="uq_visual_identity_owner_persona"),
        Index("idx_visual_identity_owner_status", "user_id", "status"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    consent_status: Mapped[str] = mapped_column(Text, nullable=False, default="not_granted")
    appearance_description: Mapped[str | None] = mapped_column(Text)
    acceptance_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.78)
    max_generation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    failure_policy: Mapped[str] = mapped_column(Text, nullable=False, default="block_claim")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_validation_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consent_granted_at: Mapped[int | None] = mapped_column(Integer)
    consent_withdrawn_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonaIdentityReference(Base):
    __tablename__ = "persona_identity_references"
    __table_args__ = (
        CheckConstraint(
            "provenance IN ('user_upload','generated_approved','imported')",
            name="ck_identity_reference_provenance",
        ),
        CheckConstraint(
            "review_status IN ('pending','approved','rejected','deleted')",
            name="ck_identity_reference_review",
        ),
        CheckConstraint("is_primary IN (0,1)", name="ck_identity_reference_primary"),
        Index("idx_identity_reference_profile_status", "identity_id", "review_status", "created_at"),
        Index("idx_identity_reference_owner_persona", "user_id", "persona_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        ForeignKey("persona_visual_identities.id", ondelete="CASCADE"), nullable=False
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    source_media_id: Mapped[str | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    filename: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    is_primary: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consent_attested_at: Mapped[int] = mapped_column(Integer, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[int | None] = mapped_column(Integer)


class PersonaIdentityValidation(Base):
    __tablename__ = "persona_identity_validations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','passed','failed','error','cancelled')",
            name="ck_identity_validation_status",
        ),
        CheckConstraint(
            "failure_policy IN ('block_claim','show_unverified')",
            name="ck_identity_validation_failure_policy",
        ),
        Index("idx_identity_validation_owner_persona", "user_id", "persona_id", "created_at"),
        Index("idx_identity_validation_candidate", "candidate_media_id", "created_at"),
        Index("idx_identity_validation_candidate_order", "candidate_media_id", "created_order"),
        UniqueConstraint("job_id", name="uq_identity_validation_job"),
        UniqueConstraint("identity_id", "sequence_number", name="uq_identity_validation_sequence"),
        UniqueConstraint("user_id", "created_order", name="uq_identity_validation_owner_order"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        ForeignKey("persona_visual_identities.id", ondelete="CASCADE"), nullable=False
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    candidate_media_id: Mapped[str] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_order: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("async_jobs.id", ondelete="SET NULL"))
    matched_reference_id: Mapped[str | None] = mapped_column(
        ForeignKey("persona_identity_references.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failure_policy: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    source_face_count: Mapped[int | None] = mapped_column(Integer)
    target_face_count: Mapped[int | None] = mapped_column(Integer)
    provider_version: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)


class PersonaIdentityEvent(Base):
    __tablename__ = "persona_identity_events"
    __table_args__ = (
        Index("idx_identity_event_profile_created", "identity_id", "created_at"),
        Index("idx_identity_event_owner_created", "user_id", "created_at"),
        UniqueConstraint("identity_id", "sequence_number", name="uq_identity_event_sequence"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    identity_id: Mapped[str] = mapped_column(
        ForeignKey("persona_visual_identities.id", ondelete="CASCADE"), nullable=False
    )
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id", ondelete="CASCADE"), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(ForeignKey("persona_identity_references.id", ondelete="SET NULL"))
    validation_id: Mapped[str | None] = mapped_column(
        ForeignKey("persona_identity_validations.id", ondelete="SET NULL")
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class MediaGenerationAttempt(Base):
    __tablename__ = "media_generation_attempts"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('generate','inpaint','outpaint','image_to_image')",
            name="ck_media_attempt_operation",
        ),
        CheckConstraint(
            "status IN ('running','passed','failed','unverified','error','cancelled')",
            name="ck_media_attempt_status",
        ),
        CheckConstraint("attempt_number BETWEEN 1 AND 10", name="ck_media_attempt_number"),
        UniqueConstraint("media_plan_id", "attempt_number", name="uq_media_attempt_plan_number"),
        Index("idx_media_attempt_owner_started", "user_id", "started_at"),
        Index("idx_media_attempt_plan_status", "media_plan_id", "status", "attempt_number"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    media_plan_id: Mapped[str] = mapped_column(
        ForeignKey("media_execution_plans.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    media_id: Mapped[str | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    validation_id: Mapped[str | None] = mapped_column(
        ForeignKey("persona_identity_validations.id", ondelete="SET NULL")
    )
    source_media_id: Mapped[str | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    workflow_resource_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_catalog_resources.id", ondelete="SET NULL")
    )
    score: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(Integer)


class ResourceCoordinationSetting(Base):
    __tablename__ = "resource_coordination_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_resource_coordination_singleton"),
        CheckConstraint("mode IN ('disabled','observe','managed')", name="ck_resource_coordination_mode"),
        CheckConstraint("reserve_vram_mb BETWEEN 0 AND 131072", name="ck_resource_coordination_reserve"),
        CheckConstraint("max_wait_seconds BETWEEN 1 AND 3600", name="ck_resource_coordination_wait"),
        CheckConstraint("poll_interval_seconds BETWEEN 0.25 AND 60", name="ck_resource_coordination_poll"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="disabled")
    reserve_vram_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    max_wait_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    poll_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class ResourceControlAuthorization(Base):
    __tablename__ = "resource_control_authorizations"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('ollama','comfyui','automatic1111')",
            name="ck_resource_control_provider",
        ),
        CheckConstraint("exclusive_control IN (0,1)", name="ck_resource_control_exclusive"),
        CheckConstraint("allow_release IN (0,1)", name="ck_resource_control_release"),
        UniqueConstraint("provider", "endpoint_fingerprint", name="uq_resource_control_endpoint"),
        Index("idx_resource_control_provider", "provider", "updated_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    exclusive_control: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_release: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    authorized_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)


class ResourceCoordinationEvent(Base):
    __tablename__ = "resource_coordination_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('waiting','admitted','released','release_failed','timed_out','cancelled')",
            name="ck_resource_coordination_event_action",
        ),
        CheckConstraint(
            "outcome IN ('info','success','failed','cancelled')",
            name="ck_resource_coordination_event_outcome",
        ),
        Index("idx_resource_coordination_events_created", "created_at"),
        Index("idx_resource_coordination_events_job", "job_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("async_jobs.id", ondelete="SET NULL"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class CapabilityRequest(Base):
    __tablename__ = "capability_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_confirmation','queued','running','completed','failed','cancelled','denied','expired')",
            name="ck_capability_requests_status",
        ),
        CheckConstraint(
            "permission_mode IN ('confirm','explicit')",
            name="ck_capability_requests_permission_mode",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_capability_requests_user_idempotency"),
        Index("idx_capability_requests_user_chat", "user_id", "chat_id", "requested_at"),
        Index("idx_capability_requests_user_status", "user_id", "status", "requested_at"),
        Index("idx_capability_requests_turn", "turn_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_turns.id", ondelete="SET NULL"))
    capability_key: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    permission_mode: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_at: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[int | None] = mapped_column(Integer)


class CapabilityEvent(Base):
    __tablename__ = "capability_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('requested','approved','denied','queued','started','completed','failed','cancelled','expired')",
            name="ck_capability_events_action",
        ),
        Index("idx_capability_events_request_created", "capability_request_id", "created_at"),
        Index("idx_capability_events_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    capability_request_id: Mapped[str] = mapped_column(
        ForeignKey("capability_requests.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    detail_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class AsyncJob(Base):
    __tablename__ = "async_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_async_jobs_status",
        ),
        Index("idx_async_jobs_user_status", "user_id", "status", "created_at"),
        Index("idx_async_jobs_turn", "turn_id"),
        Index("idx_async_jobs_capability_request", "capability_request_id"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("conversation_turns.id", ondelete="SET NULL"), unique=True)
    capability_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("capability_requests.id", ondelete="SET NULL"), unique=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
