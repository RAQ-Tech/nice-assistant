import type { SettingScalar, Settings, SettingsWire } from './types';

export const SETTINGS_DEFAULTS: Settings = {
  global_default_model: '',
  default_memory_mode: 'saved',
  stt_provider: 'disabled',
  tts_provider: 'disabled',
  tts_format: 'wav',
  openai_api_key: '',
  onboarding_done: false,
  general_theme: 'dark',
  general_show_system_messages: false,
  general_show_thinking: false,
  general_auto_logout: true,
  general_voice_responses: true,
  general_show_viz: false,
  tts_voice_openai: 'marin',
  tts_model_openai: 'gpt-4o-mini-tts',
  tts_speed_openai: '1',
  tts_instructions_openai: '',
  tts_voice_local: 'af_heart',
  tts_model_local: 'kokoro',
  tts_speed_local: '1',
  tts_local_base_url: '',
  tts_voice_filter_regions: [],
  tts_voice_filter_genders: [],
  tts_voice_filter_query: '',
  stt_language: 'auto',
  stt_store_recordings: false,
  image_provider: 'disabled',
  image_confirmation_policy: 'auto_explicit_request',
  chat_blur_images: false,
  image_size: '1024x1024',
  image_quality: 'none',
  image_local_allow_nsfw: false,
  image_local_backend: 'automatic1111',
  image_local_base_url: '',
  image_local_api_auth: '',
  image_local_model: '',
  image_local_steps: '28',
  image_local_sampler_name: 'DPM++ 2M Karras',
  image_local_scheduler: '',
  image_local_cfg_scale: '7',
  image_local_seed: '',
  image_local_additional_parameters: '',
  video_provider: 'disabled',
  video_model: 'sora-2',
  video_size: '720x1280',
  video_duration: '4',
  models_context_window_tokens: '4096',
  user_display_name: '',
  user_timezone: 'local',
  personas_default_system_prompt: 'Be helpful and concise.',
  workspaces_default_workspace_id: '',
  models_temperature: '0.7',
  models_top_p: '1',
  models_num_predict: '512',
  models_presence_penalty: '0',
  models_frequency_penalty: '0',
  model_overrides: {},
};

export const SETTINGS_SECTIONS = [
  'General',
  'TTS',
  'STT',
  'Image Generation',
  'Video Generation',
  'Memory',
  'User',
  'Personas',
  'Workspaces',
  'Models',
  'Task Models',
  'Media Catalog',
  'Visual Identity',
  'GPU Coordination',
  'Data',
] as const;

export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

export const SETTINGS_SECTION_KEYS: Record<SettingsSection, readonly (keyof Settings)[]> = {
  General: ['general_theme', 'general_show_system_messages', 'general_show_thinking', 'general_auto_logout', 'global_default_model'],
  TTS: [
    'tts_provider',
    'tts_format',
    'tts_voice_openai',
    'tts_model_openai',
    'tts_speed_openai',
    'tts_instructions_openai',
    'tts_voice_local',
    'tts_model_local',
    'tts_speed_local',
    'tts_local_base_url',
  ],
  STT: ['stt_provider', 'stt_language', 'stt_store_recordings'],
  'Image Generation': [
    'image_provider',
    'image_confirmation_policy',
    'chat_blur_images',
    'image_size',
    'image_quality',
    'image_local_allow_nsfw',
    'image_local_backend',
    'image_local_base_url',
    'image_local_api_auth',
    'image_local_model',
    'image_local_steps',
    'image_local_sampler_name',
    'image_local_scheduler',
    'image_local_cfg_scale',
    'image_local_seed',
    'image_local_additional_parameters',
  ],
  'Video Generation': ['video_provider', 'video_model', 'video_size', 'video_duration'],
  Memory: ['default_memory_mode'],
  User: ['user_display_name', 'user_timezone'],
  Personas: ['personas_default_system_prompt'],
  Workspaces: ['workspaces_default_workspace_id'],
  Models: [
    'global_default_model',
    'models_temperature',
    'models_top_p',
    'models_num_predict',
    'models_context_window_tokens',
    'models_presence_penalty',
    'models_frequency_penalty',
    'model_overrides',
  ],
  'Task Models': [],
  'Media Catalog': [],
  'Visual Identity': [],
  'GPU Coordination': [],
  Data: [],
};

const IMAGE_QUALITY = new Set(['none', 'low', 'medium', 'high', 'auto']);
const IMAGE_SIZES = new Set(['1024x1024', '1024x1536', '1536x1024', 'auto']);
const VIDEO_MODELS = new Set(['sora-2', 'sora-2-pro']);
const VIDEO_SECONDS = new Set(['4', '8', '12']);
const VIDEO_SIZES: Record<string, readonly string[]> = {
  'sora-2': ['720x1280', '1280x720'],
  'sora-2-pro': ['720x1280', '1280x720', '1024x1792', '1792x1024'],
};

