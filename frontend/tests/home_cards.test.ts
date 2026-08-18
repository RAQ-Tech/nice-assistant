import { describe, expect, it, vi } from 'vitest';

import { continueCard, personaStrip, pictureGrid, statusCard } from '../src/home_cards';
import type { Chat, LibraryEntry, Persona } from '../src/types';

function actions() {
  return {
    openChat: vi.fn(),
    startChatWith: vi.fn(),
    openSettings: vi.fn(),
    openPicture: vi.fn(),
  };
}

function persona(id: string, name: string): Persona {
  return { id, name, avatar_url: null } as unknown as Persona;
}

function chat(): Chat {
  return { id: 'chat-1', title: 'The lighthouse', persona_id: 'p1', updated_at: 1_700_000_000 } as unknown as Chat;
}

function picture(id: string): LibraryEntry {
  return { id, content_url: `/api/v1/media/${id}`, scene: { subject: 'a lighthouse' } } as unknown as LibraryEntry;
}

describe('the front page continues rather than starting over', () => {
  it('leads with the conversation you were last in', () => {
    const acts = actions();
    const node = continueCard(chat(), persona('p1', 'Nova'), acts);

    expect(node.textContent).toContain('Nova');
    expect(node.textContent).toContain('The lighthouse');
    (node.querySelector('[data-testid="home-start-chat"]') as HTMLButtonElement).click();
    // Opening the app almost always means continuing.
    expect(acts.openChat).toHaveBeenCalledWith('chat-1');
    expect(acts.startChatWith).not.toHaveBeenCalled();
  });

  it('offers to start one only when there is nothing to continue', () => {
    const acts = actions();
    const node = continueCard(null, undefined, acts);

    (node.querySelector('[data-testid="home-start-chat"]') as HTMLButtonElement).click();

    expect(acts.startChatWith).toHaveBeenCalledWith(null);
    expect(acts.openChat).not.toHaveBeenCalled();
  });
});

describe('personas are faces you can act on', () => {
  it('starts a conversation with the one that was tapped', () => {
    const acts = actions();
    const node = personaStrip([persona('p1', 'Nova'), persona('p2', 'Mara')], acts) as HTMLElement;

    (node.querySelector('[data-testid="home-persona-p2"]') as HTMLButtonElement).click();

    // A count of personas told somebody a number and gave them nothing to do.
    expect(acts.startChatWith).toHaveBeenCalledWith('p2');
  });

  it('says nothing at all when there are no personas', () => {
    expect(personaStrip([], actions())).toBeNull();
  });
});

describe('pictures open', () => {
  it('opens the one tapped, and hands over the rest to step through', () => {
    const acts = actions();
    const node = pictureGrid([picture('a'), picture('b')], true, acts);

    (node.querySelector('[data-testid="home-picture-b"]') as HTMLButtonElement).click();

    expect(acts.openPicture).toHaveBeenCalledWith('/api/v1/media/b', ['/api/v1/media/a', '/api/v1/media/b']);
  });

  it('distinguishes still loading from nothing kept', () => {
    expect(pictureGrid([], false, actions()).textContent).toContain('Checking');
    expect(pictureGrid([], true, actions()).textContent).toContain('None kept yet');
  });
});

describe('each reported fact opens the setting behind it', () => {
  it('goes to the section that owns the value', () => {
    const acts = actions();
    const node = statusCard([{ label: 'Chat model', value: 'nemo', section: 'Models' }], acts);

    (node.querySelector('[data-testid="home-fact-Models"]') as HTMLButtonElement).click();

    // Naming a setting and then making somebody find it is what made this
    // page read like a report rather than a control surface.
    expect(acts.openSettings).toHaveBeenCalledWith('Models');
  });

  it('leaves a fact with no setting behind it as plain text', () => {
    const node = statusCard([{ label: 'Last generation', value: 'none yet' }], actions());

    expect(node.querySelector('.home-fact-button')).toBeNull();
    expect(node.textContent).toContain('none yet');
  });
});
