import { el } from './dom';
import type { AppState, Chat, Id } from './types';

/**
 * The homepage.
 *
 * Loading the browser used to drop straight into whichever chat was open last,
 * which meant there was nowhere to see what the assistant is currently set up
 * to do. `#/` has parsed as a route since the router was written; it was simply
 * never reachable, because the route handler opened the first chat and rewrote
 * the URL before anything could render.
 *
 * This module is the page that route now shows. It is deliberately its own
 * module rather than another branch inside `app.ts`, which is already at its
 * size guard.
 */

const RECENT_CHATS = 6;

export interface HomeActions {
  startChat: () => void;
  openChat: (chatId: Id) => void;
  openSettings: () => void;
}

function chatLabel(chat: Chat): string {
  return (chat.title ?? '').trim() || 'Untitled conversation';
}

export class HomeView {
  constructor(
    private readonly appState: AppState,
    private readonly actions: HomeActions,
  ) {}

  node(): HTMLElement {
    return el('main', { class: 'home', 'data-testid': 'home' }, [
      this.header(),
      this.startCard(),
      this.recentCard(),
    ]);
  }

  private header(): HTMLElement {
    const personaCount = this.appState.personas.length;
    return el('header', { class: 'home-header' }, [
      el('h1', { class: 'home-title', textContent: 'Nice Assistant' }),
      el('p', {
        class: 'home-subtitle',
        textContent: personaCount
          ? `${personaCount} ${personaCount === 1 ? 'persona' : 'personas'} ready.`
          : 'No personas yet.',
      }),
    ]);
  }

  private startCard(): HTMLElement {
    return el('section', { class: 'home-card' }, [
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: 'Start a conversation',
          'data-testid': 'home-start-chat',
          onclick: () => this.actions.startChat(),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Settings',
          'data-testid': 'home-settings',
          onclick: () => this.actions.openSettings(),
        }),
      ]),
    ]);
  }

  private recentCard(): HTMLElement {
    const recent = this.appState.chats.filter((chat) => !chat.hidden_in_ui).slice(0, RECENT_CHATS);
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
        : el('p', {
            class: 'meta',
            'data-testid': 'home-recent-empty',
            textContent: 'Nothing yet. Starting a conversation is the way in.',
          }),
    ]);
  }
}
