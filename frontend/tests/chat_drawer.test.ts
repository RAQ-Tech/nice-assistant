import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import type { ChatController } from '../src/chat';
import { ChatDrawer } from '../src/chat_drawer';
import type { Dialogs } from '../src/settings_view';
import { createState } from '../src/state';

describe('chat drawer bulk actions', () => {
  it('selects visible chats and hides them through one atomic request', async () => {
    const appState = createState();
    appState.drawerOpen = true;
    appState.chats = [
      { id: 'one', workspace_id: null, persona_id: null, model_override: null, memory_mode: 'saved', title: 'One', hidden_in_ui: false, created_at: 1, updated_at: 1 },
      { id: 'two', workspace_id: null, persona_id: null, model_override: null, memory_mode: 'saved', title: 'Two', hidden_in_ui: false, created_at: 1, updated_at: 1 },
    ];
    const client = {
      bulkChatAction: vi.fn().mockResolvedValue({ action: 'hide', requested_count: 2, affected_count: 2, ids: ['one', 'two'] }),
    } as unknown as ApiClient;
    const dialogs = {
      prompt: vi.fn(),
      confirm: vi.fn().mockResolvedValue(true),
      info: vi.fn(),
    } as unknown as Dialogs;
    const callbacks = { render: vi.fn(), openChat: vi.fn(), openNewChat: vi.fn(), goHome: vi.fn() };
    const drawer = new ChatDrawer(appState, client, {} as ChatController, dialogs, callbacks);

    (drawer.node().querySelector('[data-testid="manage-chats"]') as HTMLButtonElement).click();
    [...drawer.node().querySelectorAll('button')].find((button) => button.textContent === 'Select visible')!.click();
    [...drawer.node().querySelectorAll('button')].find((button) => button.textContent === 'Hide')!.click();

    await vi.waitFor(() => expect(client.bulkChatAction).toHaveBeenCalledWith('hide', ['one', 'two']));
    expect(dialogs.confirm).toHaveBeenCalledWith('Hide chats', expect.stringContaining('2 selected chats'), 'Hide');
    expect(appState.chats).toEqual([]);
  });
});
