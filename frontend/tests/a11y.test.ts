import { afterEach, describe, expect, it, vi } from 'vitest';

import { announceChanges, focusOpenDialog } from '../src/a11y';
import type { ApiClient } from '../src/api';
import type { ChatController } from '../src/chat';
import { ChatDrawer } from '../src/chat_drawer';
import { captureFocus, el, restoreFocus } from '../src/dom';
import type { Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { Message } from '../src/types';

const persona = { id: 'p', name: 'Nova' } as never;

function said(): string[] {
  vi.runAllTimers();
  return [...document.querySelectorAll('[aria-live]')].map((node) => node.textContent ?? '').filter(Boolean);
}

function conversation() {
  const appState = createState();
  appState.session = { user_id: 'u' } as never;
  appState.personas = [persona];
  appState.currentChat = { id: 'c', persona_id: 'p' } as never;
  appState.messages = [{ id: 'old', role: 'assistant', text: 'An old reply.', created_at: 1 } as Message];
  return appState;
}

afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe('what a screen reader is told', () => {
  it('reads a reply once it has finished, and not the history', () => {
    vi.useFakeTimers();
    const appState = conversation();
    announceChanges(appState);
    expect(said()).toEqual([]);

    appState.pendingRequest = { jobId: 'j' } as never;
    announceChanges(appState);
    appState.messages = [...appState.messages, { id: 'new', role: 'assistant', text: '*smiles* Half a', created_at: 2 } as Message];
    announceChanges(appState);
    // Still streaming: nothing yet.
    expect(said()).toEqual([]);

    appState.messages[1] = { ...appState.messages[1]!, text: '*smiles* Half a thought, finished.' };
    appState.pendingRequest = null;
    announceChanges(appState);
    expect(said()).toEqual(['Nova: Half a thought, finished.']);
    announceChanges(appState);
    expect(said()).toEqual(['Nova: Half a thought, finished.']);
  });

  it('reads an error the moment it appears, once', () => {
    vi.useFakeTimers();
    const appState = conversation();
    appState.uiError = 'The model is not answering.';
    announceChanges(appState);
    announceChanges(appState);
    const alert = document.querySelector('[role="alert"]');
    vi.runAllTimers();
    expect(alert?.textContent).toBe('The model is not answering.');
  });
});

describe('where focus goes', () => {
  it('comes back to the same control after the page is redrawn', () => {
    const page = () => el('div', {}, [
      el('button', { textContent: 'New chat' }),
      el('button', { 'aria-label': 'Answer again' }),
      el('textarea', { 'data-testid': 'chat-input' }),
    ]);
    const root = el('div', {}, [page()]);
    document.body.append(root);
    root.querySelector<HTMLButtonElement>('[aria-label="Answer again"]')!.focus();

    const snapshot = captureFocus(root);
    root.replaceChildren(page());
    restoreFocus(root, snapshot, false);

    expect(document.activeElement?.getAttribute('aria-label')).toBe('Answer again');
  });

  it('moves into a dialog that has just opened', () => {
    const root = el('div', {}, [
      el('button', { textContent: 'Behind' }),
      el('div', { role: 'dialog' }, [el('p', { textContent: 'Rename?' }), el('input', { 'data-testid': 'name' })]),
    ]);
    document.body.append(root);
    focusOpenDialog(root);
    expect(document.activeElement?.getAttribute('data-testid')).toBe('name');
  });
});

describe('the chat list from a keyboard', () => {
  it('is made of buttons that open the chat', () => {
    const appState = createState();
    appState.chats = [
      { id: 'one', workspace_id: null, persona_id: null, model_override: null, memory_mode: 'saved', title: 'One', hidden_in_ui: false, created_at: 1, updated_at: 1 },
    ];
    appState.currentChat = appState.chats[0]!;
    const callbacks = { render: vi.fn(), openChat: vi.fn(), openNewChat: vi.fn(), goHome: vi.fn() };
    const drawer = new ChatDrawer(appState, {} as ApiClient, {} as ChatController, {} as Dialogs, callbacks);

    const open = drawer.node().querySelector<HTMLButtonElement>('button.chat-row-open');
    expect(open?.textContent).toContain('One');
    expect(open?.getAttribute('aria-current')).toBe('page');
    open!.click();
    expect(callbacks.openChat).toHaveBeenCalledWith('one');
  });
});
