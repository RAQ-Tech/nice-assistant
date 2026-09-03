import { describe, expect, it, vi } from 'vitest';

import { coverImage, newestImageSource } from '../src/chat_rendering';
import { clickDismissesDrawer } from '../src/chat_drawer';
import { ChatDrawer } from '../src/chat_drawer';
import type { ApiClient } from '../src/api';
import type { ChatController } from '../src/chat';
import { createState } from '../src/state';
import type { Chat } from '../src/types';

function chat(id: string, title: string): Chat {
  return { id, title, workspace_id: 'w', persona_id: 'p', created_at: 1, updated_at: 1 } as unknown as Chat;
}

function drawerWith(current: string | null, chats: Chat[]) {
  const appState = createState();
  appState.chats = chats;
  appState.currentChat = current ? chat(current, current) : null;
  const calls = { openChat: [] as string[], goHome: 0 };
  const controller = {
    hide: vi.fn(async (item: Chat) => {
      appState.chats = appState.chats.filter((row) => row.id !== item.id);
      if (appState.currentChat?.id === item.id) appState.currentChat = null;
    }),
  } as unknown as ChatController;
  const drawer = new ChatDrawer(
    appState,
    {} as ApiClient,
    controller,
    { confirm: async () => true } as never,
    {
      render: () => undefined,
      openChat: (id: string) => calls.openChat.push(id),
      openNewChat: () => undefined,
      goHome: () => { calls.goHome += 1; },
    },
  );
  return { appState, drawer, calls, controller };
}

async function hideVia(drawer: ChatDrawer, target: Chat): Promise<void> {
  // The row's hide control is the only way in; reach it the way a person does.
  await (drawer as unknown as { hide: (item: Chat) => Promise<void> }).hide(target);
}

describe('hiding a chat', () => {
  it('leaves you where you are when it was not the one you were in', async () => {
    const { drawer, calls, controller } = drawerWith('reading', [chat('reading', 'Reading'), chat('other', 'Other')]);

    await hideVia(drawer, chat('other', 'Other'));

    // Going home used to happen every time, and the homepage then started a
    // new chat - so tidying up made conversations faster than it removed them.
    expect(controller.hide).toHaveBeenCalled();
    expect(calls.goHome).toBe(0);
    expect(calls.openChat).toEqual([]);
  });

  it('moves to the next chat when you hid the one you were in', async () => {
    const { drawer, calls } = drawerWith('reading', [chat('reading', 'Reading'), chat('other', 'Other')]);

    await hideVia(drawer, chat('reading', 'Reading'));

    expect(calls.openChat).toEqual(['other']);
    expect(calls.goHome).toBe(0);
  });

  it('goes home only when nothing is left', async () => {
    const { drawer, calls } = drawerWith('only', [chat('only', 'Only')]);

    await hideVia(drawer, chat('only', 'Only'));

    expect(calls.goHome).toBe(1);
    expect(calls.openChat).toEqual([]);
  });
});

describe('putting the chat list away', () => {
  function paneWithToggle(): { pane: HTMLElement; toggle: HTMLElement; message: HTMLElement } {
    document.body.innerHTML =
      '<main id="pane"><button id="toggle" data-drawer-toggle="true">☰</button><p id="message">Hello</p></main>';
    return {
      pane: document.querySelector('#pane') as HTMLElement,
      toggle: document.querySelector('#toggle') as HTMLElement,
      message: document.querySelector('#message') as HTMLElement,
    };
  }

  it('a tap on the conversation dismisses it', () => {
    const { pane, message } = paneWithToggle();
    let dismisses: boolean | null = null;
    pane.addEventListener('click', (event) => { dismisses = clickDismissesDrawer(event); });

    message.dispatchEvent(new Event('click', { bubbles: true }));

    expect(dismisses).toBe(true);
  });

  it('the tap that opens it is not also the tap that closes it', () => {
    const { pane, toggle } = paneWithToggle();
    let dismisses: boolean | null = null;
    pane.addEventListener('click', (event) => { dismisses = clickDismissesDrawer(event); });

    // The toggle lives inside the pane, so its own click bubbles up to the
    // dismiss handler. Without the exemption, one tap opened the list and
    // immediately closed it - which read as the button doing nothing, and left
    // no way to reach any existing chat.
    toggle.dispatchEvent(new Event('click', { bubbles: true }));

    expect(dismisses).toBe(false);
  });
});

describe('covering the picture that was on screen when the conversation opened', () => {
  function pane(count: number): HTMLElement {
    document.body.innerHTML = `<div id="pane">${
      Array.from({ length: count }, (_, index) => `<img class="msg-inline-image" src="/media/${index}">`).join('')
    }</div>`;
    return document.querySelector('#pane') as HTMLElement;
  }

  it('names the last picture, and covers exactly that one', () => {
    const root = pane(3);
    const src = newestImageSource(root);
    expect(src).toContain('/media/2');

    coverImage(root, src, {});

    const images = [...root.querySelectorAll('img')];
    expect(images[2]?.classList.contains('image-blurred')).toBe(true);
    // The blur setting governs the rest; this is only about what is on screen
    // when somebody opens a conversation.
    expect(images[0]?.classList.contains('image-blurred')).toBe(false);
    expect(images[1]?.classList.contains('image-blurred')).toBe(false);
  });

  it('does not cover a picture that arrived after the conversation was opened', () => {
    const opened = pane(2);
    const src = newestImageSource(opened);
    // A third picture arrives while the person is watching. The decision was
    // made on opening, so only the picture that was there then is covered.
    const arrived = pane(3);
    coverImage(arrived, src, {});
    const images = [...arrived.querySelectorAll('img')];
    expect(images[1]?.classList.contains('image-blurred')).toBe(true);
    expect(images[2]?.classList.contains('image-blurred')).toBe(false);
  });

  it('leaves a picture somebody already uncovered alone', () => {
    const root = pane(2);
    const src = newestImageSource(root) as string;

    coverImage(root, src, { [src]: true });

    // Covering it again after a deliberate tap would read as a bug.
    expect(root.querySelectorAll('img')[1]?.classList.contains('image-blurred')).toBe(false);
  });

  it('does nothing in a conversation with no pictures', () => {
    const root = pane(0);
    expect(newestImageSource(root)).toBeNull();
    expect(() => coverImage(root, null, {})).not.toThrow();
  });
});
