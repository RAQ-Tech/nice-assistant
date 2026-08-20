import { avatarErrorFallback, avatarSource } from './avatar';
import { el, formatDate } from './dom';
import type { Chat, Id, LibraryEntry, Persona } from './types';

/**
 * The pieces of the front page.
 *
 * It was five stacked lists of text, and almost none of it could be touched:
 * the picture thumbnails were not clickable, the personas were a count rather
 * than faces, and every fact about the running system was a sentence beside
 * the setting it described rather than a way to reach it.
 *
 * So the shape here follows what somebody actually came to do. The thing they
 * are most likely to want is the largest and nearest the top; everything else
 * is something they can act on rather than read.
 */

export interface HomeCardActions {
  openChat: (chatId: Id) => void;
  startChatWith: (personaId: Id | null) => void;
  openSettings: (section?: string) => void;
  openPicture: (url: string, gallery: string[]) => void;
}

/**
 * Where you were, in the size of the thing you came back for.
 *
 * Opening the app almost always means continuing, so continuing is the whole
 * top of the page rather than a row in a list.
 */
export function continueCard(
  recent: Chat | null,
  persona: Persona | undefined,
  actions: HomeCardActions,
): HTMLElement {
  if (!recent) {
    return el('section', { class: 'home-hero home-hero-empty', 'data-testid': 'home-hero' }, [
      el('p', { class: 'home-hero-eyebrow', textContent: 'Nothing here yet' }),
      el('h2', { class: 'home-hero-title', textContent: 'Start your first conversation' }),
      el('button', {
        class: 'send-btn home-hero-action',
        textContent: 'Start a conversation',
        'data-testid': 'home-start-chat',
        onclick: () => actions.startChatWith(null),
      }),
    ]);
  }
  return el('section', { class: 'home-hero', 'data-testid': 'home-hero' }, [
    el('img', {
      class: 'home-hero-avatar',
      src: avatarSource(persona?.name ?? 'Conversation', persona?.avatar_url),
      onerror: avatarErrorFallback(persona?.name ?? 'Conversation'),
      alt: '',
      ariaHidden: true,
    }),
    el('div', { class: 'home-hero-body' }, [
      el('p', { class: 'home-hero-eyebrow', textContent: persona?.name ?? 'Conversation' }),
      el('h2', { class: 'home-hero-title', textContent: (recent.title ?? '').trim() || 'Untitled conversation' }),
      el('p', { class: 'home-hero-meta', textContent: `Last spoken ${formatDate(recent.updated_at)}` }),
    ]),
    el('button', {
      class: 'send-btn home-hero-action',
      textContent: 'Continue',
      'data-testid': 'home-start-chat',
      onclick: () => actions.openChat(recent.id),
    }),
  ]);
}

/**
 * The personas, as faces rather than as a number.
 *
 * "3 personas ready" told somebody a count and gave them nothing to do with
 * it. Tapping a face starts a conversation with that persona, which is the
 * only reason anybody was reading the count.
 */
export function personaStrip(personas: Persona[], actions: HomeCardActions): HTMLElement | null {
  if (!personas.length) return null;
  return el('section', { class: 'home-card', 'data-testid': 'home-personas' }, [
    el('h2', { class: 'home-card-title', textContent: 'Talk to' }),
    el('div', { class: 'home-persona-strip' }, personas.map((persona) =>
      el('button', {
        class: 'home-persona',
        title: `Start a conversation with ${persona.name}`,
        'aria-label': `Start a conversation with ${persona.name}`,
        'data-testid': `home-persona-${persona.id}`,
        onclick: () => actions.startChatWith(persona.id),
      }, [
        el('img', {
          class: 'home-persona-avatar',
          src: avatarSource(persona.name, persona.avatar_url),
          onerror: avatarErrorFallback(persona.name),
          alt: '',
        }),
        el('span', { class: 'home-persona-name', textContent: persona.name }),
      ]))),
  ]);
}

/**
 * Pictures that open when you touch them.
 *
 * They were thumbnails with a tooltip and no click handler - a gallery you
 * could look at from across the room and not otherwise use.
 */
export function pictureGrid(pictures: LibraryEntry[], loaded: boolean, actions: HomeCardActions): HTMLElement {
  const gallery = pictures.map((entry) => entry.content_url);
  return el('section', { class: 'home-card home-pictures-card' }, [
    el('h2', { class: 'home-card-title', textContent: 'Recent pictures' }),
    pictures.length
      ? el('div', { class: 'home-pictures', 'data-testid': 'home-pictures' }, pictures.map((entry) =>
          el('button', {
            class: 'home-picture-button',
            title: entry.scene.subject || 'Open picture',
            'aria-label': entry.scene.subject || 'Open picture',
            'data-testid': `home-picture-${entry.id}`,
            onclick: () => actions.openPicture(entry.content_url, gallery),
          }, [
            el('img', {
              class: 'home-picture',
              src: entry.content_url,
              alt: entry.scene.subject || 'A retained picture',
              loading: 'lazy',
            }),
          ])))
      : el('p', {
          class: 'meta',
          'data-testid': 'home-pictures-empty',
          textContent: loaded
            ? 'None kept yet. Ask a persona for a picture and it will be kept for reuse.'
            : 'Checking…',
        }),
  ]);
}

export interface HomeFact {
  label: string;
  value: string;
  /** The settings section this fact is about, when there is one to open. */
  section?: string;
}

/**
 * What the system is doing, and a way to go and change it.
 *
 * Each of these describes a setting. Naming the setting and then making
 * somebody go and find it is the part that made this page feel like a report.
 */
export function statusCard(facts: HomeFact[], actions: HomeCardActions): HTMLElement {
  return el('section', { class: 'home-card', 'data-testid': 'home-now' }, [
    el('h2', { class: 'home-card-title', textContent: 'Right now' }),
    el('ul', { class: 'home-facts' }, facts.map((fact) =>
      el('li', { class: 'home-fact' }, [
        fact.section
          ? el('button', {
              class: 'home-fact-button',
              title: `Open ${fact.section} settings`,
              'data-testid': `home-fact-${fact.section}`,
              onclick: () => actions.openSettings(fact.section),
            }, [
              el('span', { class: 'home-fact-label', textContent: fact.label }),
              el('span', { class: 'home-fact-value', textContent: fact.value }),
            ])
          : el('div', { class: 'home-fact-static' }, [
              el('span', { class: 'home-fact-label', textContent: fact.label }),
              el('span', { class: 'home-fact-value', textContent: fact.value }),
            ]),
      ]))),
  ]);
}
