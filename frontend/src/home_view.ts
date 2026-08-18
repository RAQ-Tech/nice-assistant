import type { ApiClient } from './api';
import { el } from './dom';
import { continueCard, type HomeFact, personaStrip, pictureGrid, statusCard } from './home_cards';
import { HomeControls } from './home_controls';
import type {
  AppState,
  Chat,
  DataLocality,
  Id,
  LibraryEntry,
  MediaJournalSummary,
  PregenerationReadiness,
  SceneBacklogEntry,
} from './types';

/**
 * The homepage.
 *
 * Loading the browser used to drop straight into whichever chat was open last,
 * which meant there was nowhere to see what the assistant is currently set up
 * to do. `#/` has parsed as a route since the router was written; it was simply
 * never reachable, because the route handler opened the first chat and rewrote
 * the URL before anything could render.
 *
 * Everything here is read from an API that already exists, and nothing is
 * modelled, estimated, or filled in with a plausible default. A value the
 * platform does not have is missing and says why, because a dashboard that
 * invents a reassuring number is worse than one that admits a gap.
 *
 * It loads once when the route is entered. There is no polling: the browser
 * already re-renders on the events it receives, and a front page that hammers
 * the server while somebody reads it would be spending the same GPU this page
 * exists to keep an eye on.
 */

const RECENT_CHATS = 6;
const RECENT_PICTURES = 4;

export interface HomeActions {
  startChat: () => void;
  startChatWith: (personaId: Id | null) => void;
  openChat: (chatId: Id) => void;
  openSettings: (section?: string) => void;
  openPicture: (url: string, gallery: string[]) => void;
}

function chatLabel(chat: Chat): string {
  return (chat.title ?? '').trim() || 'Untitled conversation';
}

function outcomeLabel(journal: MediaJournalSummary): string {
  const when = new Date(journal.started_at * 1000).toLocaleString();
  if (journal.status === 'completed') {
    const seconds = journal.duration_ms ? ` in ${(journal.duration_ms / 1000).toFixed(1)}s` : '';
    return `Last picture: finished${seconds}, ${when}`;
  }
  if (journal.status === 'running') return `Last picture: still running, started ${when}`;
  return `Last picture: ${journal.status}, ${when}`;
}

