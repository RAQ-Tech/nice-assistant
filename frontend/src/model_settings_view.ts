import { el } from './dom';
import type { SettingChange } from './everyday_settings_view';
import { modelSettings, setModelSetting } from './settings';
import {
  actionRow,
  choiceField,
  numberField,
  pageHead,
  pageHint,
  pageNav,
  switchField,
  thingList,
} from './settings_page';
import { advancedSettings, settingsCard } from './settings_ui';
import type { AppState, Settings, SettingScalar } from './types';

/**
 * Conversation models: the list Ollama reports, and one page per model.
 *
 * The page used to be a readiness summary over a form over a collapsed
 * "customize a model" editor with a dropdown inside it. Now the models are the
 * list, the shared defaults sit under them, and each model's page carries its
 * own numbers - prefilled from the defaults, and saying so, until one is
 * changed. Everything still lives in the one settings object the header's
 * Save writes; only the door is new.
 */

const MODEL_KEYS = [
  ['temperature', 'Temperature', 'Higher is less predictable. 0.7 is a balanced default.', '0.1'],
  ['num_predict', 'Reply length (tokens)', 'Room kept for the reply inside the context window.', undefined],
  ['context_window_tokens', 'Context window (tokens)', 'No more than the model and Ollama can actually hold.', undefined],
  ['top_p', 'Top P', 'Limits word choices by cumulative probability.', '0.1'],
  ['presence_penalty', 'Presence penalty', 'Positive values invite topics not yet mentioned.', '0.1'],
  ['frequency_penalty', 'Frequency penalty', 'Positive values discourage repeating the same words.', '0.1'],
] as const;

export class ModelSettingsView {
  constructor(
    private readonly appState: AppState,
    private readonly change: SettingChange,
    private readonly renderApp: () => void,
    private readonly providerControl: () => HTMLElement,
    private readonly navigate: (model: string | null) => void,
  ) {}

  nodes(settings: Settings, item: string | null): HTMLElement[] {
    return item ? this.page(settings, item) : this.list(settings);
  }

  private list(settings: Settings): HTMLElement[] {
    const models = this.appState.models;
    const customized = new Set(Object.keys(settings.model_overrides));
    return [
      thingList(models.map((model) => ({
        id: model,
        label: model,
        note: model === settings.global_default_model ? 'default' : customized.has(model) ? 'customized' : undefined,
        testId: `model-open-${model}`,
        onOpen: () => this.navigate(model),
      })), 'Ollama reports no models. Pull one, then reload this page.', 'model-list'),
      pageHint('Every model uses these unless its own page says otherwise.'),
      settingsCard([
        choiceField('Default model', settings.global_default_model, ['', ...models], (value) => {
          this.change('global_default_model', value);
        }, { display: (value) => value || 'Automatic', hover: 'Used when a persona or chat has not chosen one.', testId: 'models-default' }),
        el('div', { class: 'settings-grid' }, [
          numberField('Temperature', settings.models_temperature, (value) => this.change('models_temperature', value, false),
            { hover: MODEL_KEYS[0][2], step: '0.1' }),
          numberField('Reply length (tokens)', settings.models_num_predict, (value) => this.change('models_num_predict', value, false),
            { hover: MODEL_KEYS[1][2] }),
          numberField('Context window (tokens)', settings.models_context_window_tokens, (value) => this.change('models_context_window_tokens', value, false),
            { hover: MODEL_KEYS[2][2] }),
        ]),
        advancedSettings('More options', 'Sampling and repetition.', [
          el('div', { class: 'settings-grid' }, [
            numberField('Top P', settings.models_top_p, (value) => this.change('models_top_p', value, false), { hover: MODEL_KEYS[3][2], step: '0.1' }),
            numberField('Presence penalty', settings.models_presence_penalty, (value) => this.change('models_presence_penalty', value, false), { hover: MODEL_KEYS[4][2], step: '0.1' }),
            numberField('Frequency penalty', settings.models_frequency_penalty, (value) => this.change('models_frequency_penalty', value, false), { hover: MODEL_KEYS[5][2], step: '0.1' }),
          ]),
        ], { testId: 'models-advanced-settings' }),
        this.providerControl(),
      ]),
    ];
  }

  private page(settings: Settings, model: string): HTMLElement[] {
    const models = this.appState.models;
    const index = models.indexOf(model);
    const previous = index > 0 ? models[index - 1] : undefined;
    const next = index >= 0 ? models[index + 1] : undefined;
    const override = settings.model_overrides[model];
    const effective = modelSettings(settings, model);
    return [
      pageNav({
        back: 'All models',
        onBack: () => this.navigate(null),
        arrows: {
          previous: previous ? () => this.navigate(previous) : null,
          next: next ? () => this.navigate(next) : null,
        },
        testId: 'model-settings-page',
      }),
      settingsCard([
        pageHead({
          name: model,
          line: index >= 0 ? 'Ollama — on this machine' : 'Ollama does not report this model right now',
          testId: 'model-settings-page',
        }),
        switchField('Default model for new chats', settings.global_default_model === model, (on) => {
          this.change('global_default_model', on ? model : '');
        }, { hover: 'Used when a persona or chat has not chosen one.', testId: 'model-settings-default' }),
        pageHint(
          override
            ? `Customized. Only ${model} uses these numbers; every other model uses the shared defaults.`
            : 'Using the shared defaults. Change a number and it applies to this model only.',
          'model-settings-provenance',
        ),
        el('div', { class: 'settings-grid' }, MODEL_KEYS.map(([key, label, hover, step]) =>
          numberField(label, String(override?.[key] ?? effective[key] ?? ''), (value) => this.set(settings, model, key, value), {
            hover,
            step,
            commit: () => this.renderApp(),
          }))),
        override
          ? actionRow([
              el('button', {
                class: 'pill-btn',
                textContent: 'Use the shared defaults',
                'data-testid': 'model-settings-reset',
                onclick: () => {
                  delete settings.model_overrides[model];
                  this.change('model_overrides', { ...settings.model_overrides });
                },
              }),
            ])
          : null,
      ], 'model-settings-page', 'model-settings-page'),
    ];
  }

  private set(settings: Settings, model: string, key: string, value: string): void {
    const parsed: SettingScalar = value.trim() === '' ? null : Number(value);
    if (parsed !== null && !Number.isFinite(parsed)) return;
    setModelSetting(settings, model, key, parsed);
    this.change('model_overrides', { ...settings.model_overrides }, false);
  }
}
