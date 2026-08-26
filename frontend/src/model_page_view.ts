import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { ModelLookupView } from './model_lookup_view';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import type { SettingsDialogs } from './settings_contracts';
import { advancedSettings, settingsCard } from './settings_ui';
import type { AppState, CivitaiMatch, MediaCatalogResource, MediaPreset, ModelPrefillSuggestion } from './types';

/**
 * One model, one page.
 *
 * The catalog thinks in resources and presets; a person thinks "JuggernautXL
 * likes 30 steps." This page is that thought: the model's name stands alone at
 * the top, its settings sit under it with almost no prose, and arrows walk to
 * the next ingredient. Under the hood it edits the model resource and the
 * model's own recipe together - no new storage, only a humane door.
 */

const SHAPES = ['1024x1024', '832x1216', '1216x832', '512x512', '512x768', '768x512'];
const CUSTOM = 'custom';

interface EditState {
  name: string;
  enabled: boolean;
  routingCard: string;
  steps: string;
  cfg: string;
  sampler: string;
  scheduler: string;
  size: string;
  style: string;
  prefix: string;
  suffix: string;
  supportsNegative: boolean;
  negative: string;
  triggerPlacement: string;
  allSizes: string;
  priority: string;
}

export class ModelPageView {
  modelId: string | null = null;
  private preset: MediaPreset | null = null;
  private edit: EditState | null = null;
  private snapshot = '';
  private options: { samplers: string[]; schedulers: string[] } | null = null;
  private prefill: ModelPrefillSuggestion | null = null;
  private customSize = false;
  private moreOpen = false;
  private busy = false;