export class HomeView {
  private journals: MediaJournalSummary[] = [];
  private pictures: LibraryEntry[] = [];
  private production: PregenerationReadiness | null = null;
  private produced: SceneBacklogEntry | null = null;
  private readonly controls: HomeControls;
  private locality: DataLocality | null = null;
  private loaded = false;
  private failed = '';

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly actions: HomeActions,
    private readonly renderApp: () => void,
  ) {
    this.controls = new HomeControls(appState, client, renderApp);
  }

  /** Load once per visit. Called by the router, not by a timer. */
  async refresh(): Promise<void> {
    this.loaded = false;
    this.failed = '';
    try {
      const [journals, pictures, production, made, locality] = await Promise.all([
        this.client.mediaJournals(5).catch(() => ({ items: [] })),
        this.client.libraryEntries().catch(() => ({ items: [] })),
        this.client.productionReadiness().catch(() => null),
        this.client.sceneBacklog('done').catch(() => ({ items: [] })),
        this.client.dataLocality().catch(() => null),
      ]);
      this.locality = locality;
      this.journals = journals.items;
      this.pictures = pictures.items.slice(0, RECENT_PICTURES);
      this.production = production;
      // What production last actually did, from the backlog rather than the
      // journal: a background picture's journal is indistinguishable from a
      // conversational one in the summary list.
      this.produced = [...made.items].sort((left, right) => right.updated_at - left.updated_at)[0] ?? null;
    } catch {
      this.failed = 'Some of this could not be loaded.';
    } finally {
      this.loaded = true;
      this.renderApp();
    }
  }

  node(): HTMLElement {
    const recent = this.appState.chats.filter((chat) => !chat.hidden_in_ui);
    const latest = recent[0] ?? null;
    return el('main', { class: 'home', 'data-testid': 'home' }, [
      this.header(),
      continueCard(latest, this.appState.personas.find((item) => item.id === latest?.persona_id), this.actions),
      pictureGrid(this.pictures, this.loaded, this.actions),
      el('div', { class: 'home-columns' }, [
        personaStrip(this.appState.personas, this.actions),
        this.recentCard(),
        statusCard(this.facts(), this.actions),
        this.localityCard(),
        this.controls.node(this.production, this.produced),
      ]),
      this.failed ? el('p', { class: 'meta', textContent: this.failed }) : null,
    ]);
  }

  /** Everything the page reports, each pointing at the setting behind it. */
  private facts(): HomeFact[] {
    return [
      { label: 'New chat uses', value: this.bindingLabel(), section: 'Personas' },
      { label: 'Chat model', value: this.chatModelLabel(), section: 'Models' },
      { label: 'Images', value: this.imagesLabel(), section: 'Image Generation' },
      { label: 'Background pictures', value: this.productionLabel(), section: 'Persona Pictures' },
      { label: 'Last generation', value: this.lastGenerationLabel() },
    ];
  }

  private header(): HTMLElement {
    return el('header', { class: 'home-header' }, [
      el('h1', { class: 'home-title', textContent: 'Nice Assistant' }),
    ]);
  }

  private localityCard(): HTMLElement | null {
    const locality = this.locality;
    if (!locality) return null;
    return el('section', { class: 'home-card', 'data-testid': 'home-locality' }, [
      el('h2', { class: 'home-card-title', textContent: 'Where this goes' }),
      el('p', {
        class: 'meta',
        'data-testid': 'home-locality-summary',
        textContent: locality.everything_local
          ? 'Everything switched on runs on this machine.'
          : 'Some of this leaves this machine, or nobody has said whether it does.',
      }),
      el('ul', { class: 'home-facts' }, locality.parts.map((part) => el('li', {
        class: 'home-fact',
        title: part.detail,
        'data-testid': `home-locality-${part.locality}`,
      }, [
        el('span', { class: 'home-fact-label', textContent: part.label }),
        el('span', {
          class: `home-fact-value locality-${part.locality}`,
          textContent: part.locality === 'off'
            ? 'Off'
            : part.locality === 'cloud'
              ? `${part.provider} — leaves this machine`
              : part.locality === 'unknown'
                // Saying "on this machine" here would be a privacy claim
                // nobody has checked, which is worse than admitting ignorance.
                ? `${part.provider} — nobody has said where this runs`
                : `${part.provider} — on this machine`,
        }),
      ]))),
    ]);
  }

  private fact(label: string, value: string): HTMLElement {
    return el('li', { class: 'home-fact' }, [
      el('span', { class: 'home-fact-label', textContent: label }),
      el('span', { class: 'home-fact-value', textContent: value }),
    ]);
  }

  private bindingLabel(): string {
    const personaId = this.appState.selectedPersonaId ?? this.appState.personas[0]?.id ?? null;
    const persona = this.appState.personas.find((item) => item.id === personaId);
    if (!persona) return 'No persona yet — create one in Settings';
    const workspaceId = persona.workspace_id ?? persona.workspace_ids[0];
    const workspace = this.appState.workspaces.find((item) => item.id === workspaceId);
    return workspace ? `${persona.name} in ${workspace.name}` : persona.name;
  }

  private chatModelLabel(): string {
    const model = this.appState.settings?.global_default_model || this.appState.models[0];
    if (!model) return 'No model installed — check the provider in Settings';
    return model;
  }

  private imagesLabel(): string {
    const readiness = this.appState.mediaReadiness;
    // Absent rather than guessed: this is loaded at sign-in and a failure there
    // leaves it null, which is a different thing from "not ready".
    if (!readiness) return 'Not known — the readiness check did not answer';
    return readiness.basic_generation.ready ? 'Ready' : readiness.basic_generation.message;
  }

  private productionLabel(): string {
    if (!this.loaded) return 'Checking…';
    const production = this.production;
    if (!production) return 'Not known — the readiness check did not answer';
    if (production.deployment_forbids) return 'Turned off for this deployment';
    if (!production.enabled) return 'Off';
    const where = production.inside_window ? 'inside the window now' : 'outside the window now';
    return `On, ${production.window}, ${where} — ${production.reason}`;
  }

  private lastGenerationLabel(): string {
    if (!this.loaded) return 'Checking…';
    const newest = this.journals[0];
    if (!newest) return 'Nothing generated yet';
    return outcomeLabel(newest);
  }

  private recentCard(): HTMLElement | null {
    // The hero is already the most recent one. Repeating it directly underneath
    // was the page telling somebody the same thing twice.
    const recent = this.appState.chats.filter((chat) => !chat.hidden_in_ui).slice(1, RECENT_CHATS + 1);
    if (!recent.length) return null;
    return el('section', { class: 'home-card' }, [
      el('h2', { class: 'home-card-title', textContent: 'Recent conversations' }),
      recent.length
        ? el('ul', { class: 'home-list', 'data-testid': 'home-recent' }, recent.map((chat) =>
            el('li', {}, [
              el('button', {
                class: 'home-list-row',
                textContent: chatLabel(chat),
                onclick: () => this.actions.openChat(chat.id),
              }),
            ])))
        : null,
    ]);
  }
}
