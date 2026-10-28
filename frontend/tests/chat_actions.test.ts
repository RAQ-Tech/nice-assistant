import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { ChatController } from '../src/chat';
import { ChatRenderer, timeDivider, type MessageActions } from '../src/chat_rendering';
import type { MediaController } from '../src/media';
import type { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import type { Chat, Message, Settings, TurnEvent } from '../src/types';

const chat: Chat = {
  id: 'chat-1', workspace_id: null, persona_id: 'persona-1', model_override: 'm', memory_mode: 'saved',
  title: 'Talk', hidden_in_ui: false, created_at: 1, updated_at: 2,
};

function setup(messages: Message[], client: Partial<Record<keyof ApiClient, unknown>>) {
  const appState = createState();
  appState.phase = 'idle';
  appState.currentChat = chat;
  appState.messages = [...messages];
  appState.settings = { global_default_model: 'm', default_memory_mode: 'saved', tts_provider: 'disabled', model_overrides: {} } as unknown as Settings;
  const playback = { beginPieces: () => false, stop: vi.fn(), synthesize: vi.fn() } as unknown as PlaybackController;
  const controller = new ChatController(playback, appState, new ClientStateMachine(appState), client as unknown as ApiClient);
  return { appState, controller };
}

const said: Message = { id: 'user-1', role: 'user', text: 'My sister is Anna.', created_at: 10 };
const reply: Message = { id: 'assistant-1', role: 'assistant', text: 'Lovely name.', created_at: 11 };

function turnClient(extra: Partial<Record<keyof ApiClient, unknown>> = {}) {
  const accepted = { turn: { id: 'turn-2', user_message_id: 'user-2' }, job: { id: 'job-2', status: 'queued', progress: 'Queued' } };
  return {
    answerAgain: vi.fn().mockResolvedValue(accepted),
    createTurn: vi.fn().mockResolvedValue(accepted),
    streamTurn: vi.fn(async (_turn: string, onEvent: (event: TurnEvent) => void) => {
      onEvent({ id: 1, event: 'turn.completed', data: {} });
    }),
    job: vi.fn().mockResolvedValue({ id: 'job-2', status: 'completed', progress: 'Completed', result: {} }),
    chat: vi.fn().mockResolvedValue({ chat, messages: [] }),
    capabilityRequests: vi.fn().mockResolvedValue({ items: [] }),
    ...extra,
  };
}

describe('answering again', () => {
  it('sends the latest message again in place of the old exchange', async () => {
    const client = turnClient();
    const { appState, controller } = setup([said, reply], client);
    await controller.again('assistant-1');
    expect(client.answerAgain).toHaveBeenCalledWith('chat-1', 'assistant-1', expect.objectContaining({ text: null }));
    // Not said twice: the old exchange left the screen before the new one arrived.
    expect(client.createTurn).not.toHaveBeenCalled();
    expect(appState.phase).toBe('idle');
  });

  it('sends the correction, not the old words, when a message is edited', async () => {
    const client = turnClient();
    const { appState, controller } = setup([said, reply], client);
    appState.editingMessageId = 'user-1';
    await controller.again('user-1', 'My sister is Hannah.');
    expect(client.answerAgain).toHaveBeenCalledWith('chat-1', 'user-1', expect.objectContaining({ text: 'My sister is Hannah.' }));
    expect(appState.editingMessageId).toBeNull();
  });
});

describe('a message that did not get through', () => {
  it('goes back where it was typed', async () => {
    const client = turnClient({ createTurn: vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) });
    const { appState, controller } = setup([], client);
    appState.draftMessage = 'A long thought I typed out.';
    await controller.send(appState.draftMessage);
    expect(appState.draftMessage).toBe('A long thought I typed out.');
  });
});

describe('a reply stream that drops', () => {
  it('picks up from the last thing it delivered rather than giving up', async () => {
    const calls: (number | undefined)[] = [];
    const client = turnClient({
      job: vi.fn().mockResolvedValue({ id: 'job-2', status: 'running', progress: 'Running', result: {} }),
      streamTurn: vi.fn(async (_turn: string, onEvent: (event: TurnEvent) => void, _signal: AbortSignal, last?: number) => {
        calls.push(last);
        if (calls.length === 1) {
          onEvent({ id: 3, event: 'assistant.delta', data: { text: 'Hello ' } });
          throw new TypeError('network changed');
        }
        onEvent({ id: 4, event: 'assistant.delta', data: { text: 'there.' } });
        onEvent({ id: 5, event: 'turn.completed', data: {} });
      }),
    });
    const { controller } = setup([], client);
    vi.useFakeTimers();
    try {
      const sending = controller.send('hi');
      await vi.runAllTimersAsync();
      await sending;
    } finally {
      vi.useRealTimers();
    }
    expect(calls).toEqual([undefined, 3]);
  });
});

describe('the conversation as a list', () => {
  function rendered(messages: Message[], phase = 'idle') {
    const appState = createState();
    appState.phase = phase as never;
    const renderer = new ChatRenderer({} as MediaController, { stop: vi.fn() } as unknown as PlaybackController, () => undefined, appState);
    const actions: MessageActions = { again: vi.fn(), edit: vi.fn(), remove: vi.fn() };
    renderer.setActions(actions);
    const root = document.createElement('div');
    root.append(...renderer.messageList(messages, null, () => []));
    return { root, actions };
  }

  it('offers to try again under a message that was never answered', () => {
    const { root, actions } = rendered([said]);
    const button = root.querySelector<HTMLButtonElement>('[data-testid="message-try-again"]');
    expect(button).not.toBeNull();
    button?.click();
    expect(actions.again).toHaveBeenCalledWith('user-1');
  });

  it('offers to answer again and to edit only the latest exchange', () => {
    const earlier: Message = { id: 'user-0', role: 'user', text: 'Hi.', created_at: 1 };
    const earlierReply: Message = { id: 'assistant-0', role: 'assistant', text: 'Hello.', created_at: 2 };
    const { root } = rendered([earlier, earlierReply, said, reply]);
    expect(root.querySelectorAll('[data-testid="message-again"]')).toHaveLength(1);
    expect(root.querySelectorAll('[data-testid="message-edit"]')).toHaveLength(1);
    expect(root.querySelectorAll('[data-testid="message-try-again"]')).toHaveLength(0);
    // Any stored message can be deleted.
    expect(root.querySelectorAll('[data-testid="message-delete"]')).toHaveLength(4);
  });

  it('offers nothing to change while a reply is being written', () => {
    const { root } = rendered([said, reply], 'thinking');
    expect(root.querySelector('[data-testid="message-again"], [data-testid="message-edit"], [data-testid="message-delete"]')).toBeNull();
  });

  it('says when a conversation picked up again, and not between every message', () => {
    const now = new Date(2026, 9, 28, 21, 0).getTime();
    const at = (hours: number, minutes = 0) => new Date(2026, 9, 28, hours, minutes).getTime() / 1000;
    expect(timeDivider(at(20, 0), at(20, 5), now)).toBeNull();
    expect(timeDivider(at(18, 0), at(20, 5), now)?.textContent).toMatch(/^Today /);
    expect(timeDivider(null, at(9) - 86_400, now)?.textContent).toMatch(/^Yesterday /);
  });
});
