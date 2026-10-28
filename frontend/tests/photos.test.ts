import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { ChatController } from '../src/chat';
import { ChatRenderer } from '../src/chat_rendering';
import { composerView } from '../src/composer_view';
import type { ConversationLoop } from '../src/conversation_loop';
import type { MediaController } from '../src/media';
import { canSendPhoto } from '../src/photo_upload';
import type { PlaybackController } from '../src/playback';
import type { RecordingController } from '../src/recording';
import { ClientStateMachine, createState, state } from '../src/state';
import type { Chat, Settings, TurnEvent } from '../src/types';

/** A photo sent into the conversation, for a persona whose model can see (ADR 0046). */

const chat: Chat = {
  id: 'chat-1', workspace_id: null, persona_id: 'p', model_override: 'gemma3', memory_mode: 'saved',
  title: 'Talk', hidden_in_ui: false, created_at: 1, updated_at: 2,
};
const PHOTO = 'data:image/jpeg;base64,AAAA';

function controller(createTurn: ReturnType<typeof vi.fn>) {
  const appState = createState();
  appState.phase = 'idle';
  appState.currentChat = chat;
  appState.settings = { global_default_model: 'gemma3', default_memory_mode: 'saved', tts_provider: 'disabled', model_overrides: {} } as unknown as Settings;
  const client = {
    createTurn,
    streamTurn: vi.fn(async (_turn: string, onEvent: (event: TurnEvent) => void) => onEvent({ id: 1, event: 'turn.completed', data: {} })),
    job: vi.fn().mockResolvedValue({ id: 'j', status: 'completed', progress: 'Completed', result: {} }),
    chat: vi.fn().mockResolvedValue({ chat, messages: [] }),
    capabilityRequests: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as ApiClient;
  const playback = { beginPieces: () => false, stop: vi.fn() } as unknown as PlaybackController;
  return { appState, chats: new ChatController(playback, appState, new ClientStateMachine(appState), client) };
}

describe('sending a photo', () => {
  it('goes with the message, and a photo alone is a message', async () => {
    const createTurn = vi.fn().mockResolvedValue({ turn: { id: 't', user_message_id: 'u' }, job: { id: 'j', status: 'queued' } });
    const { chats } = controller(createTurn);
    await chats.send('', PHOTO);
    expect(createTurn).toHaveBeenCalledWith('chat-1', expect.objectContaining({ text: '', photo: PHOTO }));
  });

  it('comes back to the box when it never reached the server', async () => {
    const { appState, chats } = controller(vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    appState.draftPhoto = PHOTO;
    await chats.send('Look!', PHOTO);
    expect(appState.draftPhoto).toBe(PHOTO);
  });

  it('is offered only where the model can see it', () => {
    expect(canSendPhoto('gemma3', ['gemma3', 'llava'])).toBe(true);
    expect(canSendPhoto('llama3', ['gemma3'])).toBe(false);
    expect(canSendPhoto('', ['gemma3'])).toBe(false);
  });
});

describe('the camera button', () => {
  afterEach(() => {
    state.draftPhoto = null;
    state.phase = 'idle';
  });

  function composer(canSendPhotos: boolean) {
    state.phase = 'idle';
    state.settings = { stt_provider: 'disabled' } as Settings;
    return composerView('Nova', {
      chat: {} as ChatController,
      loop: { end: vi.fn(), running: false } as unknown as ConversationLoop,
      recording: {} as RecordingController,
      stopSpeaking: () => undefined,
      render: () => undefined,
      canSendPhotos,
    });
  }

  it('appears only when the photo would be seen', () => {
    expect(composer(true).querySelector('[data-testid="photo-attach"]')).not.toBeNull();
    // A photo the model cannot see would get a reply about something it never saw.
    expect(composer(false).querySelector('[data-testid="photo-attach"]')).toBeNull();
  });

  it('shows the chosen photo with a way to take it back', () => {
    state.draftPhoto = PHOTO;
    const node = composer(true);
    expect(node.querySelector<HTMLImageElement>('[data-testid="composer-photo"] img')?.src).toBe(PHOTO);
    node.querySelector<HTMLButtonElement>('[data-testid="composer-photo"] button')?.click();
    expect(state.draftPhoto).toBeNull();
  });
});

describe('a photo in the conversation', () => {
  it('is shown in the message it was sent with', () => {
    const appState = createState();
    const renderer = new ChatRenderer({} as MediaController, { stop: vi.fn() } as unknown as PlaybackController, () => undefined, appState);
    const node = renderer.message(
      { id: 'u', role: 'user', text: '', created_at: 1, photos: [{ id: 'p1', url: '/api/v1/photos/p1', width: 800, height: 600 }] },
      null,
    );
    expect(node?.querySelector<HTMLImageElement>('img.msg-photo')?.getAttribute('src')).toBe('/api/v1/photos/p1');
  });
});
