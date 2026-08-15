import { api, type ApiClient, type PersonaLoreInput } from './api';
import { el, errorMessage } from './dom';
import { inputField, textareaField, toggleField } from './settings_controls';
import { advancedSettings, settingsCard } from './settings_ui';
import { state } from './state';
import type { AppState, Id, Persona, PersonaLoreEntry, PersonaLorePreview } from './types';

/**
 * Lorebook editor. Entries are collapsed by name, matching the Media Catalog and Task Model
 * convention, and the preview box answers the only question that matters while tuning
 * keywords: does this message actually fire the entry?
 */
export class PersonaLoreView {
  private readonly entries = new Map<Id, PersonaLoreEntry[]>();
  private readonly previews = new Map<Id, PersonaLorePreview>();
  private readonly previewText = new Map<Id, string>();
  private readonly loaded = new Set<Id>();

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {}

  node(persona: Persona): HTMLElement {
    const items = this.entries.get(persona.id) ?? [];
    return advancedSettings(
      'Lorebook',
      'Background detail added to a turn only when the conversation mentions one of its keywords. '
        + 'Entries are matched by the platform, never by the model, and injected text is not itself scanned.',
      [
        el('div', { class: 'settings-primary-actions' }, [
          el('button', {
            class: 'send-btn',
            textContent: '+ New entry',
            'data-testid': `lore-add-${persona.id}`,
            onclick: () => void this.create(persona),
          }),
          el('span', {
            class: 'meta',
            textContent: `${items.length} ${items.length === 1 ? 'entry' : 'entries'}`,
          }),
        ]),
        ...items.map((entry) => this.entryEditor(persona, entry)),
        this.previewBox(persona),
      ],
      {
        testId: `lore-${persona.id}`,
        onToggle: (open: boolean) => {
          if (open) void this.load(persona.id);
        },
      },
    );
  }

