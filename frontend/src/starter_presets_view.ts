import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { settingsCard, settingsHeading } from './settings_ui';
import type { AppState, StarterPreset } from './types';

/**
 * Starter presets.
 *
 * These carry published defaults for a model family - sampler, steps, CFG,
 * dimensions, prompt dialect. They are a starting point, never a measurement,
 * and the card says so: nothing here has been tested on this deployment.
 *
 * A starter whose model file is not in the catalog is listed with the filename
 * it wants rather than installed as a preset that could never run.
 */
export class StarterPresetsView {
  private starters: StarterPreset[] | null = null;
  private busy = false;
  private summary = '';

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly afterInstall: () => Promise<void>,
  ) {}

  node(): HTMLElement {
    return settingsCard([
      settingsHeading(
        'Starter presets',
        'Published settings for common model families: sampler, steps, guidance, dimensions, and prompt style. They are a starting point, not a measurement — nothing here has been tested on this deployment.',
      ),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: this.busy ? 'Checking…' : 'Check starter presets',
          disabled: this.busy,
          'data-testid': 'starter-presets-check',
          onclick: () => void this.check(),
        }),
        this.starters?.some((item) => item.installable)
          ? el('button', {
              class: 'pill-btn',
              textContent: this.busy ? 'Installing…' : 'Install available starters',
              disabled: this.busy,
              'data-testid': 'starter-presets-install',
              onclick: () => void this.install(),
            })
          : null,
      ]),
      this.summary ? el('p', { class: 'meta', 'data-testid': 'starter-presets-summary', textContent: this.summary }) : null,
      this.starters ? this.list(this.starters) : null,
    ]);
  }

  private list(starters: StarterPreset[]): HTMLElement {
    return el('ul', { class: 'routing-shortlist', 'data-testid': 'starter-presets-list' }, starters.map((item) =>
      el('li', {}, [
        el('span', { class: 'routing-shortlist-title', textContent: item.name }),
        el('span', { class: 'meta', textContent: item.routing_card }),
        item.already_present
          ? el('span', { class: 'meta', textContent: 'Already in your presets — it will not be overwritten.' })
          : item.missing_assets.length
            ? el('span', {
                class: 'meta',
                textContent: `Needs a model this catalog does not have: ${item.missing_assets.join(', ')}`,
              })
            : el('span', { class: 'meta', textContent: 'Ready to install.' }),
      ]),
    ));
  }

  private async check(): Promise<void> {
    this.busy = true;
    this.renderApp();
    try {
      this.starters = (await this.client.starterPresets()).presets;
      this.summary = `${this.starters.filter((item) => item.installable).length} of ${this.starters.length} can be installed now.`;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to read the starter presets.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async install(): Promise<void> {
    this.busy = true;
    this.renderApp();
    try {
      const result = await this.client.installStarterPresets();
      this.summary = result.installed.length
        ? `Installed ${result.installed.map((item) => item.name).join(', ')}.`
        : 'Nothing was installed.';
      if (result.skipped.length) {
        this.summary += ` Skipped: ${result.skipped.map((item) => `${item.name} (${item.reason})`).join('; ')}`;
      }
      this.starters = (await this.client.starterPresets()).presets;
      await this.afterInstall();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to install the starter presets.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
