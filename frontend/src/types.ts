export type Id = string;

export type MemoryMode = 'off' | 'saved';
export type MemoryStatus = 'pending' | 'active' | 'rejected' | 'forgotten' | 'superseded';
export type MemoryScope = 'global' | 'workspace' | 'persona' | 'chat';
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type CapabilityStatus =
  | 'pending_confirmation'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'denied'
  | 'expired';
export type ClientPhase =
  | 'signed_out'
  | 'onboarding'
  | 'idle'
  | 'loading_chat'
  | 'queued'
  | 'thinking'
  | 'recording'
  | 'transcribing'
  | 'speaking'
  | 'error';

export interface Session {
  user_id: Id;
  expires_at: number | null;
  ttl_seconds: number;
  is_admin: boolean;
}

export interface Workspace {
  id: Id;
  name: string;
  created_at: number;
}

export interface PersonaTraits {
  warmth: number;
  creativity: number;
  directness: number;
  conversational: number;
  casual: number;
  gender: string;
  gender_other: string;
  age: string;
}

export interface Persona {
  id: Id;
  workspace_id: Id;
  workspace_ids: Id[];
  name: string;
  avatar_url: string | null;
  system_prompt: string | null;
  personality_details: string | null;
  traits: Partial<PersonaTraits>;
  default_model: string | null;
  preferred_voice: string | null;
  preferred_tts_model: string | null;
  preferred_tts_speed: string | null;
  preferred_voice_openai: string | null;
  preferred_tts_model_openai: string | null;
  preferred_tts_speed_openai: string | null;
  preferred_voice_local: string | null;
  preferred_tts_model_local: string | null;
  preferred_tts_speed_local: string | null;
  created_at: number;
}

export interface IdentityValidationSettings {
  provider: 'disabled' | 'compreface';
  base_url: string;
  api_key: string;
  timeout_seconds: number;
}

export interface IdentityReference {
  id: Id;
  persona_id: Id;
  source_media_id: Id | null;
  content_url: string | null;
  content_type: string;
  byte_size: number;
  width: number;
  height: number;
  sha256: string;
  provenance: 'user_upload' | 'generated_approved' | 'imported';
  review_status: 'pending' | 'approved' | 'rejected' | 'deleted';
  is_primary: boolean;
  rejection_reason: string | null;
  created_at: number;
  reviewed_at: number | null;
  deleted_at: number | null;
}

export interface VisualIdentityProfile {
  id: Id | null;
  persona_id: Id;
  status: 'draft' | 'active' | 'disabled';
  consent_status: 'not_granted' | 'granted' | 'withdrawn';
  appearance_description: string;
  acceptance_threshold: number;
  max_generation_attempts: number;
  failure_policy: 'block_claim' | 'show_unverified';
  conditioning_fallback: 'allow_unconditioned' | 'require_conditioning';
  revision: number;
  consent_granted_at: number | null;
  consent_withdrawn_at: number | null;
  created_at: number | null;
  updated_at: number | null;
  approved_reference_count: number;
  generation_workflow_configured: boolean;
  verification_configured: boolean;
  validation_ready: boolean;
  references: IdentityReference[];
}

export interface MediaLibraryItem {
  id: Id;
  chat_id: Id | null;
  kind: 'image' | 'video';
  filename: string;
  content_url: string;
  created_at: number;
}

