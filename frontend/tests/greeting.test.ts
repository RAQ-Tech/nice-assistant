import { describe, expect, it, vi } from 'vitest';

import { ApiError, type ApiClient } from '../src/api';
import { ChatController } from '../src/chat';
import type { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import type { Chat, Message, Settings, TurnEvent } from '../src/types';

/** The persona speaks first in the conversation setup opens (ADR 0052). */

const chat: Chat = {
  id: 'chat-1', workspace_id: 'home', persona_id: 'persona-1', model_override: 'seeing', memory_mode: 'saved',
  title: 'New chat', hidden_in_ui: false, created_at: 1, updated_at: 2,
};
const greeting: Message = { id: 'assistant-1', role: 'assistant', text: "Hi! I'm Nova. I've been looking forward to this.", created_at: 3 };

function setup(client: Partial<Record<keyof ApiClient, unknown>>, messages: Message[] = []) {
  const appState = createState();
  appState.phase = 'idle';
  appState.settings = { global_default_model: 'seeing', default_memory_mode: 'saved', tts_provider: 'local', model_overrides: {} } as unknown as Settings;
  appState.messages = [...messages];
  appState.currentChat = messages.length ? chat : null;
  const playback = { beginPieces: () => false, stop: vi.fn(), synthesize: vi.fn(async () => undefined) };
  const controller = new ChatController(playback as unknown as PlaybackController, appState, new ClientStateMachine(appState), client as unknown as ApiClient);
  return { appState, controller, playback };
}

function greetingClient(extra: Partial<Record<keyof ApiClient, unknown>> = {}) {
  const accepted = { turn: { id: 'turn-1', user_message_id: null }, job: { id: 'job-1', status: 'queued', progress: 'Queued' } };
  let said: Message[] = [];
  const seen: string[] = [];
  return {
    seen,
    greet: vi.fn(async () => {
      said = [greeting];
      return accepted;
    }),
    answerAgain: vi.fn(async () => {
      said = [greeting];
      return accepted;
    }),
    createTurn: vi.fn(),
    streamTurn: vi.fn(async (_turn: string, onEvent: (event: TurnEvent) => void) => {
      onEvent({ id: 1, event: 'assistant.delta', data: { text: greeting.text } });
      onEvent({ id: 2, event: 'turn.completed', data: {} });
    }),
    job: vi.fn().mockResolvedValue({ id: 'job-1', status: 'completed', progress: 'Completed', result: {} }),
    chat: vi.fn(async () => ({ chat, messages: [...said] })),
    capabilityRequests: vi.fn().mockResolvedValue({ items: [] }),
    ...extra,
  };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('the persona speaks first', () => {
  it('opens the new conversation in its own words, with nothing put in the owner\'s mouth', async () => {
    const client = greetingClient();
    const { appState, controller, playback } = setup(client);
    const shown: string[] = [];
    controller.configure({ onChange: () => shown.push(appState.messages.map((message) => `${message.role}:${message.text}`).join('|')), onNavigate: () => undefined });
    controller.greetWhenOpened(chat.id);
    await controller.open(chat.id);
    await vi.waitFor(() => expect(appState.pendingRequest).toBeNull());
    await settle();

    expect(client.greet).toHaveBeenCalledWith(chat.id, expect.objectContaining({ model: 'seeing', memory_mode: 'saved' }));
    expect(client.createTurn).not.toHaveBeenCalled();
    // Streamed into place like any reply, and never with a message from the owner in front of it.
    expect(shown.some((frame) => frame === `assistant:${greeting.text}`)).toBe(true);
    expect(shown.every((frame) => !frame.includes('user:'))).toBe(true);
    expect(appState.messages).toEqual([greeting]);
    // Spoken, when replies are spoken.
    expect(playback.synthesize).toHaveBeenCalledWith(greeting.text, greeting.id, chat.id, chat.persona_id);
    expect(appState.uiError).toBe('');
    expect(appState.phase).toBe('idle');
  });

  it('says nothing at all when the model cannot be reached, leaving the quiet invitation', async () => {
    const client = greetingClient({ greet: vi.fn().mockRejectedValue(new ApiError('Ollama is not reachable.', 503, 'unavailable')) });
    const { appState, controller } = setup(client);
    controller.greetWhenOpened(chat.id);
    await controller.open(chat.id);
    await settle();

    expect(client.greet).toHaveBeenCalled();
    expect(appState.messages).toEqual([]);
    expect(appState.uiError).toBe('');
    expect(appState.draftMessage).toBe('');
    expect(appState.phase).toBe('idle');
  });

  it('says nothing either when the reply fails part of the way', async () => {
    const client = greetingClient({
      streamTurn: vi.fn(async (_turn: string, onEvent: (event: TurnEvent) => void) => {
        onEvent({ id: 1, event: 'turn.failed', data: { error: { code: 'unavailable', message: 'Ollama is not reachable.' } } });
      }),
      chat: vi.fn(async () => ({ chat, messages: [] })),
    });
    const { appState, controller } = setup(client);
    controller.greetWhenOpened(chat.id);
    await controller.open(chat.id);
    await settle();

    expect(appState.messages).toEqual([]);
    expect(appState.uiError).toBe('');
    expect(appState.phase).toBe('idle');
  });

  it('greets only the conversation setup opened, and only once', async () => {
    const client = greetingClient();
    const { controller } = setup(client);
    await controller.open(chat.id);
    await settle();
    expect(client.greet).not.toHaveBeenCalled();
  });

  it('does not greet a conversation that already has something in it', async () => {
    const client = greetingClient({ chat: vi.fn(async () => ({ chat, messages: [greeting] })) });
    const { controller } = setup(client);
    controller.greetWhenOpened(chat.id);
    await controller.open(chat.id);
    await settle();
    expect(client.greet).not.toHaveBeenCalled();
  });

  it('greets again when its greeting is answered again', async () => {
    const client = greetingClient();
    const { appState, controller } = setup(client, [greeting]);
    appState.draftMessage = 'Half a thought';
    await controller.again(greeting.id);
    await settle();

    expect(client.answerAgain).toHaveBeenCalledWith(chat.id, greeting.id, expect.objectContaining({ text: null }));
    expect(appState.messages.filter((message) => message.role === 'user')).toEqual([]);
    expect(appState.messages).toEqual([greeting]);
    // Nothing the owner was in the middle of typing is taken away.
    expect(appState.draftMessage).toBe('Half a thought');
  });
});
