import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { CatalogSetupView } from './catalog_setup_view';
import type { SettingsDialogs } from './settings_contracts';
import { settingsCard, settingsHeading } from './settings_ui';
import type { AppState, MediaCatalogResource } from './types';

/**
 * The models the catalog knows, and the way more of them get in.
 *
 * The catalog held exactly one model while forty-five checkpoints sat
 * installed in ComfyUI, because adding one meant typing its filename exactly
 * right into a bare dialog. Every picture therefore wore the same look, which
 * is what the owner noticed before anybody found this page.
 *
 * So the list comes from ComfyUI itself: press the button, tick the models to
 * add, and there is nothing to type and nothing to misspell. Each added model
 * automatically becomes a recipe the chat's planner can pick, so adding models
 * is adding variety.
 */
export class CatalogModelsView {
  private discovered: { name: string; cataloged: boolean }[] | null = null;
  private discoveryMessage = '';
  private busy = false;
  private readonly setupView: CatalogSetupView;
  private readonly selected = new Set<string>();

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly refreshCatalog: () => Promise<void>,
    private readonly openModel: (modelId: string) => void = () => undefined,
    dialogs: Pick<SettingsDialogs, 'consent'> | null = null,
  ) {
    this.setupView = new CatalogSetupView(appState, client, renderApp, refreshCatalog, dialogs);
  }

  node(models: MediaCatalogResource[]): HTMLElement {
    const shown = models.filter((model) => model.enabled);
    const hidden = models.filter((model) => !model.enabled);
    return settingsCard([
      settingsHeading(
        `Models — the look (${shown.length} enabled)`,
        shown.length === 0
          ? 'No models yet, so nothing can be generated. Find the checkpoints ComfyUI already has below.'
          : shown.length === 1
            ? 'One model means every picture shares its look. Add more from ComfyUI, and open one to set what it likes.'
            : 'Open a model to name it, set what it likes, and say when to use it.',
      ),
      shown.length
        ? el('div', { class: 'chips' }, shown.map((model) => this.modelButton(model)))
        : null,
      hidden.length
        ? el('details', { class: 'catalog-hidden-models' }, [
            el('summary', { class: 'meta', textContent: `Hidden (${hidden.length})` }),
            el('div', { class: 'chips' }, hidden.map((model) => this.modelButton(model))),
          ])
        : null,
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.busy ? 'Asking ComfyUI…' : 'Find models on ComfyUI',
          disabled: this.busy,
          'data-testid': 'catalog-discover-models',
          onclick: () => void this.discover(),
        }),
        models.length ? this.setupView.button(this.busy) : null,
      ]),
      this.setupView.summary(),
      this.discoveryMessage
        ? el('p', { class: 'meta', 'data-testid': 'catalog-discovery-message', textContent: this.discoveryMessage })
        : null,
      ...this.discoveryList(),
    ]);
  }

  private modelButton(model: MediaCatalogResource): HTMLElement {
    const sampleId = String(model.default_settings?.sample_media_id ?? '');
    return el('button', {
      class: `pill-btn thing-open ${sampleId ? 'has-thumb' : ''}`,
      title: model.external_id,
      'data-testid': `catalog-model-open-${model.id}`,
      onclick: () => this.openModel(model.id),
    }, [
      sampleId
        ? el('img', { class: 'thing-open-thumb', src: `/api/v1/media/${sampleId}`, alt: '' })
        : null,
      el('span', { textContent: model.name }),
    ]);
  }

  private discoveryList(): HTMLElement[] {
    if (!this.discovered) return [];
    const additions = this.discovered.filter((entry) => !entry.cataloged);
    if (!additions.length) {
      return [el('p', {
        class: 'meta',
        'data-testid': 'catalog-discovery-complete',
        textContent: 'Every checkpoint ComfyUI reports is already in the catalog.',
      })];
    }
    return [
      el('div', { class: 'catalog-discovery-list', 'data-testid': 'catalog-discovery-list' }, additions.map((entry) =>
        el('label', { class: 'catalog-discovery-row' }, [
          el('input', {
            type: 'checkbox',
            checked: this.selected.has(entry.name),
            'data-testid': `catalog-discovery-${entry.name}`,
            onchange: (event: Event) => {
              if ((event.currentTarget as HTMLInputElement).checked) this.selected.add(entry.name);
              else this.selected.delete(entry.name);
              this.renderApp();
            },
          }),
          el('span', { textContent: entry.name }),
        ]))),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.selected.size
            ? `Add ${this.selected.size} ${this.selected.size === 1 ? 'model' : 'models'}`
            : 'Add selected models',
          disabled: this.busy || !this.selected.size,
          'data-testid': 'catalog-add-selected',
          onclick: () => void this.addSelected(),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Select all',
          disabled: this.busy,
          onclick: () => {
            additions.forEach((entry) => this.selected.add(entry.name));
            this.renderApp();
          },
        }),
      ]),
    ];
  }

  private async discover(): Promise<void> {
    this.busy = true;
    this.renderApp();
    try {
      const listing = await this.client.comfyuiCheckpoints();
      this.discovered = listing.ok ? listing.checkpoints : null;
      this.discoveryMessage = listing.message;
      this.selected.clear();
    } catch (error) {
      this.discovered = null;
      this.discoveryMessage = errorMessage(error, 'ComfyUI could not be asked for its models.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async addSelected(): Promise<void> {
    const names = [...this.selected];
    if (!names.length) return;
    this.busy = true;
    this.renderApp();
    let result: { added: string[]; skipped: { name: string; reason: string }[] };
    try {
      result = await this.client.addModelsFromCheckpoints(names);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The models could not be added.');
      this.busy = false;
      this.renderApp();
      return;
    }
    // Each skip is reported with the backend's own reason rather than a
    // one-size guess: a filename rejected by validation is not "already
    // present", and saying so would tell somebody a model exists that never
    // entered the catalog.
    const skipNotes = result.skipped.map((entry) => `${entry.name}: ${entry.reason}`).join('; ');
    const addedPart = result.added.length
      ? `Added ${result.added.length} ${result.added.length === 1 ? 'model' : 'models'} — each has its own recipe now.`
      : 'Nothing was added.';
    this.discoveryMessage = skipNotes ? `${addedPart} Skipped ${skipNotes}.` : addedPart;
    this.selected.clear();
    try {
      // Refetch rather than guess: the discovery list re-marks what is now
      // cataloged, and the catalog view shows the new models and presets.
      await this.refreshCatalog();
      const listing = await this.client.comfyuiCheckpoints();
      if (listing.ok) this.discovered = listing.checkpoints;
    } catch {
      // The add itself succeeded; a failed re-listing must not claim otherwise.
      this.discoveryMessage += ' The list could not be refreshed — press the button again to update it.';
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
