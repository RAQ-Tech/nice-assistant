import { el } from './dom';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import { choiceField, longField, numberField, pageHint, switchField, textField } from './settings_page';
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

/**
 * The everyday pages.
 *
 * General, Spoken replies, Transcription and Your profile are each one sparse
 * page: the choices that matter, labelled plainly, help on hover, and the rest
 * behind one "More options" fold. The two pictures pages keep the earlier
 * shape until they are redone in the same pass as the rest of Pictures.
 */
export class EverydaySettingsView {
  constructor(
    private readonly appState: AppState,
    private readonly change: SettingChange,
    private readonly providerControl: (provider: string) => HTMLElement,
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
      settingsCard([
        choiceField('Theme', settings.general_theme, ['dark', 'light'], (value) => this.change('general_theme', value), {
          display: titleCase,
          testId: 'general-theme',
        }),
        choiceField('Model', settings.global_default_model, ['', ...this.appState.models], (value) => this.change('global_default_model', value), {
          display: (value) => value || 'Automatic',
          hover: 'Used when a persona or chat has not chosen one.',
        }),
        switchField('Speak replies aloud', settings.general_voice_responses, (value) => this.change('general_voice_responses', value), {
          hover: 'Plays each finished reply, once spoken replies are set up.',
        }),
        switchField('Show the audio visualizer', settings.general_show_viz, (value) => this.change('general_show_viz', value), {
          hover: 'A waveform while a reply is playing.',
        }),
      ]),
      advancedSettings('More options', 'Technical messages, thinking, and signing out.', [
        switchField('Show system and tool messages', settings.general_show_system_messages, (value) => this.change('general_show_system_messages', value), {
          hover: 'Technical messages that are normally kept out of the conversation.',
        }),
        switchField('Show model thinking', settings.general_show_thinking, (value) => this.change('general_show_thinking', value), {
          hover: 'Only when the model and provider return it.',
        }),
        switchField('Sign out after inactivity', settings.general_auto_logout, (value) => this.change('general_auto_logout', value), {
          hover: 'Ends an idle browser session after the server’s session lifetime.',
        }),
      ], { testId: 'general-advanced-settings' }),
    ];
  }

  private tts(settings: Settings): HTMLElement[] {
    const provider = settings.tts_provider;
    const fields: HTMLElement[] = [
      choiceField('Spoken by', provider, ['disabled', 'local', 'openai'], (value) => this.change('tts_provider', value), {
        testId: 'tts-provider',
        display: providerLabel,
      }),
    ];
    const more: HTMLElement[] = [
      choiceField('Stored audio format', settings.tts_format, ['wav', 'mp3', 'opus', 'aac', 'flac'], (value) => this.change('tts_format', value), {
        display: titleCase,
        hover: 'What a finished reply is kept as for replay. Speech itself starts before the file is finished, except for WAV, which cannot.',
      }),
    ];
    if (provider === 'openai') {
      fields.push(
        textField('Voice', settings.tts_voice_openai, (value) => this.change('tts_voice_openai', value), {
          hover: 'Unless a persona chooses its own.',
        }),
        choiceField('Speech model', settings.tts_model_openai, ['gpt-4o-mini-tts', 'tts-1', 'tts-1-hd'], (value) => this.change('tts_model_openai', value)),
        numberField('Speed', settings.tts_speed_openai, (value) => this.change('tts_speed_openai', value), { step: '0.1', hover: '1 is normal.' }),
      );
      more.push(
        longField('Voice direction', settings.tts_instructions_openai, (value) => this.change('tts_instructions_openai', value), {
          hover: 'How to perform it: warmth, pacing, tone.',
        }),
      );
    } else if (provider === 'local') {
      fields.push(
        textField('Service address', settings.tts_local_base_url, (value) => this.change('tts_local_base_url', value), {
          type: 'url',
          hover: 'The Kokoro-compatible service on this network.',
        }),
        textField('Voice', settings.tts_voice_local, (value) => this.change('tts_voice_local', value), {
          hover: 'Unless a persona chooses its own.',
        }),
        numberField('Speed', settings.tts_speed_local, (value) => this.change('tts_speed_local', value), { step: '0.1', hover: '1 is normal.' }),
      );
      more.push(
        textField('Model name', settings.tts_model_local, (value) => this.change('tts_model_local', value), {
          hover: 'Only if the service offers more than one.',
        }),
      );
    }
    return [
      settingsCard(fields),
      provider === 'disabled' ? null : settingsCard([this.providerControl(provider === 'local' ? 'kokoro' : 'openai')]),
      advancedSettings('More options', 'Audio format and provider details.', more, { testId: 'tts-advanced-settings' }),
    ].filter((node): node is HTMLElement => node !== null);
  }

  private stt(settings: Settings): HTMLElement[] {
    const provider = settings.stt_provider;
    const fields: HTMLElement[] = [
      choiceField('Transcribed by', provider, ['disabled', 'local', 'openai'], (value) => this.change('stt_provider', value), {
        testId: 'stt-provider',
        display: providerLabel,
      }),
    ];
    if (provider !== 'disabled') {
      fields.push(choiceField('Language', settings.stt_language, ['auto', 'en', 'es', 'fr', 'de'], (value) => this.change('stt_language', value), {
        display: languageLabel,
        hover: 'Detecting is convenient. Fixing one is more consistent.',
      }));
    }
    if (provider === 'local') fields.push(...this.localTranscription(settings));
    return [
      settingsCard(fields),
      provider === 'disabled'
        ? null
        : settingsCard([
            switchField('Decide when I have finished talking', settings.stt_hands_free, (value) => this.change('stt_hands_free', value), {
              hover: 'A tap starts listening and the turn ends at a pause after you have actually spoken - never on silence alone. '
                + 'Holding the button always works and is never guessed at.',
              testId: 'stt-hands-free',
            }),
            switchField('Transcribe while I am still talking', settings.stt_streaming, (value) => this.change('stt_streaming', value), {
              hover: 'Transcribes at each natural pause, so the wait at the end is one sentence rather than the whole turn. '
                + 'It transcribes more audio in total, so it suits a fast model on a machine with room.',
              testId: 'stt-streaming',
            }),
          ]),
      provider === 'disabled' ? null : settingsCard([this.providerControl(provider === 'local' ? 'whisper' : 'openai')]),
      advancedSettings('More options', 'Recordings.', [
        switchField('Keep source recordings', settings.stt_store_recordings, (value) => this.change('stt_store_recordings', value), {
          hover: 'Keeps the original recording after it is transcribed. Off is the more private default.',
        }),
      ], { testId: 'stt-advanced-settings' }),
    ].filter((node): node is HTMLElement => node !== null);
  }

  /**
   * The two shapes a local Whisper service comes in.
   *
   * They are different protocols, not different vendors, and somebody who
   * already runs Home Assistant voice has the second one without knowing the
   * word for it - so the option names the thing they would recognise.
   */
  private localTranscription(settings: Settings): HTMLElement[] {
    const wyoming = settings.stt_local_backend === 'wyoming';
    return [
      choiceField('Connection', settings.stt_local_backend, ['openai_api', 'wyoming'], (value) => this.change('stt_local_backend', value), {
        testId: 'stt-local-backend',
        display: sttBackendLabel,
        hover: 'Wyoming is what Home Assistant voice uses. If you already run faster-whisper for it, choose that.',
      }),
      wyoming
        ? textField('Service address', settings.stt_wyoming_address, (value) => this.change('stt_wyoming_address', value), {
            hover: 'Host and port on this network - port 10300 unless it was changed. The model is whatever that service has loaded.',
          })
        : textField('Service address', settings.stt_local_base_url, (value) => this.change('stt_local_base_url', value), {
            type: 'url',
            hover: 'A Whisper service on this network that speaks the OpenAI transcription API.',
          }),
      ...(wyoming
        ? []
        : [textField('Model', settings.stt_model_local, (value) => this.change('stt_model_local', value), {
            hover: 'What to ask the service to load. Most accept whisper-1; some want their own name.',
          })]),
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
      toggleField(
        'Blur pictures in chat',
        settings.chat_blur_images,
        (value) => this.change('chat_blur_images', value),
        'When enabled, the first tap reveals a picture and the second opens the large preview. The default is off.',
      ),
      // A picker cannot be misspelled: "1024 x 1024" typed with spaces used
      // to fail quietly at the provider.
      selectField(
        'Shape',
        SHAPE_CHOICES.includes(settings.image_size) ? settings.image_size : 'custom',
        [...SHAPE_CHOICES, 'custom'],
        (value) => { if (value !== 'custom') this.change('image_size', value); },
        'image-shape',
        shapeLabel,
        true,
        'The default size for one-off pictures. Recipes carry their own sizes.',
      ),
      ...(SHAPE_CHOICES.includes(settings.image_size)
        ? []
        : [inputField('Custom size', settings.image_size, (value) => this.change('image_size', value), 'text', true,
            'Width × height, for example 1152x896.')]),
      // OpenAI is the only provider that reads this; showing it beside a
      // ComfyUI setup taught people the page could not be trusted.
      ...(settings.image_provider === 'openai'
        ? [selectField(
            'Prompt enhancement quality',
            settings.image_quality,
            ['none', 'low', 'medium', 'high', 'auto'],
            (value) => this.change('image_quality', value),
            undefined,
            titleCase,
            true,
            'How much OpenAI rewrites your prompt before generating. None preserves it most directly.',
          )]
        : []),
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
          'Used by direct image actions, and it breaks a tie when routing has nothing else to go on.',
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
        inputField('CFG', settings.image_local_cfg_scale, (value) => this.change('image_local_cfg_scale', value), 'number', true, 'Controls how strongly generation follows the prompt.', '0.1'),
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
      this.appState.mediaReadiness
        ? settingsCard([
            settingsHeading('Images readiness', this.appState.mediaReadiness.basic_generation.message),
            el('div', {
              class: 'settings-readiness-facts',
              textContent: [
                `Provider: ${this.appState.mediaReadiness.provider.reachable ? 'reachable' : this.appState.mediaReadiness.provider.status}`,
                `Basic generation: ${this.appState.mediaReadiness.basic_generation.ready ? 'ready' : 'not ready'}`,
                `Optional identity enhancement: ${this.appState.mediaReadiness.optional_identity.ready ? 'ready' : 'not configured'}`,
              ].join(' · '),
            }),
          ])
        : null,
      settings.image_provider === 'disabled'
        ? el('div', { class: 'settings-empty-state', textContent: 'Image generation is off.' })
        : settingsCard([
            settingsHeading('Connection check', 'Tests the selected image service with the current unsaved values.'),
            this.providerControl(settings.image_provider === 'local' ? settings.image_local_backend : 'openai'),
          ]),
      settings.image_provider === 'local'
        ? advancedSettings(
            'Tuning for one-off pictures',
            'Only direct image actions read these. Recipes in Media Catalog carry their own numbers.',
            advanced,
            { testId: 'image-advanced-settings' },
          )
        : null,
    ].filter((node): node is HTMLElement => node !== null);
  }

  // Local only, by decision (2026-08-26): every cloud video API either shut
  // down or refuses this product's content, so the UI offers what can work.
  // The cloud adapter stays in the code for the day a service worth linking
  // exists; a stored cloud choice renders and saves as Off.
  private video(settings: Settings): HTMLElement[] {
    const provider = settings.video_provider === 'local' ? 'local' : 'disabled';
    return [
      settingsIntro(
        'Choose how video clips get made',
        'Video runs on your own ComfyUI — the same service that makes pictures.',
      ),
      settingsCard([
        selectField(
          'Video provider',
          provider,
          ['disabled', 'local'],
          (value) => this.change('video_provider', value),
          'video-provider',
          providerLabel,
          true,
          'No cloud video service is currently offered. Local uses the ComfyUI configured on the Image Generation page.',
        ),
      ]),
      provider === 'local'
        ? settingsCard([
            settingsHeading(
              'What local video needs',
              'A video model and a video workflow in Media Catalog, paired as a recipe. A chat that asks for a clip picks one, exactly as pictures do.',
            ),
            this.providerControl('comfyui'),
          ])
        : el('div', { class: 'settings-empty-state', textContent: 'Video generation is off.' }),
    ];
  }

  private user(settings: Settings): HTMLElement[] {
    return [
      settingsCard([
        textField('Name', settings.user_display_name, (value) => this.change('user_display_name', value), {
          hover: 'What Nice Assistant may call you.',
        }),
        longField('About you', settings.user_profile, (value) => this.change('user_profile', value), {
          hover: 'A few durable facts, sent with every message.',
        }),
        pageHint('Sent with every message, so keep it short: it competes with the conversation itself.'),
        textField('Timezone', settings.user_timezone, (value) => this.change('user_timezone', value), {
          hover: '“local” for the browser’s own, or a name such as America/New_York.',
        }),
      ]),
      advancedSettings('More options', 'Credentials. Encrypted at rest, and never sent back in full.', [
        textField('OpenAI API key', settings.openai_api_key, (value) => this.change('openai_api_key', value), {
          type: 'password',
          hover: 'Used on the server for whichever OpenAI features are switched on.',
        }),
      ], { testId: 'user-advanced-settings' }),
    ];
  }
}

