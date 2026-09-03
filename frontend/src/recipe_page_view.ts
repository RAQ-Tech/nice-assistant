import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import type { SettingsDialogs } from './settings_contracts';
import {
  actionRow,
  choiceField,
  leaveGuard,
  longField,
  numberField,
  pageHead,
  pageHint,
  pageNav,
  saveButton,
  switchField,
  textField,
} from './settings_page';
import { advancedSettings, settingsCard } from './settings_ui';
import type { AppState, MediaCatalogResource, MediaPreset, PresetExport } from './types';

/**
 * One recipe, one page.
 *
 * A recipe is the tested pairing the chat's planner chooses between: a model,
 * how prompts are written for it, and its numbers. The model page edits the
 * recipe that belongs to one model; this is the door for any recipe - the
 * ones made by hand, installed from a starter, or brought in from a file -
 * with the same shape: the name as the headline, the note that says when to
 * use it, the numbers, and the wording folded.
 */
const STYLES = ['natural_language', 'booru', 'hybrid'];

interface Edit {
  name: string;
  enabled: boolean;
  routingCard: string;
  model: string;
  steps: string;
  cfg: string;
  sampler: string;
  scheduler: string;
  sizes: string;
  style: string;
  prefix: string;
  suffix: string;
  supportsNegative: boolean;
  negative: string;
  triggerPlacement: string;
  priority: string;
  raw: string;
}

