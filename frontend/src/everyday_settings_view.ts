import { el } from './dom';
import { availableVideoSizes, SETTINGS_DEFAULTS } from './settings';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import { advancedSettings, settingsCard, settingsHeading, settingsIntro } from './settings_ui';
import type { AppState, Settings } from './types';

export type EverydaySettingsSection =
  | 'General'
  | 'TTS'
  | 'STT'
  | 'Image Generation'
  | 'Video Generation'
  | 'User';

export type SettingChange = <K extends keyof Settings>(
  key: K,
  value: Settings[K],
  shouldRender?: boolean,
) => void;

export class EverydaySettingsView {
  constructor(
    private readonly appState: AppState,
    private readonly change: SettingChange,
    private readonly providerControl: (provider: string) => HTMLElement,
    private readonly providerPanel: () => HTMLElement,
  ) {}

  nodes(section: EverydaySettingsSection, settings: Settings): HTMLElement[] {
    if (section === 'General') return this.general(settings);
    if (section === 'TTS') return this.tts(settings);
    if (section === 'STT') return this.stt(settings);
    if (section === 'Image Generation') return this.image(settings);
    if (section === 'Video Generation') return this.video(settings);
    return this.user(settings);
  }

  private general(settings: Settings): HTMLElement[] {
    return [
      settingsIntro(
        'Choose the everyday experience',
        'Set the appearance, default model, and reply behavior most conversations should use.',
      ),
      settingsCard([
        selectField(
          'Theme',
          settings.general_theme,
          ['dark', 'light'],
          (value) => this.change('general_theme', value),
          undefined,
          titleCase,
          true,
          'Changes the interface colors for this Nice Assistant account.',
        ),
        selectField(
          'Default model',
          settings.global_default_model,
          ['', ...this.appState.models],
          (value) => this.change('global_default_model', value),
          undefined,
          (value) => value || 'Automatic',
          true,
          'Used when a persona or chat has not selected a different model.',
        ),
        toggleField(
          'Speak assistant replies',
          settings.general_voice_responses,
          (value) => this.change('general_voice_responses', value),
          'Automatically plays completed speech for assistant replies when TTS is configured.',
        ),
        toggleField(
          'Show the audio visualizer',
          settings.general_show_viz,
          (value) => this.change('general_show_viz', value),
          'Displays the playback visualization while assistant audio is playing.',
        ),
      ]),
      advancedSettings(
        'Interface, session, and connection details',
        'Optional visibility, session-expiry, and provider-diagnostic controls.',
        [
          settingsCard([
            toggleField(
              'Show system and tool messages',
              settings.general_show_system_messages,
              (value) => this.change('general_show_system_messages', value),
              'Shows technical messages that are normally hidden from the conversation.',
            ),
            toggleField(
              'Show model thinking by default',
              settings.general_show_thinking,
              (value) => this.change('general_show_thinking', value),
              'Shows reasoning content only when the selected model and provider return it.',
            ),
            toggleField(
              'Expire inactive sessions automatically',
              settings.general_auto_logout,
              (value) => this.change('general_auto_logout', value),
              'Ends an inactive browser session after the server-configured session lifetime.',
            ),
          ]),
          this.providerPanel(),
        ],
        { testId: 'general-advanced-settings' },
      ),
    ];
  }

