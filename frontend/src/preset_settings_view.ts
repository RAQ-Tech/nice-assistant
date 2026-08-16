import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import { advancedSettings, operatorEditor, settingsCard, settingsHeading } from './settings_ui';
import type { AppState, MediaCatalogResource, MediaPreset, PresetExport, PresetSignal } from './types';

/**
 * The preset editor.
 *
 * A preset is the tested recipe, so it is the thing an operator opens - not a
 * JSON blob describing one. Every value that decides how a picture comes out
 * gets a named field, because "cfg_scale" buried in raw JSON is exactly how a
 * settings screen becomes unusable.
 */

const STYLES = ['natural_language', 'booru', 'hybrid'] as const;
const PLACEMENTS = ['prefix', 'suffix'] as const;

function styleLabel(value: string): string {
  if (value === 'natural_language') return 'Natural language';
  if (value === 'booru') return 'Booru tags';
  return 'Hybrid';
}

export class PresetSettingsView {
  private presets: MediaPreset[] = [];
  private signals: PresetSignal[] = [];
  private pendingExport: PresetExport | null = null;
  private readonly openIds = new Set<string>();
  private readonly dirtyIds = new Set<string>();
  private busy = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
  ) {}

  async refresh(): Promise<void> {
    this.busy = true;
    try {
      this.presets = (await this.client.mediaPresets()).items;
      this.signals = (await this.client.presetSignals()).items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load generation presets.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async clearSignals(presetId: string): Promise<void> {
    try {
      await this.client.clearPresetSignals(presetId);
      this.signals = this.signals.filter((item) => item.preset_id !== presetId);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to reset those counts.');
    }
    this.renderApp();
  }

  private async prepareExport(preset: MediaPreset): Promise<void> {
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

  private exportPreviewCard(): HTMLElement | null {
    const pending = this.pendingExport;
    if (!pending) return null;
    return settingsCard([
      settingsHeading(
        `What will be in ${pending.filename}`,
        'Everything that leaves, field by field. Nothing is written until you save it.',
      ),
      el('ul', { class: 'settings-list', 'data-testid': 'export-preview' }, pending.preview.map((row) =>
        el('li', { class: 'settings-list-row' }, [
          el('span', { class: 'settings-list-name', textContent: row.label }),
          el('span', { class: 'settings-list-detail', textContent: row.value }),
        ]))),
      pending.requirements.length
        ? el('div', {}, [
            el('p', { class: 'meta', textContent: 'This recipe also needs things a file cannot carry. They are named in it so whoever imports it knows:' }),
            el('ul', { class: 'settings-list' }, pending.requirements.map((item) =>
              el('li', { class: 'settings-list-row' }, [el('span', { class: 'settings-list-detail', textContent: item })]))),
          ])
        : null,
      el('p', { class: 'meta', textContent: `Deliberately not included: ${pending.withheld.join('; ')}.` }),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: 'Save the file',
          'data-testid': 'export-save',
          onclick: () => this.saveExport(),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Cancel',
          onclick: () => { this.pendingExport = null; this.renderApp(); },
        }),
      ]),
    ]);
  }

  private signalsCard(): HTMLElement {
    return settingsCard([
      settingsHeading(
        'What has happened to the pictures',
        'Counted when you keep a picture, when one is sent again, or when you remove one. Making a picture is not counted, because the platform chose the preset. These counts can move a preset up the order; they can never make one eligible that does not already fit the request.',
      ),
      this.signals.length
        ? el('ul', { class: 'settings-list', 'data-testid': 'preset-signals' }, this.signals.map((signal) =>
            el('li', { class: 'settings-list-row' }, [
              el('span', { class: 'settings-list-name', textContent: signal.preset_name }),
              el('span', { class: 'settings-list-detail', textContent: signal.summary }),
              el('span', {
                class: 'settings-list-detail',
                textContent: `Score ${signal.weight > 0 ? '+' : ''}${signal.weight}`,
              }),
              el('button', {
                class: 'pill-btn',
                textContent: 'Reset',
                title: 'Forget the counts for this preset',
                onclick: () => void this.clearSignals(signal.preset_id),
              }),
            ])))
        : el('p', { class: 'settings-empty', textContent: 'Nothing counted yet. Keep, reuse, or remove a picture and it will appear here.' }),
    ]);
  }

  node(): HTMLElement[] {
    return [
      settingsCard([
        settingsHeading(
          `Generation presets (${this.presets.length})`,
          'A preset is a tested recipe: which model, which workflow, which settings, and how its prompts are written. Planning chooses between presets rather than assembling one.',
        ),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'pill-btn',
            textContent: this.busy ? 'Loading…' : 'Refresh presets',
            disabled: this.busy,
            'data-testid': 'presets-refresh',
            onclick: () => void this.refresh(),
          }),
        ]),
        this.presets.length
          ? null
          : el('p', {
              class: 'meta',
              textContent: 'No presets yet. Enable an image model, or install a starter preset below.',
            }),
      ]),
      ...this.presets.map((preset) => this.editor(preset)),
      this.exportPreviewCard(),
      // After the presets: this describes them, so it reads as a footnote
      // rather than as the point of the screen.
      this.signalsCard(),
    ].filter((node): node is HTMLElement => node !== null);
  }

  private editor(preset: MediaPreset): HTMLElement {
    const definition = preset.definition ?? {};
    const dialect = definition.prompt_dialect ?? {};
    const sampler = definition.sampler ?? {};
    const models = (this.appState.mediaCatalog?.resources ?? []).filter(
      (item: MediaCatalogResource) => item.resource_type === 'model' && item.kind === preset.kind,
    );
    const status = preset.enabled ? 'Enabled' : 'Disabled';
    return operatorEditor(
      preset.name,
      preset.routing_card || 'No routing card yet, so this can only be chosen by tags and priority.',
      status,
      [
        inputField('Name', preset.name, (value) => { preset.name = value; this.markDirty(preset); }),
        textareaField(
          'When should this be used?',
          preset.routing_card,
          (value) => { preset.routing_card = value; this.markDirty(preset); },
          false,
          'Plain language. This is what routing reads when deciding between presets, so describe the pictures it is for.',
        ),
        toggleField('Enabled', preset.enabled, (value) => { preset.enabled = value; this.markDirty(preset); }),
        inputField(
          'Priority',
          String(preset.priority),
          (value) => { preset.priority = Number(value) || 0; this.markDirty(preset); },
          'number',
          false,
          'Breaks ties when several presets fit equally well.',
        ),
        models.length
          ? selectField(
              'Base model',
              String(definition.base_model_resource_id ?? ''),
              models.map((item) => item.id),
              (value) => { definition.base_model_resource_id = value; this.markDirty(preset); },
              undefined,
              (value) => models.find((item) => item.id === value)?.name ?? value,
              false,
            )
          : null,
        el('h4', { class: 'settings-subheading', textContent: 'Prompt dialect' }),
        selectField(
          'Prompt style',
          String(dialect.style ?? 'natural_language'),
          STYLES,
          (value) => { dialect.style = value; this.markDirty(preset); },
          undefined,
          styleLabel,
          false,
          'How this model wants prompts written. Booru means comma-separated tags; natural language means a sentence.',
        ),
        inputField(
          'Prompt prefix',
          String(dialect.prefix ?? ''),
          (value) => { dialect.prefix = value; this.markDirty(preset); },
          'text',
          false,
          'Put score or quality tags here if this model family expects them. Leave empty when it does not.',
        ),
        inputField('Prompt suffix', String(dialect.suffix ?? ''), (value) => {
          dialect.suffix = value;
          this.markDirty(preset);
        }),
        toggleField(
          'This model accepts a negative prompt',
          dialect.supports_negative !== false,
          (value) => { dialect.supports_negative = value; this.markDirty(preset); },
        ),
        dialect.supports_negative !== false
          ? textareaField(
              'Negative prompt',
              String(dialect.negative_prompt ?? ''),
              (value) => { dialect.negative_prompt = value; this.markDirty(preset); },
              false,
              'The safety negative applied when adult output is off is separate from this and is added by the platform.',
            )
          : el('p', {
              class: 'meta',
              textContent: 'No negative prompt is sent, and the platform safety negative cannot be carried either.',
            }),
        selectField(
          'LoRA trigger words go',
          String(dialect.trigger_placement ?? 'suffix'),
          PLACEMENTS,
          (value) => { dialect.trigger_placement = value; this.markDirty(preset); },
          undefined,
          (value) => (value === 'prefix' ? 'Before the description' : 'After the description'),
          false,
        ),
        el('h4', { class: 'settings-subheading', textContent: 'Sampling' }),
        inputField('Steps', String(sampler.steps ?? ''), (value) => {
          sampler.steps = Number(value) || undefined;
          this.markDirty(preset);
        }, 'number'),
        inputField('Guidance (CFG)', String(sampler.cfg_scale ?? ''), (value) => {
          sampler.cfg_scale = Number(value) || undefined;
          this.markDirty(preset);
        }, 'number'),
        inputField('Sampler', String(sampler.sampler_name ?? ''), (value) => {
          sampler.sampler_name = value;
          this.markDirty(preset);
        }),
        inputField('Scheduler', String(sampler.scheduler ?? ''), (value) => {
          sampler.scheduler = value;
          this.markDirty(preset);
        }),
        inputField(
          'Dimensions',
          (definition.dimensions ?? []).join(', '),
          (value) => {
            definition.dimensions = value.split(',').map((item) => item.trim()).filter(Boolean);
            this.markDirty(preset);
          },
          'text',
          false,
          'Comma separated, widest first. The first is used unless a request needs another.',
        ),
        advancedSettings('Raw definition', 'Everything above, plus slots and stages.', [
          textareaField('Definition JSON', JSON.stringify(definition, null, 2), (value) => {
            try {
              const parsed = JSON.parse(value);
              if (parsed && typeof parsed === 'object') {
                preset.definition = parsed;
                this.markDirty(preset);
              }
            } catch {
              // A half-typed object is not an error worth shouting about.
            }
          }),
        ]),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'send-btn',
            textContent: this.dirtyIds.has(preset.id) ? 'Save preset' : 'Saved',
            disabled: !this.dirtyIds.has(preset.id) || this.busy,
            'data-testid': `preset-save-${preset.id}`,
            onclick: () => void this.save(preset),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: 'Export',
            title: 'See what a shareable file would contain, before writing one',
            'data-testid': `preset-export-${preset.id}`,
            onclick: () => void this.prepareExport(preset),
          }),
          el('button', {
            class: 'pill-btn danger',
            textContent: 'Delete',
            'data-testid': `preset-delete-${preset.id}`,
            onclick: () => void this.remove(preset),
          }),
        ]),
      ],
      {
        open: this.openIds.has(preset.id),
        onToggle: (open) => {
          if (open) this.openIds.add(preset.id);
          else this.openIds.delete(preset.id);
        },
        testId: `preset-${preset.id}`,
      },
    );
  }

  private markDirty(preset: MediaPreset): void {
    this.dirtyIds.add(preset.id);
  }

  private async save(preset: MediaPreset): Promise<void> {
    this.busy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await this.client.updateMediaPreset(preset.id, {
        name: preset.name,
        kind: preset.kind,
        enabled: preset.enabled,
        priority: preset.priority,
        routing_card: preset.routing_card,
        operations: preset.operations,
        domains: preset.domains,
        content_tags: preset.content_tags,
        features: preset.features,
        definition: preset.definition,
        estimated_vram_mb: preset.estimated_vram_mb,
        notes: preset.notes,
      });
      this.dirtyIds.delete(preset.id);
      await this.refresh();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save that preset.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async remove(preset: MediaPreset): Promise<void> {
    this.busy = true;
    this.renderApp();
    try {
      await this.client.deleteMediaPreset(preset.id);
      await this.refresh();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to delete that preset.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
