import type { ApiClient, PersonaInput } from './api';
import { avatarErrorFallback, avatarSource } from './avatar';
import { el, errorMessage } from './dom';
import type { SettingChange } from './everyday_settings_view';
import type { PersonaCardView } from './persona_card_view';
import type { PersonaFaceView } from './persona_face_view';
import type { PersonaLoreView } from './persona_lore_view';
import { SETTINGS_DEFAULTS } from './settings';
import type { SettingsDialogs } from './settings_contracts';
import {
  actionRow,
  choiceField,
  leaveGuard,
  longField,
  pageHead,
  pageNav,
  saveButton,
  switchField,
  thingList,
} from './settings_page';
import { advancedSettings, settingsCard } from './settings_ui';
import type { AppState, Persona, Settings } from './types';

/**
 * Personas: the list, and one page per person.
 *
 * A persona used to be a collapsed editor in a stack of collapsed editors,
 * each row wearing an information icon, with the character card and the
 * lorebook folded inside. A persona is supposed to feel like somebody, and
 * somebody deserves a page: their picture and name at the top, the few things
 * that are theirs to decide underneath, and arrows to the next person.
 *
 * The page edits the persona object the rest of the app holds, and remembers
 * what it looked like on arrival, so leaving can put it back. The character
 * card and the lorebook keep their own editors and their own save actions -
 * each is a thing in its own right, with a cost meter and a preview that this
 * page has no business flattening.
 */

interface BaseFields {
  name: string;
  avatar_url: string | null;
  allow_image_sends: boolean;
  default_model: string | null;
  workspace_id: string;
  workspace_ids: string[];
  personality_details: string | null;
  system_prompt: string | null;
}

function baseFields(persona: Persona): BaseFields {
  return {
    name: persona.name,
    avatar_url: persona.avatar_url,
    allow_image_sends: persona.allow_image_sends !== false,
    default_model: persona.default_model,
    workspace_id: persona.workspace_id,
    workspace_ids: [...persona.workspace_ids],
    personality_details: persona.personality_details,
    system_prompt: persona.system_prompt,
  };
}

export function personaInput(persona: Persona): PersonaInput {
  const workspaceIds = persona.workspace_ids.length ? persona.workspace_ids : [persona.workspace_id];
  return {
    workspace_id: workspaceIds[0] ?? persona.workspace_id,
    workspace_ids: workspaceIds,
    name: persona.name,
    avatar_url: persona.avatar_url,
    allow_image_sends: persona.allow_image_sends !== false,
    system_prompt: persona.system_prompt,
    personality_details: persona.personality_details,
    traits: persona.traits,
    default_model: persona.default_model,
    voice_preferences: persona.voice_preferences ?? {},
  };
}