export interface IdentityValidation {
  id: Id;
  persona_id: Id;
  candidate_media_id: Id;
  sequence_number: number;
  created_order: number;
  job_id: Id | null;
  matched_reference_id: Id | null;
  provider: string;
  status: 'queued' | 'running' | 'passed' | 'failed' | 'error' | 'cancelled';
  failure_policy: 'block_claim' | 'show_unverified';
  claim_status: 'verified' | 'rejected' | 'unverified';
  score: number | null;
  threshold: number;
  source_face_count: number | null;
  target_face_count: number | null;
  provider_version: string | null;
  request_id: string | null;
  error: { code: string; message: string } | null;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface IdentityEvent {
  id: Id;
  action: string;
  reference_id: Id | null;
  validation_id: Id | null;
  sequence_number: number;
  detail: Record<string, unknown>;
  created_at: number;
}

export interface Chat {
  id: Id;
  workspace_id: Id | null;
  persona_id: Id | null;
  model_override: string | null;
  memory_mode: MemoryMode;
  title: string | null;
  hidden_in_ui: boolean;
  created_at: number;
  updated_at: number;
}

export interface Message {
  id: Id;
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  text: string;
  created_at: number;
  isTyping?: boolean;
  retryImagePrompt?: string;
  retryChatId?: Id;
}

export interface ChatDetail {
  chat: Chat;
  messages: Message[];
}

export interface Memory {
  id: Id;
  scope: MemoryScope;
  scope_id: Id | null;
  content: string;
  status: MemoryStatus;
  confidence: number | null;
  source_type: 'legacy' | 'manual' | 'conversation' | 'edit';
  source_message_id: Id | null;
  source_turn_id: Id | null;
  extractor_provider: string | null;
  extractor_model: string | null;
  extractor_version: string | null;
  supersedes_id: Id | null;
  created_at: number;
  updated_at: number;
  reviewed_at: number | null;
  forgotten_at: number | null;
  can_undo: boolean;
}

export interface MemoryEvent {
  id: Id;
  memory_id: Id;
  related_memory_id: Id | null;
  action: string;
  from_status: MemoryStatus | null;
  to_status: MemoryStatus | null;
  created_at: number;
  undone_at: number | null;
}

export interface BulkActionResult {
  action: string;
  requested_count: number;
  affected_count: number;
  ids: Id[];
}

export interface ProviderCheckResult {
  provider: string;
  status: string;
  message?: string;
  ready?: boolean;
  [key: string]: unknown;
}

export type TaskModelRole =
  | 'title_generation'
  | 'conversation_summary'
  | 'memory_extraction'
  | 'capability_planning';

export interface TaskModelProfile {
  role: TaskModelRole;
  title: string;
  description: string;
  enabled: boolean;
  provider: string;
  model: string | null;
  fallback_provider: string | null;
  fallback_model: string | null;
  max_input_tokens: number;
  max_output_tokens: number;
  timeout_seconds: number;
  temperature: number;
  fallback_policy: 'deterministic' | 'skip' | 'fail';
  updated_at: number;
}

export interface TaskModelReadiness {
  role: TaskModelRole;
  ready: boolean;
  status: string;
  message: string;
  primary_ready: boolean;
  fallback_ready: boolean;
  effective_model: string | null;
  fallback_effective_model: string | null;
}

export interface TaskModelRun {
  id: Id;
  role: TaskModelRole;
  chat_id: Id | null;
  turn_id: Id | null;
  requested_provider: string | null;
  requested_model: string | null;
  executed_provider: string | null;
  executed_model: string | null;
  status: 'running' | 'completed' | 'fallback' | 'failed';
  fallback_used: boolean;
  error: { code: string; message: string } | null;
  attempts: Array<Record<string, unknown>>;
  input_tokens_estimated: number;
  output_tokens_estimated: number | null;
  latency_ms: number | null;
  started_at: number;
  completed_at: number | null;
}

export interface JobResult {
  text?: string;
  chatId?: Id;
  imageUrl?: string;
  videoUrl?: string;
  mediaId?: Id;
  memory_extraction_job_id?: Id;
  identityConditioning?: {
    status?: 'conditioned' | 'unconditioned';
    verification_status: 'not_evaluated' | 'passed' | 'failed' | 'unavailable' | 'error' | 'cancelled';
    claim_status?: 'verified' | 'unverified' | 'rejected';
  };
  identityWorkflow?: { attempts: number; validation?: Record<string, unknown> | null };
  [key: string]: unknown;
}

export interface Job {
  id: Id;
  kind: string;
  status: JobStatus;
  chat_id: Id | null;
  turn_id: Id | null;
  capability_request_id: Id | null;
  progress: string;
  queue_position: number | null;
  result: JobResult | null;
  error: string;
  cancel_requested: boolean;
  created_at: number | null;
  started_at: number | null;
  completed_at: number | null;
}

export interface CapabilityRequest {
  id: Id;
  capability_key: 'media.generate_image' | 'media.generate_video' | string;
  status: CapabilityStatus;
  permission_mode: 'confirm' | 'explicit';
  arguments: { prompt?: string; [key: string]: unknown };
  result: JobResult | null;
  error: { code: string; message: string } | null;
  chat_id: Id | null;
  turn_id: Id | null;
  assistant_message_id: Id | null;
  job_id: Id | null;
  requested_at: number;
  decided_at: number | null;
  started_at: number | null;
  completed_at: number | null;
  expires_at: number | null;
  media_plan: MediaPlan | null;
}

export interface IdentitySetupIntent {
  capability_request_id: Id | null;
  chat_id: Id | null;
  persona_id: Id | null;
  prompt: string;
  required_features: string[];
  block_code?: string | null;
}

export type MediaResourceType = 'model' | 'lora' | 'workflow';

export interface MediaCatalogSettings {
  vram_budget_mb: number;
  max_loras: number;
}

export interface MediaCatalogResource {
  id: Id;
  resource_type: MediaResourceType;
  kind: 'image' | 'video';
  name: string;
  provider_key: 'openai-image' | 'local-image' | 'openai-video';
  backend: 'openai' | 'automatic1111' | 'comfyui';
  external_id: string;
  enabled: boolean;
  priority: number;
  operations: ('generate' | 'inpaint' | 'outpaint' | 'image_to_image')[];
  domains: string[];
  content_tags: string[];
  features: string[];
  estimated_vram_mb: number;
  estimated_load_seconds: number;
  default_settings: Record<string, unknown>;
  notes: string;
  compatible_model_ids: Id[];
  revision: number;
  created_at: number;
  updated_at: number;
}

export interface MediaPlanningVocabulary {
  operations: string[];
  domains: string[];
  content_tags: string[];
  features: string[];
}

export interface MediaCatalog {
  settings: MediaCatalogSettings;
  resources: MediaCatalogResource[];
  vocabulary: MediaPlanningVocabulary;
}

export interface IdentityWorkflowInputCandidate {
  node_id: string;
  input_name: string;
  label: string;
}

export interface IdentityWorkflowInspection {
  provider: 'comfyui';
  status: 'provider_compatible' | 'incompatible' | 'invalid' | 'unreachable' | 'error';
  provider_compatible: boolean;
  live_tested: false;
  message: string;
  identity_input_candidates: IdentityWorkflowInputCandidate[];
  detected_node_types: string[];
  missing_node_types: string[];
  asset_checks: {
    node_id: string;
    node_type: string;
    input_name: string;
    value: string;
    available: boolean;
  }[];
  warnings: string[];
}

export interface MediaPlanRequirements {
  kind: 'image' | 'video';
  operation: 'generate' | 'inpaint' | 'outpaint' | 'image_to_image';
  domains: string[];
  content_tags: string[];
  required_features: string[];
}

export interface MediaPlanResource {
  id: Id;
  resource_type: MediaResourceType;
  name: string;
  provider_key: string;
  backend: string;
  external_id: string;
  domains: string[];
  content_tags: string[];
  features: string[];
  estimated_vram_mb: number;
  default_settings: Record<string, unknown>;
  updated_at: number;
  revision: number;
}

export interface MediaPlan {
  id: Id | null;
  source: 'coordinator' | 'manual';
  status: 'ready' | 'blocked';
  kind: 'image' | 'video';
  operation: string;
  requirements: MediaPlanRequirements;
  selected_resources: MediaPlanResource[];
  explanation: {
    summary: string;
    selected: { resource_id: Id; role: string; name: string; reason: string }[];
    warnings: string[];
    rejected: { resource_id: Id; name: string; reasons: string[] }[];
  };
  estimated_vram_mb: number;
  identity_conditioning: {
    required: boolean;
    status: 'ready' | 'blocked' | 'conditioned' | 'unconditioned';
    mode: string | null;
    persona_id: Id | null;
    profile_id: Id | null;
    profile_revision: number | null;
    reference_id: Id | null;
    reference_sha256: string | null;
    workflow_resource_id: Id | null;
    correction_workflow_resource_id?: Id | null;
    acceptance_threshold?: number | null;
    max_generation_attempts?: number | null;
    failure_policy?: 'block_claim' | 'show_unverified' | null;
    appearance_description_included: boolean;
    verification_status: 'not_evaluated' | 'passed' | 'failed' | 'unavailable' | 'error' | 'cancelled';
    claim_status?: 'verified' | 'unverified' | 'rejected' | null;
  } | null;
  block: { code: string; message: string } | null;
  created_at: number | null;
}

export interface Turn {
  id: Id;
  chat_id: Id;
  job_id: Id | null;
  status: JobStatus;
  provider: string;
  model: string;
  user_message_id: Id;
  assistant_message_id: Id | null;
  accumulated_text: string;
  error: { code: string; message: string } | null;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface TurnAccepted {
  turn: Turn;
  job: Job;
}

export interface TurnEvent {
  id: number | null;
  event: 'turn.snapshot' | 'turn.queued' | 'turn.started' | 'assistant.delta' | 'turn.completed' | 'turn.failed' | 'turn.cancelled' | string;
  data: Record<string, unknown>;
}

export type SettingScalar = string | number | boolean | string[] | null;

export interface Settings extends Record<string, SettingScalar | Record<string, Record<string, SettingScalar>>> {
  global_default_model: string;
  default_memory_mode: MemoryMode;
  stt_provider: string;
  tts_provider: string;
  tts_format: string;
  openai_api_key: string;
  onboarding_done: boolean;
  general_theme: string;
  general_show_system_messages: boolean;
  general_show_thinking: boolean;
  general_auto_logout: boolean;
  general_voice_responses: boolean;
  general_show_viz: boolean;
  tts_voice_openai: string;
  tts_model_openai: string;
  tts_speed_openai: string;
  tts_instructions_openai: string;
  tts_voice_local: string;
  tts_model_local: string;
  tts_speed_local: string;
  tts_local_base_url: string;
  tts_voice_filter_regions: string[];
  tts_voice_filter_genders: string[];
  tts_voice_filter_query: string;
  stt_language: string;
  stt_store_recordings: boolean;
  image_provider: string;
  image_size: string;
  image_quality: string;
  image_local_allow_nsfw: boolean;
  image_local_backend: string;
  image_local_base_url: string;
  image_local_api_auth: string;
  image_local_model: string;
  image_local_steps: string;
  image_local_sampler_name: string;
  image_local_scheduler: string;
  image_local_cfg_scale: string;
  image_local_seed: string;
  image_local_additional_parameters: string;
  video_provider: string;
  video_model: string;
  video_size: string;
  video_duration: string;
  models_context_window_tokens: string;
  user_display_name: string;
  user_timezone: string;
  personas_default_system_prompt: string;
  workspaces_default_workspace_id: string;
  models_temperature: string;
  models_top_p: string;
  models_num_predict: string;
  models_presence_penalty: string;
  models_frequency_penalty: string;
  model_overrides: Record<string, Record<string, SettingScalar>>;
}

export interface SettingsWire {
  global_default_model: string | null;
  default_memory_mode: string;
  stt_provider: string;
  tts_provider: string;
  tts_format: string;
  openai_api_key: string | null;
  onboarding_done: boolean;
  preferences: Record<string, unknown>;
}

export interface PendingRequest {
  jobId: Id;
  turnId?: Id;
  progress: string;
  cancel: () => Promise<void>;
}

export interface BackupItem {
  name: string;
  size: number;
  created_at: number;
  include_media?: boolean;
}

export interface BackupVerification {
  ok: boolean;
  name: string;
  database_integrity: string;
  migration_revision: string;
  entry_count: number;
  include_media: boolean;
}

export interface ResourceCoordinationSettings {
  mode: 'disabled' | 'observe' | 'managed';
  reserve_vram_mb: number;
  max_wait_seconds: number;
  poll_interval_seconds: number;
}

export interface ResourceEndpointStatus {
  provider: 'ollama' | 'comfyui' | 'automatic1111';
  endpoint_label: string;
  fingerprint: string;
  authorization: {
    exclusive_control: boolean;
    allow_release: boolean;
    authorized_at: number | null;
  };
  capabilities: {
    reports_capacity: boolean;
    reports_queue: boolean;
    supports_release: boolean;
    supports_precise_cancel: boolean;
  };
  snapshot: null | {
    status: 'known' | 'unknown' | 'unavailable';
    source: string;
    observed_at: number;
    total_vram_mb: number | null;
    free_vram_mb: number | null;
    queue_depth: number | null;
    active_jobs: number | null;
    loaded_models: { name: string; vram_mb: number | null }[];
    message: string;
  };
}

export interface ResourceCoordinationStatus {
  settings: ResourceCoordinationSettings;
  endpoints: ResourceEndpointStatus[];
}

export interface ResourceCoordinationEvent {
  id: Id;
  job_id: Id | null;
  provider: string;
  endpoint_fingerprint: string;
  action: string;
  outcome: string;
  detail: Record<string, unknown>;
  created_at: number;
}

export interface ModalAction {
  label: string;
  kind?: 'primary' | 'danger' | 'secondary';
  run: (value: string) => void | Promise<void>;
}

export interface ModalState {
  title: string;
  message: string;
  inputValue?: string;
  inputPlaceholder?: string;
  actions: ModalAction[];
}

export interface RouteState {
  kind: 'home' | 'chat' | 'settings';
  chatId?: Id;
  section?: string;
}

export interface AppState {
  session: Session | null;
  phase: ClientPhase;
  phaseBeforeError: ClientPhase;
  chats: Chat[];
  currentChat: Chat | null;
  messages: Message[];
  capabilityRequests: CapabilityRequest[];
  personas: Persona[];
  workspaces: Workspace[];
  settings: Settings | null;
  models: string[];
  memories: Memory[];
  taskModels: TaskModelProfile[];
  taskModelRuns: TaskModelRun[];
  taskModelChecks: Partial<Record<TaskModelRole, TaskModelReadiness>>;
  taskModelBusy: Partial<Record<TaskModelRole, boolean>>;
  mediaCatalog: MediaCatalog | null;
  mediaCatalogBusy: boolean;
  mediaPlanPreview: MediaPlan | null;
  mediaCatalogIdentitySetupIntent: IdentitySetupIntent | null;
  identitySettings: IdentityValidationSettings | null;
  identityProfiles: Record<Id, VisualIdentityProfile>;
  identityValidations: Record<Id, IdentityValidation[]>;
  identityEvents: Record<Id, IdentityEvent[]>;
  identitySelectedPersonaId: Id | null;
  identityBusy: boolean;
  route: RouteState;
  statusText: string;
  uiError: string;
  authError: string;
  settingsError: string;
  drawerOpen: boolean;
  chatSearch: string;
  stickMessagesToBottom: boolean;
  showJumpBottom: boolean;
  showSettings: boolean;
  settingsSection: string;
  modal: ModalState | null;
  selectedPersonaId: Id | null;
  selectedModel: string | null;
  selectedMemoryMode: MemoryMode | null;
  draftMessage: string;
  showChatControlsMenu: boolean;
  showSystemMessages: boolean;
  showThinkingByDefault: boolean;
  thinkingExpanded: Record<string, boolean>;
  showViz: boolean;
  voiceResponsesEnabled: boolean;
  currentAudioMessageId: Id | null;
  messageAudioById: Record<Id, string>;
  pendingRequest: PendingRequest | null;
  recordingStartedAt: number;
  settingsSaving: boolean;
  settingsSavedAt: number;
  providerChecks: Record<string, ProviderCheckResult>;
  providerChecksRunning: Record<string, boolean>;
  backupItems: BackupItem[];
  backupsLoading: boolean;
  backupActionRunning: boolean;
  resourceCoordination: ResourceCoordinationStatus | null;
  resourceCoordinationEvents: ResourceCoordinationEvent[];
  resourceCoordinationBusy: boolean;
  memorySections: Record<string, boolean>;
  personaAvatarPreview: string;
  chatImagePreview: string;
  chatVideoPreview: string;
  revealedImages: Record<string, boolean>;
  showNewChatPersonaModal: boolean;
  newChatPersonaId: Id | null;
  onboardingRunning: boolean;
  sessionTimer: number | null;
  lastActivityAt: number;
}
