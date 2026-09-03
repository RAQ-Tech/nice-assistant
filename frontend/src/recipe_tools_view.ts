import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { actionRow, groupTitle } from './settings_page';
import { settingsCard } from './settings_ui';
import type { AppState, PresetImportPreview, PresetSignal } from './types';

/**
 * Recipe files, and what has happened to the pictures.
 *
 * Two operator tools that used to sit beside the recipe editors. One brings in
 * a recipe file somebody exported from their own installation, shown before
 * anything is installed. The other is the counts - kept, sent again, removed -
 * that can move a recipe up the order and can never make one eligible that
 * does not already fit the request.
 */
export class RecipeToolsView {
  private signals: PresetSignal[] = [];
  private pendingImport: { bundle: unknown; preview: PresetImportPreview } | null = null;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly afterImport: () => Promise<void>,
  ) {}

  async refresh(): Promise<void> {
    try {
      this.signals = (await this.client.presetSignals()).items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load the picture counts.');
    }
    this.renderApp();
  }

  nodes(): HTMLElement[] {
    return [this.importBlock(), this.signalsBlock()];
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

  private async chooseImportFile(file: File | null): Promise<void> {
    if (!file) return;
    try {
      const bundle = JSON.parse(await file.text());
      this.pendingImport = { bundle, preview: await this.client.previewPresetImport(bundle) };
    } catch (error) {
      this.pendingImport = null;
      this.appState.settingsError = errorMessage(error, 'That file could not be read as a recipe file.');
    }
    this.renderApp();
  }

  private async confirmImport(): Promise<void> {
    const pending = this.pendingImport;
    if (!pending) return;
    try {
      await this.client.importPresets(pending.bundle);
      this.pendingImport = null;
      await this.afterImport();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Nothing was imported.');
      this.renderApp();
    }
  }

  private importBlock(): HTMLElement {
    const pending = this.pendingImport;
    return settingsCard([
      groupTitle('Import a recipe file', 'A file somebody exported from their own installation. Choosing one shows what it would do here; nothing is installed until you confirm.'),
      el('input', {
        type: 'file',
        accept: 'application/json,.json',
        'data-testid': 'import-file',
        onchange: (event: Event) => {
          const input = event.currentTarget as HTMLInputElement;
          void this.chooseImportFile(input.files?.[0] ?? null);
        },
      }),
      ...(pending
        ? [
            ...pending.preview.warnings.map((warning) => el('p', { class: 'meta warning', textContent: warning })),
            el('ul', { class: 'settings-list', 'data-testid': 'import-preview' }, pending.preview.presets.map((entry) =>
              el('li', { class: 'settings-list-row' }, [
                el('span', { class: 'settings-list-name', textContent: entry.name }),
                el('span', {
                  class: 'settings-list-detail',
                  textContent: entry.installable
                    ? (entry.requirements.length ? `Will install. Also needs: ${entry.requirements.join('; ')}` : 'Will install')
                    : entry.blockers.join('; '),
                }),
              ]))),
            actionRow([
              el('button', {
                class: 'send-btn',
                textContent: pending.preview.installable ? 'Import these recipes' : 'Cannot import this file',
                disabled: !pending.preview.installable,
                'data-testid': 'import-confirm',
                onclick: () => void this.confirmImport(),
              }),
              el('button', {
                class: 'pill-btn',
                textContent: 'Cancel',
                onclick: () => { this.pendingImport = null; this.renderApp(); },
              }),
            ]),
          ]
        : []),
    ]);
  }

  private signalsBlock(): HTMLElement {
    return settingsCard([
      groupTitle(
        'What has happened to the pictures',
        'Counted when you keep a picture, send one again, or remove one. Making a picture is not counted, because the platform chose the recipe. These counts can move a recipe up the order; they can never make one eligible that does not already fit the request.',
      ),
      this.signals.length
        ? el('ul', { class: 'settings-list', 'data-testid': 'preset-signals' }, this.signals.map((signal) =>
            el('li', { class: 'settings-list-row' }, [
              el('span', { class: 'settings-list-name', textContent: signal.preset_name }),
              el('span', { class: 'settings-list-detail', textContent: signal.summary }),
              el('span', { class: 'settings-list-detail', textContent: `Score ${signal.weight > 0 ? '+' : ''}${signal.weight}` }),
              el('button', {
                class: 'pill-btn',
                textContent: 'Reset',
                title: 'Forget the counts for this recipe',
                onclick: () => void this.clearSignals(signal.preset_id),
              }),
            ])))
        : el('p', { class: 'settings-empty', textContent: 'Nothing counted yet. Keep, reuse, or remove a picture and it will appear here.' }),
    ]);
  }
}