  private tts(settings: Settings): HTMLElement[] {
    const common: HTMLElement[] = [
      selectField(
        'Speech provider',
        settings.tts_provider,
        ['disabled', 'local', 'openai'],
        (value) => this.change('tts_provider', value),
        'tts-provider',
        providerLabel,
        true,
        'Local uses the configured Kokoro LAN service. OpenAI sends reply text to OpenAI for speech generation.',
      ),
    ];
    const advanced: HTMLElement[] = [
      selectField(
        'Completed audio format',
        settings.tts_format,
        ['wav', 'mp3', 'opus', 'aac', 'flac'],
        (value) => this.change('tts_format', value),
        undefined,
        titleCase,
        true,
        'The format stored for completed playback. Live streaming speech is not implemented yet.',
      ),
    ];
    if (settings.tts_provider === 'openai') {
      common.push(
        inputField(
          'Voice',
          settings.tts_voice_openai,
          (value) => this.change('tts_voice_openai', value),
          'text',
          true,
          'The OpenAI voice name used unless a persona overrides it.',
        ),
        selectField(
          'Speech model',
          settings.tts_model_openai,
          ['gpt-4o-mini-tts', 'tts-1', 'tts-1-hd'],
          (value) => this.change('tts_model_openai', value),
          undefined,
          (value) => value,
          true,
          'The OpenAI model that generates completed speech audio.',
        ),
        inputField(
          'Speaking speed',
          settings.tts_speed_openai,
          (value) => this.change('tts_speed_openai', value),
          'number',
          true,
          'A multiplier where 1 is the provider default.',
        ),
      );
      advanced.push(
        textareaField(
          'Voice direction',
          settings.tts_instructions_openai,
          (value) => this.change('tts_instructions_openai', value),
          true,
          'Optional performance guidance such as warmth, pacing, or emotional tone.',
        ),
      );
    } else if (settings.tts_provider === 'local') {
      common.push(
        inputField(
          'Kokoro service address',
          settings.tts_local_base_url,
          (value) => this.change('tts_local_base_url', value),
          'url',
          true,
          'The private-LAN address of the separately deployed Kokoro-compatible service.',
        ),
        inputField(
          'Voice',
          settings.tts_voice_local,
          (value) => this.change('tts_voice_local', value),
          'text',
          true,
          'The local service voice used unless a persona overrides it.',
        ),
        inputField(
          'Speaking speed',
          settings.tts_speed_local,
          (value) => this.change('tts_speed_local', value),
          'number',
          true,
          'A multiplier where 1 is the provider default.',
        ),
      );
      advanced.push(
        inputField(
          'Local model name',
          settings.tts_model_local,
          (value) => this.change('tts_model_local', value),
          'text',
          true,
          'Passed to the local speech service when it supports model selection.',
        ),
      );
    }
    return [
      settingsIntro(
        'Choose how replies sound',
        'Speech currently uses completed-audio playback. Streaming and interruption remain future voice work.',
      ),
      settingsCard(common),
      settings.tts_provider === 'disabled'
        ? el('div', { class: 'settings-empty-state', textContent: 'Speech playback is off.' })
        : settingsCard([
            settingsHeading('Connection check', 'Tests the selected service without changing saved settings.'),
            this.providerControl(settings.tts_provider === 'local' ? 'kokoro' : 'openai'),
          ]),
      advancedSettings(
        'Speech file and provider details',
        'Optional format and provider-specific controls.',
        advanced,
        { testId: 'tts-advanced-settings' },
      ),
    ];
  }

  private stt(settings: Settings): HTMLElement[] {
    return [
      settingsIntro(
        'Choose how recorded speech becomes text',
        'Push-to-talk can use OpenAI transcription. Local and live streaming transcription are not implemented yet.',
      ),
      settingsCard([
        selectField(
          'Transcription provider',
          settings.stt_provider,
          ['disabled', 'openai'],
          (value) => this.change('stt_provider', value),
          'stt-provider',
          providerLabel,
          true,
          'OpenAI uploads each completed push-to-talk recording for transcription.',
        ),
        selectField(
          'Language',
          settings.stt_language,
          ['auto', 'en', 'es', 'fr', 'de'],
          (value) => this.change('stt_language', value),
          undefined,
          languageLabel,
          true,
          'Automatic detection is convenient; a fixed language can improve consistency.',
        ),
      ]),
      settings.stt_provider === 'openai'
        ? settingsCard([
            settingsHeading('Connection check', 'Tests whether OpenAI transcription is configured and reachable.'),
            this.providerControl('openai'),
          ])
        : el('div', { class: 'settings-empty-state', textContent: 'Voice transcription is off.' }),
      advancedSettings(
        'Recording retention',
        'Optional storage behavior for source microphone recordings.',
        [toggleField(
          'Keep source recordings',
          settings.stt_store_recordings,
          (value) => this.change('stt_store_recordings', value),
          'Keeps the original recording after transcription. Leave off for the more private default.',
        )],
        { testId: 'stt-advanced-settings' },
      ),
    ];
  }