// Providers that send data to somebody else's computer. Kept in step with
// `app/data_locality.py`, which is what the homepage summary reads.
const CLOUD_PROVIDERS = new Set(['openai', 'openai-image', 'openai-video']);
const LOCAL_PROVIDERS = new Set(['ollama', 'local', 'local-image', 'kokoro', 'compreface']);

/**
 * A provider name with where it runs attached.
 *
 * Both kinds are legitimate choices. Choosing one without knowing which kind it
 * is, is not, so the distinction travels with the name rather than living in a
 * paragraph above the control.
 */
export function providerLabel(value: string): string {
  if (value === 'disabled') return 'Off';
  if (value === 'local') return 'Local service — on this machine';
  if (CLOUD_PROVIDERS.has(value)) return `${titleCase(value)} — leaves this machine`;
  if (LOCAL_PROVIDERS.has(value)) return `${titleCase(value)} — on this machine`;
  // Neither list claims it, so neither does this.
  return `${titleCase(value)} — nobody has said where this runs`;
}

// The shapes offered by name. Anything else remains reachable through Custom,
// so an unusual stored size is shown rather than silently rewritten.
const SHAPE_CHOICES = ['1024x1024', '832x1216', '1216x832', '512x512'];

function shapeLabel(value: string): string {
  if (value === 'custom') return 'Custom…';
  const [width, height] = value.split('x').map(Number);
  if (!width || !height) return value;
  const shape = width === height ? 'Square' : width > height ? 'Landscape' : 'Portrait';
  return `${shape} — ${width}×${height}`;
}

function sttBackendLabel(value: string): string {
  if (value === 'wyoming') return 'Wyoming — Home Assistant voice services';
  return 'OpenAI-compatible — speaches, whisper.cpp, LocalAI';
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
