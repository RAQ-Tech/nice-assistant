import type { ApiClient } from './api';
import { el } from './dom';
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
import { advancedSettings, settingsCard, titleCase } from './settings_ui';
import type { AppState, MediaCatalogResource, MediaResourceType } from './types';

/**
 * One workflow or LoRA, one page.
 *
 * Models have the model page. Everything else in the catalog - the workflows
 * that are the method, the LoRAs that lean a model - had a fold inside the
 * inventory fold, with an information icon on every row. This is the same
 * door the model has: the name as the headline, what it works with, and the
 * planner's metadata folded.
 *
 * The page edits a copy and hands the finished resource to the catalog view to
 * save, so the catalog's revision bookkeeping stays in one place.
 */
export class ResourcePageView {
  resourceId: string | null = null;
  private edit: MediaCatalogResource | null = null;
  private snapshot = '';
  private moreOpen = false;
  private busy = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly dialogs: SettingsDialogs,
    private readonly navigate: (item: string | null) => void,
    private readonly saveResource: (resource: MediaCatalogResource) => Promise<boolean>,
    private readonly deleteResource: (resource: MediaCatalogResource) => Promise<boolean>,
  ) {}

  open(resourceId: string): void {
    this.resourceId = resourceId;
    this.moreOpen = false;
    const resource = this.resource();
    this.edit = resource ? JSON.parse(JSON.stringify(resource)) as MediaCatalogResource : null;
    this.snapshot = this.edit ? JSON.stringify(this.edit) : '';
  }

  private resource(): MediaCatalogResource | null {
    return (this.appState.mediaCatalog?.resources ?? []).find((item) => item.id === this.resourceId) ?? null;
  }

  private siblings(type: MediaResourceType): MediaCatalogResource[] {
    return (this.appState.mediaCatalog?.resources ?? [])
      .filter((item) => item.resource_type === type)
      .sort((left, right) => left.name.localeCompare(right.name));
  }

  private dirty(): boolean {
    return this.edit !== null && JSON.stringify(this.edit) !== this.snapshot;
  }

  private async save(): Promise<boolean> {
    const edit = this.edit;
    if (!edit) return false;
    this.busy = true;
    this.renderApp();
    try {
      const saved = await this.saveResource(edit);
      if (saved) this.open(edit.id);
      return saved;
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async remove(): Promise<void> {
    const edit = this.edit;
    if (!edit) return;
    if (await this.deleteResource(edit)) {
      this.edit = null;
      this.snapshot = '';
      this.navigate(null);
    }
  }

  beforeLeave(go: () => void): void {
    void leaveGuard(this.dialogs, this.edit?.name || 'This resource', this.dirty(), () => this.save(), go);
  }

  node(): HTMLElement {
    const resource = this.resource();
    const edit = this.edit;
    const type = resource?.resource_type ?? 'workflow';
    const siblings = this.siblings(type);
    const index = siblings.findIndex((item) => item.id === this.resourceId);
    const previous = siblings[index - 1];
    const next = siblings[index + 1];
    const nav = pageNav({
      back: type === 'lora' ? 'All LoRAs' : 'All workflows',
      onBack: () => this.beforeLeave(() => this.navigate(null)),
      arrows: {
        previous: previous ? () => this.beforeLeave(() => this.navigate(previous.id)) : null,
        next: next ? () => this.beforeLeave(() => this.navigate(next.id)) : null,
      },
      busy: this.busy,
      testId: 'resource-page',
    });
    if (!resource || !edit) {
      return el('div', { class: 'model-page', 'data-testid': 'resource-page' }, [
        nav,
        settingsCard([el('p', { class: 'meta', textContent: 'That is no longer in the catalog.' })]),
      ]);
    }
    const models = (this.appState.mediaCatalog?.resources ?? []).filter((item) =>
      item.resource_type === 'model'
      && item.kind === edit.kind
      && item.provider_key === edit.provider_key
      && item.backend === edit.backend
      && item.id !== edit.id);
    const settings = edit.default_settings;
    return el('div', { class: 'model-page', 'data-testid': 'resource-page' }, [
      nav,
      settingsCard([
        pageHead({
          name: edit.name,
          onName: (value) => { edit.name = value; this.renderApp(); },
          nameTitle: edit.external_id,
          line: `${type === 'lora' ? 'LoRA' : 'Workflow'} · ${backendLabel(edit.backend)} · ${edit.external_id}`,
          testId: 'resource-page',
        }),
        switchField(type === 'lora' ? 'May join a recipe' : 'May be chosen for pictures', edit.enabled, (value) => { edit.enabled = value; }, {
          testId: 'resource-page-enabled',
          hover: 'Off keeps it as a draft: editable, never chosen.',
        }),
        type === 'workflow' && !edit.enabled
          ? pageHint('A draft is never chosen. Turn it on once its bindings and its model have been checked.', 'resource-page-hint')
          : null,
        type === 'lora'
          ? numberField('Weight', String(settings.weight ?? 1), (value) => { settings.weight = Number(value) || 0; }, {
              step: '0.05',
              hover: 'How strongly this LoRA leans the model. 1 is what its author intended.',
            })
          : null,
        type === 'lora'
          ? textField('Trigger words', (Array.isArray(settings.trigger_words) ? settings.trigger_words : []).join(', '), (value) => {
              settings.trigger_words = value.split(',').map((item) => item.trim()).filter(Boolean);
            }, { hover: 'Comma separated. Added to the prompt where the recipe says trigger words go.' })
          : null,
        el('div', { class: 'setting-row', title: 'Only the ticked models can run with this. They must share its provider and backend.' }, [
          el('label', { textContent: 'Works with' }),
          models.length
            ? el('div', { class: 'chips', 'data-testid': 'resource-page-models' }, models.map((model) =>
                el('label', { class: 'checkbox-row' }, [
                  el('input', {
                    type: 'checkbox',
                    checked: edit.compatible_model_ids.includes(model.id),
                    onchange: (event: Event) => {
                      const checked = (event.currentTarget as HTMLInputElement).checked;
                      edit.compatible_model_ids = checked
                        ? [...new Set([...edit.compatible_model_ids, model.id])]
                        : edit.compatible_model_ids.filter((id) => id !== model.id);
                    },
                  }),
                  model.name,
                ])))
            : el('span', { class: 'meta', textContent: 'No model of the same kind, provider and backend is in the catalog.' }),
        ]),
        advancedSettings('More options', 'What the planner reads, the provider payload, and notes.', [
          el('div', { class: 'settings-grid' }, [
            choiceField('Makes', edit.kind, ['image', 'video'], (value) => { edit.kind = value === 'video' ? 'video' : 'image'; this.renderApp(); }, {
              display: (value) => (value === 'video' ? 'Video clips' : 'Pictures'),
            }),
            choiceField('Provider adapter', edit.provider_key, [...new Set([edit.provider_key, 'local-image', 'local-video'])], (value) => {
              edit.provider_key = value as MediaCatalogResource['provider_key'];
              this.renderApp();
            }, { display: titleCase, hover: 'The provider contract this runs through.' }),
            choiceField('Backend', edit.backend, [...new Set([edit.backend, 'comfyui', 'automatic1111'])], (value) => {
              edit.backend = value as MediaCatalogResource['backend'];
              this.renderApp();
            }, { display: backendLabel, hover: 'The service that owns the file.' }),
            textField(type === 'workflow' ? 'Catalog workflow ID' : 'Filename', edit.external_id, (value) => { edit.external_id = value; }, {
              hover: type === 'workflow' ? 'A label for the graph; the executable graph itself is in the default settings below.' : 'The exact filename the service knows.',
            }),
            numberField('Priority', String(edit.priority), (value) => { edit.priority = bounded(value, 0, 100, edit.priority); }, {
              hover: 'Breaks otherwise equal choices; higher wins.',
            }),
            numberField('Estimated VRAM (MB)', String(edit.estimated_vram_mb), (value) => { edit.estimated_vram_mb = bounded(value, 0, 131072, edit.estimated_vram_mb); }, {
              hover: '0 when unknown, rather than a guess.',
            }),
            numberField('Load time (seconds)', String(edit.estimated_load_seconds), (value) => { edit.estimated_load_seconds = bounded(value, 0, 3600, edit.estimated_load_seconds); }),
            textField('Operations', edit.operations.join(', '), (value) => { edit.operations = tags(value) as MediaCatalogResource['operations']; }, {
              hover: 'generate, inpaint, outpaint, image_to_image - comma separated.',
            }),
            textField('Domain strengths', edit.domains.join(', '), (value) => { edit.domains = tags(value); }, {
              hover: 'Subjects it is good at: fantasy, portrait, photorealism.',
            }),
            textField('Content strengths', edit.content_tags.join(', '), (value) => { edit.content_tags = tags(value); }, {
              hover: 'Content it is allowed and suited to handle.',
            }),
            textField('Features', edit.features.join(', '), (value) => { edit.features = tags(value); }, {
              hover: 'Hard capabilities such as text_to_image or identity_control.',
            }),
          ]),
          longField('Default settings JSON', JSON.stringify(edit.default_settings, null, 2), (value) => {
            try {
              const parsed = JSON.parse(value || '{}');
              if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) edit.default_settings = parsed;
              this.appState.settingsError = '';
            } catch {
              this.appState.settingsError = `Default settings for ${edit.name || 'this resource'} are not valid JSON.`;
            }
          }, { rows: 8, hover: 'Provider-specific defaults. A field the service does not know can block execution.' }),
          longField('Notes', edit.notes, (value) => { edit.notes = value; }, { hover: 'For whoever maintains the catalog next.' }),
        ], { testId: 'resource-page-more', open: this.moreOpen, onToggle: (open) => { this.moreOpen = open; } }),
        actionRow([
          saveButton({ dirty: this.dirty(), busy: this.busy, onSave: () => void this.save(), testId: 'resource-page-save' }),
          el('button', {
            class: 'pill-btn danger',
            textContent: 'Delete',
            disabled: this.busy,
            'data-testid': 'resource-page-delete',
            onclick: () => void this.remove(),
          }),
        ]),
      ].filter((node): node is HTMLElement => node !== null)),
    ]);
  }
}

function backendLabel(value: string): string {
  if (value === 'comfyui') return 'ComfyUI';
  if (value === 'automatic1111') return 'Automatic1111';
  if (value === 'openai') return 'OpenAI';
  return titleCase(value);
}

function tags(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

function bounded(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}