  private image(settings: Settings): HTMLElement[] {
    const common: HTMLElement[] = [
      selectField(
        'Image provider',
        settings.image_provider,
        ['disabled', 'local', 'openai'],
        (value) => this.change('image_provider', value),
        'image-provider',
        providerLabel,
        true,
        'Local uses Automatic1111 or ComfyUI on your LAN. OpenAI sends the prompt to OpenAI.',
      ),
      inputField(
        'Resolution',
        settings.image_size,
        (value) => this.change('image_size', value),
        'text',
        true,
        'Enter width × height, for example 1024x1024. Provider support still determines what can run.',
      ),
      selectField(
        'Prompt enhancement quality',
        settings.image_quality,
        ['none', 'low', 'medium', 'high', 'auto'],
        (value) => this.change('image_quality', value),
        undefined,
        titleCase,
        true,
        'Controls provider-specific prompt enhancement. None preserves the prompt most directly.',
      ),
    ];
    const advanced: HTMLElement[] = [];
    if (settings.image_provider === 'local') {
      common.push(
        selectField(
          'Local image service',
          settings.image_local_backend,
          ['automatic1111', 'comfyui'],
          (value) => this.change('image_local_backend', value),
          undefined,
          (value) => value === 'automatic1111' ? 'Automatic1111' : 'ComfyUI',
          true,
          'Choose the API exposed by the local image container.',
        ),
        inputField(
          'Service address',
          settings.image_local_base_url,
          (value) => this.change('image_local_base_url', value),
          'url',
          true,
          'The private-LAN address of the selected image service.',
        ),
        inputField(
          'Model or checkpoint',
          settings.image_local_model,
          (value) => this.change('image_local_model', value),
          'text',
          true,
          'Used by direct image actions. Platform-planned persona images use the Media Catalog selection instead.',
        ),
        toggleField(
          'Allow explicit local prompts',
          settings.image_local_allow_nsfw,
          (value) => this.change('image_local_allow_nsfw', value),
          'Allows explicit prompt content only for the self-hosted local image path.',
        ),
      );
      advanced.push(
        inputField(
          'Basic authentication',
          settings.image_local_api_auth,
          (value) => this.change('image_local_api_auth', value),
          'password',
          true,
          'Optional user:password credentials for the local image service.',
        ),
        inputField('Steps', settings.image_local_steps, (value) => this.change('image_local_steps', value), 'number', true, 'Higher values may refine an image but take longer.'),
        inputField('Sampler', settings.image_local_sampler_name, (value) => this.change('image_local_sampler_name', value), 'text', true, 'Sampling algorithm passed to compatible local backends.'),
        inputField('Scheduler', settings.image_local_scheduler, (value) => this.change('image_local_scheduler', value), 'text', true, 'Optional scheduler name for compatible local backends.'),
        inputField('CFG scale', settings.image_local_cfg_scale, (value) => this.change('image_local_cfg_scale', value), 'number', true, 'Controls how strongly generation follows the prompt.'),
        inputField('Seed', settings.image_local_seed, (value) => this.change('image_local_seed', value), 'text', true, 'Reuse a seed for repeatability, or leave blank for a new result.'),
        textareaField(
          'Additional JSON parameters',
          settings.image_local_additional_parameters,
          (value) => this.change('image_local_additional_parameters', value),
          true,
          'Advanced provider payload values. Invalid or unsupported fields can make generation fail.',
        ),
      );
    }
    return [
      settingsIntro(
        'Choose the default image path',
        'These defaults power direct image actions. Persona-planned generation may select richer Media Catalog resources.',
      ),
      settingsCard(common),
      settings.image_provider === 'disabled'
        ? el('div', { class: 'settings-empty-state', textContent: 'Image generation is off.' })
        : settingsCard([
            settingsHeading('Connection check', 'Tests the selected image service with the current unsaved values.'),
            this.providerControl(settings.image_provider === 'local' ? settings.image_local_backend : 'openai'),
          ]),
      settings.image_provider === 'local'
        ? advancedSettings(
            'Local generation tuning',
            'Optional authentication and sampling controls for direct local image actions.',
            advanced,
            { testId: 'image-advanced-settings' },
          )
        : null,
    ].filter((node): node is HTMLElement => node !== null);
  }

