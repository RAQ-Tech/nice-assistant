import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { settingsCard, settingsHeading } from './settings_ui';
import type { AppState, LibraryEntry, MediaPreset, VisualIdentityProfile } from './types';

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
  private presets: MediaPreset[] = [];
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
      this.presets = (await this.client.mediaPresets()).items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load kept pictures.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  node(
    personaId: string,
    profile: VisualIdentityProfile | null = null,
    onSaved: () => Promise<void> = async () => undefined,
  ): HTMLElement {
    const preferred = profile?.preferred_preset_ids ?? [];
    const save = (ids: string[]) => void this.savePreferred(personaId, profile, ids, onSaved);
    const stale = personaId !== this.loadedPersonaId;
    return settingsCard([
      settingsHeading('Preferred recipes', 'Which presets are known to work for this persona, best first. Routing prefers them when a request does not call for something else.'),
      this.preferences(preferred, save),
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

  private async savePreferred(
    personaId: string,
    profile: VisualIdentityProfile | null,
    ids: string[],
    onSaved: () => Promise<void>,
  ): Promise<void> {
    if (!personaId || !profile) return;
    profile.preferred_preset_ids = ids;
    this.renderApp();
    try {
      await this.client.updateVisualIdentity(personaId, { ...profile, preferred_preset_ids: ids });
      await onSaved();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save preferred recipes.');
      // The copy on screen is behind whatever refused the save. Reload it, or
      // every later reorder is refused for the same reason.
      await onSaved().catch(() => undefined);
    }
    this.renderApp();
  }

  private preferences(preferred: string[], save: (ids: string[]) => void): HTMLElement {
    const named = (id: string) => this.presets.find((item) => item.id === id)?.name ?? id;
    const remaining = this.presets.filter((item) => !preferred.includes(item.id));
    return el('div', { class: 'preset-preferences', 'data-testid': 'preset-preferences' }, [
      preferred.length
        ? el('ol', { class: 'routing-shortlist' }, preferred.map((id, index) =>
            el('li', {}, [
              el('span', { class: 'routing-shortlist-title', textContent: named(id) }),
              el('div', { class: 'chips' }, [
                index
                  ? el('button', {
                      class: 'pill-btn',
                      textContent: 'Move up',
                      onclick: () => {
                        const next = [...preferred];
                        [next[index - 1], next[index]] = [next[index]!, next[index - 1]!];
                        save(next);
                      },
                    })
                  : null,
                el('button', {
                  class: 'pill-btn danger',
                  textContent: 'Remove',
                  'data-testid': `preference-remove-${id}`,
                  onclick: () => save(preferred.filter((item) => item !== id)),
                }),
              ]),
            ]),
          ))
        : el('p', { class: 'meta', textContent: 'No preferred recipe yet, so routing decides on its own.' }),
      remaining.length
        ? el('select', {
            class: 'search-input',
            'data-testid': 'preference-add',
            onchange: (event: Event) => {
              const value = (event.currentTarget as HTMLSelectElement).value;
              if (value) save([...preferred, value]);
            },
          }, [
            el('option', { value: '', textContent: 'Add a preferred recipe…' }),
            ...remaining.map((item) => el('option', { value: item.id, textContent: item.name })),
          ])
        : null,
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