  private entryEditor(persona: Persona, entry: PersonaLoreEntry): HTMLElement {
    const fired = this.previews.get(persona.id)?.items.find((item) => item.id === entry.id);
    return advancedSettings(
      `${entry.title}${entry.enabled ? '' : ' (off)'}`,
      entry.always_on
        ? 'Always included, regardless of keywords.'
        : `Fires on: ${entry.keys.join(', ') || 'nothing yet'}`,
      [
        inputField('Title', entry.title, (value) => { entry.title = value; }, 'text', false,
          'Your label for this entry. It is never sent to the model.'),
        textareaField('Content', entry.content, (value) => { entry.content = value; }, false,
          'The text injected when this entry fires.'),
        inputField('Keywords', entry.keys.join(', '), (value) => { entry.keys = splitKeys(value); }, 'text', false,
          'Comma separated. Matched as whole words, never as patterns.'),
        inputField(
          'Also requires',
          entry.secondary_keys.join(', '),
          (value) => { entry.secondary_keys = splitKeys(value); },
          'text',
          false,
          'Optional. When set, one of these must appear as well before the entry fires.',
        ),
        inputField('Priority', String(entry.priority), (value) => { entry.priority = clampPriority(value); },
          'number', false, 'Higher entries win when the allowance runs out. 0 to 100.'),
        toggleField('Always include', entry.always_on, (value) => { entry.always_on = value; },
          'Include this entry on every turn instead of matching keywords.'),
        toggleField('Match case', entry.case_sensitive, (value) => { entry.case_sensitive = value; },
          'Off means Bakery and bakery both match.'),
        toggleField('Match plurals', entry.match_word_forms, (value) => { entry.match_word_forms = value; },
          'On means a keyword of sister also fires on sisters, and bakery on bakeries.'),
        toggleField('Enabled', entry.enabled, (value) => { entry.enabled = value; },
          'Turn off to keep the entry without sending it.'),
        el('div', {
          class: 'meta',
          'data-testid': `lore-entry-meta-${entry.id}`,
          textContent: fired
            ? `${entry.token_estimate} tokens · fires on the preview text${fired.included ? '' : ', but does not fit'}`
            : `${entry.token_estimate} tokens of the ${entry.budget_tokens}-token lorebook allowance`,
        }),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'pill-btn',
            textContent: 'Save entry',
            'data-testid': `lore-save-${entry.id}`,
            onclick: () => void this.save(persona, entry),
          }),
          el('button', {
            class: 'icon-btn danger',
            textContent: 'Delete',
            'data-testid': `lore-delete-${entry.id}`,
            onclick: () => void this.remove(persona, entry),
          }),
        ]),
      ],
      { testId: `lore-entry-${entry.id}` },
    );
  }

  private previewBox(persona: Persona): HTMLElement {
    const preview = this.previews.get(persona.id);
    return settingsCard([
      textareaField(
        'Preview',
        this.previewText.get(persona.id) ?? '',
        (value) => { this.previewText.set(persona.id, value); },
        false,
        'Paste a message to see which entries it fires, before a real conversation depends on it.',
      ),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: 'Check what fires',
          'data-testid': `lore-preview-${persona.id}`,
          onclick: () => void this.preview(persona),
        }),
      ]),
      el('div', {
        class: 'meta',
        'data-testid': `lore-preview-result-${persona.id}`,
        textContent: previewSummary(preview),
      }),
      ...(preview?.items ?? []).map((item) =>
        el('div', {
          class: 'meta',
          textContent: `${item.included ? '✓' : '✕'} ${item.title}`
            + `${item.fired_keys.length ? ` · on "${item.fired_keys.join('", "')}"` : ' · always included'}`
            + ` · ${item.token_estimate} tokens${item.included ? '' : ' · left out, over the allowance'}`,
        }),
      ),
    ]);
  }

  private async load(personaId: Id): Promise<void> {
    if (this.loaded.has(personaId)) return;
    this.loaded.add(personaId);
    try {
      this.entries.set(personaId, (await this.client.personaLore(personaId)).items);
    } catch (error) {
      this.loaded.delete(personaId);
      this.appState.settingsError = errorMessage(error, 'The lorebook could not be loaded.');
    }
    this.renderApp();
  }

  private async create(persona: Persona): Promise<void> {
    await this.load(persona.id);
    await this.write(persona, () =>
      this.client.createPersonaLore(persona.id, {
        title: 'New entry',
        content: 'Background detail this persona knows.',
        keys: [],
        secondary_keys: [],
        always_on: true,
        case_sensitive: false,
        match_word_forms: true,
        priority: 50,
        enabled: true,
      }),
    );
  }

  private async save(persona: Persona, entry: PersonaLoreEntry): Promise<void> {
    await this.write(persona, () => this.client.updatePersonaLore(persona.id, entry.id, loreInput(entry)));
  }

  private async write(persona: Persona, action: () => Promise<PersonaLoreEntry>): Promise<void> {
    try {
      const saved = await action();
      const items = this.entries.get(persona.id) ?? [];
      const index = items.findIndex((item) => item.id === saved.id);
      if (index === -1) items.push(saved);
      else items[index] = saved;
      this.entries.set(persona.id, items);
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The lore entry could not be saved.');
    }
    this.renderApp();
  }

  private async remove(persona: Persona, entry: PersonaLoreEntry): Promise<void> {
    try {
      await this.client.deletePersonaLore(persona.id, entry.id);
      this.entries.set(persona.id, (this.entries.get(persona.id) ?? []).filter((item) => item.id !== entry.id));
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The lore entry could not be deleted.');
    }
    this.renderApp();
  }

  private async preview(persona: Persona): Promise<void> {
    const text = (this.previewText.get(persona.id) ?? '').trim();
    if (!text) return;
    try {
      this.previews.set(persona.id, await this.client.previewPersonaLore(persona.id, text));
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The preview could not be run.');
    }
    this.renderApp();
  }
}

function splitKeys(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function clampPriority(value: string): number {
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) return 50;
  return Math.min(100, Math.max(0, parsed));
}

function loreInput(entry: PersonaLoreEntry): PersonaLoreInput {
  return {
    title: entry.title,
    content: entry.content,
    keys: entry.keys,
    secondary_keys: entry.secondary_keys,
    always_on: entry.always_on,
    case_sensitive: entry.case_sensitive,
    match_word_forms: entry.match_word_forms,
    priority: entry.priority,
    enabled: entry.enabled,
  };
}

function previewSummary(preview: PersonaLorePreview | undefined): string {
  if (!preview) return 'Paste a message above to see which entries it fires.';
  if (!preview.items.length) return 'Nothing fires on that message.';
  const included = preview.items.filter((item) => item.included).length;
  const noun = preview.items.length === 1 ? 'entry fires' : 'entries fire';
  return `${preview.items.length} ${noun}; ${included} fit, using ${preview.used_tokens} of `
    + `${preview.budget_tokens} tokens.`;
}