  private video(settings: Settings): HTMLElement[] {
    return [
      settingsIntro(
        'Choose the default video path',
        'Video generation is optional and currently uses completed OpenAI jobs.',
      ),
      settingsCard([
        selectField(
          'Video provider',
          settings.video_provider,
          ['disabled', 'openai'],
          (value) => this.change('video_provider', value),
          'video-provider',
          providerLabel,
          true,
          'OpenAI sends the prompt to OpenAI and polls until the video is complete.',
        ),
        selectField(
          'Model',
          settings.video_model,
          ['sora-2', 'sora-2-pro'],
          (value) => {
            this.change('video_model', value, false);
            this.change('video_size', availableVideoSizes(value)[0] ?? SETTINGS_DEFAULTS.video_size);
          },
          undefined,
          (value) => value,
          true,
          'The selected model determines the available sizes and provider cost.',
        ),
        selectField(
          'Size',
          settings.video_size,
          [...availableVideoSizes(settings.video_model)],
          (value) => this.change('video_size', value),
          undefined,
          (value) => value,
          true,
          'Output width × height. Portrait sizes are listed with the narrower dimension first.',
        ),
        selectField(
          'Duration',
          settings.video_duration,
          ['4', '8', '12'],
          (value) => this.change('video_duration', value),
          undefined,
          (value) => `${value} seconds`,
          true,
          'Longer videos take more time and may cost more.',
        ),
      ]),
      settings.video_provider === 'openai'
        ? settingsCard([
            settingsHeading('Connection check', 'Tests whether OpenAI video generation is configured and reachable.'),
            this.providerControl('openai'),
          ])
        : el('div', { class: 'settings-empty-state', textContent: 'Video generation is off.' }),
    ];
  }

  private user(settings: Settings): HTMLElement[] {
    return [
      settingsIntro(
        'Your account defaults',
        'Set how your name and local time appear. Provider credentials remain server-side.',
      ),
      settingsCard([
        inputField(
          'Display name',
          settings.user_display_name,
          (value) => this.change('user_display_name', value),
          'text',
          true,
          'The friendly name Nice Assistant may use for this account.',
        ),
        inputField(
          'Timezone',
          settings.user_timezone,
          (value) => this.change('user_timezone', value),
          'text',
          true,
          'Use local for the browser timezone, or enter a standard timezone such as America/New_York.',
        ),
      ]),
      advancedSettings(
        'Provider credentials',
        'Credentials are encrypted at rest and are never returned to the browser in full.',
        [inputField(
          'OpenAI API key',
          settings.openai_api_key,
          (value) => this.change('openai_api_key', value),
          'password',
          true,
          'Used server-side for enabled OpenAI speech, transcription, image, and video features.',
        )],
        { testId: 'user-advanced-settings' },
      ),
    ];
  }
}

function providerLabel(value: string): string {
  if (value === 'disabled') return 'Off';
  if (value === 'local') return 'Local service';
  return titleCase(value);
}

function languageLabel(value: string): string {
  const labels: Record<string, string> = {
    auto: 'Detect automatically',
    en: 'English',
    es: 'Spanish',
    fr: 'French',
    de: 'German',
  };
  return labels[value] ?? value;
}

function titleCase(value: string): string {
  if (!value) return 'None';
  return value.replace(/[-_]/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}