export class RecipePageView {
  presetId: string | null = null;
  private presets: MediaPreset[] = [];
  private edit: Edit | null = null;
  private definition: Record<string, any> = {};
  private snapshot = '';
  private moreOpen = false;
  private busy = false;
  private pendingExport: PresetExport | null = null;
  /** Whether the recipes have been asked for once; a list before that is not empty, only unknown. */
  loaded = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly dialogs: SettingsDialogs,
    private readonly navigate: (item: string | null) => void,
  ) {}

  /** Every recipe, by name, for the list and for the arrows. */
  async refresh(): Promise<MediaPreset[]> {
    try {
      this.presets = (await this.client.mediaPresets()).items.sort((left, right) => left.name.localeCompare(right.name));
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load the recipes.');
    } finally {
      this.loaded = true;
    }
    // A page opened by its address before the recipes arrived gets its recipe now.
    if (this.presetId && !this.edit) this.open(this.presetId);
    return this.presets;
  }

  list(): MediaPreset[] {
    return this.presets;
  }

  knows(presetId: string): boolean {
    return this.presets.some((item) => item.id === presetId);
  }

  open(presetId: string): void {
    this.presetId = presetId;
    this.edit = null;
    this.pendingExport = null;
    this.moreOpen = false;
    const preset = this.preset();
    if (preset) this.start(preset, preset.definition ?? {});
  }

  private preset(): MediaPreset | null {
    return this.presets.find((item) => item.id === this.presetId) ?? null;
  }

  private start(preset: MediaPreset, definition: Record<string, any>): void {
    this.definition = definition;
    const dialect = definition.prompt_dialect ?? {};
    const sampler = definition.sampler ?? {};
    const kept = this.edit;
    this.edit = {
      name: kept?.name ?? preset.name,
      enabled: kept?.enabled ?? preset.enabled,
      routingCard: kept?.routingCard ?? preset.routing_card,
      priority: kept?.priority ?? String(preset.priority),
      model: String(definition.base_model_resource_id ?? ''),
      steps: sampler.steps === undefined ? '' : String(sampler.steps),
      cfg: sampler.cfg_scale === undefined ? '' : String(sampler.cfg_scale),
      sampler: String(sampler.sampler_name ?? ''),
      scheduler: String(sampler.scheduler ?? ''),
      sizes: (definition.dimensions ?? []).join(', '),
      style: String(dialect.style ?? 'natural_language'),
      prefix: String(dialect.prefix ?? ''),
      suffix: String(dialect.suffix ?? ''),
      supportsNegative: dialect.supports_negative !== false,
      negative: String(dialect.negative_prompt ?? ''),
      triggerPlacement: String(dialect.trigger_placement ?? 'suffix'),
      raw: JSON.stringify(definition, null, 2),
    };
    if (!kept) this.snapshot = JSON.stringify(this.edit);
  }

  private dirty(): boolean {
    return this.edit !== null && JSON.stringify(this.edit) !== this.snapshot;
  }

  /** The definition as the page would save it: the raw text, then the named fields on top. */
  private compose(edit: Edit): Record<string, any> {
    const sizes = edit.sizes.split(',').map((item) => item.trim()).filter(Boolean);
    return {
      ...this.definition,
      ...(edit.model ? { base_model_resource_id: edit.model } : {}),
      prompt_dialect: {
        ...this.definition.prompt_dialect,
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
  }

  /** The raw definition was edited by hand; when it parses, the named fields follow it. */
  private rawChanged(value: string): void {
    if (!this.edit) return;
    this.edit.raw = value;
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        const preset = this.preset();
        if (preset) this.start(preset, parsed);
      }
    } catch {
      // Half-typed JSON is not an error worth shouting about.
    }
  }

  private async save(): Promise<boolean> {
    const preset = this.preset();
    const edit = this.edit;
    if (!preset || !edit) return false;
    this.busy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const saved = await this.client.updateMediaPreset(preset.id, {
        ...preset,
        name: edit.name.trim() || preset.name,
        enabled: edit.enabled,
        priority: Number(edit.priority) || preset.priority,
        routing_card: edit.routingCard,
        definition: this.compose(edit),
      });
      this.presets = this.presets.map((item) => (item.id === saved.id ? saved : item));
      this.edit = null;
      this.start(saved, saved.definition ?? {});
      return true;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save this recipe.');
      return false;
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async remove(): Promise<void> {
    const preset = this.preset();
    if (!preset) return;
    if (!(await this.dialogs.confirm('Delete this recipe?', `${preset.name} will no longer be offered to chats.`, 'Delete'))) return;
    this.busy = true;
    this.renderApp();
    try {
      await this.client.deleteMediaPreset(preset.id);
      this.presets = this.presets.filter((item) => item.id !== preset.id);
      this.edit = null;
      this.snapshot = '';
      this.navigate(null);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to delete this recipe.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async prepareExport(): Promise<void> {
    const preset = this.preset();
    if (!preset) return;
    try {
      this.pendingExport = await this.client.exportPreset(preset.id);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to prepare that export.');
    }
    this.renderApp();
  }

  private saveExport(): void {
    const pending = this.pendingExport;
    if (!pending) return;
    const blob = new Blob([JSON.stringify(pending.bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = pending.filename;
    link.click();
    URL.revokeObjectURL(url);
    this.pendingExport = null;
    this.renderApp();
  }

  /** Leave only with intact work. */
  beforeLeave(go: () => void): void {
    void leaveGuard(this.dialogs, this.edit?.name || 'This recipe', this.dirty(), () => this.save(), go);
  }

  private models(kind: string): MediaCatalogResource[] {
    return (this.appState.mediaCatalog?.resources ?? [])
      .filter((item) => item.resource_type === 'model' && item.kind === kind)
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  node(): HTMLElement {
    const preset = this.preset();
    const edit = this.edit;
    const index = this.presets.findIndex((item) => item.id === this.presetId);
    const previous = this.presets[index - 1];
    const next = this.presets[index + 1];
    const nav = pageNav({
      back: 'All recipes',
      onBack: () => this.beforeLeave(() => this.navigate(null)),
      arrows: {
        previous: previous ? () => this.beforeLeave(() => this.navigate(previous.id)) : null,
        next: next ? () => this.beforeLeave(() => this.navigate(next.id)) : null,
      },
      busy: this.busy,
      testId: 'recipe-page',
    });
    if (!preset || !edit) {
      return el('div', { class: 'model-page', 'data-testid': 'recipe-page' }, [
        nav,
        settingsCard([el('p', { class: 'meta', textContent: 'That recipe is no longer in the catalog.' })]),
      ]);
    }
    const models = this.models(preset.kind);
    const model = models.find((item) => item.id === edit.model);
    return el('div', { class: 'model-page', 'data-testid': 'recipe-page' }, [
      nav,
      settingsCard([
        pageHead({
          name: edit.name,
          onName: (value) => { edit.name = value; this.renderApp(); },
          nameTitle: 'The recipe’s name, as chats and the model page will show it.',
          line: `${preset.kind === 'video' ? 'Video clips' : 'Pictures'} · ${model?.name ?? 'no model chosen'}`,
          testId: 'recipe-page',
        }),
        // Typing re-renders: the hint below goes as soon as there is a note,
        // and Save wakes up. Focus and caret survive the render.
        longField('When should this be used?', edit.routingCard, (value) => { edit.routingCard = value; this.renderApp(); }, {
          testId: 'recipe-page-card',
          hover: 'Plain language. Chats read this note when choosing between recipes, so describe the pictures it is for.',
        }),
        edit.routingCard.trim()
          ? null
          : pageHint('Without a note, this recipe can only be chosen by tags and priority.', 'recipe-page-hint'),
        switchField('Offer this recipe to chats', edit.enabled, (value) => { edit.enabled = value; }, {
          testId: 'recipe-page-enabled',
          hover: 'Off keeps the recipe but never offers it.',
        }),
        models.length
          ? choiceField('Model', edit.model, [...new Set([edit.model, ...models.map((item) => item.id)].filter(Boolean))], (value) => { edit.model = value; }, {
              testId: 'recipe-page-model',
              display: (value) => models.find((item) => item.id === value)?.name ?? value,
              hover: 'The look. A recipe runs on exactly one model.',
            })
          : null,
        el('div', { class: 'settings-grid' }, [
          numberField('Steps', edit.steps, (value) => { edit.steps = value; }, { hover: 'More steps refine a picture and take longer.' }),
          numberField('CFG', edit.cfg, (value) => { edit.cfg = value; }, { step: '0.1', hover: 'How strongly the picture follows the prompt.' }),
          textField('Sampler', edit.sampler, (value) => { edit.sampler = value; }, { hover: 'By the name ComfyUI uses.' }),
          textField('Scheduler', edit.scheduler, (value) => { edit.scheduler = value; }, { hover: 'Optional, by the name ComfyUI uses.' }),
        ]),
        textField('Sizes', edit.sizes, (value) => { edit.sizes = value; }, {
          hover: 'Width×height, comma separated, the usual one first. A request that needs another shape picks from the rest.',
        }),
        advancedSettings('More options', 'Prompt wording, negatives, priority, and the raw definition.', [
          el('div', { class: 'settings-grid' }, [
            choiceField('Prompt style', edit.style, STYLES, (value) => { edit.style = value; }, {
              display: styleLabel,
              hover: 'Booru means comma-separated tags; natural language means a sentence.',
            }),
            choiceField('LoRA trigger words go', edit.triggerPlacement, ['prefix', 'suffix'], (value) => { edit.triggerPlacement = value; }, {
              display: (value) => (value === 'prefix' ? 'Before the description' : 'After the description'),
            }),
            textField('Prompt prefix', edit.prefix, (value) => { edit.prefix = value; }, {
              hover: 'Score or quality tags, if this model family expects them.',
            }),
            textField('Prompt suffix', edit.suffix, (value) => { edit.suffix = value; }),
            numberField('Priority', edit.priority, (value) => { edit.priority = value; }, {
              hover: 'Breaks ties when several recipes fit equally well.',
            }),
          ]),
          switchField('Accepts a negative prompt', edit.supportsNegative, (value) => { edit.supportsNegative = value; this.renderApp(); }, {
            hover: 'Off means no negative prompt is sent, and the safety negative cannot be carried either.',
          }),
          edit.supportsNegative
            ? longField('Negative prompt', edit.negative, (value) => { edit.negative = value; }, {
                hover: 'The safety negative applied when adult output is off is separate and added by the platform.',
              })
            : null,
          longField('Raw definition', edit.raw, (value) => this.rawChanged(value), {
            rows: 12,
            testId: 'recipe-page-raw',
            hover: 'Everything above, plus slots and stages. The fields above follow it once it parses.',
          }),
        ].filter((node): node is HTMLElement => node !== null), {
          testId: 'recipe-page-more',
          open: this.moreOpen,
          onToggle: (open) => { this.moreOpen = open; },
        }),
        actionRow([
          saveButton({ dirty: this.dirty(), busy: this.busy, onSave: () => void this.save(), testId: 'recipe-page-save' }),
          el('button', {
            class: 'pill-btn',
            textContent: 'Export',
            title: 'See what a shareable file would contain, before writing one.',
            'data-testid': 'recipe-page-export',
            onclick: () => void this.prepareExport(),
          }),
          el('button', {
            class: 'pill-btn danger',
            textContent: 'Delete',
            disabled: this.busy,
            'data-testid': 'recipe-page-delete',
            onclick: () => void this.remove(),
          }),
        ]),
        this.exportPreview(),
      ].filter((node): node is HTMLElement => node !== null)),
    ]);
  }

  /** Everything that leaves, field by field. Nothing is written until it is saved. */
  private exportPreview(): HTMLElement | null {
    const pending = this.pendingExport;
    if (!pending) return null;
    return el('div', { class: 'recipe-export-preview', 'data-testid': 'recipe-page-export-preview' }, [
      el('ul', { class: 'settings-list', 'data-testid': 'export-preview' }, pending.preview.map((row) =>
        el('li', { class: 'settings-list-row' }, [
          el('span', { class: 'settings-list-name', textContent: row.label }),
          el('span', { class: 'settings-list-detail', textContent: row.value }),
        ]))),
      pending.requirements.length
        ? el('p', { class: 'meta', textContent: `Also needed, and named in the file so whoever imports it knows: ${pending.requirements.join('; ')}.` })
        : null,
      el('p', { class: 'meta', textContent: `Deliberately not included: ${pending.withheld.join('; ')}.` }),
      actionRow([
        el('button', { class: 'pill-btn', textContent: `Save ${pending.filename}`, 'data-testid': 'export-save', onclick: () => this.saveExport() }),
        el('button', { class: 'pill-btn', textContent: 'Cancel', onclick: () => { this.pendingExport = null; this.renderApp(); } }),
      ]),
    ]);
  }
}

function styleLabel(value: string): string {
  if (value === 'natural_language') return 'Natural language';
  if (value === 'booru') return 'Booru tags';
  return 'Hybrid';
}
