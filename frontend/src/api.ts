import type {
  BackupItem,
  BackupVerification,
  BulkActionResult,
  CapabilityRequest,
  Chat,
  ChatDetail,
  Job,
  Memory,
  MemoryEvent,
  MemoryMode,
  MediaCatalog,
  MediaCatalogResource,
  MediaCatalogSettings,
  MediaLibraryItem,
  MediaPlan,
  MediaPlanRequirements,
  IdentityEvent,
  IdentityReference,
  IdentityValidation,
  IdentityValidationSettings,
  Persona,
  ProviderCheckResult,
  ResourceCoordinationEvent,
  ResourceCoordinationStatus,
  Session,
  SettingsWire,
  TaskModelProfile,
  TaskModelReadiness,
  TaskModelRole,
  TaskModelRun,
  TurnAccepted,
  TurnEvent,
  VisualIdentityProfile,
  Workspace,
} from './types';

interface ErrorPayload {
  error?: string | { code?: string | number; message?: string };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface ChatCreateInput {
  workspace_id: string | null;
  persona_id: string | null;
  model: string | null;
  memory_mode: MemoryMode;
  title: string;
}

export interface PersonaInput {
  workspace_id: string;
  workspace_ids?: string[];
  name: string;
  avatar_url?: string | null;
  system_prompt?: string | null;
  personality_details?: string | null;
  traits?: Record<string, unknown>;
  default_model?: string | null;
  preferred_voice?: string | null;
  preferred_tts_model?: string | null;
  preferred_tts_speed?: string | null;
  preferred_voice_openai?: string | null;
  preferred_tts_model_openai?: string | null;
  preferred_tts_speed_openai?: string | null;
  preferred_voice_local?: string | null;
  preferred_tts_model_local?: string | null;
  preferred_tts_speed_local?: string | null;
}

export interface TurnInput {
  text: string;
  workspace_id: string | null;
  persona_id: string | null;
  model: string | null;
  memory_mode: MemoryMode;
  model_settings: {
    temperature?: number;
    top_p?: number;
    num_predict?: number;
    presence_penalty?: number;
    frequency_penalty?: number;
    context_window_tokens?: number;
  };
}

export interface MediaJobInput {
  prompt: string;
  chat_id?: string | null;
  provider?: string;
  model?: string;
  size?: string;
  quality?: string;
  seconds?: string;
  backend?: string;
  base_url?: string;
}

export interface MediaEditJobInput {
  prompt: string;
  operation: 'image_to_image' | 'inpaint' | 'outpaint';
  source_media_id: string;
  mask_media_id?: string;
  chat_id?: string | null;
  domains?: string[];
  content_tags?: string[];
  required_features?: string[];
}

export class ApiClient {
  constructor(private readonly base = '/api/v1') {}

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    const method = (init.method ?? 'GET').toUpperCase();
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      headers.set('X-Nice-Assistant-CSRF', '1');
    }
    if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    const response = await fetch(`${this.base}${path}`, {
      credentials: 'same-origin',
      ...init,
      headers,
    });
    const contentType = response.headers.get('content-type') ?? '';
    const payload = contentType.includes('application/json')
      ? ((await response.json()) as unknown)
      : await response.text();
    if (!response.ok) {
      const error = typeof payload === 'object' && payload !== null ? (payload as ErrorPayload).error : undefined;
      const message = typeof error === 'string' ? error : error?.message ?? `Request failed (${response.status})`;
      const code = typeof error === 'object' && error?.code !== undefined ? error.code : response.status;
      if (response.status === 401) window.dispatchEvent(new CustomEvent('nice:unauthorized'));
      throw new ApiError(message, response.status, code);
    }
    return payload as T;
  }

  createUser(username: string, password: string): Promise<{ id: string }> {
    return this.request('/users', { method: 'POST', body: JSON.stringify({ username, password }) });
  }

  login(username: string, password: string): Promise<Session> {
    return this.request('/session', { method: 'POST', body: JSON.stringify({ username, password }) });
  }

  session(): Promise<Session> {
    return this.request('/session');
  }

  logout(): Promise<{ ok: boolean }> {
    return this.request('/session', { method: 'DELETE' });
  }

  settings(): Promise<SettingsWire> {
    return this.request('/settings');
  }

  updateSettings(settings: SettingsWire): Promise<SettingsWire> {
    return this.request('/settings', { method: 'PUT', body: JSON.stringify(settings) });
  }

  workspaces(): Promise<{ items: Workspace[] }> {
    return this.request('/workspaces');
  }

  createWorkspace(name: string): Promise<Workspace> {
    return this.request('/workspaces', { method: 'POST', body: JSON.stringify({ name }) });
  }

  updateWorkspace(id: string, name: string): Promise<Workspace> {
    return this.request(`/workspaces/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ name }) });
  }

  deleteWorkspace(id: string): Promise<{ ok: boolean }> {
    return this.request(`/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  personas(): Promise<{ items: Persona[] }> {
    return this.request('/personas');
  }

  createPersona(input: PersonaInput): Promise<Persona> {
    return this.request('/personas', { method: 'POST', body: JSON.stringify(input) });
  }

  updatePersona(id: string, input: PersonaInput): Promise<Persona> {
    return this.request(`/personas/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(input) });
  }

  deletePersona(id: string): Promise<{ ok: boolean }> {
    return this.request(`/personas/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  identitySettings(): Promise<IdentityValidationSettings> {
    return this.request('/identity-validation/settings');
  }

  updateIdentitySettings(settings: IdentityValidationSettings): Promise<IdentityValidationSettings> {
    return this.request('/identity-validation/settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  }

  checkIdentityProvider(): Promise<ProviderCheckResult> {
    return this.request('/identity-validation/check', { method: 'POST' });
  }

  visualIdentity(personaId: string): Promise<VisualIdentityProfile> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity`);
  }

  updateVisualIdentity(personaId: string, profile: VisualIdentityProfile): Promise<VisualIdentityProfile> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity`, {
      method: 'PUT',
      body: JSON.stringify({
        appearance_description: profile.appearance_description,
        acceptance_threshold: profile.acceptance_threshold,
        max_generation_attempts: profile.max_generation_attempts,
        failure_policy: profile.failure_policy,
      }),
    });
  }

  grantIdentityConsent(personaId: string): Promise<VisualIdentityProfile> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/consent`, {
      method: 'POST',
      body: JSON.stringify({ attested: true }),
    });
  }

  withdrawIdentityConsent(personaId: string): Promise<VisualIdentityProfile> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/consent`, { method: 'DELETE' });
  }

  uploadIdentityReference(
    personaId: string,
    file: File,
    provenance: IdentityReference['provenance'],
  ): Promise<IdentityReference> {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('provenance', provenance);
    form.append('attested', 'true');
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/references`, {
      method: 'POST',
      body: form,
    });
  }

  identityReferenceFromMedia(personaId: string, mediaId: string): Promise<IdentityReference> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/references/from-media`, {
      method: 'POST',
      body: JSON.stringify({ media_id: mediaId, attested: true }),
    });
  }

  approveIdentityReference(id: string): Promise<IdentityReference> {
    return this.request(`/identity-references/${encodeURIComponent(id)}/approval`, { method: 'POST' });
  }

  rejectIdentityReference(id: string, reason: string): Promise<IdentityReference> {
    return this.request(`/identity-references/${encodeURIComponent(id)}/rejection`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  }

  deleteIdentityReference(id: string): Promise<{ ok: boolean }> {
    return this.request(`/identity-references/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  validateIdentityMedia(personaId: string, mediaId: string): Promise<{ validation: IdentityValidation; job: Job }> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/validations`, {
      method: 'POST',
      body: JSON.stringify({ media_id: mediaId }),
    });
  }

  identityValidations(personaId: string): Promise<{ items: IdentityValidation[] }> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/validations`);
  }

  mediaLibrary(kind: 'image' | 'video' = 'image', limit = 100): Promise<{ items: MediaLibraryItem[] }> {
    return this.request(`/media?kind=${encodeURIComponent(kind)}&limit=${encodeURIComponent(String(limit))}`);
  }

  identityHistory(personaId: string): Promise<{ items: IdentityEvent[] }> {
    return this.request(`/personas/${encodeURIComponent(personaId)}/visual-identity/history`);
  }

  chats(): Promise<{ items: Chat[] }> {
    return this.request('/chats');
  }

  createChat(input: ChatCreateInput): Promise<Chat> {
    return this.request('/chats', { method: 'POST', body: JSON.stringify(input) });
  }

  chat(id: string): Promise<ChatDetail> {
    return this.request(`/chats/${encodeURIComponent(id)}`);
  }

  updateChat(id: string, input: Partial<Pick<Chat, 'title' | 'model_override' | 'memory_mode' | 'persona_id' | 'hidden_in_ui'>>): Promise<Chat> {
    return this.request(`/chats/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(input) });
  }

  hideChat(id: string): Promise<{ ok: boolean; id: string; hidden: boolean }> {
    return this.request(`/chats/${encodeURIComponent(id)}/hide`, { method: 'POST' });
  }

  deleteChat(id: string): Promise<{ ok: boolean; id: string; deleted: boolean }> {
    return this.request(`/chats/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  bulkChatAction(action: 'hide' | 'delete', ids: string[]): Promise<BulkActionResult> {
    return this.request('/chats/bulk-actions', {
      method: 'POST',
      body: JSON.stringify({ action, ids }),
    });
  }

  createTurn(chatId: string, input: TurnInput): Promise<TurnAccepted> {
    return this.request(`/chats/${encodeURIComponent(chatId)}/turns`, {
      method: 'POST',
      body: JSON.stringify(input),
    });
  }

  job(id: string): Promise<Job> {
    return this.request(`/jobs/${encodeURIComponent(id)}`);
  }

  cancelJob(id: string): Promise<Job> {
    return this.request(`/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  capabilityRequests(chatId?: string): Promise<{ items: CapabilityRequest[] }> {
    const query = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : '';
    return this.request(`/capability-requests${query}`);
  }

  capabilityRequest(id: string): Promise<CapabilityRequest> {
    return this.request(`/capability-requests/${encodeURIComponent(id)}`);
  }

  approveCapability(id: string): Promise<CapabilityRequest> {
    return this.request(`/capability-requests/${encodeURIComponent(id)}/approval`, { method: 'POST' });
  }

  denyCapability(id: string): Promise<CapabilityRequest> {
    return this.request(`/capability-requests/${encodeURIComponent(id)}/denial`, { method: 'POST' });
  }

  cancelCapability(id: string): Promise<CapabilityRequest> {
    return this.request(`/capability-requests/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  mediaCatalog(): Promise<MediaCatalog> {
    return this.request('/media-catalog');
  }

  updateMediaCatalogSettings(settings: MediaCatalogSettings): Promise<MediaCatalogSettings> {
    return this.request('/media-catalog/settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  }

  createMediaCatalogResource(resource: Omit<MediaCatalogResource, 'id' | 'revision' | 'created_at' | 'updated_at'>): Promise<MediaCatalogResource> {
    return this.request('/media-catalog/resources', {
      method: 'POST',
      body: JSON.stringify(resource),
    });
  }

  updateMediaCatalogResource(resource: MediaCatalogResource): Promise<MediaCatalogResource> {
    const { id, revision: _revision, created_at: _created, updated_at: _updated, ...body } = resource;
    return this.request(`/media-catalog/resources/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  deleteMediaCatalogResource(id: string): Promise<{ ok: boolean }> {
    return this.request(`/media-catalog/resources/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  previewMediaPlan(requirements: MediaPlanRequirements): Promise<MediaPlan> {
    return this.request('/media-catalog/plan-previews', {
      method: 'POST',
      body: JSON.stringify(requirements),
    });
  }

  taskModels(): Promise<{ items: TaskModelProfile[] }> {
    return this.request('/task-models');
  }

  updateTaskModel(profile: TaskModelProfile): Promise<TaskModelProfile> {
    const body = {
      enabled: profile.enabled,
      provider: profile.provider,
      model: profile.model,
      fallback_provider: profile.fallback_provider,
      fallback_model: profile.fallback_model,
      max_input_tokens: profile.max_input_tokens,
      max_output_tokens: profile.max_output_tokens,
      timeout_seconds: profile.timeout_seconds,
      temperature: profile.temperature,
      fallback_policy: profile.fallback_policy,
    };
    return this.request(`/task-models/${encodeURIComponent(profile.role)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  checkTaskModel(role: TaskModelRole): Promise<TaskModelReadiness> {
    return this.request(`/task-models/${encodeURIComponent(role)}/check`, { method: 'POST' });
  }

  taskModelRuns(role?: TaskModelRole, limit = 50): Promise<{ items: TaskModelRun[] }> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (role) query.set('role', role);
    return this.request(`/task-model-runs?${query.toString()}`);
  }

  models(): Promise<{ models: string[] }> {
    return this.request('/models');
  }

  providerCheck(provider: string, settings: object): Promise<ProviderCheckResult> {
    return this.request('/provider-checks', {
      method: 'POST',
      body: JSON.stringify({ provider, settings }),
    });
  }

  memories(status?: string): Promise<{ items: Memory[] }> {
    const query = status ? `?status=${encodeURIComponent(status)}` : '';
    return this.request(`/memories${query}`);
  }

  createMemory(scope: string, scopeId: string | null, content: string): Promise<Memory> {
    return this.request('/memories', {
      method: 'POST',
      body: JSON.stringify({ scope, scope_id: scopeId, content }),
    });
  }

  updateMemory(id: string, scope: string, scopeId: string | null, content: string): Promise<Memory> {
    return this.request(`/memories/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({ scope, scope_id: scopeId, content }),
    });
  }

  memoryAction(id: string, action: 'approve' | 'reject' | 'forget' | 'undo'): Promise<Memory> {
    return this.request(`/memories/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
  }

  deleteMemory(id: string): Promise<{ ok: boolean; id: string; deleted: boolean }> {
    return this.request(`/memories/${encodeURIComponent(id)}`, { method: 'DELETE' });
  }

  bulkMemoryAction(action: 'forget' | 'delete', ids: string[]): Promise<BulkActionResult> {
    return this.request('/memories/bulk-actions', {
      method: 'POST',
      body: JSON.stringify({ action, ids }),
    });
  }

  memoryHistory(id: string): Promise<{ memory: Memory; events: MemoryEvent[] }> {
    return this.request(`/memories/${encodeURIComponent(id)}/history`);
  }

  imageJob(input: MediaJobInput): Promise<{ job_id: string; capability_request_id: string; chat_id: string | null; status: JobStatus }> {
    return this.request('/media/image-jobs', { method: 'POST', body: JSON.stringify(input) });
  }

  imageEditJob(input: MediaEditJobInput): Promise<{ job_id: string; capability_request_id: string; chat_id: string | null; status: JobStatus }> {
    return this.request('/media/image-edit-jobs', { method: 'POST', body: JSON.stringify(input) });
  }

  videoJob(input: MediaJobInput): Promise<{ job_id: string; capability_request_id: string; chat_id: string | null; status: JobStatus }> {
    return this.request('/media/video-jobs', { method: 'POST', body: JSON.stringify(input) });
  }

  voices(baseUrl: string): Promise<{ voices: string[] }> {
    return this.request(`/speech/voices?base_url=${encodeURIComponent(baseUrl)}`);
  }

  synthesize(input: Record<string, unknown>): Promise<{ audio_id: string; audio_url: string; format: string }> {
    return this.request('/speech/syntheses', { method: 'POST', body: JSON.stringify(input) });
  }

  transcribe(file: Blob, filename: string): Promise<{ text: string }> {
    const form = new FormData();
    form.append('file', file, filename);
    return this.request('/speech/transcriptions', { method: 'POST', body: form });
  }

  backups(): Promise<{ items: BackupItem[] }> {
    return this.request('/admin/backups');
  }

  createBackup(includeMedia: boolean): Promise<Record<string, unknown>> {
    return this.request('/admin/backups', { method: 'POST', body: JSON.stringify({ include_media: includeMedia }) });
  }

  deleteBackup(name: string): Promise<{ ok: boolean }> {
    return this.request(`/admin/backups/${encodeURIComponent(name)}`, { method: 'DELETE' });
  }

  verifyBackup(name: string): Promise<BackupVerification> {
    return this.request(`/admin/backups/${encodeURIComponent(name)}/verify`, { method: 'POST' });
  }

  resourceCoordination(): Promise<ResourceCoordinationStatus> {
    return this.request('/admin/resource-coordination');
  }

  saveResourceCoordination(input: ResourceCoordinationStatus): Promise<ResourceCoordinationStatus> {
    return this.request('/admin/resource-coordination', {
      method: 'PUT',
      body: JSON.stringify({
        ...input.settings,
        authorizations: input.endpoints.map((endpoint) => ({
          provider: endpoint.provider,
          exclusive_control: endpoint.authorization.exclusive_control,
          allow_release: endpoint.authorization.allow_release,
        })),
      }),
    });
  }

  checkResourceCoordination(): Promise<ResourceCoordinationStatus> {
    return this.request('/admin/resource-coordination/check', { method: 'POST' });
  }

  resourceCoordinationEvents(limit = 100): Promise<{ items: ResourceCoordinationEvent[] }> {
    return this.request(`/admin/resource-coordination/events?limit=${limit}`);
  }

  backupDownloadUrl(name: string): string {
    return `${this.base}/admin/backups/${encodeURIComponent(name)}/download`;
  }

  diagnosticLogUrl(): string {
    return `${this.base}/admin/diagnostics/log`;
  }

  mediaUrl(mediaId: string): string {
    return `${this.base}/media/${encodeURIComponent(mediaId)}`;
  }

  async clientEvent(type: string, message: string, details: Record<string, unknown> = {}): Promise<void> {
    try {
      await this.request('/diagnostics/client-events', {
        method: 'POST',
        body: JSON.stringify({ type, message, details }),
      });
    } catch {
      // Diagnostics must never block the product flow.
    }
  }

  async streamTurn(
    turnId: string,
    onEvent: (event: TurnEvent) => void,
    signal: AbortSignal,
    lastEventId?: number,
  ): Promise<void> {
    const headers = new Headers({ Accept: 'text/event-stream' });
    if (lastEventId !== undefined) headers.set('Last-Event-ID', String(lastEventId));
    const response = await fetch(`${this.base}/turns/${encodeURIComponent(turnId)}/events`, {
      credentials: 'same-origin',
      headers,
      signal,
    });
    if (!response.ok || !response.body) {
      throw new ApiError(`Turn stream failed (${response.status})`, response.status, response.status);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseFrame(frame);
        if (parsed) onEvent(parsed);
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }
  }
}

function parseSseFrame(frame: string): TurnEvent | null {
  if (!frame || frame.startsWith(':')) return null;
  let id: number | null = null;
  let event = 'message';
  const data: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('id:')) id = Number(line.slice(3).trim());
    else if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  const value = JSON.parse(data.join('\n')) as unknown;
  return {
    id: Number.isFinite(id) ? id : null,
    event,
    data: typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : { value },
  };
}

type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export const api = new ApiClient();
