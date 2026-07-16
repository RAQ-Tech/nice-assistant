import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { ChatController } from '../src/chat';
import type { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import type { Chat, Settings } from '../src/types';

function chatWithTitle(title: string): Chat {
  return {
    id: 'chat-1',
    workspace_id: null,
    persona_id: 'persona-1',
    model_override: 'persona-model',
    memory_mode: 'saved',
    title,
    hidden_in_ui: false,
    created_at: 1,
    updated_at: 2,
  };
}

function completedTurnClient(chat: Chat) {
  return {
    createTurn: vi.fn().mockResolvedValue({
      turn: { id: 'turn-1', user_message_id: 'user-1' },
      job: { id: 'job-1', status: 'queued', progress: 'Queued' },
    }),
    streamTurn: vi.fn().mockResolvedValue(undefined),
    job: vi.fn().mockResolvedValue({ id: 'job-1', status: 'completed', progress: 'Completed' }),
    chat: vi.fn().mockResolvedValue({
      chat,
      messages: [
        { id: 'user-1', role: 'user', text: 'Hello', created_at: 1 },
        { id: 'assistant-1', role: 'assistant', text: 'Hi there', created_at: 2 },
      ],
    }),
    capabilityRequests: vi.fn().mockResolvedValue({ items: [] }),
    clientEvent: vi.fn().mockResolvedValue(undefined),
  };
}

function readyState(chat: Chat) {
  const appState = createState();
  appState.phase = 'idle';
  appState.currentChat = chat;
  appState.chats = [chat];
  appState.selectedPersonaId = chat.persona_id;
  appState.selectedModel = chat.model_override;
  appState.settings = {
    global_default_model: 'persona-model',
    default_memory_mode: 'saved',
    tts_provider: 'local',
    tts_format: 'wav',
    model_overrides: {},
  } as Settings;
  return appState;
}

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

  it('stops current speech and sends the typed interruption', async () => {
    const chat = chatWithTitle('Existing title');
    const appState = readyState(chat);
    appState.phase = 'speaking';
    const client = completedTurnClient(chat);
    const playback = {
      stop: vi.fn(() => { appState.phase = 'idle'; }),
      synthesize: vi.fn().mockResolvedValue(undefined),
    } as unknown as PlaybackController;
    const controller = new ChatController(
      playback,
      appState,
      new ClientStateMachine(appState),
      client as unknown as ApiClient,
    );

    await controller.send('Please pause and answer this instead');

    expect(playback.stop).toHaveBeenCalledWith(false);
    expect(client.createTurn).toHaveBeenCalledWith(
      chat.id,
      expect.objectContaining({ text: 'Please pause and answer this instead' }),
    );
  });

  it('renders the generated chat title before waiting for speech synthesis', async () => {
    const original = chatWithTitle('New chat');
    const named = chatWithTitle('Perfect Summer Morning');
    const appState = readyState(original);
    const client = completedTurnClient(named);
    let releaseSpeech!: () => void;
    const speechPending = new Promise<void>((resolve) => { releaseSpeech = resolve; });
    const playback = {
      stop: vi.fn(),
      synthesize: vi.fn().mockReturnValue(speechPending),
    } as unknown as PlaybackController;
    const renderedTitles: Array<string | null | undefined> = [];
    const controller = new ChatController(
      playback,
      appState,
      new ClientStateMachine(appState),
      client as unknown as ApiClient,
    );
    controller.configure({
      onChange: () => renderedTitles.push(appState.currentChat?.title),
      onNavigate: () => undefined,
    });

    const sending = controller.send('Hello');
    await vi.waitFor(() => expect(playback.synthesize).toHaveBeenCalledOnce());

    expect(renderedTitles).toContain('Perfect Summer Morning');
    expect(appState.pendingRequest).toBeNull();
    releaseSpeech();
    await sending;
  });

  it('reconciles the deterministic first-turn title while the persona reply is still running', async () => {
    const original = chatWithTitle('New chat');
    const named = chatWithTitle('Help me plan a garden');
    const appState = readyState(original);
    let finishTurn!: () => void;
    const runningTurn = new Promise<void>((resolve) => { finishTurn = resolve; });
    const client = {
      ...completedTurnClient(named),
      streamTurn: vi.fn().mockReturnValue(runningTurn),
    };
    const playback = { stop: vi.fn(), synthesize: vi.fn() } as unknown as PlaybackController;
    const controller = new ChatController(
      playback,
      appState,
      new ClientStateMachine(appState),
      client as unknown as ApiClient,
    );

    const sending = controller.send('Help me plan a garden');
    await vi.waitFor(() => expect(appState.currentChat?.title).toBe('Help me plan a garden'));
    expect(appState.phase).toBe('queued');
    finishTurn();
    await sending;
  });

  it('keeps a completed reply usable when Kokoro playback fails', async () => {
    const chat = chatWithTitle('Existing title');
    const appState = readyState(chat);
    const client = completedTurnClient(chat);
    const playback = {
      stop: vi.fn(),
      synthesize: vi.fn().mockRejectedValue(new Error('speaker unavailable')),
    } as unknown as PlaybackController;
    const controller = new ChatController(
      playback,
      appState,
      new ClientStateMachine(appState),
      client as unknown as ApiClient,
    );

    await controller.send('Hello');

    expect(appState.phase).toBe('idle');
    expect(appState.uiError).toBe('');
    expect(appState.messageAudioErrors['assistant-1']).toContain('speaker unavailable');
    expect(client.clientEvent).toHaveBeenCalledWith('tts.playback_error', expect.stringContaining('speaker unavailable'));
  });
});