export class PersonaPageView {
  private openedId: string | null = null;
  private original: BaseFields | null = null;
  private busy = false;
  private moreOpen = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly dialogs: SettingsDialogs,
    private readonly navigate: (personaId: string | null) => void,
    private readonly card: PersonaCardView,
    private readonly lore: PersonaLoreView,
    private readonly face: PersonaFaceView,
    private readonly change: SettingChange,
  ) {}

  /** The Personas page: everybody, and the way a new person arrives. */
  list(settings: Settings): HTMLElement[] {
    const personas = this.appState.personas;
    return [
      thingList(personas.map((persona) => ({
        id: persona.id,
        label: persona.name,
        thumb: avatarSource(persona.name, persona.avatar_url),
        thumbFallback: avatarErrorFallback(persona.name),
        note: persona.default_model ?? undefined,
        testId: `persona-open-${persona.id}`,
        onOpen: () => this.navigate(persona.id),
      })), 'No personas yet.', 'persona-list'),
      actionRow([
        el('button', { class: 'send-btn', textContent: '+ New persona', 'data-testid': 'persona-new', onclick: () => void this.create() }),
      ]),
      advancedSettings('More options', 'What a new persona starts with.', [
        longField(
          'Instructions for a new persona',
          settings.personas_default_system_prompt,
          (value) => this.change('personas_default_system_prompt', value, false),
          { hover: 'Copied into each new persona when it is created. Existing personas keep their own.' },
        ),
      ], { testId: 'personas-advanced-settings' }),
    ];
  }

  /** One persona's page. */
  page(personaId: string): HTMLElement[] {
    const persona = this.appState.personas.find((item) => item.id === personaId);
    if (!persona) {
      return [
        pageNav({ back: 'All personas', onBack: () => this.navigate(null), testId: 'persona-page' }),
        el('p', { class: 'meta', textContent: 'That persona is no longer here.' }),
      ];
    }
    if (this.openedId !== personaId) {
      this.openedId = personaId;
      this.original = baseFields(persona);
      this.moreOpen = false;
      void this.face.load(personaId);
    }
    const personas = this.appState.personas;
    const index = personas.indexOf(persona);
    const previous = personas[index - 1];
    const next = personas[index + 1];
    return [
      pageNav({
        back: 'All personas',
        onBack: () => void this.leave(persona, () => this.navigate(null)),
        arrows: {
          previous: previous ? () => void this.leave(persona, () => this.navigate(previous.id)) : null,
          next: next ? () => void this.leave(persona, () => this.navigate(next.id)) : null,
        },
        busy: this.busy,
        testId: 'persona-page',
      }),
      settingsCard([
        pageHead({
          thumb: this.picture(persona),
          name: persona.name,
          onName: (value) => { persona.name = value; this.renderApp(); },
          nameTitle: 'Name',
          testId: 'persona-page',
        }),
        choiceField('Model', persona.default_model ?? '', ['', ...this.appState.models], (value) => {
          persona.default_model = value || null;
          this.renderApp();
        }, { display: (value) => value || 'Automatic', hover: 'Overrides the default model for this persona only.', testId: 'persona-model' }),
        switchField('Allowed to send pictures', persona.allow_image_sends !== false, (value) => {
          persona.allow_image_sends = value;
          this.renderApp();
        }, { hover: 'Pictures asked for in conversation. Direct image actions stay available either way.', testId: 'persona-pictures' }),
        this.appState.workspaces.length > 1 ? this.workspaceRow(persona) : null,
        this.face.node(persona),
        this.card.node(persona),
        this.lore.node(persona),
        advancedSettings('More options', 'Free-form instructions, and deleting this persona.', [
          longField('Personality details', persona.personality_details ?? '', (value) => {
            persona.personality_details = value;
            this.renderApp();
          }, { hover: 'Traits and background, in prose. The character card is the structured version.' }),
          longField('System prompt', persona.system_prompt ?? '', (value) => {
            persona.system_prompt = value;
            this.renderApp();
          }, { hover: 'Sent first, before everything else, on every turn.' }),
          actionRow([
            el('button', { class: 'pill-btn danger', textContent: 'Delete persona', 'data-testid': 'persona-delete', onclick: () => void this.remove(persona) }),
          ]),
        ], { testId: `persona-advanced-${persona.id}`, open: this.moreOpen, onToggle: (open) => { this.moreOpen = open; } }),
        actionRow([
          saveButton({ dirty: this.dirty(persona), busy: this.busy, onSave: () => void this.save(persona), testId: 'persona-save' }),
        ]),
      ], 'persona-page', 'persona-page'),
    ];
  }

  /** Leaving for another section: the same guard the page's own buttons use. */
  beforeLeave(go: () => void): void {
    const persona = this.openedId ? this.appState.personas.find((item) => item.id === this.openedId) : null;
    if (!persona) {
      this.openedId = null;
      go();
      return;
    }
    void this.leave(persona, go);
  }

  private workspaceRow(persona: Persona): HTMLElement {
    const ids = new Set(persona.workspace_ids.length ? persona.workspace_ids : [persona.workspace_id]);
    return el('div', { class: 'setting-row', title: 'Every workspace where this persona can be talked to.' }, [
      el('label', { textContent: 'Available in' }),
      el('div', { class: 'chips' }, this.appState.workspaces.map((workspace) =>
        el('label', { class: 'checkbox-row' }, [
          el('input', {
            type: 'checkbox',
            checked: ids.has(workspace.id),
            onchange: (event: Event) => {
              if ((event.currentTarget as HTMLInputElement).checked) ids.add(workspace.id);
              else ids.delete(workspace.id);
              persona.workspace_ids = [...ids];
              persona.workspace_id = persona.workspace_ids[0] ?? persona.workspace_id;
              this.renderApp();
            },
          }),
          workspace.name,
        ]))),
    ]);
  }

  /** The face: the picture if there is one, initials if not, and the way to change it. */
  private picture(persona: Persona): HTMLElement {
    const image = el('img', {
      class: 'page-thumb page-thumb-round',
      src: avatarSource(persona.name, persona.avatar_url),
      onerror: avatarErrorFallback(persona.name),
      alt: `${persona.name} avatar`,
    });
    return el('div', { class: 'page-thumb-slot' }, [
      persona.avatar_url
        ? el('button', {
            class: 'persona-avatar-preview-button',
            type: 'button',
            title: `View ${persona.name}'s full-size avatar`,
            'aria-label': `View ${persona.name}'s full-size avatar`,
            onclick: () => {
              this.appState.personaAvatarPreview = persona.avatar_url ?? '';
              this.renderApp();
            },
          }, [image])
        : image,
      el('button', {
        class: 'pill-btn page-thumb-action',
        textContent: persona.avatar_url ? 'Change picture' : 'Add a picture',
        title: 'Paste a link to an image. It is copied and kept, so it keeps working if the source goes away.',
        'data-testid': 'persona-picture',
        onclick: () => void this.changePicture(persona),
      }),
    ]);
  }

  private async changePicture(persona: Persona): Promise<void> {
    const value = await this.dialogs.prompt(
      'Picture',
      'Paste a link to an image. It is copied and kept, so it keeps working if the source goes away. Leave it empty for initials.',
      persona.avatar_url ?? '',
    );
    if (value === null || value === undefined) return;
    persona.avatar_url = value.trim() || null;
    this.renderApp();
  }

  private dirty(persona: Persona): boolean {
    return this.original !== null && JSON.stringify(baseFields(persona)) !== JSON.stringify(this.original);
  }

  private revert(persona: Persona): void {
    if (!this.original) return;
    Object.assign(persona, this.original, { workspace_ids: [...this.original.workspace_ids] });
  }

  private async leave(persona: Persona, go: () => void): Promise<void> {
    await leaveGuard(
      this.dialogs,
      persona.name || 'This persona',
      this.dirty(persona),
      () => this.save(persona),
      () => {
        this.revert(persona);
        this.openedId = null;
        go();
      },
    );
  }

  private async save(persona: Persona): Promise<boolean> {
    this.busy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const updated = await this.client.updatePersona(persona.id, personaInput(persona));
      // The reply is the truth; keep the object the page is editing, so a
      // reference held by the card or lorebook editor stays current too.
      Object.assign(persona, updated);
      this.original = baseFields(persona);
      return true;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save this persona.');
      return false;
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async create(): Promise<void> {
    const settings = this.appState.settings;
    const workspace = this.appState.workspaces.find((item) => item.id === settings?.workspaces_default_workspace_id)
      ?? this.appState.workspaces[0];
    if (!workspace) {
      this.appState.settingsError = 'Create a workspace before adding a persona.';
      this.renderApp();
      return;
    }
    const name = await this.dialogs.prompt('New persona', 'Choose a name.');
    if (!name?.trim()) return;
    try {
      const persona = await this.client.createPersona({
        workspace_id: workspace.id,
        workspace_ids: [workspace.id],
        name: name.trim(),
        system_prompt: settings?.personas_default_system_prompt ?? SETTINGS_DEFAULTS.personas_default_system_prompt,
        default_model: this.appState.models[0] ?? null,
      });
      this.appState.personas.push(persona);
      this.navigate(persona.id);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to create the persona.');
      this.renderApp();
    }
  }

  private async remove(persona: Persona): Promise<void> {
    if (!(await this.dialogs.confirm('Delete persona', `Delete ${persona.name}?`, 'Delete'))) return;
    try {
      await this.client.deletePersona(persona.id);
      this.appState.personas = this.appState.personas.filter((item) => item.id !== persona.id);
      this.openedId = null;
      this.navigate(null);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to delete the persona.');
      this.renderApp();
    }
  }
}
