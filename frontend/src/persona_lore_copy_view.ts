import { api, type ApiClient } from './api';
import { el, errorMessage } from './dom';
import { advancedSettings, settingsCard } from './settings_ui';
import { state } from './state';
import type { AppState, Id, Persona, PersonaLoreCopyGroup, PersonaLoreEntry } from './types';

/**
 * Taking a lore entry from a persona alongside this one.
 *
 * Two personas in the same setting want the same facts about it, and retyping
 * them is how the second one ends up subtly different from the first.
 *
 * Separate from the lorebook editor because it is a separate question. That one
 * asks what this persona knows; this one asks what somebody else already wrote
 * down.
 */
export class PersonaLoreCopyView {
  private readonly groups = new Map<Id, PersonaLoreCopyGroup[]>();

  constructor(
    private readonly renderApp: () => void,
    private readonly onCopied: (persona: Persona, entry: PersonaLoreEntry) => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {}

  node(persona: Persona): HTMLElement {
    const groups = this.groups.get(persona.id) ?? [];
    return advancedSettings(
      'Take from another persona',
      groups.length
        ? 'Copy an entry a persona in this workspace already has.'
        : 'Nothing to take yet. Open to look.',
      groups.length ? [this.warning(persona), ...groups.map((group) => this.group(persona, group))] : [],
      {
        testId: `lore-copy-${persona.id}`,
        onToggle: (open: boolean) => {
          if (open) void this.load(persona.id);
        },
      },
    );
  }

  /**
   * Said here rather than anywhere else, because this is the moment somebody is
   * thinking about the relationship between the two entries.
   *
   * Without it they will edit one and expect both to change, and find out they
   * did not when a persona says something stale in front of them.
   */
  private warning(persona: Persona): HTMLElement {
    return el('div', {
      class: 'meta',
      'data-testid': `lore-copy-warning-${persona.id}`,
      textContent:
        'A copy is its own entry from then on. Editing the original later does not change it, '
        + 'and editing this one does not change the original.',
    });
  }

  private group(persona: Persona, group: PersonaLoreCopyGroup): HTMLElement {
    return settingsCard([
      el('div', { class: 'settings-heading', textContent: group.persona_name }),
      el(
        'div',
        { class: 'chips' },
        group.entries.map((candidate) =>
          el('button', {
            class: 'pill-btn',
            textContent: `Copy "${candidate.title}"`,
            'data-testid': `lore-copy-${candidate.id}`,
            onclick: () => void this.copy(persona, candidate.id),
          }),
        ),
      ),
    ]);
  }

  async load(personaId: Id): Promise<void> {
    try {
      this.groups.set(personaId, (await this.client.copyablePersonaLore(personaId)).groups);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Nearby personas could not be listed.');
    }
    this.renderApp();
  }

  private async copy(persona: Persona, sourceEntryId: Id): Promise<void> {
    try {
      this.onCopied(persona, await this.client.copyPersonaLore(persona.id, sourceEntryId));
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The entry could not be copied.');
    }
    // What was just taken is no longer on offer, and this re-renders.
    await this.load(persona.id);
  }
}
