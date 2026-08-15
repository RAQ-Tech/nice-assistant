import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { settingsCard, settingsHeading } from './settings_ui';
import type { AppState, LibraryEntry } from './types';

/**
 * The retained picture library, per persona.
 *
 * Pictures kept for reuse are the persona's, so they belong on the persona's
 * own screen rather than in the media catalog next to checkpoints. Every entry
 * shows the scene it was kept under, because that is what a later request is
 * matched against - and an entry nobody can interpret is one nobody can decide
 * to delete.
 */
export class PictureLibraryView {
  private entries: LibraryEntry[] = [];
  private busy = false;
  private loadedPersonaId = '';

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
  ) {}

  async refresh(personaId: string): Promise<void> {
    this.busy = true;
    this.loadedPersonaId = personaId;
    try {
      this.entries = (await this.client.libraryEntries(personaId)).items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load kept pictures.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  node(personaId: string): HTMLElement {
    const stale = personaId !== this.loadedPersonaId;
    return settingsCard([
      settingsHeading(
        `Kept pictures (${stale ? 0 : this.entries.length})`,
        'Pictures kept for reuse, with the description they were kept under. A later request is matched against that description, never against prompt text.',
      ),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: this.busy ? 'Loading…' : 'Show kept pictures',
          disabled: this.busy || !personaId,
          'data-testid': 'library-refresh',
          onclick: () => void this.refresh(personaId),
        }),
      ]),
      stale
        ? el('p', { class: 'meta', textContent: 'Choose a persona and show its kept pictures.' })
        : this.entries.length
          ? el('ul', { class: 'library-entries', 'data-testid': 'library-entries' }, this.entries.map((entry) => this.entry(entry)))
          : el('p', {
              class: 'meta',
              textContent: 'Nothing kept yet. Pictures are kept automatically once they are made with a description.',
            }),
    ]);
  }

  private entry(entry: LibraryEntry): HTMLElement {
    const scene = entry.scene ?? {};
    const description = [scene.subject, scene.action, scene.setting].filter(Boolean).join(', ');
    return el('li', { class: 'library-entry' }, [
      el('img', { class: 'library-thumb', src: entry.content_url, alt: description || 'Kept picture' }),
      el('div', { class: 'library-entry-body' }, [
        el('span', { textContent: description || 'No description recorded.' }),
        el('span', {
          class: 'meta',
          textContent:
            entry.state === 'retired'
              ? 'Retired: past the keep limit, so it will not be sent again. The picture itself is untouched.'
              : entry.state === 'served'
                ? `Sent ${entry.served_count} time${entry.served_count === 1 ? '' : 's'}.`
                : 'Ready to send.',
        }),
      ]),
      el('button', {
        class: 'pill-btn danger',
        textContent: 'Forget',
        title: 'Stop reusing this picture. The picture itself stays.',
        'data-testid': `library-forget-${entry.id}`,
        onclick: () => void this.forget(entry),
      }),
    ]);
  }

  private async forget(entry: LibraryEntry): Promise<void> {
    this.busy = true;
    this.renderApp();
    try {
      await this.client.deleteLibraryEntry(entry.id);
      this.entries = this.entries.filter((item) => item.id !== entry.id);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to forget that picture.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
