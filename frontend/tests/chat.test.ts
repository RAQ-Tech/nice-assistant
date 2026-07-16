import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { ChatController } from '../src/chat';
import type { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import type { Chat, Settings } from '../src/types';

describe('ChatController', () => {
  it('creates chats with the server-recognized automatic-title placeholder', async () => {
    const appState = createState();
    appState.settings = {
      global_default_model: 'persona-model',
      default_memory_mode: 'saved',
    } as Settings;
    const chat: Chat = {
      id: 'chat-1',
      workspace_id: null,
      persona_id: null,
      model_override: 'persona-model',
      memory_mode: 'saved',
      title: 'New chat',
      hidden_in_ui: false,
      created_at: 1,
      updated_at: 1,
    };
    const client = { createChat: vi.fn().mockResolvedValue(chat) } as unknown as ApiClient;
    const controller = new ChatController(
      {} as PlaybackController,
      appState,
      new ClientStateMachine(appState),
      client,
    );

    await controller.create();

    expect(client.createChat).toHaveBeenCalledWith(expect.objectContaining({ title: 'New chat' }));
  });
});
