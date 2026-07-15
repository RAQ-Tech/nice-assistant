import { el } from './dom';
import { modelSettings, setModelSetting } from './settings';
import { inputField, selectField } from './settings_controls';
import { advancedSettings, readinessRow, settingsCard, settingsHeading, settingsIntro } from './settings_ui';
import type { SettingChange } from './everyday_settings_view';
import type { AppState, Settings, SettingScalar } from './types';

const MODEL_KEYS = [
  ['temperature', 'Temperature'],
  ['top_p', 'Top P'],
  ['num_predict', 'Maximum output tokens'],
  ['context_window_tokens', 'Context window tokens'],
  ['presence_penalty', 'Presence penalty'],
  ['frequency_penalty', 'Frequency penalty'],
] as const;

export class ModelSettingsView {
  private selectedOverrideModel = '';

  constructor(
    private readonly appState: AppState,
    private readonly change: SettingChange,
    private readonly renderApp: () => void,
    private readonly providerControl: () => HTMLElement,
  ) {}

  nodes(settings: Settings): HTMLElement[] {
    const selectedModel = settings.global_default_model || this.appState.models[0] || '';
    const effective = modelSettings(settings, selectedModel);
    const installed = this.appState.models.length;
    return [
      settingsIntro(
        'Set the default conversation behavior',
        'These values apply to conversations unless a persona, chat, or per-model customization is more specific.',
      ),
      el('div', { class: 'settings-readiness-list' }, [
        readinessRow(
          'Default model',
          selectedModel || 'Automatic; the provider chooses from installed models',
          selectedModel || installed ? 'ready' : 'attention',
          'A persona or chat may still select a different model.',
        ),
        readinessRow(
          'Installed models',
          installed ? `${installed} reported by Ollama` : 'No installed models reported',
          installed ? 'ready' : 'attention',
          'This list comes from Ollama and is refreshed when Nice Assistant loads provider data.',
        ),
        readinessRow(
          'Effective context window',
          `${effective.context_window_tokens} tokens for ${selectedModel || 'the automatic model'}`,
          'ready',
          'Nice Assistant budgets instructions, history, memories, output, and safety room inside this limit.',
        ),
        readinessRow(
          'Per-model customizations',
          `${Object.keys(settings.model_overrides).length} saved`,
          Object.keys(settings.model_overrides).length ? 'ready' : 'off',
          'A customization changes only the named model and takes precedence over the account defaults below.',
        ),
      ]),
      settingsCard([
        selectField(
          'Default model',
          settings.global_default_model,
          ['', ...this.appState.models],
          (value) => this.change('global_default_model', value),
          undefined,
          (value) => value || 'Automatic',
          true,
          'Used when neither the persona nor chat chooses a different model.',
        ),
        inputField('Temperature', settings.models_temperature, (value) => this.change('models_temperature', value), 'number', true, 'Higher values make replies less predictable; 0.7 is a balanced default.'),
        inputField('Maximum output tokens', settings.models_num_predict, (value) => this.change('models_num_predict', value), 'number', true, 'Reserves this many tokens for the reply inside the context window.'),
        inputField('Context window tokens', settings.models_context_window_tokens, (value) => this.change('models_context_window_tokens', value), 'number', true, 'Must not exceed what the selected model and Ollama configuration can actually support.'),
      ]),
      advancedSettings(
        'Sampling and repetition controls',
        'Optional generation controls for experienced model operators.',
        [
          inputField('Top P', settings.models_top_p, (value) => this.change('models_top_p', value), 'number', true, 'Limits token choices by cumulative probability.'),
          inputField('Presence penalty', settings.models_presence_penalty, (value) => this.change('models_presence_penalty', value), 'number', true, 'Positive values encourage introducing topics not already present.'),
          inputField('Frequency penalty', settings.models_frequency_penalty, (value) => this.change('models_frequency_penalty', value), 'number', true, 'Positive values discourage repeatedly using the same tokens.'),
        ],
        { testId: 'models-advanced-settings' },
      ),
      this.overrideEditor(settings),
      settingsCard([
        settingsHeading('Ollama connection', 'Checks whether Ollama is reachable and reports models without changing saved settings.'),
        this.providerControl(),
      ]),
    ];
  }

  private overrideEditor(settings: Settings): HTMLElement {
    const candidates = this.appState.models;
    if (!this.selectedOverrideModel || !candidates.includes(this.selectedOverrideModel)) {
      this.selectedOverrideModel = settings.global_default_model || candidates[0] || '';
    }
    const model = this.selectedOverrideModel;
    const override = model ? settings.model_overrides[model] : undefined;
    const effective = model ? modelSettings(settings, model) : null;
    const children: HTMLElement[] = [
      selectField(
        'Model to customize',
        model,
        candidates,
        (value) => { this.selectedOverrideModel = value; this.renderApp(); },
        'model-override-model',
        (value) => value,
        true,
        'Only installed models are listed. The customization is stored by exact model name.',
      ),
    ];
    if (!model) {
      children.push(el('div', { class: 'settings-empty-state', textContent: 'Install or expose an Ollama model before creating a model-specific customization.' }));
    } else if (!override) {
      children.push(
        el('div', { class: 'meta', textContent: `Using account defaults: ${effective?.context_window_tokens ?? 0} context tokens, temperature ${effective?.temperature ?? 0}.` }),
        el('button', {
          class: 'pill-btn',
          textContent: `Customize ${model}`,
          onclick: () => this.createOverride(settings, model),
        }),
      );
    } else {
      children.push(
        el('div', { class: 'settings-grid' }, MODEL_KEYS.map(([key, label]) =>
          inputField(
            label,
            String(override[key] ?? effective?.[key] ?? ''),
            (value) => this.updateOverride(settings, model, key, value),
            'number',
            false,
            `Overrides the account ${label.toLowerCase()} only when ${model} is selected.`,
          ),
        )),
        el('button', {
          class: 'pill-btn danger',
          textContent: 'Use account defaults for this model',
          onclick: () => this.removeOverride(settings, model),
        }),
      );
    }
    return advancedSettings(
      'Per-model customizations',
      'Optional values that take precedence only when a specific installed model runs.',
      children,
      { testId: 'model-overrides-settings' },
    );
  }

  private createOverride(settings: Settings, model: string): void {
    const effective = modelSettings(settings, model);
    settings.model_overrides[model] = { ...effective };
    this.change('model_overrides', { ...settings.model_overrides });
  }

  private updateOverride(settings: Settings, model: string, key: string, value: string): void {
    const parsed: SettingScalar = value.trim() === '' ? null : Number(value);
    if (parsed !== null && !Number.isFinite(parsed)) return;
    setModelSetting(settings, model, key, parsed);
    this.change('model_overrides', { ...settings.model_overrides }, false);
  }

  private removeOverride(settings: Settings, model: string): void {
    delete settings.model_overrides[model];
    this.change('model_overrides', { ...settings.model_overrides });
  }
}