  private readonly lookup: ModelLookupView;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly dialogs: SettingsDialogs,
    private readonly refreshCatalog: () => Promise<void>,
  ) {
    this.lookup = new ModelLookupView(appState, client, renderApp, dialogs, (match) => this.applyMatch(match));
  }

  open(modelId: string): void {
    this.modelId = modelId;
    this.preset = null;
    this.edit = null;
    this.prefill = null;
    this.customSize = false;
    this.moreOpen = false;
    this.lookup.reset();
    void this.load();
  }

  /** A person picked a CivitAI match; its published settings fill the form. */
  private applyMatch(match: CivitaiMatch): void {
    const edit = this.edit;
    if (!edit) return;
    if (match.model_name) edit.name = match.model_name;
    if (match.steps !== undefined) edit.steps = String(match.steps);
    if (match.cfg_scale !== undefined) edit.cfg = String(match.cfg_scale);
    if (match.sampler) edit.sampler = match.sampler;
    if (match.scheduler) edit.scheduler = match.scheduler;
    if (match.width && match.height) edit.size = `${match.width}x${match.height}`;
    if (match.trigger_words.length) {
      const words = match.trigger_words.join(', ');
      edit.prefix = edit.prefix.includes(words) ? edit.prefix : [words, edit.prefix].filter(Boolean).join(', ');
    }
    this.renderApp();
  }

  private models(): MediaCatalogResource[] {
    return (this.appState.mediaCatalog?.resources ?? [])
      .filter((item) => item.resource_type === 'model' && item.kind === 'image')
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  private model(): MediaCatalogResource | null {
    return this.models().find((item) => item.id === this.modelId) ?? null;
  }

  private async load(): Promise<void> {
    const model = this.model();
    if (!model) return;
    this.busy = true;
    this.renderApp();
    try {
      const presets = (await this.client.mediaPresets()).items;
      this.preset = presets.find((item) => item.definition?.base_model_resource_id === model.id) ?? null;
      this.edit = this.fromCurrent(model, this.preset);
      this.snapshot = JSON.stringify(this.edit);
      if (!this.options) {
        const listing = await this.client.comfyuiCheckpoints().catch(() => null);
        this.options = { samplers: listing?.samplers ?? [], schedulers: listing?.schedulers ?? [] };
      }
      this.prefill = await this.client.modelPrefill(model.external_id).catch(() => null);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to open that model.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private fromCurrent(model: MediaCatalogResource, preset: MediaPreset | null): EditState {
    const definition = preset?.definition ?? {};
    const dialect = definition.prompt_dialect ?? {};
    const sampler = definition.sampler ?? {};
    const sizes: string[] = definition.dimensions ?? [];
    return {
      name: model.name,
      enabled: model.enabled,
      routingCard: preset?.routing_card ?? '',
      steps: sampler.steps === undefined ? '' : String(sampler.steps),
      cfg: sampler.cfg_scale === undefined ? '' : String(sampler.cfg_scale),
      sampler: String(sampler.sampler_name ?? ''),
      scheduler: String(sampler.scheduler ?? ''),
      size: sizes[0] ?? '',
      style: String(dialect.style ?? 'natural_language'),
      prefix: String(dialect.prefix ?? ''),
      suffix: String(dialect.suffix ?? ''),
      supportsNegative: dialect.supports_negative !== false,
      negative: String(dialect.negative_prompt ?? ''),
      triggerPlacement: String(dialect.trigger_placement ?? 'suffix'),
      allSizes: sizes.join(', '),
      priority: String(preset?.priority ?? model.priority),
    };
  }

  private dirty(): boolean {
    return this.edit !== null && JSON.stringify(this.edit) !== this.snapshot;
  }

  /** Leave only with intact work: stay, discard, or save first. */
  private async guard(leave: () => void): Promise<void> {
    if (!this.dirty()) return leave();
    const name = this.edit?.name || 'This model';
    const answer = await this.dialogs.choice(
      'Save these changes?',
      `${name} has unsaved changes.`,
      ['Stay here', 'Leave without saving', 'Save and continue'],
    );
    if (answer === 1) return leave();
    if (answer === 2 && (await this.save())) leave();
  }

  async close(done: () => void): Promise<void> {
    await this.guard(() => { this.modelId = null; done(); });
  }

  private async step(offset: number): Promise<void> {
    const models = this.models();
    const index = models.findIndex((item) => item.id === this.modelId);
    const target = models[index + offset];
    if (!target) return;
    await this.guard(() => this.open(target.id));
  }

  private applyPrefill(): void {
    const suggestion = this.prefill;
    if (!suggestion || !this.edit) return;
    if (suggestion.steps !== undefined) this.edit.steps = String(suggestion.steps);
    if (suggestion.cfg_scale !== undefined) this.edit.cfg = String(suggestion.cfg_scale);
    if (suggestion.width && suggestion.height) this.edit.size = `${suggestion.width}x${suggestion.height}`;
    if (suggestion.prompt_style) this.edit.style = suggestion.prompt_style;
    this.renderApp();
  }

  private async save(): Promise<boolean> {
    const model = this.model();
    const edit = this.edit;
    if (!model || !edit) return false;
    this.busy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const previousName = model.name;
      Object.assign(model, { name: edit.name.trim() || model.name, enabled: edit.enabled });
      await this.client.updateMediaCatalogResource(model);
      if (this.preset) {
        const sizes = [...new Set([edit.size, ...edit.allSizes.split(',').map((item) => item.trim())].filter(Boolean))];
        const definition = {
          ...this.preset.definition,
          prompt_dialect: {
            ...this.preset.definition?.prompt_dialect,
            style: edit.style,
            prefix: edit.prefix,
            suffix: edit.suffix,
            supports_negative: edit.supportsNegative,
            negative_prompt: edit.negative,
            trigger_placement: edit.triggerPlacement,
          },
          sampler: {
            ...(edit.steps.trim() ? { steps: Number(edit.steps) } : {}),
            ...(edit.cfg.trim() ? { cfg_scale: Number(edit.cfg) } : {}),
            ...(edit.sampler ? { sampler_name: edit.sampler } : {}),
            ...(edit.scheduler ? { scheduler: edit.scheduler } : {}),
          },
          dimensions: sizes,
        };
        this.preset = await this.client.updateMediaPreset(this.preset.id, {
          ...this.preset,
          // The recipe follows its model's nickname unless it was renamed on
          // its own, so the two never drift apart silently.
          name: this.preset.name === previousName ? model.name : this.preset.name,
          priority: Number(edit.priority) || this.preset.priority,
          routing_card: edit.routingCard,
          definition,
        });
      }
      await this.refreshCatalog();
      this.snapshot = JSON.stringify(edit);
      return true;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save this model.');
      return false;
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  node(done: () => void): HTMLElement {
    const model = this.model();
    const edit = this.edit;
    if (!model) return settingsCard([el('p', { class: 'meta', textContent: 'That model is no longer in the catalog.' })]);
    const models = this.models();
    const index = models.findIndex((item) => item.id === model.id);
    const family = this.prefill?.family_label;
    return el('div', { class: 'model-page', 'data-testid': 'model-page' }, [
      el('div', { class: 'model-page-nav' }, [
        el('button', { class: 'pill-btn', textContent: '‹ All models', 'data-testid': 'model-page-back', onclick: () => void this.close(done) }),
        el('div', { class: 'chips' }, [
          el('button', { class: 'pill-btn', textContent: '‹ Previous', disabled: index <= 0 || this.busy, 'data-testid': 'model-page-previous', onclick: () => void this.step(-1) }),
          el('button', { class: 'pill-btn', textContent: 'Next ›', disabled: index >= models.length - 1 || this.busy, 'data-testid': 'model-page-next', onclick: () => void this.step(1) }),
        ]),
      ]),
      !edit ? el('p', { class: 'meta', textContent: 'Opening…' }) : settingsCard([
        el('input', {
          class: 'model-page-name',
          value: edit.name,
          title: model.external_id,
          'aria-label': 'Model nickname',
          'data-testid': 'model-page-name',
          oninput: (event: Event) => { edit.name = (event.currentTarget as HTMLInputElement).value; },
        }),
        el('p', { class: 'meta model-page-file', textContent: family ? `${model.external_id} · ${family}` : model.external_id }),
        toggleField('Show in Nice Assistant', edit.enabled, (value) => { edit.enabled = value; this.renderApp(); }),
        ...(this.preset ? this.recipeFields(edit) : [el('p', {
          class: 'meta',
          textContent: 'Turn on “Show in Nice Assistant” and save — its settings appear here once it has a recipe.',
        })]),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'send-btn',
            textContent: this.busy ? 'Saving…' : this.dirty() ? 'Save' : 'Saved',
            disabled: this.busy || !this.dirty(),
            'data-testid': 'model-page-save',
            onclick: () => void this.save(),
          }),
        ]),
      ]),
    ]);
  }

  private recipeFields(edit: EditState): HTMLElement[] {
    const suggestion = this.prefill;
    const sizeOptions = [...new Set([edit.size, ...SHAPES].filter(Boolean)), CUSTOM];
    const pickSize = this.customSize || (edit.size !== '' && !SHAPES.includes(edit.size)) ? CUSTOM : edit.size;
    return [
      textareaField('When should this model be used?', edit.routingCard, (value) => { edit.routingCard = value; }, false,
        'Plain language. Chats read this note when choosing between models.'),
      suggestion && suggestion.source !== 'none'
        ? el('div', { class: 'model-page-suggestion', 'data-testid': 'model-page-suggestion' }, [
            el('span', { class: 'meta', textContent: `${suggestion.message} ${suggestion.steps} steps · guidance ${suggestion.cfg_scale} · ${suggestion.width}×${suggestion.height}` }),
            el('button', { class: 'pill-btn', textContent: 'Apply', 'data-testid': 'model-page-apply-suggestion', onclick: () => this.applyPrefill() }),
          ])
        : null,
      this.lookup.node(this.model()?.external_id ?? ''),
      el('div', { class: 'settings-grid' }, [
        inputField('Steps', edit.steps, (value) => { edit.steps = value; }, 'number', false),
        inputField('Guidance', edit.cfg, (value) => { edit.cfg = value; }, 'number', false),
        this.optionField('Sampler', edit.sampler, this.options?.samplers ?? [], (value) => { edit.sampler = value; }, 'model-page-sampler'),
        this.optionField('Scheduler', edit.scheduler, this.options?.schedulers ?? [], (value) => { edit.scheduler = value; }, 'model-page-scheduler'),
        selectField('Size', pickSize, sizeOptions, (value) => {
          this.customSize = value === CUSTOM;
          if (value !== CUSTOM) edit.size = value;
          this.renderApp();
        }, 'model-page-size', shapeLabel, false),
        pickSize === CUSTOM
          ? inputField('Custom size', edit.size, (value) => { edit.size = value.trim(); }, 'text', false)
          : null,
      ].filter((nodeItem): nodeItem is HTMLElement => nodeItem !== null)),
      advancedSettings('More options', 'Prompt wording, negatives, extra sizes, priority.', [
        el('div', { class: 'settings-grid' }, [
          selectField('Prompt style', edit.style, ['natural_language', 'booru', 'hybrid'], (value) => { edit.style = value; }, undefined, styleLabel, false),
          selectField('LoRA trigger words go', edit.triggerPlacement, ['prefix', 'suffix'], (value) => { edit.triggerPlacement = value; }, undefined,
            (value) => (value === 'prefix' ? 'Before the description' : 'After the description'), false),
          inputField('Prompt prefix', edit.prefix, (value) => { edit.prefix = value; }, 'text', false),
          inputField('Prompt suffix', edit.suffix, (value) => { edit.suffix = value; }, 'text', false),
          inputField('All sizes', edit.allSizes, (value) => { edit.allSizes = value; }, 'text', false),
          inputField('Priority', edit.priority, (value) => { edit.priority = value; }, 'number', false),
        ]),
        toggleField('This model accepts a negative prompt', edit.supportsNegative, (value) => { edit.supportsNegative = value; this.renderApp(); }),
        edit.supportsNegative
          ? textareaField('Negative prompt', edit.negative, (value) => { edit.negative = value; }, false)
          : null,
      ].filter((nodeItem): nodeItem is HTMLElement => nodeItem !== null),
      { testId: 'model-page-more', open: this.moreOpen, onToggle: (open) => { this.moreOpen = open; } }),
    ].filter((nodeItem): nodeItem is HTMLElement => nodeItem !== null);
  }

  /** A dropdown of what ComfyUI has; a plain box when it could not be asked. */
  private optionField(label: string, value: string, values: string[], change: (value: string) => void, testId: string): HTMLElement {
    if (!values.length) return inputField(label, value, change, 'text', false);
    return selectField(label, value, [...new Set([value, ...values].filter(Boolean))], change, testId, (item) => item, false);
  }
}

function shapeLabel(value: string): string {
  if (value === CUSTOM) return 'Custom…';
  const [width, height] = value.split('x').map(Number);
  if (!width || !height) return value;
  const shape = width === height ? 'square' : width > height ? 'landscape' : 'portrait';
  return `${width}×${height} — ${shape}`;
}

function styleLabel(value: string): string {
  if (value === 'natural_language') return 'Natural language';
  if (value === 'booru') return 'Booru tags';
  return 'Hybrid';
}
