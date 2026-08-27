import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import type { SettingsDialogs } from './settings_contracts';
import { settingsWire } from './settings';
import type { AppState, CivitaiMatch } from './types';

/**
 * The CivitAI lookup on a model's page.
 *
 * The one call in the catalog that leaves the LAN, so it never happens as a
 * side effect: a person presses the button, a popup names civitai.com and
 * offers cancel, ok, and "don't show this again", and only then does the
 * filename go out. The answer is a pick-list, not an auto-fill - the search
 * runs on the filename, and only the person knows which match is their file.
 */
export class ModelLookupView {
  private matches: CivitaiMatch[] | null = null;
  private message = '';
  private failed = false;
  private busy = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly dialogs: SettingsDialogs,
    private readonly apply: (match: CivitaiMatch) => void,
  ) {}

  reset(): void {
    this.matches = null;
    this.message = '';
  }

  node(checkpoint: string): HTMLElement {
    return el('div', { class: 'model-lookup' }, [
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: this.busy ? 'Asking CivitAI…' : 'Look up on CivitAI',
          disabled: this.busy,
          'data-testid': 'model-lookup-run',
          onclick: () => void this.run(checkpoint),
        }),
      ]),
      this.message
        ? el('p', {
            // A failure must be loud enough to notice: a quiet gray line under
            // the button reads as nothing having happened at all.
            class: this.failed ? 'settings-warning' : 'meta',
            'data-testid': 'model-lookup-message',
            textContent: this.message,
          })
        : null,
      this.matches?.length
        ? el('div', { class: 'model-lookup-matches', 'data-testid': 'model-lookup-matches' },
            this.matches.map((match, index) => this.row(match, index)))
        : null,
    ]);
  }

  private row(match: CivitaiMatch, index: number): HTMLElement {
    const settings = [
      match.steps !== undefined ? `${match.steps} steps` : '',
      match.cfg_scale !== undefined ? `guidance ${match.cfg_scale}` : '',
      match.sampler ?? '',
      match.width && match.height ? `${match.width}×${match.height}` : '',
    ].filter(Boolean).join(' · ');
    const source = match.settings_source === 'showcase'
      ? `${settings} (from the creator’s showcase)`
      : match.settings_source === 'family'
        ? `${settings} (typical for ${match.family_label ?? match.base_model})`
        : 'no settings published';
    return el('div', { class: 'model-lookup-row' }, [
      el('div', {}, [
        el('strong', { textContent: `${match.model_name} — ${match.version_name}` }),
        el('div', {
          class: 'meta',
          textContent: [
            match.base_model,
            match.file_match ? 'matches your file exactly' : '',
            source,
          ].filter(Boolean).join(' · '),
        }),
      ]),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: 'Use',
          'data-testid': `model-lookup-use-${index}`,
          onclick: () => this.apply(match),
        }),
        el('a', { class: 'pill-btn', textContent: 'Open page', href: match.url, target: '_blank', rel: 'noreferrer noopener' }),
      ]),
    ]);
  }

  private async run(checkpoint: string): Promise<void> {
    const settings = this.appState.settings;
    if (settings && !settings.civitai_lookup_skip_confirm) {
      const answer = await this.dialogs.consent(
        'Look this model up online?',
        'This sends the model’s file name to civitai.com to find its page and the settings its creator published. Nothing else is sent.',
        'OK',
        'Don’t show this again',
      );
      if (!answer.ok) return;
      if (answer.remember) {
        settings.civitai_lookup_skip_confirm = true;
        // Best effort: a failed remember only means the popup returns.
        await this.client.updateSettings(settingsWire(settings)).catch(() => undefined);
      }
    }
    this.busy = true;
    this.renderApp();
    try {
      const result = await this.client.civitaiLookup(checkpoint);
      this.matches = result.matches;
      this.message = result.message;
      this.failed = !result.ok;
    } catch (error) {
      this.matches = null;
      this.message = errorMessage(error, 'civitai.com could not be reached.');
      this.failed = true;
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