export function normalizeSettings(wire: SettingsWire): Settings {
  const preferences = { ...wire.preferences };
  delete preferences.image_prompt_generation;
  const rawImageProvider = String(preferences.image_provider ?? '').trim().toLowerCase();
  if (rawImageProvider === 'local/automatic1111') {
    preferences.image_provider = 'local';
    preferences.image_local_backend = 'automatic1111';
  } else if (rawImageProvider === 'local/comfyui') {
    preferences.image_provider = 'local';
    preferences.image_local_backend = 'comfyui';
  }
  const values = {
    ...SETTINGS_DEFAULTS,
    ...preferences,
    global_default_model: wire.global_default_model ?? '',
    default_memory_mode: wire.default_memory_mode === 'off' ? 'off' : 'saved',
    stt_provider: wire.stt_provider || 'disabled',
    tts_provider: wire.tts_provider || 'disabled',
    tts_format: wire.tts_format || 'wav',
    openai_api_key: wire.openai_api_key ?? '',
    onboarding_done: Boolean(wire.onboarding_done),
  } as Settings;
  values.image_quality = normalizeImageQuality(values.image_quality);
  values.image_size = normalizeImageSize(values.image_size);
  values.image_local_backend = ['automatic1111', 'comfyui'].includes(String(values.image_local_backend).toLowerCase())
    ? String(values.image_local_backend).toLowerCase()
    : SETTINGS_DEFAULTS.image_local_backend;
  values.video_model = normalizeVideoModel(values.video_model);
  values.video_duration = normalizeVideoDuration(values.video_duration);
  values.video_size = normalizeVideoSize(values.video_model, values.video_size);
  values.tts_voice_filter_regions = stringArray(values.tts_voice_filter_regions);
  values.tts_voice_filter_genders = stringArray(values.tts_voice_filter_genders);
  values.model_overrides = isRecord(values.model_overrides)
    ? (values.model_overrides as Record<string, Record<string, SettingScalar>>)
    : {};
  return values;
}

export function settingsWire(settings: Settings): SettingsWire {
  const preferences: Record<string, unknown> = {};
  const core = new Set<keyof Settings>([
    'global_default_model',
    'default_memory_mode',
    'stt_provider',
    'tts_provider',
    'tts_format',
    'openai_api_key',
    'onboarding_done',
  ]);
  for (const [key, value] of Object.entries(settings)) {
    if (!core.has(key)) preferences[key] = value;
  }
  preferences.image_size = normalizeImageSize(settings.image_size);
  preferences.image_quality = normalizeImageQuality(settings.image_quality);
  preferences.video_model = normalizeVideoModel(settings.video_model);
  preferences.video_size = normalizeVideoSize(settings.video_model, settings.video_size);
  preferences.video_duration = normalizeVideoDuration(settings.video_duration);
  return {
    global_default_model: settings.global_default_model || null,
    default_memory_mode: settings.default_memory_mode,
    stt_provider: settings.stt_provider,
    tts_provider: settings.tts_provider,
    tts_format: settings.tts_format,
    openai_api_key: settings.openai_api_key || null,
    onboarding_done: Boolean(settings.onboarding_done),
    preferences,
  };
}

export function resetSettingsSection(settings: Settings, section: SettingsSection): void {
  for (const key of SETTINGS_SECTION_KEYS[section]) {
    settings[key] = structuredClone(SETTINGS_DEFAULTS[key]) as never;
  }
}

export function modelSettings(settings: Settings, model: string): Record<string, number> {
  const override = settings.model_overrides[model] ?? {};
  return {
    temperature: numeric(override.temperature ?? settings.models_temperature, 0.7),
    top_p: numeric(override.top_p ?? settings.models_top_p, 1),
    num_predict: integer(override.num_predict ?? settings.models_num_predict, 512),
    presence_penalty: numeric(override.presence_penalty ?? settings.models_presence_penalty, 0),
    frequency_penalty: numeric(override.frequency_penalty ?? settings.models_frequency_penalty, 0),
    context_window_tokens: integer(override.context_window_tokens ?? settings.models_context_window_tokens, 4096),
  };
}

export function setModelSetting(settings: Settings, model: string, key: string, value: SettingScalar): void {
  settings.model_overrides[model] = { ...(settings.model_overrides[model] ?? {}), [key]: value };
}

export function normalizeImageSize(value: unknown): string {
  const candidate = String(value ?? '').trim().toLowerCase();
  return IMAGE_SIZES.has(candidate) || /^\d{2,5}x\d{2,5}$/.test(candidate)
    ? candidate
    : SETTINGS_DEFAULTS.image_size;
}

export function normalizeImageQuality(value: unknown): string {
  const candidate = String(value ?? '').trim().toLowerCase();
  const normalized = candidate === 'standard' ? 'medium' : candidate === 'hd' ? 'high' : candidate;
  return IMAGE_QUALITY.has(normalized) ? normalized : SETTINGS_DEFAULTS.image_quality;
}

export function normalizeVideoModel(value: unknown): string {
  const candidate = String(value ?? '').trim().toLowerCase();
  return VIDEO_MODELS.has(candidate) ? candidate : SETTINGS_DEFAULTS.video_model;
}

export function availableVideoSizes(model: unknown): readonly string[] {
  return VIDEO_SIZES[normalizeVideoModel(model)] ?? VIDEO_SIZES['sora-2'] ?? [];
}

export function normalizeVideoSize(model: unknown, value: unknown): string {
  const options = availableVideoSizes(model);
  const candidate = String(value ?? '').trim().toLowerCase();
  return options.includes(candidate) ? candidate : (options[0] ?? SETTINGS_DEFAULTS.video_size);
}

export function normalizeVideoDuration(value: unknown): string {
  const candidate = String(value ?? '').trim();
  return VIDEO_SECONDS.has(candidate) ? candidate : SETTINGS_DEFAULTS.video_duration;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function numeric(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function integer(value: unknown, fallback: number): number {
  return Math.round(numeric(value, fallback));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
