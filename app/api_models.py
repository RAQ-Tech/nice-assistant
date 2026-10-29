"""The shapes of what `/api/v1` accepts and returns.

Moved out of `api_v1.py`, which keeps the routes. A request model here is the
whole of what a route will accept; a representation is the whole of what it
promises to send back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.speech_clients import DEFAULT_TTS_FORMAT


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=512)
    # Stay signed in on this device; see ADR 0047.
    remember: bool = False


class SettingsUpdate(StrictModel):
    global_default_model: str | None = None
    default_memory_mode: str = Field(default="saved", pattern="^(off|saved)$")
    stt_provider: str = Field(default="disabled", pattern="^(disabled|openai|local)$")
    tts_provider: str = Field(default="disabled", pattern="^(disabled|openai|local)$")
    tts_format: str = Field(default=DEFAULT_TTS_FORMAT, pattern="^(mp3|opus|aac|flac|wav|pcm)$")
    openai_api_key: str | None = None
    onboarding_done: bool = False
    preferences: dict = Field(default_factory=dict)


class WorkspaceWrite(StrictModel):
    name: str = Field(min_length=1, max_length=160)


class PersonaWrite(StrictModel):
    workspace_id: str
    workspace_ids: list[str] | None = None
    name: str = Field(min_length=1, max_length=160)
    avatar_url: str | None = None
    system_prompt: str | None = None
    personality_details: str | None = None
    traits: dict = Field(default_factory=dict)
    default_model: str | None = None
    allow_image_sends: bool | None = None
    # Keyed by provider, with an optional "default" any provider falls back to.
    voice_preferences: dict[str, dict[str, str]] = Field(default_factory=dict)


class PersonaCardWrite(StrictModel):
    card_definition: str | None = None
    card_personality: str | None = None
    card_style: str | None = None
    card_behavior: str | None = None
    card_example_dialogue: str | None = None


class PersonaLoreWrite(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=8000)
    keys: list[str] = Field(default_factory=list)
    secondary_keys: list[str] = Field(default_factory=list)
    always_on: bool = False
    case_sensitive: bool = False
    match_word_forms: bool = True
    priority: int = Field(default=50, ge=0, le=100)
    enabled: bool = True


class PersonaLoreCopy(StrictModel):
    source_entry_id: str


class PersonaLorePreview(StrictModel):
    text: str = Field(min_length=1, max_length=8000)


class MemoryCreate(StrictModel):
    scope: str = Field(pattern="^(global|workspace|persona|chat)$")
    scope_id: str | None = None
    content: str = Field(min_length=1, max_length=8000)


class MemoryProposalCreate(MemoryCreate):
    source_message_id: str | None = None


class MemoryUpdate(StrictModel):
    scope: str | None = Field(default=None, pattern="^(global|workspace|persona|chat)$")
    scope_id: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=8000)


class MemoryBulkAction(StrictModel):
    action: Literal["forget", "delete"]
    ids: list[str] = Field(min_length=1, max_length=2000)


class ChatBulkAction(StrictModel):
    action: Literal["hide", "delete"]
    ids: list[str] = Field(min_length=1, max_length=2000)


class BulkActionRepresentation(BaseModel):
    action: str
    requested_count: int
    affected_count: int
    ids: list[str]


class MemoryRepresentation(BaseModel):
    id: str
    scope: str
    scope_id: str | None = None
    content: str
    status: str
    confidence: float | None = None
    source_type: str
    source_message_id: str | None = None
    source_turn_id: str | None = None
    extractor_provider: str | None = None
    extractor_model: str | None = None
    extractor_version: str | None = None
    supersedes_id: str | None = None
    created_at: int
    updated_at: int
    reviewed_at: int | None = None
    forgotten_at: int | None = None
    can_undo: bool = False


class MemoryEventRepresentation(BaseModel):
    id: str
    memory_id: str
    related_memory_id: str | None = None
    action: str
    from_status: str | None = None
    to_status: str | None = None
    created_at: int
    undone_at: int | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryRepresentation]


class MemoryHistoryResponse(BaseModel):
    memory: MemoryRepresentation
    events: list[MemoryEventRepresentation]


class ChatCreate(StrictModel):
    workspace_id: str | None = None
    persona_id: str | None = None
    model: str | None = None
    memory_mode: str = Field(default="saved", pattern="^(off|saved)$")
    title: str = "New chat"


class ChatUpdate(StrictModel):
    title: str | None = None
    model_override: str | None = None
    memory_mode: str | None = Field(default=None, pattern="^(off|saved)$")
    persona_id: str | None = None
    hidden_in_ui: bool | None = None


class ModelGenerationSettings(StrictModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    num_predict: int | None = Field(default=None, ge=1, le=8192)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    context_window_tokens: int | None = Field(default=None, ge=2048, le=262144)


class TurnCreate(StrictModel):
    # May be empty when a photo says it all.
    text: str = Field(default="", max_length=100_000)
    # One photo, as a data URL the browser has already shrunk; see app/chat_photos.py.
    photo: str | None = Field(default=None, max_length=1_200_000)
    workspace_id: str | None = None
    persona_id: str | None = None
    model: str | None = None
    memory_mode: str | None = Field(default=None, pattern="^(off|saved)$")
    model_settings: ModelGenerationSettings = Field(default_factory=ModelGenerationSettings)


class TurnAgain(StrictModel):
    # Absent means the same words again; present means what they were corrected to.
    text: str | None = Field(default=None, max_length=100_000)
    model: str | None = None
    memory_mode: str | None = Field(default=None, pattern="^(off|saved)$")
    model_settings: ModelGenerationSettings = Field(default_factory=ModelGenerationSettings)


class TurnGreeting(StrictModel):
    # The persona speaks first, so there are no words; only how the reply is made.
    model: str | None = None
    memory_mode: str | None = Field(default=None, pattern="^(off|saved)$")
    model_settings: ModelGenerationSettings = Field(default_factory=ModelGenerationSettings)


class ProviderCheck(StrictModel):
    provider: str
    settings: dict = Field(default_factory=dict)


class ComfyUIWorkflowInspection(StrictModel):
    workflow_patch: dict
    settings: dict = Field(default_factory=dict)
    # "identity" demands a reference-image path through an identity node;
    # "general" only demands somewhere for the request prompt to land.
    role: Literal["identity", "general"] = "identity"


class CheckpointDiscovery(StrictModel):
    settings: dict = Field(default_factory=dict)


class ModelSetupRequest(StrictModel):
    limit: int = Field(default=5, ge=1, le=20)
    # Only a person's consent sends file names off the machine; the browser
    # asks before it sets this.
    lookup: bool = False
    force: bool = False


class ModelsFromCheckpoints(StrictModel):
    names: list[str] = Field(min_length=1, max_length=200)


class ModelPrefill(StrictModel):
    checkpoint: str = Field(min_length=1, max_length=512)
    settings: dict = Field(default_factory=dict)


class CivitaiLookup(StrictModel):
    checkpoint: str = Field(min_length=1, max_length=512)


class ComfyUIIdentityInputCandidate(BaseModel):
    node_id: str
    input_name: str
    label: str


class ComfyUIRequestInputCandidate(BaseModel):
    node_id: str
    input_name: str
    label: str
    # The operator tells a positive prompt input from a negative one by what is
    # currently in it, so the preview is part of the choice, not decoration.
    current_value: str


class ComfyUIRequestInputCandidates(BaseModel):
    prompt: list[ComfyUIRequestInputCandidate] = Field(default_factory=list)
    seed: list[ComfyUIRequestInputCandidate] = Field(default_factory=list)
    width: list[ComfyUIRequestInputCandidate] = Field(default_factory=list)
    height: list[ComfyUIRequestInputCandidate] = Field(default_factory=list)
    checkpoint: list[ComfyUIRequestInputCandidate] = Field(default_factory=list)


class ComfyUIAssetCheck(BaseModel):
    node_id: str
    node_type: str
    input_name: str
    value: str
    available: bool
    # What the provider does have for this input, when what the graph names is
    # missing. Empty otherwise, so a working graph does not carry a file list.
    options: list[str] = Field(default_factory=list)


class ComfyUIWorkflowInspectionRepresentation(BaseModel):
    provider: Literal["comfyui"]
    status: Literal["provider_compatible", "incompatible", "invalid", "unreachable", "error"]
    provider_compatible: bool
    live_tested: Literal[False]
    message: str
    identity_input_candidates: list[ComfyUIIdentityInputCandidate]
    # Without this the browser cannot offer a prompt binding, and guided setup
    # cannot be completed at all: a response model omits what it does not name.
    request_input_candidates: ComfyUIRequestInputCandidates
    detected_node_types: list[str]
    missing_node_types: list[str]
    asset_checks: list[ComfyUIAssetCheck]
    warnings: list[str]


class MediaJobCreate(StrictModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    # A passage somebody asked to see, rather than a prompt they wrote. It gets
    # turned into a typed scene before anything reaches an image model, because
    # prose is not prompt syntax and the two must not be confused.
    illustrate_text: str | None = Field(default=None, max_length=100_000)
    chat_id: str | None = None
    provider: str | None = None
    model: str | None = None
    size: str | None = None
    quality: str | None = None
    seconds: str | None = None
    backend: str | None = None
    base_url: str | None = None
    # Per-call tuning, so a caller can render with exact numbers - the model
    # page's sample button sends the values on screen rather than whatever
    # the global preferences happen to be.
    steps: int | None = Field(default=None, ge=1, le=200)
    cfg_scale: float | None = Field(default=None, ge=0, le=40)
    sampler_name: str | None = Field(default=None, max_length=120)
    scheduler: str | None = Field(default=None, max_length=120)


class MediaEditJobCreate(StrictModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    operation: Literal["image_to_image", "inpaint", "outpaint"]
    source_media_id: str
    mask_media_id: str | None = None
    chat_id: str | None = None
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    required_features: list[str] = Field(default_factory=list, max_length=64)


class SpeechSynthesisCreate(StrictModel):
    text: str
    chat_id: str | None = None
    persona_id: str | None = None
    format: str | None = None
    voice: str | None = None
    model: str | None = None
    speed: str | None = None
    instructions: str | None = None


class SpeechSessionCreate(StrictModel):
    chat_id: str | None = None
    persona_id: str | None = None
    format: str | None = None
    voice: str | None = None
    model: str | None = None
    speed: str | None = None
    instructions: str | None = None


class SpeechPieceCreate(StrictModel):
    text: str


class BackupCreate(StrictModel):
    include_media: bool = False


class BackupRepresentation(BaseModel):
    name: str
    size: int
    created_at: int
    created_at_iso: str
    include_media: bool | None = None
    download_url: str


class BackupListResponse(BaseModel):
    items: list[BackupRepresentation]


class ResourceControlAuthorizationUpdate(StrictModel):
    provider: Literal["ollama", "comfyui", "automatic1111"]
    exclusive_control: bool = False
    allow_release: bool = False


class ResourceCoordinationUpdate(StrictModel):
    mode: Literal["disabled", "observe", "managed"] = "disabled"
    reserve_vram_mb: int = Field(default=1024, ge=0, le=131072)
    max_wait_seconds: int = Field(default=300, ge=1, le=3600)
    poll_interval_seconds: float = Field(default=2.0, ge=0.25, le=60)
    authorizations: list[ResourceControlAuthorizationUpdate] = Field(default_factory=list, max_length=3)


TurnState = Literal["queued", "running", "completed", "failed", "cancelled"]
CapabilityState = Literal[
    "pending_confirmation",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "denied",
    "expired",
]


class ChatRepresentation(BaseModel):
    id: str
    workspace_id: str | None = None
    persona_id: str | None = None
    model_override: str | None = None
    memory_mode: str
    title: str | None = None
    hidden_in_ui: bool
    created_at: int
    updated_at: int


class AttachmentFrameRepresentation(BaseModel):
    media_id: str
    content_url: str
    frame_index: int | None = None


class ChatAttachmentRepresentation(BaseModel):
    id: str
    kind: Literal["image", "video"]
    status: Literal["queued", "running", "completed", "failed", "cancelled", "retried"]
    capability_request_id: str
    media_id: str | None = None
    content_url: str | None = None
    # Other frames of the same photo set, sent beside the first. Empty for an
    # ordinary picture.
    frames: list[AttachmentFrameRepresentation] = Field(default_factory=list)
    identity_state: Literal["not_applicable", "unconditioned", "verified", "unverified"]
    safe_error: str | None = None
    retry_available: bool
    created_at: int
    updated_at: int
    completed_at: int | None = None


class MessagePhotoRepresentation(BaseModel):
    id: str
    url: str
    width: int
    height: int


class MessageRepresentation(BaseModel):
    id: str
    role: str
    text: str
    created_at: int
    attachments: list[ChatAttachmentRepresentation] = Field(default_factory=list)
    degraded_reason: str | None = None
    photos: list[MessagePhotoRepresentation] = Field(default_factory=list)


class ChatListResponse(BaseModel):
    items: list[ChatRepresentation]


class ChatDetailResponse(BaseModel):
    chat: ChatRepresentation
    messages: list[MessageRepresentation]


class TurnErrorRepresentation(BaseModel):
    code: str
    message: str


class TurnContextRepresentation(BaseModel):
    context_window_tokens: int | None = None
    prompt_budget_tokens: int | None = None
    prompt_tokens_estimated: int | None = None
    prompt_tokens_actual: int | None = None
    included_message_count: int | None = None
    omitted_message_count: int | None = None
    included_memory_count: int | None = None
    omitted_memory_count: int | None = None
    summary_id: str | None = None
    degraded_reason: str | None = None


class ConversationSummaryRepresentation(BaseModel):
    id: str
    through_message_id: str
    provider: str
    model: str
    prompt_version: str
    content: str
    estimated_tokens: int
    created_at: int


class LatestTurnContextRepresentation(TurnContextRepresentation):
    turn_id: str


class ChatContextResponse(BaseModel):
    chat_id: str
    memory_mode: str
    summary: ConversationSummaryRepresentation | None = None
    latest_turn_context: LatestTurnContextRepresentation | None = None


class TurnRepresentation(BaseModel):
    id: str
    chat_id: str
    job_id: str | None = None
    status: TurnState
    provider: str
    model: str
    # None for a turn the persona opened: nothing was said before it.
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    accumulated_text: str = ""
    error: TurnErrorRepresentation | None = None
    created_at: int
    started_at: int | None = None
    completed_at: int | None = None
    context: TurnContextRepresentation | None = None


class JobRepresentation(BaseModel):
    id: str
    kind: str
    status: TurnState
    chat_id: str | None = None
    turn_id: str | None = None
    capability_request_id: str | None = None
    progress: str = ""
    queue_position: int | None = None
    result: dict | None = None
    error: str = ""
    cancel_requested: bool = False
    created_at: int | None = None
    started_at: int | None = None
    completed_at: int | None = None


class TurnAcceptedResponse(BaseModel):
    turn: TurnRepresentation
    job: JobRepresentation


class MediaJobAcceptedResponse(BaseModel):
    job_id: str
    capability_request_id: str
    chat_id: str | None = None
    status: TurnState


class MediaGenerationAttemptRepresentation(BaseModel):
    id: str
    media_plan_id: str
    attempt_number: int
    operation: Literal["generate", "inpaint", "outpaint", "image_to_image"]
    status: Literal["running", "passed", "failed", "unverified", "error", "cancelled"]
    media_id: str | None = None
    media_url: str | None = None
    validation_id: str | None = None
    source_media_id: str | None = None
    workflow_resource_id: str | None = None
    score: float | None = None
    threshold: float | None = None
    error: dict | None = None
    started_at: int
    completed_at: int | None = None


class MediaGenerationAttemptListResponse(BaseModel):
    items: list[MediaGenerationAttemptRepresentation]


class MediaJournalStageRepresentation(BaseModel):
    sequence: int
    stage: str
    status: Literal["ok", "skipped", "failed"]
    summary: str
    detail: dict
    started_at: int
    duration_ms: int | None = None


class MediaJournalError(BaseModel):
    code: str
    message: str


class MediaJournalRepresentation(BaseModel):
    id: str
    kind: Literal["image", "video"]
    origin: Literal["conversation", "direct", "edit", "library"]
    status: Literal["running", "completed", "failed", "cancelled"]
    chat_id: str | None = None
    persona_id: str | None = None
    media_id: str | None = None
    media_plan_id: str | None = None
    capability_request_id: str | None = None
    started_at: int
    completed_at: int | None = None
    duration_ms: int | None = None
    error: MediaJournalError | None = None
    stages: list[MediaJournalStageRepresentation]


class MediaJournalSummaryRepresentation(BaseModel):
    id: str
    kind: Literal["image", "video"]
    origin: Literal["conversation", "direct", "edit", "library"]
    status: Literal["running", "completed", "failed", "cancelled"]
    media_id: str | None = None
    started_at: int
    duration_ms: int | None = None
    stage_count: int


class MediaJournalListResponse(BaseModel):
    items: list[MediaJournalSummaryRepresentation]


class MediaLibraryItemRepresentation(BaseModel):
    id: str
    chat_id: str | None = None
    kind: Literal["image", "video"]
    filename: str
    content_url: str
    created_at: int


class MediaLibraryListResponse(BaseModel):
    items: list[MediaLibraryItemRepresentation]


class CapabilityDefinitionRepresentation(BaseModel):
    key: str
    tool_name: str
    title: str
    description: str
    permission_mode: Literal["confirm", "explicit", "auto"]
    available: bool


class CapabilityDefinitionListResponse(BaseModel):
    items: list[CapabilityDefinitionRepresentation]


class MediaCatalogSettingsUpdate(StrictModel):
    vram_budget_mb: int = Field(ge=0, le=131072)
    max_loras: int = Field(ge=0, le=8)


class MediaCatalogSettingsRepresentation(BaseModel):
    vram_budget_mb: int
    max_loras: int


class MediaCatalogResourceWrite(StrictModel):
    resource_type: Literal["model", "lora", "workflow"]
    kind: Literal["image", "video"]
    name: str = Field(min_length=1, max_length=160)
    provider_key: Literal["openai-image", "local-image", "local-video"]
    backend: Literal["openai", "automatic1111", "comfyui"]
    external_id: str = Field(min_length=1, max_length=500)
    enabled: bool = True
    priority: int = Field(default=50, ge=0, le=100)
    operations: list[Literal["generate", "inpaint", "outpaint", "image_to_image"]] = Field(
        default=["generate"], min_length=1, max_length=4
    )
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    features: list[str] = Field(default_factory=list, max_length=64)
    estimated_vram_mb: int = Field(default=0, ge=0, le=131072)
    estimated_load_seconds: float = Field(default=0, ge=0, le=3600)
    default_settings: dict = Field(default_factory=dict)
    notes: str = Field(default="", max_length=4000)
    compatible_model_ids: list[str] = Field(default_factory=list, max_length=100)


class MediaCatalogResourceRepresentation(MediaCatalogResourceWrite):
    id: str
    revision: int
    created_at: int
    updated_at: int
    # Derived, not stored: a workflow that predates declared prompt bindings.
    needs_binding_review: bool = False
    # Which shipped template this graph came from. Empty means it did not.
    source_template_id: str = ""
    source_template_version: int | None = None


class WorkflowTemplateRepresentation(BaseModel):
    id: str
    name: str
    template_version: int
    summary: str
    kind: str = "image"
    mechanism: str | None = None
    architectures: list[str]
    required_assets: list[str]
    required_prompt_token: str
    installed_resource_id: str | None
    installed_version: int | None
    installed_count: int = 0
    update_available: bool
    architecture_matches: bool


class WorkflowTemplateListResponse(BaseModel):
    model_id: str
    model_kind: str = ""
    model_architecture: str
    templates: list[WorkflowTemplateRepresentation]


class WorkflowTemplateAssetChoice(StrictModel):
    node_id: str = Field(min_length=1, max_length=16)
    input_name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=300)


class WorkflowTemplateInstall(StrictModel):
    model_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    # A downloaded model keeps the name its source gave it, so the graph is
    # pointed at the file rather than the file renamed to match the graph.
    asset_choices: list[WorkflowTemplateAssetChoice] = Field(default_factory=list, max_length=16)
    # Optional: add a post-pass template to this recipe as a later pass, rather
    # than leaving somebody to hand-edit a preset's definition JSON.
    preset_id: str = Field(default="", max_length=64)


class SceneBacklogRepresentation(BaseModel):
    id: str
    persona_id: str
    scene: dict
    summary: str
    state: Literal["proposed", "approved", "generating", "done", "retired"]
    source: Literal["operator", "persona_card", "lorebook", "conversation"]
    source_detail: str
    media_id: str | None = None
    created_at: int
    updated_at: int


class SceneBacklogListResponse(BaseModel):
    items: list[SceneBacklogRepresentation]


class SceneBacklogCreate(StrictModel):
    persona_id: str = Field(min_length=1, max_length=64)
    scene: dict = Field(default_factory=dict)
    source_detail: str = Field(default="", max_length=500)


class PhotoSetFrameRepresentation(BaseModel):
    frame_index: int | None = None
    media_id: str
    content_url: str
    seed: int


class PhotoSetRepresentation(BaseModel):
    id: str
    persona_id: str
    scene: dict
    shared: str
    variations: list[dict]
    state: Literal["planned", "generating", "done", "partial", "retired"]
    base_seed: int
    frame_count: int
    frames_done: int
    frames_missing: int
    frames: list[PhotoSetFrameRepresentation]
    created_at: int
    updated_at: int


class PhotoSetListResponse(BaseModel):
    items: list[PhotoSetRepresentation]


class PhotoSetCreate(StrictModel):
    persona_id: str = Field(min_length=1, max_length=64)
    scene: dict = Field(default_factory=dict)
    # Each entry may set pose, angle, framing, or mood. Everything else belongs
    # to the set and is shared, which is the point of a set.
    variations: list[dict] = Field(default_factory=list, max_length=12)


class PhotoSetProductionResponse(BaseModel):
    set_id: str
    started: list[dict]


class PresetPreviewRow(BaseModel):
    label: str
    value: str


class PresetExportRepresentation(BaseModel):
    filename: str
    bundle: dict
    preview: list[PresetPreviewRow]
    requirements: list[str]
    withheld: list[str]


class PresetImportEntry(BaseModel):
    name: str
    routing_card: str
    requirements: list[str]
    blockers: list[str]
    installable: bool


class PresetImportPreviewRepresentation(BaseModel):
    version: int
    presets: list[PresetImportEntry]
    installable: bool
    warnings: list[str]


class PresetImportRequest(StrictModel):
    bundle: dict


class PresetImportResultRepresentation(BaseModel):
    installed: list[dict]
    warnings: list[str]


class PresetSignalRepresentation(BaseModel):
    preset_id: str
    preset_name: str
    kept: int
    sent_again: int
    removed: int
    weight: int
    summary: str


class PresetSignalListResponse(BaseModel):
    items: list[PresetSignalRepresentation]


class PregenerationReadinessRepresentation(BaseModel):
    allowed: bool
    reason: str
    approved_waiting: int
    window: str
    enabled: bool
    start_hour: int = 2
    end_hour: int = 6
    max_per_run: int = 3
    # A deployment may refuse background production outright. The control is
    # then shown disabled with the reason rather than shown and ignored.
    deployment_forbids: bool = False
    inside_window: bool = False


class SceneProposalRequest(StrictModel):
    persona_id: str = Field(min_length=1, max_length=64)
    limit: int = Field(default=5, ge=1, le=10)


class SceneProposalResponse(BaseModel):
    requested: int
    # False when the task model did not answer and its fallback was used, so an
    # empty result is not mistaken for "no ideas".
    model_answered: bool = True
    proposed: list[SceneBacklogRepresentation]


class SceneBacklogStateUpdate(StrictModel):
    state: Literal["proposed", "approved", "retired"]


class LibraryEntryRepresentation(BaseModel):
    id: str
    persona_id: str | None = None
    media_id: str
    content_url: str
    scene: dict
    state: Literal["ready", "served", "retired"]
    served_count: int
    created_at: int
    last_served_at: int | None = None


class LibraryEntryListResponse(BaseModel):
    items: list[LibraryEntryRepresentation]


class LibraryEntryCreate(StrictModel):
    media_id: str = Field(min_length=1, max_length=64)
    persona_id: str | None = Field(default=None, max_length=64)
    scene: dict = Field(default_factory=dict)


class StarterPresetRepresentation(BaseModel):
    name: str
    routing_card: str
    notes: str
    installable: bool
    already_present: bool
    missing_assets: list[str]


class StarterPresetListResponse(BaseModel):
    version: int
    presets: list[StarterPresetRepresentation]


class StarterPresetInstalled(BaseModel):
    name: str
    id: str


class StarterPresetSkipped(BaseModel):
    name: str
    reason: str


class StarterPresetInstallResponse(BaseModel):
    installed: list[StarterPresetInstalled]
    skipped: list[StarterPresetSkipped]


class RoutingPreviewCreate(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["image", "video"] = "image"


class RoutingPreviewShortlistEntry(BaseModel):
    reference: str
    title: str
    routing_card: str


class RoutingPreviewTaskModel(BaseModel):
    ran: bool
    error: str
    chose: str


class RoutingPreviewRepresentation(BaseModel):
    message: str
    shortlist: list[RoutingPreviewShortlistEntry]
    requested: bool
    task_model: RoutingPreviewTaskModel
    plan: MediaPlanRepresentation | None = None


class MediaPresetWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["image", "video"] = "image"
    enabled: bool = True
    priority: int = Field(default=50, ge=0, le=100)
    # Plain language, written by the operator: when should this be chosen?
    routing_card: str = Field(default="", max_length=2000)
    operations: list[str] = Field(default_factory=lambda: ["generate"], max_length=8)
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    features: list[str] = Field(default_factory=list, max_length=64)
    # Validated by the service against the owner's catalog, which is the only
    # place that can answer whether a referenced resource is real and paired.
    definition: dict = Field(default_factory=dict)
    estimated_vram_mb: int = Field(default=0, ge=0, le=1_048_576)
    notes: str = Field(default="", max_length=4000)


class MediaPresetRepresentation(MediaPresetWrite):
    id: str
    revision: int
    created_at: int
    updated_at: int


class MediaPresetListResponse(BaseModel):
    items: list[MediaPresetRepresentation]


class MediaPlanningVocabularyRepresentation(BaseModel):
    operations: list[str]
    domains: list[str]
    content_tags: list[str]
    features: list[str]


class MediaCatalogRepresentation(BaseModel):
    settings: MediaCatalogSettingsRepresentation
    resources: list[MediaCatalogResourceRepresentation]
    vocabulary: MediaPlanningVocabularyRepresentation


class MediaPlanRequirementsCreate(StrictModel):
    kind: Literal["image", "video"]
    # Previewing a persona's routing needs the persona, because preferences and
    # the Identity Spec are persona-scoped.
    persona_id: str | None = Field(default=None, max_length=64)
    operation: Literal["generate", "inpaint", "outpaint", "image_to_image"] = "generate"
    domains: list[str] = Field(default_factory=list, max_length=64)
    content_tags: list[str] = Field(default_factory=list, max_length=64)
    required_features: list[str] = Field(default_factory=list, max_length=64)


class MediaPlanResourceSnapshot(BaseModel):
    id: str
    resource_type: Literal["model", "lora", "workflow"]
    name: str
    provider_key: str
    backend: str
    external_id: str
    domains: list[str]
    content_tags: list[str]
    features: list[str]
    estimated_vram_mb: int
    default_settings: dict
    updated_at: int
    revision: int


class MediaPlanSelectionExplanation(BaseModel):
    resource_id: str
    role: str
    name: str
    reason: str


class MediaPlanRejectionExplanation(BaseModel):
    resource_id: str
    name: str
    reasons: list[str]


class MediaPlanConsideredPreset(BaseModel):
    id: str
    name: str


class MediaPlanPresetExplanation(BaseModel):
    id: str
    name: str
    revision: int
    priority: int
    routing_card: str
    # Who chose this preset, in the order they are consulted: the task model
    # for this request, an operator-set persona preference, the counts of what
    # happened to earlier pictures, or the deterministic score.
    source: Literal["task_model", "persona_preference", "measured_preference", "default_model", "deterministic"] = (
        "deterministic"
    )
    reason: str
    considered: list[MediaPlanConsideredPreset] = Field(default_factory=list)


class MediaPlanExplanation(BaseModel):
    summary: str
    # Absent on a manual plan, which bypasses preset selection by design.
    preset: MediaPlanPresetExplanation | None = None
    selected: list[MediaPlanSelectionExplanation]
    warnings: list[str]
    rejected: list[MediaPlanRejectionExplanation]


class MediaPlanBlock(BaseModel):
    code: str
    message: str


class MediaIdentityConditioningRepresentation(BaseModel):
    required: bool
    status: Literal["ready", "blocked", "conditioned", "unconditioned"]
    mode: str | None = None
    persona_id: str | None = None
    profile_id: str | None = None
    profile_revision: int | None = None
    reference_id: str | None = None
    reference_sha256: str | None = None
    workflow_resource_id: str | None = None
    conditioning_fallback: Literal["allow_unconditioned", "require_conditioning"] | None = None
    appearance_description_included: bool = False
    verification_status: Literal["not_evaluated"] = "not_evaluated"
    claim_status: Literal["unverified"] | None = None


class MediaPlanRepresentation(BaseModel):
    id: str | None = None
    source: Literal["coordinator", "manual"]
    status: Literal["ready", "blocked"]
    kind: Literal["image", "video"]
    operation: str
    requirements: dict
    selected_resources: list[MediaPlanResourceSnapshot]
    explanation: MediaPlanExplanation
    estimated_vram_mb: int
    identity_conditioning: MediaIdentityConditioningRepresentation | None = None
    block: MediaPlanBlock | None = None
    created_at: int | None = None


class CapabilityRequestRepresentation(BaseModel):
    id: str
    capability_key: str
    status: CapabilityState
    permission_mode: Literal["confirm", "explicit", "auto"]
    arguments: dict
    result: dict | None = None
    error: TurnErrorRepresentation | None = None
    chat_id: str | None = None
    turn_id: str | None = None
    assistant_message_id: str | None = None
    job_id: str | None = None
    requested_at: int
    decided_at: int | None = None
    started_at: int | None = None
    completed_at: int | None = None
    expires_at: int | None = None
    retry_of_request_id: str | None = None
    attachment: ChatAttachmentRepresentation | None = None
    media_plan: MediaPlanRepresentation | None = None


class CapabilityRequestListResponse(BaseModel):
    items: list[CapabilityRequestRepresentation]


class CapabilityEventRepresentation(BaseModel):
    id: str
    capability_request_id: str
    action: str
    from_status: str | None = None
    to_status: str | None = None
    detail: dict
    created_at: int


class CapabilityHistoryResponse(BaseModel):
    request: CapabilityRequestRepresentation
    events: list[CapabilityEventRepresentation]


class MediaReadinessFact(BaseModel):
    ready: bool
    message: str


class OptionalIdentityReadinessFact(MediaReadinessFact):
    status: str


class MediaProviderReadiness(BaseModel):
    key: str
    reachable: bool
    status: str
    message: str


class MediaReadinessResponse(BaseModel):
    provider: MediaProviderReadiness
    basic_generation: MediaReadinessFact
    optional_identity: OptionalIdentityReadinessFact


class TaskModelProfileUpdate(StrictModel):
    enabled: bool
    provider: str = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=240)
    fallback_provider: str | None = Field(default=None, max_length=80)
    fallback_model: str | None = Field(default=None, max_length=240)
    max_input_tokens: int = Field(ge=128, le=262144)
    max_output_tokens: int = Field(ge=16, le=8192)
    timeout_seconds: float = Field(ge=1, le=600)
    temperature: float = Field(ge=0, le=2)
    fallback_policy: Literal["deterministic", "skip", "fail"]


class TaskModelProfileRepresentation(BaseModel):
    role: str
    title: str
    description: str
    enabled: bool
    provider: str
    model: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    temperature: float
    fallback_policy: Literal["deterministic", "skip", "fail"]
    updated_at: int


class TaskModelProfileListResponse(BaseModel):
    items: list[TaskModelProfileRepresentation]


class TaskModelReadinessRepresentation(BaseModel):
    role: str
    ready: bool
    status: str
    message: str
    primary_ready: bool
    fallback_ready: bool
    effective_model: str | None = None
    fallback_effective_model: str | None = None
    # An installed adapter is not a configured account, and neither is a
    # verified one. Reported separately so no client can conflate them.
    adapter_installed: bool = False
    credentials_configured: bool = False
    live_verified: bool = False


class TaskModelRunRepresentation(BaseModel):
    id: str
    role: str
    chat_id: str | None = None
    turn_id: str | None = None
    requested_provider: str | None = None
    requested_model: str | None = None
    executed_provider: str | None = None
    executed_model: str | None = None
    status: Literal["running", "completed", "fallback", "failed"]
    fallback_used: bool
    error: TurnErrorRepresentation | None = None
    attempts: list[dict]
    input_tokens_estimated: int
    output_tokens_estimated: int | None = None
    latency_ms: int | None = None
    started_at: int
    completed_at: int | None = None


class TaskModelRunListResponse(BaseModel):
    items: list[TaskModelRunRepresentation]


class ModelListResponse(BaseModel):
    models: list[str]
    vision_models: list[str] = Field(default_factory=list)
