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

  it('clears request-scoped identity setup context when switching chats', async () => {
    const appState = createState();
    appState.phase = 'idle';
    appState.currentChat = {
      id: 'chat-a', workspace_id: null, persona_id: 'persona-a', model_override: null,
      memory_mode: 'saved', title: 'Chat A', hidden_in_ui: false, created_at: 1, updated_at: 1,
    };
    appState.mediaCatalogIdentitySetupIntent = {
      capability_request_id: 'request-a', chat_id: 'chat-a', persona_id: 'persona-a',
      prompt: 'private request A', required_features: ['identity_control'],
      block_code: 'identity_workflow_unavailable',
    };
    const nextChat: Chat = {
      id: 'chat-b', workspace_id: null, persona_id: 'persona-b', model_override: null,
      memory_mode: 'saved', title: 'Chat B', hidden_in_ui: false, created_at: 2, updated_at: 2,
    };
    const client = {
      chat: vi.fn().mockResolvedValue({ chat: nextChat, messages: [] }),
      capabilityRequests: vi.fn().mockResolvedValue({ items: [] }),
    } as unknown as ApiClient;
    const controller = new ChatController(
      {} as PlaybackController,
      appState,
      new ClientStateMachine(appState),
      client,
    );

    await controller.open('chat-b');

    expect(appState.currentChat?.id).toBe('chat-b');
    expect(appState.mediaCatalogIdentitySetupIntent).toBeNull();
  });
});
