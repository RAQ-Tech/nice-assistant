import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { settingsWire } from './settings';
import type { SettingsDialogs } from './settings_contracts';
import type { AppState, ModelSetupReport } from './types';

interface SetupProgress {
  running: boolean;
  done: number;
  total: number;
  lookup: boolean;
  processed: ModelSetupReport['processed'];
  withoutCard: string[];
  message: string;
}

/**
 * Setting up every model in one sitting.
 *
 * Forty-five models, one page each, is why none were ever set up. This runs
 * the fills the model page offers - family from the file, name, numbers and
 * trigger words from CivitAI - over all of them, five at a time so the page
 * shows progress and Stop means stop. CivitAI is the one call that leaves the
 * machine, so it is asked about first, in the model page's own words, and
 * declining still sets up from the files alone.
 */
export class CatalogSetupView {
  private progress: SetupProgress | null = null;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly refreshCatalog: () => Promise<void>,
    private readonly dialogs: Pick<SettingsDialogs, 'consent'> | null,
  ) {}

  button(disabled: boolean): HTMLElement {
    const running = Boolean(this.progress?.running);
    return el('button', {
      class: 'pill-btn',
      textContent: running ? 'Stop' : 'Set up all models',
      title: 'Reads each file for its family, looks the file name up on CivitAI with your say-so, and fills what each answer supports, saying where it came from.',
      disabled,
      'data-testid': 'catalog-setup-models',
      onclick: () => {
        if (this.progress?.running) this.progress.running = false;
        else void this.run();
      },
    });
  }

  /** What the pass did, in counts, and who still has no routing card. */
  summary(): HTMLElement | null {
    const progress = this.progress;
    if (!progress) return null;
    if (progress.running) {
      return el('p', { class: 'meta', 'data-testid': 'catalog-setup-progress', textContent: `Setting up ${progress.done} of ${progress.total || '…'}…` });
    }
    const count = (word: string) => progress.processed.filter((item) => item.filled.some((fill) => fill.startsWith(word))).length;
    const picks = progress.processed.filter((item) => item.lookup === 'nearest').length;
    const parts = [
      `${progress.done} of ${progress.total} set up.`,
      `Family for ${count('family')}, numbers for ${count('steps')}, names for ${count('name')}, trigger words for ${count('trigger')}.`,
      picks ? `${picks} had near matches only on CivitAI; pick one on the model’s page if it is right.` : '',
      progress.lookup ? '' : 'CivitAI was skipped, so names and trigger words were not looked up.',
      progress.message,
    ].filter(Boolean);
    return el('div', { class: 'catalog-setup-report', 'data-testid': 'catalog-setup-report' }, [
      el('p', { class: 'meta', textContent: parts.join(' ') }),
      progress.withoutCard.length
        ? el('details', { class: 'catalog-hidden-models' }, [
            el('summary', { class: 'meta', textContent: `Still without a routing card (${progress.withoutCard.length}) - open a model to say when it should be used` }),
            el('p', { class: 'meta', 'data-testid': 'catalog-setup-without-card', textContent: progress.withoutCard.join(', ') }),
          ])
        : null,
    ]);
  }

  /** The pass, a few models at a time, so progress is visible and Stop means stop. */
  private async run(): Promise<void> {
    const settings = this.appState.settings;
    let lookup = Boolean(settings?.civitai_lookup_skip_confirm);
    if (!lookup && this.dialogs) {
      const answer = await this.dialogs.consent(
        'Look these models up online?',
        'This sends each model’s file name to civitai.com to find its page, its trigger words and the settings its creator published. Nothing else is sent. Cancel to set up from the files alone.',
        'OK',
        'Don’t show this again',
      );
      lookup = answer.ok;
      if (answer.ok && answer.remember && settings) {
        settings.civitai_lookup_skip_confirm = true;
        await this.client.updateSettings(settingsWire(settings)).catch(() => undefined);
      }
    }
    const progress: SetupProgress = { running: true, done: 0, total: 0, lookup, processed: [], withoutCard: [], message: '' };
    this.progress = progress;
    this.renderApp();
    try {
      let remaining = 1;
      while (remaining > 0 && progress.running) {
        const page = await this.client.setupModels({ limit: 5, lookup });
        progress.processed.push(...page.processed);
        progress.done += page.processed.length;
        progress.total = page.total;
        progress.withoutCard = page.without_routing_card;
        remaining = page.remaining;
        if (!page.processed.length) break;
        this.renderApp();
      }
    } catch (error) {
      progress.message = errorMessage(error, 'The models could not be set up.');
    } finally {
      progress.running = false;
      this.renderApp();
      await this.refreshCatalog();
    }
  }
}
